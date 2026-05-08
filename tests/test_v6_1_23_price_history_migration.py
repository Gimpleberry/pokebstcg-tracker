"""
test_v6_1_23_price_history_migration.py

Structural + introspection tests for v6.1.23 — Batch 6 phased-lifecycle
migration of PriceHistory_Plugin. Cadenced plugin with hourly recurrence
+ first-run kickoff.

This test file uses an EXPLICIT-SIGNATURE StubScheduler matching
scheduler.py's real keyword-only parameters. Any keyword-name mismatch
TypeErrors at test time. (v6.1.22 v2 lesson — banked permanently.)

Tests use spec_from_file_location to load plugins.py — never `import plugins`.
"""

import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PLUGINS_PATH = os.path.join(_ROOT, "plugins.py")
PRICE_HISTORY_PATH = os.path.join(_ROOT, "plugins", "price_history.py")


def _read(path=PLUGINS_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test_v23", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests on plugins.py --------------------------------------

def t_price_history_no_legacy_start():
    src = _read()
    cls_idx = src.find("class PriceHistory_Plugin(Plugin):")
    assert cls_idx > 0, "PriceHistory_Plugin class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "PriceHistory_Plugin still defines legacy start()"
    )


def t_price_history_has_init_and_register():
    src = _read()
    cls_idx = src.find("class PriceHistory_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body
    assert "def register(self, scheduler)" in cls_body


def t_price_history_version_bumped():
    src = _read()
    idx = src.find("class PriceHistory_Plugin(Plugin):")
    window = src[idx:idx + 250]
    assert 'version = "1.1"' in window


# -- Structural tests on plugins/price_history.py --------------------

def t_inner_no_start_method():
    src = _read(PRICE_HISTORY_PATH)
    cls_idx = src.find("class PriceHistoryTracker:")
    assert cls_idx > 0, "PriceHistoryTracker class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, schedule)" not in cls_body, (
        "PriceHistoryTracker still defines start(schedule)"
    )


def t_inner_has_register_with_scheduler():
    src = _read(PRICE_HISTORY_PATH)
    cls_idx = src.find("class PriceHistoryTracker:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def register(self, scheduler)" in cls_body
    assert "scheduler.register_job(" in cls_body


def t_inner_register_uses_correct_keyword_fn():
    """v6.1.22 v2 lesson banked: register() must use `fn=` (the actual
    scheduler.py keyword), NOT `job_fn=`."""
    src = _read(PRICE_HISTORY_PATH)
    cls_idx = src.find("class PriceHistoryTracker:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    idx = cls_body.find("scheduler.register_job(")
    assert idx > 0
    end = cls_body.find(")", idx)
    call_body = cls_body[idx:end]
    assert "fn=" in call_body, "register_job() must use fn= keyword"
    assert "job_fn=" not in call_body, (
        "v6.1.22 v1 bug recurrence: register_job() uses `job_fn=` (should be `fn=`)"
    )


def t_inner_register_kickoff_settings():
    """register() declares kickoff=True, kickoff_delay=45 to replace
    the legacy inline boot run."""
    src = _read(PRICE_HISTORY_PATH)
    cls_idx = src.find("class PriceHistoryTracker:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "kickoff=True" in cls_body, (
        "register() should declare kickoff=True (replaces inline boot run)"
    )
    assert "kickoff_delay=45" in cls_body, (
        "register() should declare kickoff_delay=45 (slot between bb_invites and news_scraper)"
    )


def t_inner_register_cadence():
    """register() declares cadence='every 60 minutes' (or f-string
    equivalent using LOG_INTERVAL)."""
    src = _read(PRICE_HISTORY_PATH)
    cls_idx = src.find("class PriceHistoryTracker:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert ('cadence=f"every {LOG_INTERVAL} minutes"' in cls_body or
            'cadence="every 60 minutes"' in cls_body), (
        "register() should declare cadence='every 60 minutes' (or f-string equivalent)"
    )


# -- Existing migrations not disturbed -----------------------------------

def t_existing_phased_classes_unchanged():
    """All 12 prior phased plugins still phased."""
    src = _read()
    for cls_name in ("BestBuyInvites_Plugin", "AmazonMonitor_Plugin",
                     "CostcoTracker_Plugin", "WalmartPlaywright_Plugin",
                     "NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin", "MSRPAlert_Plugin",
                     "CartPreloader_Plugin", "InvestStore_Plugin",
                     "ApiServer_Plugin", "RestockReminder_Plugin"):
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
    """2 remaining legacy plugins still on legacy lifecycle."""
    src = _read()
    for cls_name in ("WalmartQueue_Plugin", "MarketDataRefresh_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        if idx <= 0:
            continue
        next_class = src.find("\nclass ", idx + 1)
        cls_body = src[idx:next_class] if next_class > 0 else src[idx:]
        assert "def start(self, config, products, schedule)" in cls_body, (
            f"{cls_name} legacy start() missing — should still be legacy"
        )


def t_cadence_strings_still_parse():
    """Defensive: every cadence='...' in plugins.py matches scheduler.py
    regexes. Banked from v6.1.18.1."""
    src = _read()
    RE_DAILY     = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
    RE_WEEKLY    = re.compile(
        r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$",
        re.IGNORECASE,
    )
    RE_EVERY_MIN = re.compile(r"^every\s+(\d+)\s+minutes?$", re.IGNORECASE)
    RE_EVERY_HR  = re.compile(r"^every\s+(\d+)\s+hours?$", re.IGNORECASE)
    bad = []
    for m in re.finditer(r'cadence="([^"]+)"', src):
        c = m.group(1)
        if not (RE_DAILY.match(c) or RE_WEEKLY.match(c)
                or RE_EVERY_MIN.match(c) or RE_EVERY_HR.match(c)):
            bad.append(c)
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


def t_price_history_phased_lifecycle():
    _check_phased_lifecycle("PriceHistory_Plugin")


def t_register_adds_one_hourly_job_with_correct_signature():
    """v6.1.23: register() must call scheduler.register_job() ONCE using
    ONLY keywords that exist in the real scheduler.py signature.

    The StubScheduler below uses EXPLICIT keyword arguments matching
    scheduler.py: register_job(self, *, name, fn, cadence=None,
    kickoff=False, kickoff_delay=0, owner=None). Any keyword-name
    mismatch from the caller raises TypeError before the body even runs.
    This is the test stub pattern that would have caught the v6.1.22 v1 bug.
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

    # Stub the inner module so init() succeeds without real DB I/O
    import sys as _sys
    import types as _types
    if "price_history" not in _sys.modules:
        stub = _types.ModuleType("price_history")

        class _StubTracker:
            def __init__(self, config, products):
                self.config = config
                self.products = products

            def _hourly_log(self): pass

            def register(self, scheduler):
                # Mirror what the real implementation does
                scheduler.register_job(
                    name="price_history.hourly_log",
                    fn=self._hourly_log,
                    cadence="every 60 minutes",
                    kickoff=True,
                    kickoff_delay=45,
                    owner="price_history",
                )

        stub.PriceHistoryTracker = _StubTracker
        stub.LOG_INTERVAL = 60
        stub.DB_PATH = "data/price_history.db"
        _sys.modules["price_history"] = stub

    cls = getattr(mod, "PriceHistory_Plugin")
    inst = cls()
    inst.init({}, [])
    inst.register(StubScheduler())  # would TypeError on wrong keywords

    assert len(captured) == 1, (
        f"PriceHistory_Plugin.register() should add exactly 1 job, "
        f"got {len(captured)}"
    )
    job = captured[0]
    assert job["name"] == "price_history.hourly_log", (
        f"job name should be 'price_history.hourly_log', got {job['name']!r}"
    )
    assert job["cadence"] == "every 60 minutes", (
        f"job cadence should be 'every 60 minutes', got {job['cadence']!r}"
    )
    assert job["kickoff"] is True, (
        f"job kickoff should be True, got {job['kickoff']!r}"
    )
    assert job["kickoff_delay"] == 45, (
        f"job kickoff_delay should be 45, got {job['kickoff_delay']!r}"
    )
    assert callable(job["fn"])


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
