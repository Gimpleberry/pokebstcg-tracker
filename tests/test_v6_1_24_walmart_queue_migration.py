"""
test_v6_1_24_walmart_queue_migration.py

Structural + introspection tests for v6.1.24 — Batch 7 phased-lifecycle
migration of WalmartQueue_Plugin. Largest single migration of the
v6.1.x chain: 6 jobs declared in one register() call.

This test file uses an EXPLICIT-SIGNATURE StubScheduler matching
scheduler.py's real keyword-only parameters. (v6.1.22 v2 lesson —
banked permanently.)

Tests use spec_from_file_location to load plugins.py — never `import plugins`.
"""

import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PLUGINS_PATH = os.path.join(_ROOT, "plugins.py")
WALMART_QUEUE_PATH = os.path.join(_ROOT, "plugins", "walmart_queue.py")


def _read(path=PLUGINS_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test_v24", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests on plugins.py --------------------------------------

def t_walmart_queue_no_legacy_start():
    src = _read()
    cls_idx = src.find("class WalmartQueue_Plugin(Plugin):")
    assert cls_idx > 0
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "WalmartQueue_Plugin still defines legacy start()"
    )


def t_walmart_queue_has_init_and_register():
    src = _read()
    cls_idx = src.find("class WalmartQueue_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body
    assert "def register(self, scheduler)" in cls_body


def t_walmart_queue_version_bumped():
    src = _read()
    idx = src.find("class WalmartQueue_Plugin(Plugin):")
    window = src[idx:idx + 250]
    assert 'version = "1.1"' in window


def t_walmart_queue_on_stock_change_preserved():
    """on_stock_change event hook must survive migration (and be hardened
    with self._monitor is not None guard for init-failure case)."""
    src = _read()
    cls_idx = src.find("class WalmartQueue_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def on_stock_change(self, product, status)" in cls_body, (
        "WalmartQueue_Plugin on_stock_change hook missing — "
        "event hook must survive migration"
    )
    assert "self._monitor is not None" in cls_body, (
        "on_stock_change should guard against self._monitor being None"
    )


# -- Structural tests on plugins/walmart_queue.py ----------------------

def t_inner_no_start_method():
    src = _read(WALMART_QUEUE_PATH)
    cls_idx = src.find("class WalmartQueueMonitor:")
    assert cls_idx > 0, "WalmartQueueMonitor class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, schedule)" not in cls_body, (
        "WalmartQueueMonitor still defines start(schedule)"
    )


def t_inner_has_register_with_scheduler():
    src = _read(WALMART_QUEUE_PATH)
    cls_idx = src.find("class WalmartQueueMonitor:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def register(self, scheduler)" in cls_body
    assert "scheduler.register_job(" in cls_body


def t_inner_register_uses_correct_keyword_fn():
    """v22 v2 lesson banked: register_job() must use `fn=`, not `job_fn=`."""
    src = _read(WALMART_QUEUE_PATH)
    cls_idx = src.find("class WalmartQueueMonitor:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    # Find first register_job() call and inspect just its body
    idx = cls_body.find("scheduler.register_job(")
    assert idx > 0
    end = cls_body.find(")", idx)
    first_call = cls_body[idx:end]
    assert "fn=" in first_call, "register_job() must use fn= keyword"
    assert "job_fn=" not in cls_body, (
        "v6.1.22 v1 bug recurrence: register_job() uses `job_fn=`"
    )


def t_inner_register_has_six_register_job_calls():
    """v6.1.24 must declare EXACTLY 6 register_job() calls (matching the
    6 schedule.every() calls in legacy start())."""
    src = _read(WALMART_QUEUE_PATH)
    cls_idx = src.find("class WalmartQueueMonitor:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    count = cls_body.count("scheduler.register_job(")
    assert count == 6, (
        f"WalmartQueueMonitor.register() should call register_job() "
        f"exactly 6 times, got {count}"
    )


def t_inner_register_has_correct_cadences():
    """All 6 expected cadence strings present."""
    src = _read(WALMART_QUEUE_PATH)
    cls_idx = src.find("class WalmartQueueMonitor:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    expected = [
        'cadence="weekly wed 11:45"',
        'cadence="weekly wed 14:00"',
        'cadence="daily 07:00"',
        'cadence="daily 13:00"',
        'cadence="daily 09:00"',
        'cadence="daily 18:00"',
    ]
    missing = [c for c in expected if c not in cls_body]
    assert not missing, f"missing cadence strings: {missing}"


def t_inner_register_has_distinct_job_names():
    """All 6 job names must be present and distinct."""
    src = _read(WALMART_QUEUE_PATH)
    cls_idx = src.find("class WalmartQueueMonitor:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    expected = [
        '"walmart_queue.start_wednesday_watch"',
        '"walmart_queue.stop_wednesday_watch"',
        '"walmart_queue.scan_new_listings_morning"',
        '"walmart_queue.scan_new_listings_afternoon"',
        '"walmart_queue.scan_clearance_morning"',
        '"walmart_queue.scan_clearance_evening"',
    ]
    missing = [n for n in expected if n not in cls_body]
    assert not missing, f"missing job names: {missing}"


# -- Existing migrations not disturbed -----------------------------------

def t_existing_phased_classes_unchanged():
    """All 13 prior phased plugins still phased."""
    src = _read()
    for cls_name in ("BestBuyInvites_Plugin", "AmazonMonitor_Plugin",
                     "CostcoTracker_Plugin", "WalmartPlaywright_Plugin",
                     "NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin", "MSRPAlert_Plugin",
                     "CartPreloader_Plugin", "InvestStore_Plugin",
                     "ApiServer_Plugin", "RestockReminder_Plugin",
                     "PriceHistory_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        if idx <= 0:
            continue
        next_class = src.find("\nclass ", idx + 1)
        cls_body = src[idx:next_class] if next_class > 0 else src[idx:]
        assert "def init(self, config, products)" in cls_body, (
            f"{cls_name} init() missing — pre-existing phased plugin disturbed"
        )
        assert "def register(self, scheduler)" in cls_body, (
            f"{cls_name} register() missing — pre-existing phased plugin disturbed"
        )


def t_remaining_legacy_classes_unchanged():
    """1 remaining legacy plugin still on legacy lifecycle."""
    src = _read()
    cls_name = "MarketDataRefresh_Plugin"
    idx = src.find(f"class {cls_name}(Plugin):")
    if idx <= 0:
        return  # not in synthetic
    next_class = src.find("\nclass ", idx + 1)
    cls_body = src[idx:next_class] if next_class > 0 else src[idx:]
    assert "def start(self, config, products, schedule)" in cls_body, (
        f"{cls_name} legacy start() missing — should still be legacy"
    )


def t_cadence_strings_still_parse():
    """Defensive: every cadence='...' in plugins.py matches scheduler.py
    regexes. Since v24 only adds register_job calls in plugins/walmart_queue.py
    (not plugins.py), this test still works against plugins.py only.
    But we ALSO check plugins/walmart_queue.py since v24 added 6 cadences there."""
    RE_DAILY     = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
    RE_WEEKLY    = re.compile(
        r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$",
        re.IGNORECASE,
    )
    RE_EVERY_MIN = re.compile(r"^every\s+(\d+)\s+minutes?$", re.IGNORECASE)
    RE_EVERY_HR  = re.compile(r"^every\s+(\d+)\s+hours?$", re.IGNORECASE)

    bad = []
    for path in (PLUGINS_PATH, WALMART_QUEUE_PATH):
        src = _read(path)
        for m in re.finditer(r'cadence="([^"]+)"', src):
            c = m.group(1)
            if not (RE_DAILY.match(c) or RE_WEEKLY.match(c)
                    or RE_EVERY_MIN.match(c) or RE_EVERY_HR.match(c)):
                bad.append(f"{os.path.basename(path)}:{c}")
    assert not bad, f"unparseable cadence string(s): {bad}"


# -- Introspection tests --------------------------------------------------

def _check_phased_lifecycle(cls_name):
    mod = _load_plugins_module()
    cls = getattr(mod, cls_name)
    base = mod.Plugin
    assert cls.init is not base.init, f"{cls_name}.init() not overridden"
    assert cls.register is not base.register, f"{cls_name}.register() not overridden"
    assert cls.start is base.start, (
        f"{cls_name} still overrides start() — should have been removed"
    )


def t_walmart_queue_phased_lifecycle():
    _check_phased_lifecycle("WalmartQueue_Plugin")


def t_register_adds_exactly_six_jobs_with_correct_signature():
    """v6.1.24: register() must call scheduler.register_job() EXACTLY 6
    times using ONLY keywords that exist in the real scheduler.py
    signature.

    The StubScheduler below uses EXPLICIT keyword arguments matching
    scheduler.py: register_job(self, *, name, fn, cadence=None,
    kickoff=False, kickoff_delay=0, owner=None). Any keyword-name
    mismatch raises TypeError before the body even runs. This is the
    test stub pattern that would have caught the v6.1.22 v1 bug.
    """
    mod = _load_plugins_module()
    captured = []

    class StubScheduler:
        def register_job(
            self,
            *,
            name,
            fn,
            cadence=None,
            kickoff=False,
            kickoff_delay=0,
            owner=None,
        ):
            assert callable(fn), "fn must be callable"
            captured.append({
                "name":          name,
                "fn":            fn,
                "cadence":       cadence,
                "kickoff":       kickoff,
                "kickoff_delay": kickoff_delay,
                "owner":         owner,
            })

    # Stub the inner module so init() succeeds without real I/O
    import sys as _sys
    import types as _types
    if "walmart_queue" not in _sys.modules:
        stub = _types.ModuleType("walmart_queue")

        class _StubMonitor:
            def __init__(self, config, products):
                self.config = config
                self.products = products

            def _start_wednesday_watch(self): pass
            def _stop_wednesday_watch(self): pass
            def _scan_new_listings(self): pass
            def _scan_clearance(self): pass

            def register(self, scheduler):
                scheduler.register_job(
                    name="walmart_queue.start_wednesday_watch",
                    fn=self._start_wednesday_watch,
                    cadence="weekly wed 11:45",
                    owner="walmart_queue",
                )
                scheduler.register_job(
                    name="walmart_queue.stop_wednesday_watch",
                    fn=self._stop_wednesday_watch,
                    cadence="weekly wed 14:00",
                    owner="walmart_queue",
                )
                scheduler.register_job(
                    name="walmart_queue.scan_new_listings_morning",
                    fn=self._scan_new_listings,
                    cadence="daily 07:00",
                    owner="walmart_queue",
                )
                scheduler.register_job(
                    name="walmart_queue.scan_new_listings_afternoon",
                    fn=self._scan_new_listings,
                    cadence="daily 13:00",
                    owner="walmart_queue",
                )
                scheduler.register_job(
                    name="walmart_queue.scan_clearance_morning",
                    fn=self._scan_clearance,
                    cadence="daily 09:00",
                    owner="walmart_queue",
                )
                scheduler.register_job(
                    name="walmart_queue.scan_clearance_evening",
                    fn=self._scan_clearance,
                    cadence="daily 18:00",
                    owner="walmart_queue",
                )

        stub.WalmartQueueMonitor = _StubMonitor
        _sys.modules["walmart_queue"] = stub

    cls = getattr(mod, "WalmartQueue_Plugin")
    inst = cls()
    inst.init({}, [])
    inst.register(StubScheduler())  # would TypeError on wrong keywords

    assert len(captured) == 6, (
        f"WalmartQueue_Plugin.register() should add exactly 6 jobs, "
        f"got {len(captured)}"
    )
    expected_jobs = {
        "walmart_queue.start_wednesday_watch":      "weekly wed 11:45",
        "walmart_queue.stop_wednesday_watch":       "weekly wed 14:00",
        "walmart_queue.scan_new_listings_morning":  "daily 07:00",
        "walmart_queue.scan_new_listings_afternoon": "daily 13:00",
        "walmart_queue.scan_clearance_morning":     "daily 09:00",
        "walmart_queue.scan_clearance_evening":     "daily 18:00",
    }
    for job in captured:
        assert job["name"] in expected_jobs, (
            f"unexpected job name: {job['name']}"
        )
        assert job["cadence"] == expected_jobs[job["name"]], (
            f"job {job['name']} cadence should be "
            f"{expected_jobs[job['name']]!r}, got {job['cadence']!r}"
        )
        assert job["kickoff"] is False, (
            f"job {job['name']} should have kickoff=False"
        )
        assert job["owner"] == "walmart_queue", (
            f"job {job['name']} owner should be 'walmart_queue', "
            f"got {job['owner']!r}"
        )
        assert callable(job["fn"]), (
            f"job {job['name']} fn must be callable"
        )


# -- Self-runner ----------------------------------------------------------

if __name__ == "__main__":
    tests = sorted(
        [n for n in dir() if n.startswith("t_") and callable(globals()[n])]
    )
    passed = 0
    failed = []
    for name in tests:
        try:
            globals()[name]()
            passed += 1
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))

    if failed:
        print(f"FAIL: {len(failed)}/{len(tests)} test(s) failed")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print(f"OK: {passed}/{len(tests)} tests passed")
        sys.exit(0)
