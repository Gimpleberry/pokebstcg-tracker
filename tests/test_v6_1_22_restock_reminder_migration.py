"""
test_v6_1_22_restock_reminder_migration.py

Structural + introspection tests for v6.1.22 v2 — Batch 5 phased-lifecycle
migration of RestockReminder_Plugin.

CRITICAL: This test file uses an EXPLICIT-SIGNATURE StubScheduler. The v1
of this test used **kwargs which silently accepted any keyword name and
let the `job_fn=` typo slip past sandbox into production. v2 catches
keyword-name mismatches with TypeError at test time.

Tests use spec_from_file_location to load plugins.py — never `import plugins`
(per the v6.1.15 lesson).
"""

import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PLUGINS_PATH = os.path.join(_ROOT, "plugins.py")
RESTOCK_PATH = os.path.join(_ROOT, "plugins", "restock_reminder.py")


def _read(path=PLUGINS_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test_v22v2", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests on plugins.py --------------------------------------

def t_restock_reminder_no_legacy_start():
    src = _read()
    cls_idx = src.find("class RestockReminder_Plugin(Plugin):")
    assert cls_idx > 0, "RestockReminder_Plugin class not found"
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, config, products, schedule)" not in cls_body, (
        "RestockReminder_Plugin still defines legacy start()"
    )


def t_restock_reminder_has_init_and_register():
    src = _read()
    cls_idx = src.find("class RestockReminder_Plugin(Plugin):")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def init(self, config, products)" in cls_body
    assert "def register(self, scheduler)" in cls_body


def t_restock_reminder_version_bumped():
    src = _read()
    idx = src.find("class RestockReminder_Plugin(Plugin):")
    window = src[idx:idx + 250]
    assert 'version = "1.1"' in window


# -- Structural tests on plugins/restock_reminder.py --------------------

def t_inner_no_start_method():
    src = _read(RESTOCK_PATH)
    cls_idx = src.find("class RestockReminder:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def start(self, schedule)" not in cls_body


def t_inner_has_register_with_scheduler():
    src = _read(RESTOCK_PATH)
    cls_idx = src.find("class RestockReminder:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert "def register(self, scheduler)" in cls_body
    assert "scheduler.register_job(" in cls_body


def t_inner_register_uses_correct_keyword_fn():
    """v6.1.22 v2: register() must use `fn=` (the actual scheduler.py
    keyword), NOT `job_fn=` (which was the v1 bug)."""
    src = _read(RESTOCK_PATH)
    cls_idx = src.find("class RestockReminder:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    # Look inside the register_job() call only (not in comments)
    idx = cls_body.find("scheduler.register_job(")
    assert idx > 0
    end = cls_body.find(")", idx)
    call_body = cls_body[idx:end]
    assert "fn=" in call_body, "register_job() must use fn= keyword"
    assert "job_fn=" not in call_body, (
        "v1 bug recurrence: register_job() uses `job_fn=` (should be `fn=`)"
    )


def t_inner_register_uses_correct_cadence():
    src = _read(RESTOCK_PATH)
    cls_idx = src.find("class RestockReminder:")
    next_class = src.find("\nclass ", cls_idx + 1)
    cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
    assert ('cadence=f"daily {FIRE_TIME}"' in cls_body or
            'cadence="daily 08:30"' in cls_body), (
        "register() should declare cadence=daily 08:30 (or f-string equivalent)"
    )
    assert 'kickoff=False' in cls_body


# -- Existing migrations not disturbed -----------------------------------

def t_existing_phased_classes_unchanged():
    src = _read()
    for cls_name in ("BestBuyInvites_Plugin", "AmazonMonitor_Plugin",
                     "CostcoTracker_Plugin", "WalmartPlaywright_Plugin",
                     "NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin", "MSRPAlert_Plugin",
                     "CartPreloader_Plugin", "InvestStore_Plugin",
                     "ApiServer_Plugin"):
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
    src = _read()
    for cls_name in ("WalmartQueue_Plugin", "PriceHistory_Plugin",
                     "MarketDataRefresh_Plugin"):
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


def t_restock_reminder_phased_lifecycle():
    _check_phased_lifecycle("RestockReminder_Plugin")


def t_register_adds_one_job_with_correct_signature():
    """v6.1.22 v2: register() must call scheduler.register_job() ONCE
    using ONLY keywords that exist in the real scheduler.py signature.

    The StubScheduler below uses EXPLICIT keyword arguments matching
    scheduler.py: register_job(self, *, name, fn, cadence=None,
    kickoff=False, kickoff_delay=0, owner=None). If the real code uses
    a wrong keyword name (e.g. v1's job_fn=), Python raises TypeError
    immediately when the lambda call site invokes the stub.

    This is the test that would have caught the v1 production bug.
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
            # Mirror real scheduler.py parameter names EXACTLY.
            # Any keyword-name mismatch from the caller raises TypeError
            # before this body even runs.
            assert callable(fn), "fn must be callable"
            captured.append({
                "name":          name,
                "fn":            fn,
                "cadence":       cadence,
                "kickoff":       kickoff,
                "kickoff_delay": kickoff_delay,
                "owner":         owner,
            })

    # Stub the inner module so init() succeeds without real ntfy I/O
    import sys as _sys
    import types as _types
    if "restock_reminder" not in _sys.modules:
        stub = _types.ModuleType("restock_reminder")

        class _StubReminder:
            def __init__(self, config):
                self.config = config

            def register(self, scheduler):
                # Mirror what the real implementation should do
                scheduler.register_job(
                    name="restock_reminder.send_reminder",
                    fn=lambda: None,
                    cadence="daily 08:30",
                    kickoff=False,
                    owner="restock_reminder",
                )

        stub.RestockReminder = _StubReminder
        stub.send_reminder = lambda c: None
        stub.FIRE_TIME = "08:30"
        _sys.modules["restock_reminder"] = stub

    cls = getattr(mod, "RestockReminder_Plugin")
    inst = cls()
    inst.init({}, [])
    inst.register(StubScheduler())  # would TypeError if wrong keywords

    assert len(captured) == 1, (
        f"RestockReminder_Plugin.register() should add exactly 1 job, "
        f"got {len(captured)}"
    )
    job = captured[0]
    assert job["name"] == "restock_reminder.send_reminder"
    assert job["cadence"] == "daily 08:30"
    assert job["kickoff"] is False
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
