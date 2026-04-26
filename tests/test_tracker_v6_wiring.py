#!/usr/bin/env python3
"""
tests/test_tracker_v6_wiring.py - Verify tracker.py uses the v6.0.0 Scheduler

Runs 5 checks against the actual tracker.py file to confirm the Step 3
wiring is in place. These are STRUCTURAL tests — they read tracker.py
as text and look for expected code patterns. They don't actually run
tracker.main() because that requires real config + network + plugin
imports that aren't appropriate for a unit test environment.

  1.  scheduler_import_present
        tracker.py imports Scheduler from the scheduler module.

  2.  scheduler_construction_present
        tracker.py constructs Scheduler(schedule) — i.e., wraps the real
        schedule library in our coordinator.

  3.  load_plugins_passes_scheduler
        load_plugins() is called with the Scheduler instance, not the
        raw schedule library. This is the actual switch-over to the new
        lifecycle path.

  4.  boot_ready_called
        scheduler.boot_ready() is invoked after load_plugins(). This is
        what triggers staggered kickoff once Steps 4-6 ship.

  5.  scheduler_module_imports_cleanly
        Smoke check: 'from scheduler import Scheduler' can actually run
        and the resulting class has the expected methods. Catches subtle
        import path or syntax errors in scheduler.py that wouldn't show
        up in a string match.

Exit code 0 = all 5 pass. Non-zero = at least one failed.

Run from project root:
    python tests/test_tracker_v6_wiring.py
"""

from __future__ import annotations

import os
import sys
import traceback

# Path resolution
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here
TRACKER_PATH = os.path.join(_root, "tracker.py")


def _read_tracker():
    """Read tracker.py source code as text."""
    with open(TRACKER_PATH, "r", encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_scheduler_import_present():
    src = _read_tracker()
    assert "from scheduler import Scheduler" in src, (
        "tracker.py should import Scheduler from the scheduler module "
        "(expected line: 'from scheduler import Scheduler')"
    )


def t_scheduler_construction_present():
    src = _read_tracker()
    assert "Scheduler(schedule)" in src, (
        "tracker.py should construct Scheduler(schedule) to wrap the "
        "real schedule library"
    )


def t_load_plugins_passes_scheduler():
    src = _read_tracker()
    # Should pass scheduler instance, not raw schedule lib
    assert "load_plugins(CONFIG, PRODUCTS, scheduler)" in src, (
        "tracker.py should call plugin_system.load_plugins(CONFIG, PRODUCTS, scheduler) "
        "with the Scheduler instance, not the raw schedule library"
    )
    # And the OLD form should be gone
    assert "load_plugins(CONFIG, PRODUCTS, schedule)" not in src, (
        "tracker.py still has the legacy 'load_plugins(CONFIG, PRODUCTS, schedule)' "
        "call — the patch did not fully apply"
    )


def t_boot_ready_called():
    src = _read_tracker()
    assert "scheduler.boot_ready()" in src, (
        "tracker.py should call scheduler.boot_ready() after load_plugins() "
        "to trigger staggered kickoff dispatch"
    )


def t_scheduler_module_imports_cleanly():
    """End-to-end smoke: the imports in tracker.py actually resolve."""
    if _root not in sys.path:
        sys.path.insert(0, _root)
    # Import the Scheduler class
    from scheduler import Scheduler

    # Need a schedule library instance — try real first, fall back to stub
    try:
        import schedule as _schedule_lib
    except ImportError:
        import _schedule_stub as _schedule_lib    # type: ignore

    s = Scheduler(_schedule_lib)
    assert hasattr(s, "register_job"), \
        "Scheduler instance is missing register_job() method"
    assert hasattr(s, "boot_ready"),   \
        "Scheduler instance is missing boot_ready() method"

    # Verify boot_ready is idempotent (sanity check on the construction)
    s.boot_ready()
    s.boot_ready()


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(" v6.0.0 step 3 - tracker.py wiring tests")
    print("=" * 70)

    tests = [
        ("scheduler_import_present",          t_scheduler_import_present),
        ("scheduler_construction_present",    t_scheduler_construction_present),
        ("load_plugins_passes_scheduler",     t_load_plugins_passes_scheduler),
        ("boot_ready_called",                 t_boot_ready_called),
        ("scheduler_module_imports_cleanly",  t_scheduler_module_imports_cleanly),
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
