#!/usr/bin/env python3
"""
tests/test_browser_profiles_v6_1_3.py

Verifies the BROWSER_PROFILES dict added to shared.py in v6.1.3 step 1.
STRUCTURAL tests - they read shared.py as text and look for expected
patterns, plus they import shared and inspect the dict at runtime.

Why this exists
---------------
After v6.1.2 step 2, ALL plugins started using launch_chromium_with_fallback
against the same BROWSER_PROFILE directory. This caused profile lock
contention on Windows: when Target's lazy-persistent session held the
profile, every other plugin (Amazon, Costco, BestBuy, Walmart) failed at
launch with `Settings version is not 1` / `exitCode=21` from Crashpad.

v6.1.3 step 1 adds a registry of per-plugin profile dirs in shared.py.
Step 2 will refactor the headless-scrape callsites to use them. Headful
flows (cart_preloader, open_browser, etc.) keep using BROWSER_PROFILE
because they need shared user-login cookies.

The 7 tests
-----------
  1. dict_is_defined
        shared.BROWSER_PROFILES exists and is a dict.

  2. has_required_keys
        All 7 expected keys are present: default, walmart, target,
        amazon, bestbuy_batch, bestbuy_invites, costco.

  3. default_aliases_browser_profile
        BROWSER_PROFILES["default"] == BROWSER_PROFILE.
        Backward-compat for any code that still imports BROWSER_PROFILE.

  4. walmart_aliases_browser_profile
        BROWSER_PROFILES["walmart"] == BROWSER_PROFILE.
        Walmart needs the WARMED profile (PerimeterX-trusted). Re-warming
        a fresh profile would defeat the v6.1.1 plugin's whole anti-detection
        stack.

  5. headless_keys_are_distinct_paths
        target / amazon / bestbuy_batch / bestbuy_invites / costco each
        point to a unique path under APPDATA_DIR. No two plugins share
        a profile - that was the bug.

  6. headless_paths_under_appdata
        All non-alias paths live under APPDATA_DIR. Putting browser data
        in the project tree would risk it getting committed to git.

  7. dict_has_no_duplicate_paths_among_isolated_plugins
        The 5 isolated profiles must point to 5 different directories.
        Catches any copy-paste mistakes in the dict.

Exit code 0 = all 7 pass.

Run from project root:
    python tests/test_browser_profiles_v6_1_3.py
"""

from __future__ import annotations

import os
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

SHARED_PATH = os.path.join(_root, "shared.py")

REQUIRED_KEYS = {
    "default",
    "walmart",
    "target",
    "amazon",
    "bestbuy_batch",
    "bestbuy_invites",
    "costco",
}

# Keys that SHOULD have isolated paths (everyone except aliases)
ISOLATED_KEYS = {
    "target",
    "amazon",
    "bestbuy_batch",
    "bestbuy_invites",
    "costco",
}

# Keys that SHOULD alias BROWSER_PROFILE (the warmed one)
ALIAS_KEYS = {"default", "walmart"}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------------

def t_dict_is_defined():
    src = _read(SHARED_PATH)
    assert "BROWSER_PROFILES" in src, (
        "shared.py should define BROWSER_PROFILES at module level"
    )
    # Confirm it's a dict literal
    assert "BROWSER_PROFILES = {" in src, (
        "BROWSER_PROFILES should be defined as a dict literal "
        "(`BROWSER_PROFILES = {...}`)"
    )

    # Live import + type check
    if _root not in sys.path:
        sys.path.insert(0, _root)
    try:
        import shared
    except Exception as e:
        raise AssertionError(
            f"shared.py failed to import: {type(e).__name__}: {e}"
        )
    assert hasattr(shared, "BROWSER_PROFILES"), (
        "shared.BROWSER_PROFILES not exported"
    )
    assert isinstance(shared.BROWSER_PROFILES, dict), (
        f"BROWSER_PROFILES should be a dict, got {type(shared.BROWSER_PROFILES)}"
    )


def t_has_required_keys():
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import shared
    actual = set(shared.BROWSER_PROFILES.keys())
    missing = REQUIRED_KEYS - actual
    assert not missing, f"BROWSER_PROFILES missing required keys: {missing}"


def t_default_aliases_browser_profile():
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import shared
    assert shared.BROWSER_PROFILES["default"] == shared.BROWSER_PROFILE, (
        "BROWSER_PROFILES['default'] must equal BROWSER_PROFILE - it's the "
        "backward-compat alias for any unmigrated callers"
    )


def t_walmart_aliases_browser_profile():
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import shared
    assert shared.BROWSER_PROFILES["walmart"] == shared.BROWSER_PROFILE, (
        "BROWSER_PROFILES['walmart'] must equal BROWSER_PROFILE - walmart "
        "uses the WARMED PerimeterX-trusted profile. Pointing it elsewhere "
        "would defeat the v6.1.1 anti-detection stack."
    )


def t_headless_keys_are_distinct_paths():
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import shared
    paths = {k: shared.BROWSER_PROFILES[k] for k in ISOLATED_KEYS}
    # Check all paths are distinct from each other
    seen_paths = set()
    for key, path in paths.items():
        assert path not in seen_paths, (
            f"BROWSER_PROFILES['{key}'] = {path!r} duplicates another "
            f"isolated key. Each isolated plugin needs its OWN dir to "
            f"avoid the lock contention bug we're fixing."
        )
        seen_paths.add(path)
    # Also check none of them equal BROWSER_PROFILE (would defeat isolation)
    for key, path in paths.items():
        assert path != shared.BROWSER_PROFILE, (
            f"BROWSER_PROFILES['{key}'] must NOT equal BROWSER_PROFILE "
            f"(found: {path!r}). Isolated profiles need their own dir."
        )


def t_headless_paths_under_appdata():
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import shared
    appdata = shared.APPDATA_DIR
    for key in ISOLATED_KEYS:
        path = shared.BROWSER_PROFILES[key]
        # Normalize for case-insensitive Windows paths
        path_norm = os.path.normcase(os.path.normpath(path))
        appdata_norm = os.path.normcase(os.path.normpath(appdata))
        assert path_norm.startswith(appdata_norm), (
            f"BROWSER_PROFILES['{key}'] = {path!r} should live under "
            f"APPDATA_DIR ({appdata!r}). Putting browser data in the "
            f"project tree risks committing it to git."
        )


def t_dict_has_no_duplicate_paths_among_isolated_plugins():
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import shared
    # All isolated paths combined - count must equal number of isolated keys
    isolated_paths = [shared.BROWSER_PROFILES[k] for k in ISOLATED_KEYS]
    assert len(set(isolated_paths)) == len(isolated_paths), (
        f"Found duplicate paths among isolated profiles: {isolated_paths}. "
        f"Each must be unique to avoid the contention bug."
    )


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" v6.1.3 step 1 - BROWSER_PROFILES dict in shared.py")
    print("=" * 70)

    tests = [
        ("dict_is_defined",
            t_dict_is_defined),
        ("has_required_keys",
            t_has_required_keys),
        ("default_aliases_browser_profile",
            t_default_aliases_browser_profile),
        ("walmart_aliases_browser_profile",
            t_walmart_aliases_browser_profile),
        ("headless_keys_are_distinct_paths",
            t_headless_keys_are_distinct_paths),
        ("headless_paths_under_appdata",
            t_headless_paths_under_appdata),
        ("dict_has_no_duplicate_paths_among_isolated_plugins",
            t_dict_has_no_duplicate_paths_among_isolated_plugins),
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
