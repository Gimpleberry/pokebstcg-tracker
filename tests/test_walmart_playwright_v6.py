#!/usr/bin/env python3
"""
tests/test_walmart_playwright_v6.py - Verify walmart_playwright plugin (v6.1.1)

Runs 8 structural checks against the actual plugin file + plugins.py to
confirm Step 2 of v6.1.1 is in place. These are STRUCTURAL tests - they
read the source files as text and look for expected code patterns. They
don't actually run the plugin (which would require Playwright + Chrome +
real network + warmed BROWSER_PROFILE).

  1.  walmart_playwright_uses_register_not_start
        plugins/walmart_playwright.py defines `def register(self, scheduler)`
        and no longer defines `def start(self, schedule)`. Plugin is born
        on the v6.0.0 phased lifecycle.

  2.  daemon_thread_wrapping_present
        _check_all() spawns a daemon thread to wrap the Playwright work.
        Same pattern as the three v6.0.0-migrated plugins. Required to
        avoid sync_playwright conflict with tracker.py's asyncio loop.

  3.  scheduler_register_job_called_with_kickoff_210
        register() calls scheduler.register_job(...) with kickoff=True
        and kickoff_delay=210 - staggered behind costco at T+150s.

  4.  plugins_py_wrapper_uses_new_lifecycle
        WalmartPlaywright_Plugin in plugins.py overrides init() and
        register(). No legacy start(). Version 1.0.

  5.  walmart_playwright_uses_patchright
        Plugin imports patchright (with playwright fallback). Recon
        proved vanilla playwright + stealth is insufficient for PerimeterX;
        patchright's browser-launch-level evasions are required.

  6.  walmart_playwright_uses_offscreen_window_args
        Plugin passes --window-position to keep the headful window
        invisible. Required because headless=False is required for
        PerimeterX bypass, but a visible window every 15 min would
        be unacceptable operationally.

  7.  walmart_playwright_uses_chrome_channel_chain
        Plugin tries chrome -> msedge -> chromium fallback. Real Chrome
        has the most authentic fingerprint per patchright recommendations.

  8.  walmart_playwright_extracts_from_next_data
        Plugin parses __NEXT_DATA__ and reads from primaryOffer as the
        primary signal source. Recon confirmed this is more reliable
        than CSS selectors which churn frequently.

Exit code 0 = all 8 pass.

Run from project root:
    python tests/test_walmart_playwright_v6.py
"""

from __future__ import annotations

import os
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

WALMART_PATH = os.path.join(_root, "plugins", "walmart_playwright.py")
PLUGINS_PATH = os.path.join(_root, "plugins.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------------

def t_walmart_playwright_uses_register_not_start():
    src = _read(WALMART_PATH)
    assert "def register(self, scheduler)" in src, (
        "plugins/walmart_playwright.py should define "
        "`def register(self, scheduler)` for the v6.0.0 phased boot lifecycle"
    )
    assert "def start(self, schedule)" not in src, (
        "plugins/walmart_playwright.py should NOT define legacy "
        "`def start(self, schedule)` - it's born on phased lifecycle"
    )


def t_daemon_thread_wrapping_present():
    src = _read(WALMART_PATH)
    assert "import threading" in src, (
        "_check_all should import threading to wrap Playwright work "
        "(avoids sync_playwright conflict with tracker.py asyncio loop)"
    )
    assert "daemon=True" in src, (
        "Should use daemon=True on the wrapping thread"
    )
    assert ('name="walmart_playwright_check_all"' in src
            or "name='walmart_playwright_check_all'" in src), (
        "Daemon thread should have the diagnostic name "
        "'walmart_playwright_check_all'"
    )
    assert ".start()" in src, "wrapping thread .start() must be called"
    assert ".join(timeout=" in src, (
        "wrapping thread .join(timeout=N) must be called - prevents a hung "
        "Playwright session blocking the check loop indefinitely"
    )


def t_scheduler_register_job_called_with_kickoff_210():
    src = _read(WALMART_PATH)
    assert "scheduler.register_job(" in src, (
        "register() should call scheduler.register_job(...)"
    )
    assert "kickoff=True" in src, (
        "register_job should use kickoff=True for staggered first dispatch"
    )
    assert "kickoff_delay=210" in src, (
        "walmart_playwright should use kickoff_delay=210 (staggered behind "
        "costco at T+150s, all four heavy Playwright plugins spaced 60s apart)"
    )
    assert ('cadence="every 30 minutes"' in src
            or "cadence='every 30 minutes'" in src), (
        "register_job should declare cadence='every 30 minutes'"
    )


def t_plugins_py_wrapper_uses_new_lifecycle():
    src = _read(PLUGINS_PATH)

    cls_start = src.find("class WalmartPlaywright_Plugin(Plugin):")
    assert cls_start >= 0, (
        "WalmartPlaywright_Plugin class not found in plugins.py - the "
        "plugin wrapper is missing"
    )
    next_cls = src.find("\nclass ", cls_start + 1)
    cls_block = src[cls_start:next_cls if next_cls > 0 else len(src)]

    assert "def init(self, config, products):" in cls_block, (
        "WalmartPlaywright_Plugin should override init(config, products)"
    )
    assert "def register(self, scheduler):" in cls_block, (
        "WalmartPlaywright_Plugin should override register(scheduler)"
    )
    assert "def start(self, config, products, schedule):" not in cls_block, (
        "WalmartPlaywright_Plugin should NOT have legacy "
        "start(config, products, schedule) - it's born phased"
    )
    assert 'version = "1.0"' in cls_block, (
        "WalmartPlaywright_Plugin version should be '1.0' (initial release)"
    )


def t_walmart_playwright_uses_patchright():
    """Recon proved patchright's browser-launch-level evasions are required;
    vanilla playwright (even with stealth + warmed profile + system Chrome)
    was blocked. Plugin must try patchright first."""
    src = _read(WALMART_PATH)
    assert "patchright.sync_api" in src, (
        "Plugin should import from patchright.sync_api as the primary engine "
        "(fall back to vanilla playwright is acceptable but not preferred)"
    )


def t_walmart_playwright_uses_offscreen_window_args():
    """Plugin must use --window-position to keep the headful window
    invisible. Recon confirmed --window-position=-2400,-2400 works on
    Windows without breaking patchright evasions."""
    src = _read(WALMART_PATH)
    assert "--window-position=" in src, (
        "Plugin should use --window-position to push the window off-screen "
        "(required for operational invisibility - headless=False mandatory "
        "for PerimeterX bypass)"
    )


def t_walmart_playwright_uses_chrome_channel_chain():
    """Plugin tries chrome (best fingerprint) before falling back to
    msedge / chromium. Per patchright docs."""
    src = _read(WALMART_PATH)
    assert '"chrome"' in src, (
        "Plugin should try channel='chrome' first (real system Chrome has "
        "the most authentic fingerprint per patchright docs)"
    )
    assert '"msedge"' in src, (
        "Plugin should fall back to channel='msedge' (Edge ships with Windows "
        "by default, also Chromium-based)"
    )
    assert '"chromium"' in src, (
        "Plugin should fall back to channel='chromium' as last resort "
        "(bundled Chromium-for-Testing - works but most easily fingerprinted)"
    )


def t_walmart_playwright_extracts_from_next_data():
    """Plugin parses __NEXT_DATA__ as the primary signal source. Recon
    confirmed __NEXT_DATA__ is present on Walmart product pages and contains
    the full product tree under primaryOffer. Reading JSON is more stable
    than CSS selectors which churn."""
    src = _read(WALMART_PATH)
    assert "__NEXT_DATA__" in src, (
        "Plugin should reference __NEXT_DATA__ for primary signal extraction"
    )
    assert "primaryOffer" in src, (
        "Plugin should drill into primaryOffer for stock/price/seller fields"
    )


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------
def main():
    print("=" * 70)
    print(" v6.1.1 step 2 - walmart_playwright structural checks")
    print("=" * 70)

    tests = [
        ("walmart_playwright_uses_register_not_start",
            t_walmart_playwright_uses_register_not_start),
        ("daemon_thread_wrapping_present",
            t_daemon_thread_wrapping_present),
        ("scheduler_register_job_called_with_kickoff_210",
            t_scheduler_register_job_called_with_kickoff_210),
        ("plugins_py_wrapper_uses_new_lifecycle",
            t_plugins_py_wrapper_uses_new_lifecycle),
        ("walmart_playwright_uses_patchright",
            t_walmart_playwright_uses_patchright),
        ("walmart_playwright_uses_offscreen_window_args",
            t_walmart_playwright_uses_offscreen_window_args),
        ("walmart_playwright_uses_chrome_channel_chain",
            t_walmart_playwright_uses_chrome_channel_chain),
        ("walmart_playwright_extracts_from_next_data",
            t_walmart_playwright_extracts_from_next_data),
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
