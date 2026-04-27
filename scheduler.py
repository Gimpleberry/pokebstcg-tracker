#!/usr/bin/env python3
"""
scheduler.py - Unified Scheduler (v6.0.0)
Part of Keith's PokeBS Tracker.

A thin coordinator over the `schedule` library. Adds:
  - Boot-phase ordering (register-time vs kickoff-time separation)
  - Staggered first-run dispatch via threading.Timer
  - Job metadata for diagnostics (last_run, last_status, next_run)
  - Restrictive cadence parser (4 patterns + callable escape hatch)

v6.0.0 scope is intentionally minimal. Resource pools (Playwright semaphore,
pokemontcg.io token bucket) and the introspection panel land in v6.0.2.

USAGE (from plugins.py during boot):

    import schedule
    from scheduler import Scheduler

    scheduler = Scheduler(schedule)

    # Phase 1: each plugin's register() declares jobs (no I/O, no blocking)
    plugin.register(scheduler)
        # which calls:
        # scheduler.register_job(
        #     name="amazon_monitor.check_all",
        #     fn=self._check_all,
        #     cadence="every 15 minutes",
        #     kickoff=True,
        #     kickoff_delay=90,
        #     owner="amazon_monitor",
        # )

    # Phase 3: tracker.py calls boot_ready() AFTER api_server is bound
    scheduler.boot_ready()
        # ...which dispatches all kickoff jobs at their staggered delays
        # in background threading.Timer instances.

CADENCE PATTERNS (restrictive — bad strings raise ValueError at boot):
  "every N minutes"             -> schedule.every(N).minutes
  "every N hours"               -> schedule.every(N).hours
  "daily HH:MM"                 -> schedule.every().day.at("HH:MM")
  "weekly <day> HH:MM"          -> schedule.every().<day>.at("HH:MM")
                                   (day = mon|tue|wed|thu|fri|sat|sun)
  callable(schedule_module)     -> escape hatch for edge cases like
                                   "first Monday of the month"

BACK-COMPAT NOTE:
  This module does not replace the `schedule` library. It wraps it.
  Plugins that haven't migrated to register() continue to receive the
  raw schedule module via the legacy start(config, products, schedule) shim
  in plugins.py. The same `schedule` instance is used either way, so
  cadences set via legacy start() and cadences set via register_job()
  share one scheduler clock.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# ── Cadence patterns ─────────────────────────────────────────────────────────
# Compiled once at module load; case-insensitive.
_RE_EVERY_MINUTES = re.compile(r"^every\s+(\d+)\s+minutes?$", re.IGNORECASE)
_RE_EVERY_HOURS   = re.compile(r"^every\s+(\d+)\s+hours?$",   re.IGNORECASE)
_RE_DAILY         = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
_RE_WEEKLY        = re.compile(
    r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$",
    re.IGNORECASE,
)

_DAY_SHORT_TO_LONG = {
    "mon": "monday",   "tue": "tuesday", "wed": "wednesday",
    "thu": "thursday", "fri": "friday",  "sat": "saturday", "sun": "sunday",
}


class CadenceError(ValueError):
    """Raised when a cadence string can't be parsed.
    Subclass of ValueError so existing 'except ValueError' callers still catch it."""


class Scheduler:
    """
    Thin coordinator over the `schedule` library.

    Constructor takes the schedule module (or anything with the same shape —
    e.g. the test stub) and an optional timer_factory for dependency injection
    in tests. Production code passes the real schedule module and lets
    timer_factory default to threading.Timer.
    """

    def __init__(
        self,
        schedule_lib: Any,
        timer_factory: Optional[Callable] = None,
    ) -> None:
        self._schedule = schedule_lib
        self._timer_factory = timer_factory or threading.Timer
        self._jobs_meta: dict[str, dict] = {}
        self._kickoff_queue: list[tuple[int, Callable, str]] = []
        self._timers: list = []        # references so they don't get GC'd
        self._ready = False
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────────────

    def register_job(
        self,
        name: str,
        fn: Callable,
        cadence: Optional[Any] = None,         # str | callable | None
        kickoff: bool = False,
        kickoff_delay: int = 0,
        owner: Optional[str] = None,
    ) -> None:
        """
        Register a job.

        - If `cadence` is provided, the recurring schedule is wired immediately
          via the underlying schedule library.
        - If `kickoff=True`, the job is queued for staggered first-run dispatch.
          It will fire `kickoff_delay` seconds after `boot_ready()` is called.
        - At least one of cadence or kickoff must be set, otherwise the job
          would never run and registering it is pointless (raises ValueError).

        Args:
            name:           Unique job name (e.g. "amazon_monitor.check_all").
            fn:             Zero-argument callable.
            cadence:        Cadence string or callable. See module docstring.
            kickoff:        If True, schedule a one-shot first run.
            kickoff_delay:  Seconds to wait after boot_ready() before kickoff.
                            Must be >= 0.
            owner:          Plugin name, for diagnostics. Optional.

        Raises:
            ValueError:     name empty / fn not callable / duplicate name /
                            negative kickoff_delay / no cadence and no kickoff.
            CadenceError:   cadence string doesn't match a known pattern.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Job name must be a non-empty string, got: {name!r}")
        if not callable(fn):
            raise ValueError(f"Job fn must be callable, got: {fn!r}")
        if kickoff_delay < 0:
            raise ValueError(f"kickoff_delay must be >= 0, got: {kickoff_delay}")
        if cadence is None and not kickoff:
            raise ValueError(
                f"Job {name!r} has no cadence and no kickoff — it would never run."
            )
        if name in self._jobs_meta:
            raise ValueError(f"Job already registered: {name!r}")

        # Wrap fn so we can record last_run / last_status without changing
        # the original function's behavior or signature.
        wrapped = self._wrap_with_status(name, fn)

        # Wire recurring cadence (if any). Bad cadence raises here at boot,
        # not later at runtime — fail loudly per tenet #3.
        job_ref = None
        cadence_display = self._stringify_cadence(cadence)
        if cadence is not None:
            job_builder = self._parse_cadence(cadence, name)
            job_ref = job_builder.do(wrapped)

        # Queue kickoff if requested.
        if kickoff:
            with self._lock:
                self._kickoff_queue.append((kickoff_delay, wrapped, name))

        # Record metadata.
        self._jobs_meta[name] = {
            "name":          name,
            "owner":         owner or "unknown",
            "cadence":       cadence_display,
            "kickoff":       kickoff,
            "kickoff_delay": kickoff_delay,
            "job_ref":       job_ref,
            "last_run":      None,
            "last_status":   None,
            # Internal: the schedule-lib-agnostic wrapped function.
            # Tests invoke this directly to verify last_run/last_status
            # without depending on the underlying schedule library's
            # internal attribute names (which differ between real lib
            # and the test stub).
            "_wrapped_fn":   wrapped,
        }
        log.info(
            f"[scheduler] registered: {name} "
            f"(owner={owner or 'unknown'}, cadence={cadence_display}, "
            f"kickoff={kickoff}{f', delay={kickoff_delay}s' if kickoff else ''})"
        )

    def boot_ready(self) -> None:
        """
        Dispatch all queued kickoff jobs at their configured delays.

        Called once by tracker.py after:
          - All plugins have registered their jobs
          - api_server has bound its port
          - The dashboard is therefore already serveable

        Idempotent: a second call logs a debug message and returns.
        """
        with self._lock:
            if self._ready:
                log.debug("[scheduler] boot_ready called twice — ignored")
                return
            self._ready = True
            queue_snapshot = list(self._kickoff_queue)

        log.info(
            f"[scheduler] boot_ready — dispatching {len(queue_snapshot)} "
            f"kickoff job(s)"
        )
        for delay, fn, name in queue_snapshot:
            timer = self._timer_factory(delay, fn)
            try:
                timer.daemon = True
            except AttributeError:
                # Some Timer-likes (test mocks) may not support daemon.
                pass
            try:
                timer.name = f"kickoff_{name}"
            except AttributeError:
                pass
            timer.start()
            self._timers.append(timer)
            log.info(f"[scheduler]   kickoff queued: {name} @ T+{delay}s")

    def jobs(self) -> list[dict]:
        """
        Return a snapshot of all registered jobs with current metadata.

        Each entry: {name, owner, cadence, kickoff, kickoff_delay,
                     next_run, last_run, last_status}

        Used by tests today; will feed the introspection panel in v6.0.2.
        """
        result = []
        for name, meta in self._jobs_meta.items():
            result.append({
                "name":          meta["name"],
                "owner":         meta["owner"],
                "cadence":       meta["cadence"],
                "kickoff":       meta["kickoff"],
                "kickoff_delay": meta["kickoff_delay"],
                "next_run":      meta["job_ref"].next_run if meta["job_ref"] else None,
                "last_run":      meta["last_run"],
                "last_status":   meta["last_status"],
            })
        return result

    def cancel(self, name: str) -> bool:
        """
        Cancel a registered job. Returns True if cancelled, False if name unknown.

        Mostly intended for tests and admin operations. Plugins should not
        cancel each other's jobs in normal flow.
        """
        if name not in self._jobs_meta:
            return False
        meta = self._jobs_meta[name]
        if meta["job_ref"] is not None:
            try:
                self._schedule.cancel_job(meta["job_ref"])
            except Exception as e:
                log.warning(f"[scheduler] cancel_job error for {name}: {e}")
        del self._jobs_meta[name]
        log.info(f"[scheduler] cancelled: {name}")
        return True

    @property
    def is_ready(self) -> bool:
        """True after boot_ready() has been called."""
        return self._ready

    # ── Internals ───────────────────────────────────────────────────────────

    def _wrap_with_status(self, name: str, fn: Callable) -> Callable:
        """
        Wrap fn so we can record last_run and last_status, and isolate
        exceptions from the rest of the tracker (v6.0.0 step 4.8.7).

        The wrapper:
          - Records last_run timestamp before calling fn()
          - On success: records last_status="ok", returns fn()'s result
          - On exception: records last_status="error: <type>: <msg>",
            logs the full traceback via log.exception(), then SWALLOWS
            the exception (returns None) so it doesn't propagate to
            schedule.run_pending() and crash the entire tracker.

        The swallow is intentional. A buggy plugin must not take down
        the whole process - it gets isolated, logged, and retried on
        the next cadence cycle. Plugins that need to react to their own
        failures should handle exceptions inside fn() before this
        outer wrapper sees them.
        """
        def wrapped():
            self._jobs_meta[name]["last_run"] = datetime.now()
            try:
                result = fn()
                self._jobs_meta[name]["last_status"] = "ok"
                return result
            except Exception as e:
                self._jobs_meta[name]["last_status"] = (
                    f"error: {type(e).__name__}: {e}"
                )
                # Exception isolation (v6.0.0 step 4.8.7): record failure,
                # log full traceback, then SWALLOW so it doesn't propagate
                # to schedule.run_pending() and crash the tracker. A single
                # buggy plugin must not take everything down. The job will
                # retry on its next cadence cycle.
                log.exception(
                    f"[scheduler] job {name!r} raised - isolated, "
                    f"tracker continues"
                )
                return None
        wrapped.__name__ = f"sched_{name}"
        wrapped.__qualname__ = wrapped.__name__
        return wrapped

    def _parse_cadence(self, cadence: Any, job_name: str):
        """
        Parse a cadence into a schedule.Job builder (an object you can .do() on).

        Returns whatever the underlying schedule library's .every()-chain
        returns at the point just before .do(). Raises CadenceError on
        unrecognized strings; raises ValueError on bad input types or
        out-of-range numeric values.
        """
        # Callable escape hatch — for edge cases like "first Monday of month"
        # that don't fit the four patterns. Plugin passes a lambda that
        # builds the schedule directly.
        if callable(cadence):
            try:
                builder = cadence(self._schedule)
            except Exception as e:
                raise CadenceError(
                    f"Job {job_name!r}: callable cadence raised {type(e).__name__}: {e}"
                ) from e
            if not hasattr(builder, "do"):
                raise CadenceError(
                    f"Job {job_name!r}: callable cadence must return a schedule.Job "
                    f"builder (something with a .do() method); got {type(builder).__name__}"
                )
            return builder

        if not isinstance(cadence, str):
            raise ValueError(
                f"Job {job_name!r}: cadence must be a string or callable, "
                f"got {type(cadence).__name__}"
            )

        s = cadence.strip()

        # every N minutes
        m = _RE_EVERY_MINUTES.match(s)
        if m:
            n = int(m.group(1))
            if n < 1:
                raise CadenceError(
                    f"Job {job_name!r}: 'every N minutes' requires N >= 1, got {n}"
                )
            return self._schedule.every(n).minutes

        # every N hours
        m = _RE_EVERY_HOURS.match(s)
        if m:
            n = int(m.group(1))
            if n < 1:
                raise CadenceError(
                    f"Job {job_name!r}: 'every N hours' requires N >= 1, got {n}"
                )
            return self._schedule.every(n).hours

        # daily HH:MM
        m = _RE_DAILY.match(s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if not (0 <= hh < 24) or not (0 <= mm < 60):
                raise CadenceError(
                    f"Job {job_name!r}: invalid time in {cadence!r} "
                    f"(hours 0-23, minutes 0-59)"
                )
            return self._schedule.every().day.at(f"{hh:02d}:{mm:02d}")

        # weekly <day> HH:MM
        m = _RE_WEEKLY.match(s)
        if m:
            day_short = m.group(1).lower()
            hh, mm = int(m.group(2)), int(m.group(3))
            if not (0 <= hh < 24) or not (0 <= mm < 60):
                raise CadenceError(
                    f"Job {job_name!r}: invalid time in {cadence!r} "
                    f"(hours 0-23, minutes 0-59)"
                )
            day_long = _DAY_SHORT_TO_LONG[day_short]
            day_attr = getattr(self._schedule.every(), day_long)
            return day_attr.at(f"{hh:02d}:{mm:02d}")

        raise CadenceError(
            f"Job {job_name!r}: unrecognized cadence {cadence!r}. "
            f"Expected one of: 'every N minutes', 'every N hours', "
            f"'daily HH:MM', 'weekly <day> HH:MM', or a callable."
        )

    @staticmethod
    def _stringify_cadence(cadence: Any) -> Optional[str]:
        """Display-friendly cadence string for logs and metadata."""
        if cadence is None:
            return None
        if callable(cadence):
            return "<callable>"
        return str(cadence)
