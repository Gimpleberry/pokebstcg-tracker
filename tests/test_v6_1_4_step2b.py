#!/usr/bin/env python3
"""
tests/test_v6_1_4_step2b.py

STRUCTURAL tests for v6.1.4 step 2b (callsite migration).

This is the same test pattern as the v6.1.3 step 2 tests we wrote
last night. They verify that each runtime callsite has migrated from
the shared BROWSER_PROFILE to its dedicated BROWSER_PROFILES["..."]
key, while leaving headful flows untouched.

These tests do NOT exercise Playwright. Step 2a's auto-warm infrastructure
guarantees profile dirs are warm before tracker.py runs, so behavior
verification happens during the live boot test after applying.

Tests:
  1. tracker_callsites_migrated
  2. amazon_headless_callsite_migrated
  3. bestbuy_invites_headless_migrated
  4. costco_callsite_migrated
  5. headful_flows_untouched

Exit code 0 = all 5 pass.

Run from project root:
    python tests/test_v6_1_4_step2b.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback


_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

TRACKER_PATH = os.path.join(_root, "tracker.py")
SHARED_PATH = os.path.join(_root, "shared.py")
AMAZON_PATH = os.path.join(_root, "plugins", "amazon_monitor.py")
BESTBUY_INVITES_PATH = os.path.join(_root, "plugins", "bestbuy_invites.py")
COSTCO_PATH = os.path.join(_root, "plugins", "costco_tracker.py")
CART_PRELOADER_PATH = os.path.join(_root, "plugins", "cart_preloader.py")
WALMART_PATH = os.path.join(_root, "plugins", "walmart_playwright.py")

IMPORT_LINE_RE = re.compile(
    r"^\s*from\s+shared\s+import\s+BROWSER_PROFILES\b",
    re.MULTILINE,
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _has_import(src: str) -> bool:
    return bool(IMPORT_LINE_RE.search(src))


# ----------------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------------

def t_tracker_callsites_migrated():
    src = _read(TRACKER_PATH)
    assert _has_import(src), (
        "tracker.py is missing `from shared import BROWSER_PROFILES`. "
        "v6.1.4 step 2b should have added this import."
    )
    assert 'BROWSER_PROFILES["target"]' in src, (
        'tracker.py:check_target should use BROWSER_PROFILES["target"], '
        "not BROWSER_PROFILE."
    )
    assert 'BROWSER_PROFILES["bestbuy_batch"]' in src, (
        'tracker.py:check_bestbuy_batch should use '
        'BROWSER_PROFILES["bestbuy_batch"], not BROWSER_PROFILE.'
    )


def t_amazon_headless_callsite_migrated():
    src = _read(AMAZON_PATH)
    assert _has_import(src), (
        "plugins/amazon_monitor.py is missing `from shared import "
        "BROWSER_PROFILES`."
    )
    assert 'BROWSER_PROFILES["amazon"]' in src, (
        'amazon_monitor.py headless callsite should use '
        'BROWSER_PROFILES["amazon"].'
    )
    # Headful callsite (headless=False) must STILL use BROWSER_PROFILE.
    # Cart preload needs the warmed Amazon login session.
    assert "BROWSER_PROFILE,\n                        headless=False" in src, (
        "amazon_monitor.py headful callsite must STILL use BROWSER_PROFILE "
        "(not BROWSER_PROFILES). Migrating it would break cart preload by "
        "evicting the warmed Amazon login session."
    )


def t_bestbuy_invites_headless_migrated():
    src = _read(BESTBUY_INVITES_PATH)
    assert _has_import(src), (
        "plugins/bestbuy_invites.py is missing `from shared import "
        "BROWSER_PROFILES`."
    )
    assert 'BROWSER_PROFILES["bestbuy_invites"]' in src, (
        'bestbuy_invites.py headless callsite should use '
        'BROWSER_PROFILES["bestbuy_invites"].'
    )
    assert "BROWSER_PROFILE,\n                        headless=False" in src, (
        "bestbuy_invites.py headful invite-request callsite must STILL use "
        "BROWSER_PROFILE. Migrating it would break the user-visible invite "
        "verification flow."
    )


def t_costco_callsite_migrated():
    src = _read(COSTCO_PATH)
    assert _has_import(src), (
        "plugins/costco_tracker.py is missing `from shared import "
        "BROWSER_PROFILES`."
    )
    assert 'BROWSER_PROFILES["costco"]' in src, (
        'costco_tracker.py callsite should use BROWSER_PROFILES["costco"].'
    )


def t_headful_flows_untouched():
    """Defensive: confirm step 2b did NOT migrate headful flows."""
    # shared.py:open_browser still uses BROWSER_PROFILE
    shared_src = _read(SHARED_PATH)
    assert re.search(
        r"BROWSER_PROFILE,\s*\n\s+headless=False",
        shared_src,
    ), (
        "shared.py:open_browser headful callsite seems to have been "
        "migrated. It should STILL use BROWSER_PROFILE (warmed profile)."
    )

    # cart_preloader.py shouldn't reference BROWSER_PROFILES at all
    if os.path.isfile(CART_PRELOADER_PATH):
        cp_src = _read(CART_PRELOADER_PATH)
        assert "BROWSER_PROFILES[" not in cp_src, (
            "cart_preloader.py shouldn't reference BROWSER_PROFILES - it "
            "uses async_playwright + headful and needs the warmed profile."
        )
        assert "BROWSER_PROFILE" in cp_src, (
            "cart_preloader.py should still use BROWSER_PROFILE."
        )

    # walmart_playwright.py still uses BROWSER_PROFILE
    if os.path.isfile(WALMART_PATH):
        wm_src = _read(WALMART_PATH)
        assert "BROWSER_PROFILE" in wm_src, (
            "walmart_playwright.py should still reference BROWSER_PROFILE."
        )


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" v6.1.4 step 2b - per-plugin profile callsite migration")
    print("=" * 70)

    tests = [
        ("tracker_callsites_migrated", t_tracker_callsites_migrated),
        ("amazon_headless_callsite_migrated", t_amazon_headless_callsite_migrated),
        ("bestbuy_invites_headless_migrated", t_bestbuy_invites_headless_migrated),
        ("costco_callsite_migrated", t_costco_callsite_migrated),
        ("headful_flows_untouched", t_headful_flows_untouched),
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
