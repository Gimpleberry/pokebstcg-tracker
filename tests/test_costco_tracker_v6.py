#!/usr/bin/env python3
"""
tests/test_costco_tracker_v6.py - Verify costco_tracker is migrated to v6.0.0 lifecycle

Runs 4 structural checks against the actual plugin file + plugins.py to
confirm Step 6's migration is in place. These are STRUCTURAL tests - they
read the source files as text and look for expected code patterns. They
don't actually run the plugin (which would require Playwright + real
network + Costco cookies).

  1.  costco_tracker_uses_register_not_start
        plugins/costco_tracker.py defines `def register(self, scheduler)`
        and no longer defines `def start(self, schedule)`.

  2.  daemon_thread_wrapping_intact
        _check_all_online() still spawns a daemon thread to wrap the
        Playwright work. This pattern existed pre-Step-6 (it was the
        original sync_playwright-in-asyncio fix) and Step 6 must
        preserve it intact.

  3.  scheduler_register_job_called_with_kickoff
        register() calls scheduler.register_job(...) FOUR times - one
        per cadenced job. The main 15-minute online check uses
        kickoff=True with kickoff_delay=150 (staggered behind
        amazon_monitor at T+90s). The daily 09:00 warehouse check and
        the daily 10:45 / 16:45 drop-window online checks have no
        kickoff (they fire at their scheduled times only).

  4.  plugins_py_wrapper_uses_new_lifecycle
        CostcoTracker_Plugin in plugins.py now overrides init() and
        register() instead of the legacy start(). Version bumped to 1.1.

Exit code 0 = all 4 pass.

Run from project root:
    python tests/test_costco_tracker_v6.py
"""

from __future__ import annotations

import os
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

COSTCO_PATH  = os.path.join(_root, "plugins", "costco_tracker.py")
PLUGINS_PATH = os.path.join(_root, "plugins.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# -----------------------------------------------------------------------------
# TESTS
# -----------------------------------------------------------------------------

def t_costco_tracker_uses_register_not_start():
    src = _read(COSTCO_PATH)
    # New lifecycle method must be present
    assert "def register(self, scheduler)" in src, (
        "plugins/costco_tracker.py should define `def register(self, scheduler)` "
        "for the v6.0.0 phased boot lifecycle"
    )
    # Old lifecycle method must be gone
    assert "def start(self, schedule)" not in src, (
        "plugins/costco_tracker.py still defines `def start(self, schedule)` "
        "- the migration is incomplete"
    )


def t_daemon_thread_wrapping_intact():
    """The daemon thread wrapping in _check_all_online() existed pre-Step-6
    (it was the fix for sync_playwright inside asyncio) and must be
    preserved by the migration."""
    src = _read(COSTCO_PATH)
    assert "import threading" in src, (
        "_check_all_online should import threading to wrap Playwright work"
    )
    assert "daemon=True" in src, (
        "Should use daemon=True on the wrapping thread"
    )
    assert 'name="costco_check_all"' in src or \
           "name='costco_check_all'" in src, (
        "Daemon thread should retain its 'costco_check_all' diagnostic name"
    )
    # Wrapping pattern: t.start() + t.join(timeout=N)
    assert ".start()" in src, "wrapping thread .start() must remain"
    assert ".join(timeout=" in src, "wrapping thread .join(timeout=N) must remain"


def t_scheduler_register_job_called_with_kickoff():
    src = _read(COSTCO_PATH)
    # Must call register_job
    assert "scheduler.register_job(" in src, (
        "register() should call scheduler.register_job(...)"
    )
    # Counted: 4 jobs (online 15min, warehouses 09:00, online 10:45, online 16:45)
    n_calls = src.count("scheduler.register_job(")
    assert n_calls == 4, (
        f"Expected 4 scheduler.register_job() calls (one per cadenced job), "
        f"found {n_calls}"
    )
    # Main online check uses kickoff
    assert "kickoff=True" in src, (
        "Main register_job should use kickoff=True for staggered first dispatch"
    )
    assert "kickoff_delay=150" in src, (
        "costco_tracker should use kickoff_delay=150 (staggered behind "
        "amazon_monitor at T+90s, since both run heavy Playwright)"
    )
    # All 4 cadence strings must be present
    for cadence in [
        '"every 15 minutes"',
        '"daily 09:00"',
        '"daily 10:45"',
        '"daily 16:45"',
    ]:
        assert cadence in src or cadence.replace('"', "'") in src, (
            f"Expected cadence {cadence} in costco_tracker register()"
        )


def t_plugins_py_wrapper_uses_new_lifecycle():
    src = _read(PLUGINS_PATH)

    # Find the CostcoTracker_Plugin class block and check it specifically
    cls_start = src.find("class CostcoTracker_Plugin(Plugin):")
    assert cls_start >= 0, "CostcoTracker_Plugin class not found in plugins.py"
    next_cls = src.find("\nclass ", cls_start + 1)
    cls_block = src[cls_start:next_cls if next_cls > 0 else len(src)]

    # New lifecycle methods present
    assert "def init(self, config, products):" in cls_block, (
        "CostcoTracker_Plugin should override init(config, products)"
    )
    assert "def register(self, scheduler):" in cls_block, (
        "CostcoTracker_Plugin should override register(scheduler)"
    )
    # Old lifecycle method gone from THIS class
    assert "def start(self, config, products, schedule):" not in cls_block, (
        "CostcoTracker_Plugin still has legacy start() - migration incomplete"
    )
    # Version bumped
    assert 'version = "1.1"' in cls_block, (
        "CostcoTracker_Plugin version should be bumped to 1.1 to mark the migration"
    )


# -----------------------------------------------------------------------------
# RUNNER
# -----------------------------------------------------------------------------
def main():
    print("=" * 70)
    print(" v6.0.0 step 6 - costco_tracker lifecycle migration tests")
    print("=" * 70)

    tests = [
        ("costco_tracker_uses_register_not_start",     t_costco_tracker_uses_register_not_start),
        ("daemon_thread_wrapping_intact",              t_daemon_thread_wrapping_intact),
        ("scheduler_register_job_called_with_kickoff", t_scheduler_register_job_called_with_kickoff),
        ("plugins_py_wrapper_uses_new_lifecycle",      t_plugins_py_wrapper_uses_new_lifecycle),
    ]

    passed = failed = 0
    for i, (name, fn) in enumerate(tests, start=1):
        try:
            fn()
            print(f"  [{i}] PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  [{i}] FAIL  {name}")
            print(f"        {e}")
            failed += 1
        except Exception as e:
            print(f"  [{i}] ERROR {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("-" * 70)
    print(f"  Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
