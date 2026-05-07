"""
test_v6_1_19_lifecycle_migration.py

Structural + introspection tests for v6.1.19 — Batch 2 phased-lifecycle
migration of MSRPAlert_Plugin and CartPreloader_Plugin.

These are SERVICE-STYLE plugins (no periodic schedule). Migration shape:
  - start() body → init() body
  - register() defined as no-op pass (full phased lifecycle, Q1 answer 'a')
  - Net jobs added to scheduler: +0 (event hooks only)

Also includes the v6.1.18.1 cadence-regex regression check applied to the
new v6.1.19 changes (none added cadences, but the test is cheap and
defends against future drift).

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


def _read():
    with open(PLUGINS_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    """Each call returns a fresh plugins.py module."""
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test_v19", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests -----------------------------------------------------

def t_msrp_alert_no_legacy_start():
    """MSRPAlert_Plugin no longer overrides start()."""
    src = _read()
    cls_idx = src.find("class MSRPAlert_Plugin(Plugin):")
    assert cls_idx > 0, "MSRPAlert_Plugin class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "MSRPAlert_Plugin still defines legacy start()"
    )


def t_cart_preloader_no_legacy_start():
    """CartPreloader_Plugin no longer overrides start()."""
    src = _read()
    cls_idx = src.find("class CartPreloader_Plugin(Plugin):")
    assert cls_idx > 0, "CartPreloader_Plugin class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "CartPreloader_Plugin still defines legacy start()"
    )


def t_msrp_alert_has_init_and_register():
    """MSRPAlert defines both init() and register()."""
    src = _read()
    cls_idx = src.find("class MSRPAlert_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body, (
        "MSRPAlert_Plugin missing init()"
    )
    assert "def register(self, scheduler)" in cls_body, (
        "MSRPAlert_Plugin missing register()"
    )


def t_cart_preloader_has_init_and_register():
    """CartPreloader defines both init() and register()."""
    src = _read()
    cls_idx = src.find("class CartPreloader_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body, (
        "CartPreloader_Plugin missing init()"
    )
    assert "def register(self, scheduler)" in cls_body, (
        "CartPreloader_Plugin missing register()"
    )


def t_versions_bumped():
    """Both migrated plugins bumped to version 1.1."""
    src = _read()
    for cls_name in ("MSRPAlert_Plugin", "CartPreloader_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        assert idx > 0, f"{cls_name} not found"
        window = src[idx:idx + 250]
        assert 'version = "1.1"' in window, (
            f"{cls_name} version not bumped to 1.1"
        )


def t_msrp_alert_event_hook_preserved():
    """MSRPAlert.on_post_check() still defined (event hook is the
    plugin's actual work — must survive migration)."""
    src = _read()
    cls_idx = src.find("class MSRPAlert_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def on_post_check(self)" in cls_body, (
        "MSRPAlert.on_post_check() removed — event hook is critical"
    )


def t_cadence_strings_still_parse():
    """Defensive: every cadence="..." string in plugins.py matches one
    of scheduler.py's four regex patterns. Mirrors the v6.1.18.1
    regression check. Re-run here so v6.1.19 can't introduce new
    cadence drift."""
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


def t_msrp_alert_phased_lifecycle():
    _check_phased_lifecycle("MSRPAlert_Plugin")


def t_cart_preloader_phased_lifecycle():
    _check_phased_lifecycle("CartPreloader_Plugin")


def t_register_is_noop_for_service_plugins():
    """Service-style plugins have no periodic jobs to register. Their
    register() bodies should not invoke scheduler.register_job(). Verify
    by passing a stub Scheduler that records calls — both should record 0."""
    mod = _load_plugins_module()
    captured = []

    class StubScheduler:
        def register_job(self, **kwargs):
            captured.append(kwargs)

    # Stub the inner modules so init() succeeds
    import sys as _sys
    import types as _types
    for mod_name, fn_name in [
        ("msrp_alert", "check_msrp_prices"),
        ("cart_preloader", "patch_msrp_alert"),
    ]:
        if mod_name not in _sys.modules:
            stub = _types.ModuleType(mod_name)
            setattr(stub, fn_name, lambda *a, **kw: None)
            _sys.modules[mod_name] = stub

    for cls_name in ("MSRPAlert_Plugin", "CartPreloader_Plugin"):
        cls = getattr(mod, cls_name)
        inst = cls()
        inst.init({}, [])
        inst.register(StubScheduler())

    assert captured == [], (
        f"service-style plugins should add 0 jobs, got {len(captured)}: "
        f"{[k['name'] for k in captured]}"
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
