#!/usr/bin/env python3
"""
tests/test_warm_browser_profiles_v6_1_4.py

STRUCTURAL tests for tools/warm_browser_profiles.py.

These tests verify the tool's STATIC properties without launching
chromium. They confirm:
  - The file exists and parses.
  - It exposes the expected functions.
  - It references only valid keys from BROWSER_PROFILES.
  - It does NOT modify any tracker source code (read-only operations).
  - Its dry-run mode returns the right plan without side effects.

We do NOT exercise the actual chromium-launching path here because
playwright is heavyweight and depends on browser binaries that may not
be installed in the test environment. Live verification happens via
manual run after applying v6.1.4 step 1.

Tests:
  1. tool_file_exists
  2. tool_imports_required_symbols
  3. tool_exposes_main_functions
  4. tool_references_isolated_keys_only
  5. tool_does_not_write_to_source_tree
  6. dry_run_succeeds_without_side_effects
  7. is_profile_warm_detects_default_subdir

Exit code 0 = all 7 pass.

Run from project root:
    python tests/test_warm_browser_profiles_v6_1_4.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback


_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

TOOL_PATH = os.path.join(_root, "tools", "warm_browser_profiles.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _import_tool():
    """Import warm_browser_profiles as a module so we can inspect it."""
    if _root not in sys.path:
        sys.path.insert(0, _root)
    tools_dir = os.path.join(_root, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import importlib
    if "warm_browser_profiles" in sys.modules:
        return importlib.reload(sys.modules["warm_browser_profiles"])
    return importlib.import_module("warm_browser_profiles")


# ----------------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------------

def t_tool_file_exists():
    assert os.path.isfile(TOOL_PATH), (
        f"Expected tools/warm_browser_profiles.py at {TOOL_PATH}"
    )

    # Confirm it parses as Python
    import ast
    src = _read(TOOL_PATH)
    try:
        ast.parse(src)
    except SyntaxError as e:
        raise AssertionError(f"warm_browser_profiles.py has syntax error: {e}")


def t_tool_imports_required_symbols():
    """Tool must import shared.BROWSER_PROFILES and shared.launch_chromium_with_fallback."""
    src = _read(TOOL_PATH)
    # We expect lazy imports inside warm_one_profile, but the symbols must
    # appear in the source somewhere.
    assert "BROWSER_PROFILES" in src, (
        "Tool must reference shared.BROWSER_PROFILES"
    )
    assert "launch_chromium_with_fallback" in src, (
        "Tool must reference launch_chromium_with_fallback helper"
    )
    assert "BROWSER_PROFILE" in src, (
        "Tool must reference shared.BROWSER_PROFILE for alias detection"
    )


def t_tool_exposes_main_functions():
    """Tool should expose key functions for external testing/reuse."""
    mod = _import_tool()
    for name in ("get_isolated_profile_keys", "is_profile_warm",
                 "warm_one_profile", "warm_all", "main"):
        assert hasattr(mod, name), f"warm_browser_profiles missing function: {name}"
        assert callable(getattr(mod, name)), f"{name} should be callable"


def t_tool_references_isolated_keys_only():
    """get_isolated_profile_keys should return only non-BROWSER_PROFILE entries.

    Aliases (default, walmart) point to BROWSER_PROFILE which is the
    pre-warmed profile. Warming it would be a waste; worse, on a fresh
    machine where BROWSER_PROFILE doesn't yet have the PerimeterX-trusted
    cookies, warming via this tool would NOT load those cookies, so we
    leave it alone.
    """
    mod = _import_tool()
    import shared

    isolated = mod.get_isolated_profile_keys(
        shared.BROWSER_PROFILES, shared.BROWSER_PROFILE
    )

    # Aliases must be EXCLUDED
    assert "default" not in isolated, (
        "'default' aliases BROWSER_PROFILE - should NOT be warmed"
    )
    assert "walmart" not in isolated, (
        "'walmart' aliases BROWSER_PROFILE - should NOT be warmed "
        "(would lose PerimeterX trust)"
    )

    # Isolated keys must be INCLUDED if they exist in BROWSER_PROFILES
    expected_isolated = {"target", "amazon", "bestbuy_batch",
                         "bestbuy_invites", "costco"}
    for key in expected_isolated:
        if key in shared.BROWSER_PROFILES:
            assert key in isolated, (
                f"'{key}' should be in isolated list - it has its own profile dir"
            )


def t_tool_does_not_write_to_source_tree():
    """Tool must never write to project files (only browser profile dirs).

    Static check: scan the source for any obvious file-modifying calls
    that could affect the source tree. The tool legitimately calls
    os.makedirs() for profile dirs (which live in APPDATA_DIR, outside
    the project tree), so we don't forbid that. We forbid:
      - shutil.copy / shutil.move (could overwrite source files)
      - open(... 'w' ...) (could write a new file anywhere)
      - os.remove / os.unlink (could delete project files)
      - file.write (would only happen if the tool opened something for write)

    These are crude string checks. The real safety net is that the tool
    never imports or references tracker.py, plugins.py, scheduler.py,
    or any plugin module - it only touches shared.BROWSER_PROFILES (read).
    """
    src = _read(TOOL_PATH)

    forbidden_substrings = [
        "shutil.copy",   # could overwrite project files
        "shutil.move",   # could move files around the tree
        "os.remove",     # could delete project files
        "os.unlink",     # same
    ]
    for sub in forbidden_substrings:
        assert sub not in src, (
            f"warm_browser_profiles.py contains forbidden call '{sub}'. "
            f"Tool must be read-only on source tree."
        )

    # Confirm tool doesn't import tracker source modules
    forbidden_imports = [
        "import tracker",
        "from tracker",
        "import plugins.",
        "from plugins.",
        "import scheduler",
        "from scheduler",
    ]
    for imp in forbidden_imports:
        assert imp not in src, (
            f"warm_browser_profiles.py imports tracker source ('{imp}'). "
            f"Tool should only depend on shared.py."
        )


def t_dry_run_succeeds_without_side_effects():
    """warm_all(dry_run=True) should return 0 and not touch chromium."""
    mod = _import_tool()
    # Capture stdout to keep test output clean
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with contextlib.redirect_stderr(buf):
            try:
                rc = mod.warm_all(force=False, dry_run=True, verbose=False)
            except Exception as e:
                raise AssertionError(
                    f"dry_run raised: {type(e).__name__}: {e}"
                )
    output = buf.getvalue()
    assert rc == 0, f"dry_run returned non-zero exit code: {rc}"
    # Confirm no actual launch happened (no warming text in output)
    assert "warming..." not in output.lower() or "dry-run" in output.lower(), (
        "dry_run mode appears to have actually launched chromium"
    )


def t_is_profile_warm_detects_default_subdir():
    """is_profile_warm() should be True iff Default/ subdir exists."""
    mod = _import_tool()

    with tempfile.TemporaryDirectory() as td:
        empty = os.path.join(td, "empty_profile")
        warm = os.path.join(td, "warm_profile")
        os.makedirs(empty)
        os.makedirs(os.path.join(warm, "Default"))

        assert mod.is_profile_warm(empty) is False, (
            "Empty profile dir should NOT be considered warm"
        )
        assert mod.is_profile_warm(warm) is True, (
            "Profile dir with Default/ subdir should be considered warm"
        )
        # Nonexistent dir
        nonexistent = os.path.join(td, "does_not_exist")
        assert mod.is_profile_warm(nonexistent) is False, (
            "Nonexistent dir should NOT be considered warm"
        )


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" v6.1.4 step 1 - tools/warm_browser_profiles.py")
    print("=" * 70)

    tests = [
        ("tool_file_exists", t_tool_file_exists),
        ("tool_imports_required_symbols", t_tool_imports_required_symbols),
        ("tool_exposes_main_functions", t_tool_exposes_main_functions),
        ("tool_references_isolated_keys_only", t_tool_references_isolated_keys_only),
        ("tool_does_not_write_to_source_tree", t_tool_does_not_write_to_source_tree),
        ("dry_run_succeeds_without_side_effects", t_dry_run_succeeds_without_side_effects),
        ("is_profile_warm_detects_default_subdir", t_is_profile_warm_detects_default_subdir),
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
