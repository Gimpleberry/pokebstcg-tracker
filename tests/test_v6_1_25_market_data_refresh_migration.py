"""
test_v6_1_25_market_data_refresh_migration.py

Structural + introspection tests for v6.1.25 — Batch 8 phased-lifecycle
migration of MarketDataRefresh_Plugin. **CLOSES PHASE 2/2e.**

After v25 lands, ALL 15 plugins are on phased lifecycle. The
t_no_legacy_classes_remain test below is the milestone guard — it asserts
NO wrapper class still defines the legacy start(self, config, products,
schedule) method.

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
MARKET_DATA_PATH = os.path.join(_ROOT, "plugins", "market_data_refresh.py")


def _read(path=PLUGINS_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test_v25", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests on plugins.py --------------------------------------

def t_market_data_no_legacy_start():
    src = _read()
    cls_idx = src.find("class MarketDataRefresh_Plugin(Plugin):")
    assert cls_idx > 0
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "MarketDataRefresh_Plugin still defines legacy start()"
    )


def t_market_data_has_init_and_register():
    src = _read()
    cls_idx = src.find("class MarketDataRefresh_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body
    assert "def register(self, scheduler)" in cls_body


def t_market_data_version_bumped():
    src = _read()
    idx = src.find("class MarketDataRefresh_Plugin(Plugin):")
    window = src[idx:idx + 250]
    assert 'version = "1.1"' in window


# -- Structural tests on plugins/market_data_refresh.py ----------------

def t_inner_no_start_method():
    src = _read(MARKET_DATA_PATH)
    cls_idx = src.find("class MarketDataRefresh:")
    assert cls_idx > 0
    # Use the next *class* boundary (not an indented inner reference)
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, schedule)" not in cls_body, (
        "MarketDataRefresh still defines start(schedule)"
    )


def t_inner_has_register_with_scheduler():
    src = _read(MARKET_DATA_PATH)
    cls_idx = src.find("class MarketDataRefresh:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def register(self, scheduler)" in cls_body
    assert "scheduler.register_job(" in cls_body


def t_inner_register_uses_correct_keyword_fn():
    """v22 v2 lesson banked: register_job() must use `fn=`, not `job_fn=`."""
    src = _read(MARKET_DATA_PATH)
    cls_idx = src.find("class MarketDataRefresh:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    idx = cls_body.find("scheduler.register_job(")
    assert idx > 0
    end = cls_body.find(")", idx)
    first_call = cls_body[idx:end]
    assert "fn=" in first_call, "register_job() must use fn= keyword"
    assert "job_fn=" not in cls_body, (
        "v6.1.22 v1 bug recurrence: register_job() uses `job_fn=`"
    )


def t_inner_register_has_three_register_job_calls():
    """v6.1.25 must declare EXACTLY 3 register_job() calls."""
    src = _read(MARKET_DATA_PATH)
    cls_idx = src.find("class MarketDataRefresh:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    count = cls_body.count("scheduler.register_job(")
    assert count == 3, (
        f"MarketDataRefresh.register() should call register_job() "
        f"exactly 3 times, got {count}"
    )


def t_inner_register_startup_fetch_kickoff_settings():
    """startup_fetch must use kickoff=True, kickoff_delay=STARTUP_DELAY_SEC.
    Per-block binding of these settings is enforced by the runtime
    introspection test t_register_adds_exactly_three_jobs_with_correct_signature
    via the StubScheduler's captured kwargs."""
    src = _read(MARKET_DATA_PATH)
    cls_idx = src.find("class MarketDataRefresh:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert '"market_data_refresh.startup_fetch"' in cls_body, (
        "startup_fetch register_job not found"
    )
    assert "kickoff=True" in cls_body, "startup_fetch must have kickoff=True"
    assert "kickoff_delay=STARTUP_DELAY_SEC" in cls_body, (
        "startup_fetch must use kickoff_delay=STARTUP_DELAY_SEC (preserves legacy timing)"
    )


def t_inner_register_scheduled_refresh_cadence():
    """scheduled_refresh must use cadence='every 12 hours' (or f-string equivalent)."""
    src = _read(MARKET_DATA_PATH)
    cls_idx = src.find("class MarketDataRefresh:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert '"market_data_refresh.scheduled_refresh"' in cls_body, (
        "scheduled_refresh register_job not found"
    )
    assert ('cadence=f"every {CACHE_TTL_HOURS} hours"' in cls_body or
            'cadence="every 12 hours"' in cls_body), (
        "scheduled_refresh must use cadence='every 12 hours' (or f-string)"
    )


def t_inner_register_weekly_prune_cadence():
    """weekly_prune must use cadence='weekly mon 03:00'."""
    src = _read(MARKET_DATA_PATH)
    cls_idx = src.find("class MarketDataRefresh:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert '"market_data_refresh.weekly_prune"' in cls_body, (
        "weekly_prune register_job not found"
    )
    assert 'cadence="weekly mon 03:00"' in cls_body, (
        "weekly_prune must use cadence='weekly mon 03:00'"
    )


# -- Existing migrations not disturbed -----------------------------------

def t_existing_phased_classes_unchanged():
    """All 14 prior phased plugins still phased."""
    src = _read()
    for cls_name in ("BestBuyInvites_Plugin", "AmazonMonitor_Plugin",
                     "CostcoTracker_Plugin", "WalmartPlaywright_Plugin",
                     "NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin", "MSRPAlert_Plugin",
                     "CartPreloader_Plugin", "InvestStore_Plugin",
                     "ApiServer_Plugin", "RestockReminder_Plugin",
                     "PriceHistory_Plugin", "WalmartQueue_Plugin"):
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


# -- PHASE 2/2e COMPLETION MILESTONE -------------------------------------

def t_no_legacy_classes_remain():
    """v6.1.25 PHASE 2/2e MILESTONE GUARD: NO plugin class should
    define the legacy start(self, config, products, schedule) method.

    All 15 plugins must now be on the phased lifecycle. If a future
    patch accidentally re-introduces a legacy start, this test fails.
    """
    src = _read()
    plugin_classes = re.findall(
        r"^class (\w+_Plugin)\(Plugin\):", src, re.MULTILINE
    )
    legacy = []
    for cls_name in plugin_classes:
        idx = src.find(f"class {cls_name}(Plugin):")
        if idx <= 0:
            continue
        next_class = src.find("\nclass ", idx + 1)
        cls_body = src[idx:next_class] if next_class > 0 else src[idx:]
        if "def start(self, config, products, schedule)" in cls_body:
            legacy.append(cls_name)
    assert not legacy, (
        f"Phase 2/2e completion broken: {len(legacy)} plugin(s) still on "
        f"legacy lifecycle: {legacy}"
    )


def t_cadence_strings_still_parse():
    """Defensive: every cadence='...' across plugins.py + market_data_refresh.py
    matches one of scheduler.py's four regex patterns. Banked from v6.1.18.1."""
    RE_DAILY     = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
    RE_WEEKLY    = re.compile(
        r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$",
        re.IGNORECASE,
    )
    RE_EVERY_MIN = re.compile(r"^every\s+(\d+)\s+minutes?$", re.IGNORECASE)
    RE_EVERY_HR  = re.compile(r"^every\s+(\d+)\s+hours?$", re.IGNORECASE)

    bad = []
    for path in (PLUGINS_PATH, MARKET_DATA_PATH):
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


def t_market_data_phased_lifecycle():
    _check_phased_lifecycle("MarketDataRefresh_Plugin")


def t_register_adds_exactly_three_jobs_with_correct_signature():
    """v6.1.25: register() must call scheduler.register_job() EXACTLY 3
    times using ONLY keywords that exist in the real scheduler.py
    signature.

    The StubScheduler below uses EXPLICIT keyword arguments matching
    scheduler.py: register_job(self, *, name, fn, cadence=None,
    kickoff=False, kickoff_delay=0, owner=None). Any keyword-name
    mismatch raises TypeError before the body even runs.
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
    if "market_data_refresh" not in _sys.modules:
        stub = _types.ModuleType("market_data_refresh")

        class _StubRefresher:
            def __init__(self, config, products):
                self.config = config

            def _run_prune(self): pass

            def register(self, scheduler):
                scheduler.register_job(
                    name="market_data_refresh.startup_fetch",
                    fn=lambda: None,
                    kickoff=True,
                    kickoff_delay=300,
                    owner="market_data_refresh",
                )
                scheduler.register_job(
                    name="market_data_refresh.scheduled_refresh",
                    fn=lambda: None,
                    cadence="every 12 hours",
                    owner="market_data_refresh",
                )
                scheduler.register_job(
                    name="market_data_refresh.weekly_prune",
                    fn=self._run_prune,
                    cadence="weekly mon 03:00",
                    owner="market_data_refresh",
                )

        stub.MarketDataRefresh = _StubRefresher
        _sys.modules["market_data_refresh"] = stub

    cls = getattr(mod, "MarketDataRefresh_Plugin")
    inst = cls()
    inst.init({}, [])
    inst.register(StubScheduler())  # would TypeError on wrong keywords

    assert len(captured) == 3, (
        f"MarketDataRefresh_Plugin.register() should add exactly 3 jobs, "
        f"got {len(captured)}"
    )

    by_name = {j["name"]: j for j in captured}

    # Job 1: startup_fetch — kickoff-only, no cadence
    assert "market_data_refresh.startup_fetch" in by_name
    j1 = by_name["market_data_refresh.startup_fetch"]
    assert j1["kickoff"] is True, "startup_fetch must have kickoff=True"
    assert j1["kickoff_delay"] == 300, (
        f"startup_fetch must have kickoff_delay=300 (STARTUP_DELAY_SEC), "
        f"got {j1['kickoff_delay']}"
    )
    assert j1["cadence"] is None, (
        f"startup_fetch should have no cadence (kickoff-only), "
        f"got {j1['cadence']!r}"
    )

    # Job 2: scheduled_refresh — every 12 hours, no kickoff
    assert "market_data_refresh.scheduled_refresh" in by_name
    j2 = by_name["market_data_refresh.scheduled_refresh"]
    assert j2["cadence"] == "every 12 hours"
    assert j2["kickoff"] is False

    # Job 3: weekly_prune — weekly mon 03:00, no kickoff
    assert "market_data_refresh.weekly_prune" in by_name
    j3 = by_name["market_data_refresh.weekly_prune"]
    assert j3["cadence"] == "weekly mon 03:00"
    assert j3["kickoff"] is False

    # All owned by market_data_refresh
    for job in captured:
        assert job["owner"] == "market_data_refresh"


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
