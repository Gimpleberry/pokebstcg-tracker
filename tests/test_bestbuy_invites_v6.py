#!/usr/bin/env python3
"""
tests/test_bestbuy_invites_v6.py - Verify bestbuy_invites is migrated to v6.0.0 lifecycle

Runs 4 structural checks against the actual plugin file + plugins.py to
confirm Step 4's migration is in place. These are STRUCTURAL tests — they
read the source files as text and look for expected code patterns. They
don't actually run the plugin (which would require Playwright + real
network + retailer cookies).

  1.  bestbuy_invites_uses_register_not_start
        plugins/bestbuy_invites.py defines `def register(self, scheduler)`
        and no longer defines `def start(self, schedule)`.

  2.  daemon_thread_wrapping_present
        _check_all_products() spawns a daemon thread to wrap the Playwright
        work. Same pattern amazon_monitor and costco_tracker already use.
        This is the fix for the "sync_playwright inside asyncio loop" error.

  3.  scheduler_register_job_called_with_kickoff
        register() calls scheduler.register_job(...) with kickoff=True and
        kickoff_delay=30 — the staggered first-run dispatch promised in
        the V6_0_0_SPEC stagger schedule.

  4.  plugins_py_wrapper_uses_new_lifecycle
        BestBuyInvites_Plugin in plugins.py now overrides init() and
        register() instead of the legacy start(). Version bumped to 1.1.

Exit code 0 = all 4 pass.

Run from project root:
    python tests/test_bestbuy_invites_v6.py
"""

from __future__ import annotations

import os
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

BESTBUY_PATH = os.path.join(_root, "plugins", "bestbuy_invites.py")
PLUGINS_PATH = os.path.join(_root, "plugins.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_bestbuy_invites_uses_register_not_start():
    src = _read(BESTBUY_PATH)
    # New lifecycle method must be present
    assert "def register(self, scheduler)" in src, (
        "plugins/bestbuy_invites.py should define `def register(self, scheduler)` "
        "for the v6.0.0 phased boot lifecycle"
    )
    # Old lifecycle method must be gone
    assert "def start(self, schedule)" not in src, (
        "plugins/bestbuy_invites.py still defines `def start(self, schedule)` "
        "— the migration is incomplete"
    )


def t_daemon_thread_wrapping_present():
    src = _read(BESTBUY_PATH)
    # The fix for "sync_playwright inside asyncio loop": run in daemon thread
    assert "import threading" in src, (
        "_check_all_products should import threading to wrap Playwright work"
    )
    assert "daemon=True" in src, (
        "Should use daemon=True on the wrapping thread (matches amazon_monitor "
        "and costco_tracker patterns)"
    )
    assert "name=\"bestbuy_invites_check\"" in src or \
           "name='bestbuy_invites_check'" in src, (
        "Daemon thread should have a recognizable name for diagnostics"
    )


def t_scheduler_register_job_called_with_kickoff():
    src = _read(BESTBUY_PATH)
    # Must call register_job
    assert "scheduler.register_job(" in src, (
        "register() should call scheduler.register_job(...)"
    )
    # Must use kickoff for staggered first run (key v6.0.0 behavior)
    assert "kickoff=True" in src, (
        "register_job should use kickoff=True for staggered first dispatch"
    )
    assert "kickoff_delay=30" in src, (
        "bestbuy_invites should use kickoff_delay=30 (per V6_0_0_SPEC stagger schedule)"
    )


def t_plugins_py_wrapper_uses_new_lifecycle():
    src = _read(PLUGINS_PATH)

    # Find the BestBuyInvites_Plugin class block and look at it specifically
    cls_start = src.find("class BestBuyInvites_Plugin(Plugin):")
    assert cls_start >= 0, "BestBuyInvites_Plugin class not found in plugins.py"
    # Walk to next class declaration to bound the block
    next_cls = src.find("\nclass ", cls_start + 1)
    cls_block = src[cls_start:next_cls if next_cls > 0 else len(src)]

    # New lifecycle methods present
    assert "def init(self, config, products):" in cls_block, (
        "BestBuyInvites_Plugin should override init(config, products)"
    )
    assert "def register(self, scheduler):" in cls_block, (
        "BestBuyInvites_Plugin should override register(scheduler)"
    )
    # Old lifecycle method gone from THIS class (other plugins still have start())
    assert "def start(self, config, products, schedule):" not in cls_block, (
        "BestBuyInvites_Plugin still has legacy start() — the migration is incomplete"
    )
    # Version bumped
    assert 'version = "1.1"' in cls_block, (
        "BestBuyInvites_Plugin version should be bumped to 1.1 to mark the migration"
    )


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(" v6.0.0 step 4 - bestbuy_invites lifecycle migration tests")
    print("=" * 70)

    tests = [
        ("bestbuy_invites_uses_register_not_start",   t_bestbuy_invites_uses_register_not_start),
        ("daemon_thread_wrapping_present",             t_daemon_thread_wrapping_present),
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
