#!/usr/bin/env python3
"""
tests/test_scheduler.py - Verify scheduler.py (v6.0.0)

Runs 10 checks in isolation. No real time.sleep, no real subprocess.
The boot-stall regression test lives separately in tests/test_boot_stall.py.

  1.  register_recurring_creates_job
        register_job(cadence="every 5 minutes") wires a real schedule entry
        and exposes it through scheduler.jobs() with next_run populated.

  2.  register_kickoff_queues_only
        register_job(kickoff=True) does NOT fire until boot_ready() is called.
        Verified by checking the mock Timer factory was never invoked.

  3.  boot_ready_dispatches_kickoffs_with_delays
        After boot_ready(), every queued kickoff is handed to the Timer
        factory exactly once with the configured delay.

  4.  boot_ready_idempotent
        Calling boot_ready() twice does not double-dispatch kickoffs.

  5.  cadence_parser_every_n_minutes
        "every 15 minutes" parses; "every 1 minute" parses (singular form).

  6.  cadence_parser_daily_time
        "daily 09:00" parses correctly.

  7.  cadence_parser_weekly_day_time
        "weekly mon 03:00" parses correctly.

  8.  cadence_parser_invalid_raises
        Bad cadence strings raise CadenceError at registration (not later).

  9.  jobs_metadata_complete
        scheduler.jobs() returns name, owner, cadence, kickoff, kickoff_delay,
        next_run, last_run, last_status for every registered job. Status
        updates after the wrapped function runs. Exception in fn is recorded
        in last_status and SWALLOWED (does not propagate to caller).

  10. exception_isolation_protects_other_jobs   (v6.0.0 step 4.8.7)
        A buggy job in the middle of a run_pending() cycle must NOT prevent
        subsequent jobs from running. Verified by registering good/bad/good
        jobs and asserting all three wrapped functions invoke without raising,
        and the two good ones actually executed their fn() bodies.

Exit code 0 = all 10 pass. Non-zero = at least one failed.

Run from project root:
    python tests/test_scheduler.py
"""

from __future__ import annotations

import os
import sys
import traceback

# Path resolution — works whether we run from project root or tests/ folder
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)

from scheduler import Scheduler, CadenceError    # noqa: E402

# Use the sandbox stub locally; the real schedule module on the user's machine
# exposes the same API surface.
try:
    import schedule    # noqa: F401
    _schedule_lib = schedule
    _SCHEDULE_SOURCE = "real"
except ImportError:
    import _schedule_stub as _schedule_lib    # type: ignore
    _SCHEDULE_SOURCE = "stub"


# ── Mock Timer factory ───────────────────────────────────────────────────────
# Captures (delay, fn) without ever firing real threads. Tests can manually
# fire timers to simulate the delay elapsing.
class _MockTimer:
    instances: list["_MockTimer"] = []

    def __init__(self, delay, fn):
        self.delay = delay
        self.fn = fn
        self.daemon = False
        self.name = ""
        self.started = False
        self.fired = False
        _MockTimer.instances.append(self)

    def start(self):
        self.started = True

    def fire(self):
        """Test helper: simulate the delay elapsing and the callback firing."""
        if not self.started:
            raise RuntimeError("Timer.fire() called before Timer.start()")
        self.fired = True
        self.fn()

    @classmethod
    def reset(cls):
        cls.instances = []


def _fresh_scheduler():
    """Brand-new Scheduler with a fresh schedule lib state and clean Timer mock."""
    _schedule_lib.clear()
    _MockTimer.reset()
    return Scheduler(_schedule_lib, timer_factory=_MockTimer)


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_register_recurring_creates_job():
    s = _fresh_scheduler()
    s.register_job(
        name="t1.tick",
        fn=lambda: None,
        cadence="every 5 minutes",
        owner="t1",
    )
    jobs = s.jobs()
    assert len(jobs) == 1, f"expected 1 job, got {len(jobs)}"
    j = jobs[0]
    assert j["name"]    == "t1.tick"
    assert j["owner"]   == "t1"
    assert j["cadence"] == "every 5 minutes"
    assert j["kickoff"] is False
    assert j["next_run"] is not None, "next_run should be populated by schedule lib"
    assert j["last_run"] is None
    assert j["last_status"] is None


def t_register_kickoff_queues_only():
    s = _fresh_scheduler()
    fired = []
    s.register_job(
        name="t2.first_check",
        fn=lambda: fired.append(1),
        cadence="every 10 minutes",
        kickoff=True,
        kickoff_delay=30,
        owner="t2",
    )
    # Before boot_ready: NO timers should have been created
    assert len(_MockTimer.instances) == 0, (
        f"kickoff fired before boot_ready — got {len(_MockTimer.instances)} timers"
    )
    assert fired == [], "kickoff function should not have run yet"
    assert s.is_ready is False


def t_boot_ready_dispatches_kickoffs_with_delays():
    s = _fresh_scheduler()
    fired = []

    s.register_job(
        name="t3.bestbuy",
        fn=lambda: fired.append("bestbuy"),
        cadence="every 10 minutes",
        kickoff=True, kickoff_delay=30, owner="t3",
    )
    s.register_job(
        name="t3.amazon",
        fn=lambda: fired.append("amazon"),
        cadence="every 15 minutes",
        kickoff=True, kickoff_delay=90, owner="t3",
    )
    s.register_job(
        name="t3.costco",
        fn=lambda: fired.append("costco"),
        cadence="every 15 minutes",
        kickoff=True, kickoff_delay=150, owner="t3",
    )
    # Plus one non-kickoff job — must NOT be queued for kickoff
    s.register_job(
        name="t3.recurring_only",
        fn=lambda: fired.append("recurring"),
        cadence="every 5 minutes",
        owner="t3",
    )

    s.boot_ready()

    # Three kickoff timers, in registration order
    assert len(_MockTimer.instances) == 3, (
        f"expected 3 kickoff timers, got {len(_MockTimer.instances)}"
    )
    delays = [t.delay for t in _MockTimer.instances]
    assert delays == [30, 90, 150], f"unexpected delays: {delays}"

    # Each timer was started but not fired automatically
    for t in _MockTimer.instances:
        assert t.started is True, "Timer should have been started by boot_ready()"
        assert t.fired   is False, "Timer should not have fired before its delay"

    # Simulate the delays elapsing — fire each in order
    for t in _MockTimer.instances:
        t.fire()

    assert fired == ["bestbuy", "amazon", "costco"], (
        f"fired in wrong order or wrong items: {fired}"
    )
    assert s.is_ready is True


def t_boot_ready_idempotent():
    s = _fresh_scheduler()
    s.register_job(
        name="t4.kick",
        fn=lambda: None,
        kickoff=True,
        kickoff_delay=30,
        owner="t4",
    )
    s.boot_ready()
    first_count = len(_MockTimer.instances)

    # Second call should be a no-op
    s.boot_ready()
    second_count = len(_MockTimer.instances)

    assert first_count  == 1, f"first boot_ready should create 1 timer, got {first_count}"
    assert second_count == 1, (
        f"second boot_ready double-dispatched — got {second_count} timers"
    )


def t_cadence_parser_every_n_minutes():
    s = _fresh_scheduler()

    # Plural form
    s.register_job(name="t5a", fn=lambda: None, cadence="every 15 minutes")
    # Singular form (regex allows optional 's')
    s.register_job(name="t5b", fn=lambda: None, cadence="every 1 minute")
    # Hours
    s.register_job(name="t5c", fn=lambda: None, cadence="every 12 hours")

    jobs = {j["name"]: j for j in s.jobs()}
    assert jobs["t5a"]["cadence"] == "every 15 minutes"
    assert jobs["t5b"]["cadence"] == "every 1 minute"
    assert jobs["t5c"]["cadence"] == "every 12 hours"
    # All three should have non-None next_run
    for name in ("t5a", "t5b", "t5c"):
        assert jobs[name]["next_run"] is not None, f"{name} next_run is None"


def t_cadence_parser_daily_time():
    s = _fresh_scheduler()
    s.register_job(name="t6", fn=lambda: None, cadence="daily 09:00", owner="t6")
    j = s.jobs()[0]
    assert j["cadence"] == "daily 09:00"
    assert j["next_run"] is not None


def t_cadence_parser_weekly_day_time():
    s = _fresh_scheduler()
    # Test all 7 days parse without error
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    for i, d in enumerate(days):
        s.register_job(
            name=f"t7.{d}",
            fn=lambda: None,
            cadence=f"weekly {d} 03:00",
            owner="t7",
        )
    jobs = s.jobs()
    assert len(jobs) == 7, f"expected 7 weekly jobs, got {len(jobs)}"
    for j in jobs:
        assert j["cadence"].startswith("weekly ")
        assert j["next_run"] is not None


def t_cadence_parser_invalid_raises():
    s = _fresh_scheduler()
    bad_cases = [
        "every banana",
        "every 0 minutes",       # zero is rejected
        "daily 25:00",           # bad hour
        "daily 09:99",           # bad minute
        "weekly funday 09:00",   # bad day
        "evry 15 minutes",       # typo on 'every'
        "every 15 mins",         # 'mins' not allowed (only minutes/minute)
        "",                      # empty string
    ]
    for bad in bad_cases:
        try:
            s.register_job(name=f"bad_{bad[:5]}", fn=lambda: None, cadence=bad)
        except (CadenceError, ValueError):
            continue
        raise AssertionError(f"cadence {bad!r} should have raised, but didn't")

    # Non-string, non-callable cadence
    try:
        s.register_job(name="bad_int", fn=lambda: None, cadence=15)
    except ValueError:
        pass
    else:
        raise AssertionError("cadence=15 (int) should have raised ValueError")

    # Callable that returns something without .do()
    try:
        s.register_job(
            name="bad_callable",
            fn=lambda: None,
            cadence=lambda sched: "not a job builder",
        )
    except CadenceError:
        pass
    else:
        raise AssertionError("callable returning non-Job should have raised")


def t_jobs_metadata_complete():
    s = _fresh_scheduler()
    runs = []

    def my_fn():
        runs.append(1)

    s.register_job(
        name="t9.metadata",
        fn=my_fn,
        cadence="every 5 minutes",
        kickoff=True,
        kickoff_delay=42,
        owner="t9_owner",
    )

    j = s.jobs()[0]
    expected_keys = {
        "name", "owner", "cadence", "kickoff", "kickoff_delay",
        "next_run", "last_run", "last_status",
    }
    assert set(j.keys()) == expected_keys, (
        f"missing/extra keys: {set(j.keys()) ^ expected_keys}"
    )
    assert j["name"]          == "t9.metadata"
    assert j["owner"]         == "t9_owner"
    assert j["cadence"]       == "every 5 minutes"
    assert j["kickoff"]       is True
    assert j["kickoff_delay"] == 42
    assert j["last_run"]      is None    # hasn't run yet
    assert j["last_status"]   is None

    # Now simulate the recurring fn firing once. We grab the wrapped function
    # from the scheduler's internal metadata rather than the schedule library's
    # internals — this keeps the test independent of whether `schedule.jobs` is
    # a list (real lib) or a function (test stub) and whether the fn is stored
    # at `.job_func` (real) or `.fn` (stub).
    wrapped = s._jobs_meta["t9.metadata"]["_wrapped_fn"]
    wrapped()

    j = [x for x in s.jobs() if x["name"] == "t9.metadata"][0]
    assert j["last_run"]    is not None,  "last_run should be set after a run"
    assert j["last_status"] == "ok",      f"last_status: {j['last_status']!r}"

    # Now simulate a failing run — last_status should reflect the error
    def boom():
        raise RuntimeError("something blew up")

    s.register_job(name="t9.boom", fn=boom, cadence="every 5 minutes")
    boom_wrapped = s._jobs_meta["t9.boom"]["_wrapped_fn"]
    # v6.0.0 step 4.8.7: exception isolation — wrapped fn now SWALLOWS
    # exceptions so a buggy plugin can't crash the tracker via
    # schedule.run_pending(). last_status still records the error for
    # diagnostics, and log.exception() still logs the full traceback.
    result = boom_wrapped()
    assert result is None, (
        f"wrapped fn should return None when fn raises, got: {result!r}"
    )
    boom_meta = [j for j in s.jobs() if j["name"] == "t9.boom"][0]
    assert boom_meta["last_status"].startswith("error: RuntimeError"), (
        f"unexpected status after failure: {boom_meta['last_status']!r}"
    )
    assert "something blew up" in boom_meta["last_status"], (
        f"last_status should include error message: {boom_meta['last_status']!r}"
    )



# ─────────────────────────────────────────────────────────────────────────────
# Test 10 — v6.0.0 step 4.8.7: exception isolation across run_pending() cycle
# ─────────────────────────────────────────────────────────────────────────────
def t_exception_isolation_protects_other_jobs():
    """A buggy job must not prevent subsequent jobs from running.

    This is the architectural regression test for the 08:30 strftime crash:
    one plugin's ValueError used to bubble up through schedule.run_pending()
    and kill the entire tracker. After step 4.8.7, the wrapped fn swallows
    exceptions so other jobs in the same cycle still execute.
    """
    s = _fresh_scheduler()

    runs: list[str] = []

    def good_a():
        runs.append("a")

    def boom():
        raise RuntimeError("simulated plugin bug")

    def good_b():
        runs.append("b")

    s.register_job(name="iso.good_a", fn=good_a, cadence="every 5 minutes")
    s.register_job(name="iso.boom",   fn=boom,   cadence="every 5 minutes")
    s.register_job(name="iso.good_b", fn=good_b, cadence="every 5 minutes")

    # Simulate schedule.run_pending() firing all three sequentially.
    # CRITICAL: the buggy job's exception MUST NOT bubble out of wrapped().
    # If it did, this whole loop would abort partway through.
    for name in ["iso.good_a", "iso.boom", "iso.good_b"]:
        wrapped = s._jobs_meta[name]["_wrapped_fn"]
        result = wrapped()    # must not raise
        # All wrapped fns return None on either success-with-no-return or error
        assert result is None, f"{name}: unexpected return: {result!r}"

    # Both good jobs ran despite the boom in the middle
    assert runs == ["a", "b"], (
        f"expected good_a and good_b to both run, got: {runs}"
    )

    # Boom's status correctly captures the error
    boom_meta = [j for j in s.jobs() if j["name"] == "iso.boom"][0]
    assert boom_meta["last_status"].startswith("error: RuntimeError"), (
        f"boom status should reflect error, got: {boom_meta['last_status']!r}"
    )
    assert "simulated plugin bug" in boom_meta["last_status"], (
        f"boom status should include error msg, got: {boom_meta['last_status']!r}"
    )
    assert boom_meta["last_run"] is not None, "boom last_run should be set"

    # Good jobs status is "ok"
    for n in ["iso.good_a", "iso.good_b"]:
        meta = [j for j in s.jobs() if j["name"] == n][0]
        assert meta["last_status"] == "ok", (
            f"{n} should be ok, got: {meta['last_status']!r}"
        )
        assert meta["last_run"] is not None, f"{n} last_run should be set"


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(f" v6.0.0 scheduler.py unit tests  (using {_SCHEDULE_SOURCE} schedule lib)")
    print("=" * 70)

    tests = [
        ("register_recurring_creates_job",          t_register_recurring_creates_job),
        ("register_kickoff_queues_only",            t_register_kickoff_queues_only),
        ("boot_ready_dispatches_kickoffs_with_delays",
                                                    t_boot_ready_dispatches_kickoffs_with_delays),
        ("boot_ready_idempotent",                   t_boot_ready_idempotent),
        ("cadence_parser_every_n_minutes",          t_cadence_parser_every_n_minutes),
        ("cadence_parser_daily_time",               t_cadence_parser_daily_time),
        ("cadence_parser_weekly_day_time",          t_cadence_parser_weekly_day_time),
        ("cadence_parser_invalid_raises",           t_cadence_parser_invalid_raises),
        ("jobs_metadata_complete",                  t_jobs_metadata_complete),
        ("exception_isolation_protects_other_jobs",  t_exception_isolation_protects_other_jobs),
    ]

    passed = failed = 0
    for i, (name, fn) in enumerate(tests, start=1):
        try:
            fn()
            print(f"  [{i:2d}] PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  [{i:2d}] FAIL  {name}")
            print(f"         {e}")
            failed += 1
        except Exception as e:
            print(f"  [{i:2d}] ERROR {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("-" * 70)
    print(f"  Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
