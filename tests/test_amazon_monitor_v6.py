#!/usr/bin/env python3
"""
tests/test_amazon_monitor_v6.py - Verify amazon_monitor is migrated to v6.0.0 lifecycle

Runs 4 structural checks against the actual plugin file + plugins.py to
confirm Step 5's migration is in place. These are STRUCTURAL tests - they
read the source files as text and look for expected code patterns. They
don't actually run the plugin (which would require Playwright + real
network + Amazon cookies).

  1.  amazon_monitor_uses_register_not_start
        plugins/amazon_monitor.py defines `def register(self, scheduler)`
        and no longer defines `def start(self, schedule)`.

  2.  daemon_thread_wrapping_intact
        _check_all() still spawns a daemon thread to wrap the Playwright
        work. This pattern existed pre-Step-5 (it was the original fix for
        sync_playwright inside asyncio) and Step 5 must preserve it.

  3.  scheduler_register_job_called_with_kickoff
        register() calls scheduler.register_job(...) with kickoff=True and
        kickoff_delay=90 - staggered behind bestbuy_invites (T+30s) so two
        heavy Playwright sessions don't stack during boot.

  4.  plugins_py_wrapper_uses_new_lifecycle
        AmazonMonitor_Plugin in plugins.py now overrides init() and
        register() instead of the legacy start(). Version bumped to 1.1.

Exit code 0 = all 4 pass.

Run from project root:
    python tests/test_amazon_monitor_v6.py
"""

from __future__ import annotations

import os
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

AMAZON_PATH  = os.path.join(_root, "plugins", "amazon_monitor.py")
PLUGINS_PATH = os.path.join(_root, "plugins.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# -----------------------------------------------------------------------------
# TESTS
# -----------------------------------------------------------------------------

def t_amazon_monitor_uses_register_not_start():
    src = _read(AMAZON_PATH)
    # New lifecycle method must be present
    assert "def register(self, scheduler)" in src, (
        "plugins/amazon_monitor.py should define `def register(self, scheduler)` "
        "for the v6.0.0 phased boot lifecycle"
    )
    # Old lifecycle method must be gone
    assert "def start(self, schedule)" not in src, (
        "plugins/amazon_monitor.py still defines `def start(self, schedule)` "
        "- the migration is incomplete"
    )


def t_daemon_thread_wrapping_intact():
    """The daemon thread wrapping in _check_all() existed pre-Step-5
    (it was the fix for sync_playwright inside asyncio) and must be
    preserved by the migration."""
    src = _read(AMAZON_PATH)
    assert "import threading" in src, (
        "_check_all should import threading to wrap Playwright work"
    )
    assert "daemon=True" in src, (
        "Should use daemon=True on the wrapping thread"
    )
    assert 'name="amz_check_all"' in src or            "name='amz_check_all'" in src, (
        "Daemon thread should retain its 'amz_check_all' diagnostic name"
    )
    # Also: the threading wrapper call pattern. Both were there pre-Step-5
    # and must survive the migration unchanged.
    assert ".start()" in src, "wrapping thread.start() must remain"
    assert ".join(timeout=" in src, "wrapping thread.join(timeout=N) must remain"


def t_scheduler_register_job_called_with_kickoff():
    src = _read(AMAZON_PATH)
    # Must call register_job
    assert "scheduler.register_job(" in src, (
        "register() should call scheduler.register_job(...)"
    )
    # Must use kickoff for staggered first run
    assert "kickoff=True" in src, (
        "register_job should use kickoff=True for staggered first dispatch"
    )
    assert "kickoff_delay=90" in src, (
        "amazon_monitor should use kickoff_delay=90 (staggered behind "
        "bestbuy_invites at T+30s, since both run heavy Playwright)"
    )
    # Cadence string must be correct
    assert 'cadence="every 15 minutes"' in src or            "cadence='every 15 minutes'" in src, (
        "register_job should declare cadence='every 15 minutes'"
    )


def t_plugins_py_wrapper_uses_new_lifecycle():
    src = _read(PLUGINS_PATH)

    # Find the AmazonMonitor_Plugin class block and check it specifically
    cls_start = src.find("class AmazonMonitor_Plugin(Plugin):")
    assert cls_start >= 0, "AmazonMonitor_Plugin class not found in plugins.py"
    # Walk to next class declaration to bound the block
    next_cls = src.find("\nclass ", cls_start + 1)
    cls_block = src[cls_start:next_cls if next_cls > 0 else len(src)]

    # New lifecycle methods present
    assert "def init(self, config, products):" in cls_block, (
        "AmazonMonitor_Plugin should override init(config, products)"
    )
    assert "def register(self, scheduler):" in cls_block, (
        "AmazonMonitor_Plugin should override register(scheduler)"
    )
    # Old lifecycle method gone from THIS class (other plugins still have start())
    assert "def start(self, config, products, schedule):" not in cls_block, (
        "AmazonMonitor_Plugin still has legacy start() - migration incomplete"
    )
    # Version bumped
    assert 'version = "1.1"' in cls_block, (
        "AmazonMonitor_Plugin version should be bumped to 1.1 to mark the migration"
    )


# -----------------------------------------------------------------------------
# RUNNER
# -----------------------------------------------------------------------------
def main():
    print("=" * 70)
    print(" v6.0.0 step 5 - amazon_monitor lifecycle migration tests")
    print("=" * 70)

    tests = [
        ("amazon_monitor_uses_register_not_start",     t_amazon_monitor_uses_register_not_start),
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
