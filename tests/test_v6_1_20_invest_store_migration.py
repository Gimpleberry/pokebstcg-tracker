"""
test_v6_1_20_invest_store_migration.py

Structural + introspection tests for v6.1.20 — Batch 3 phased-lifecycle
migration of InvestStore_Plugin.

InvestStore is a SERVICE-STYLE plugin (passive CRUD store, no schedule).
Migration shape mirrors v6.1.19:
  - start() body → init() body
  - register() defined as no-op pass
  - Net jobs added to scheduler: +0

Plus inner-module cleanup: dead InvestStore.start(schedule) method removed,
docstring updated.

Tests use spec_from_file_location to load plugins.py — never `import plugins`
(per the v6.1.15 lesson banked in PROJECT_KNOWLEDGE).
"""

import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PLUGINS_PATH = os.path.join(_ROOT, "plugins.py")
INVEST_STORE_PATH = os.path.join(_ROOT, "plugins", "invest_store.py")


def _read(path=PLUGINS_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    """Each call returns a fresh plugins.py module."""
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test_v20", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests on plugins.py --------------------------------------

def t_invest_store_no_legacy_start():
    """InvestStore_Plugin no longer overrides start()."""
    src = _read()
    cls_idx = src.find("class InvestStore_Plugin(Plugin):")
    assert cls_idx > 0, "InvestStore_Plugin class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "InvestStore_Plugin still defines legacy start()"
    )


def t_invest_store_has_init_and_register():
    """InvestStore_Plugin defines both init() and register()."""
    src = _read()
    cls_idx = src.find("class InvestStore_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body, (
        "InvestStore_Plugin missing init()"
    )
    assert "def register(self, scheduler)" in cls_body, (
        "InvestStore_Plugin missing register()"
    )


def t_invest_store_version_bumped():
    """InvestStore_Plugin version bumped to 1.1."""
    src = _read()
    idx = src.find("class InvestStore_Plugin(Plugin):")
    assert idx > 0, "InvestStore_Plugin not found"
    window = src[idx:idx + 250]
    assert 'version = "1.1"' in window, (
        "InvestStore_Plugin version not bumped to 1.1 in window: "
        f"{window[:250]!r}"
    )


def t_invest_store_init_instantiates_store():
    """init() body still creates self._store = InvestStore(config, products)."""
    src = _read()
    cls_idx = src.find("class InvestStore_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "self._store = InvestStore(config, products)" in cls_body, (
        "InvestStore_Plugin.init() should instantiate self._store"
    )


# -- Structural tests on plugins/invest_store.py ------------------------

def t_inner_invest_store_no_start_method():
    """The dead InvestStore.start(schedule) method has been removed."""
    src = _read(INVEST_STORE_PATH)
    cls_idx = src.find("class InvestStore:")
    assert cls_idx > 0, "InvestStore class not found in plugins/invest_store.py"
    cls_body = src[cls_idx:]
    assert "def start(self, schedule)" not in cls_body, (
        "InvestStore class still defines dead start(schedule) method"
    )


# -- Existing migrations not disturbed -----------------------------------

def t_existing_phased_classes_unchanged():
    """Sanity: all v6.0.0 + v6.1.18 + v6.1.19 phased plugins still phased."""
    src = _read()
    for cls_name in ("BestBuyInvites_Plugin", "AmazonMonitor_Plugin",
                     "CostcoTracker_Plugin", "WalmartPlaywright_Plugin",
                     "NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin", "MSRPAlert_Plugin",
                     "CartPreloader_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        if idx <= 0:
            # Some classes may not exist in early sandbox; only check those present
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
    """Sanity: WalmartQueue, RestockReminder, PriceHistory, MarketDataRefresh,
    ApiServer still on legacy lifecycle (only InvestStore migrated this batch)."""
    src = _read()
    for cls_name in ("WalmartQueue_Plugin", "RestockReminder_Plugin",
                     "PriceHistory_Plugin", "MarketDataRefresh_Plugin",
                     "ApiServer_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        if idx <= 0:
            continue  # not present in sandbox
        next_class = src.find("\nclass ", idx + 1)
        cls_body = src[idx:next_class] if next_class > 0 else src[idx:]
        assert "def start(self, config, products, schedule)" in cls_body, (
            f"{cls_name} legacy start() missing — should still be legacy"
        )


# -- Cadence regex regression check (banked from v6.1.18.1) -------------

def t_cadence_strings_still_parse():
    """Defensive: every cadence='...' string in plugins.py matches one of
    scheduler.py's four regex patterns. Mirrors v6.1.18.1 / v6.1.19 check."""
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
    """Phased = overrides init() AND register(), does NOT override start()."""
    mod = _load_plugins_module()
    cls = getattr(mod, cls_name)
    base = mod.Plugin
    assert cls.init is not base.init, f"{cls_name}.init() not overridden"
    assert cls.register is not base.register, f"{cls_name}.register() not overridden"
    assert cls.start is base.start, (
        f"{cls_name} still overrides start() — should have been removed"
    )


def t_invest_store_phased_lifecycle():
    _check_phased_lifecycle("InvestStore_Plugin")


def t_register_is_noop_for_invest_store():
    """Service-style: register() should NOT call scheduler.register_job().
    Verify by passing a stub Scheduler that records calls — should record 0."""
    mod = _load_plugins_module()
    captured = []

    class StubScheduler:
        def register_job(self, **kwargs):
            captured.append(kwargs)

    # Stub the inner module so init() succeeds
    import sys as _sys
    import types as _types
    if "invest_store" not in _sys.modules:
        stub = _types.ModuleType("invest_store")

        class _StubInvestStore:
            def __init__(self, *a, **kw): pass

        stub.InvestStore = _StubInvestStore
        _sys.modules["invest_store"] = stub

    cls = getattr(mod, "InvestStore_Plugin")
    inst = cls()
    inst.init({}, [])
    inst.register(StubScheduler())

    assert captured == [], (
        f"InvestStore_Plugin.register() should add 0 jobs, got {len(captured)}: "
        f"{[k.get('name') for k in captured]}"
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
