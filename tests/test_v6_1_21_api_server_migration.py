"""
test_v6_1_21_api_server_migration.py

Structural + introspection tests for v6.1.21 — Batch 4 phased-lifecycle
migration of ApiServer_Plugin.

ApiServer is a SERVICE-STYLE plugin (daemon HTTP thread, no schedule).
Migration shape mirrors v6.1.20:
  - start() body → init() body
  - register() defined as no-op pass
  - Net jobs added to scheduler: +0

Plus inner-module restructure: ApiServer.__init__ now absorbs the daemon
thread spawn that previously lived in ApiServer.start(schedule).
ApiServer.start() removed (tombstone comment preserved as marker).

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
API_SERVER_PATH = os.path.join(_ROOT, "plugins", "api_server.py")


def _read(path=PLUGINS_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test_v21", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests on plugins.py --------------------------------------

def t_api_server_no_legacy_start():
    src = _read()
    cls_idx = src.find("class ApiServer_Plugin(Plugin):")
    assert cls_idx > 0, "ApiServer_Plugin class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "ApiServer_Plugin still defines legacy start()"
    )


def t_api_server_has_init_and_register():
    src = _read()
    cls_idx = src.find("class ApiServer_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body, (
        "ApiServer_Plugin missing init()"
    )
    assert "def register(self, scheduler)" in cls_body, (
        "ApiServer_Plugin missing register()"
    )


def t_api_server_version_bumped():
    src = _read()
    idx = src.find("class ApiServer_Plugin(Plugin):")
    assert idx > 0
    window = src[idx:idx + 250]
    assert 'version = "1.1"' in window, (
        f"ApiServer_Plugin version not bumped to 1.1; window: {window[:250]!r}"
    )


def t_api_server_init_instantiates():
    src = _read()
    cls_idx = src.find("class ApiServer_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "self._api = ApiServer(config, products)" in cls_body, (
        "ApiServer_Plugin.init() should instantiate self._api"
    )


def t_api_server_stop_guards_none():
    """stop() guards against self._api being None (init-failure case)."""
    src = _read()
    cls_idx = src.find("class ApiServer_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "self._api is not None" in cls_body, (
        "ApiServer_Plugin.stop() should guard against self._api being None"
    )


# -- Structural tests on plugins/api_server.py --------------------------

def t_inner_api_server_no_start_method():
    """The dead ApiServer.start(schedule) method has been removed."""
    src = _read(API_SERVER_PATH)
    cls_idx = src.find("class ApiServer:")
    assert cls_idx > 0, "ApiServer class not found in plugins/api_server.py"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, schedule)" not in cls_body, (
        "ApiServer class still defines start(schedule) method"
    )


def t_inner_api_server_init_spawns_thread():
    """ApiServer.__init__ now spawns the daemon thread (work moved from start())."""
    src = _read(API_SERVER_PATH)
    cls_idx = src.find("class ApiServer:")
    assert cls_idx > 0
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "self._thread = _ApiServerThread()" in cls_body, (
        "ApiServer.__init__ should spawn _ApiServerThread (moved from start)"
    )
    assert "self._thread.start()" in cls_body, (
        "ApiServer.__init__ should call self._thread.start()"
    )


# -- Existing migrations not disturbed -----------------------------------

def t_existing_phased_classes_unchanged():
    src = _read()
    for cls_name in ("BestBuyInvites_Plugin", "AmazonMonitor_Plugin",
                     "CostcoTracker_Plugin", "WalmartPlaywright_Plugin",
                     "NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin", "MSRPAlert_Plugin",
                     "CartPreloader_Plugin", "InvestStore_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        if idx <= 0:
            continue  # not in synthetic
        next_class = src.find("\nclass ", idx + 1)
        cls_body = src[idx:next_class] if next_class > 0 else src[idx:]
        assert "def init(self, config, products)" in cls_body, (
            f"{cls_name} init() missing — pre-existing phased plugin disturbed"
        )
        assert "def register(self, scheduler)" in cls_body, (
            f"{cls_name} register() missing — pre-existing phased plugin disturbed"
        )


def t_remaining_legacy_classes_unchanged():
    src = _read()
    for cls_name in ("WalmartQueue_Plugin", "RestockReminder_Plugin",
                     "PriceHistory_Plugin", "MarketDataRefresh_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        if idx <= 0:
            continue
        next_class = src.find("\nclass ", idx + 1)
        cls_body = src[idx:next_class] if next_class > 0 else src[idx:]
        assert "def start(self, config, products, schedule)" in cls_body, (
            f"{cls_name} legacy start() missing — should still be legacy"
        )


def t_cadence_strings_still_parse():
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


def t_api_server_phased_lifecycle():
    _check_phased_lifecycle("ApiServer_Plugin")


def t_register_is_noop_for_api_server():
    """Service-style: register() should NOT call scheduler.register_job()."""
    mod = _load_plugins_module()
    captured = []

    class StubScheduler:
        def register_job(self, **kwargs):
            captured.append(kwargs)

    # Stub the inner module so init() succeeds without spawning a real thread
    import sys as _sys
    import types as _types
    if "api_server" not in _sys.modules:
        stub = _types.ModuleType("api_server")

        class _StubApiServer:
            def __init__(self, *a, **kw): pass

        stub.ApiServer = _StubApiServer
        _sys.modules["api_server"] = stub

    cls = getattr(mod, "ApiServer_Plugin")
    inst = cls()
    inst.init({}, [])
    inst.register(StubScheduler())

    assert captured == [], (
        f"ApiServer_Plugin.register() should add 0 jobs, got {len(captured)}: "
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
