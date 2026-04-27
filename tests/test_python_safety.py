#!/usr/bin/env python3
"""
tests/test_python_safety.py - Verify Python version safety guards are in place

Runs 4 structural checks confirming Step 4.6's safety net is installed.
These don't actually run tracker.py with a wrong Python (would be hard to
arrange), they just verify the guard CODE is present and would fire.

  1.  tracker_bat_exists
        tracker.bat exists at project root and uses py -3.14.

  2.  tracker_py_version_check_present
        tracker.py contains a sys.version_info check that exits with a
        helpful error if Python < 3.14.

  3.  tracker_py_check_fires_before_imports
        The version check appears BEFORE any 3rd-party imports (so
        running on Python 3.12 fails fast with the helpful error,
        rather than mid-import with a cryptic ModuleNotFoundError).

  4.  readme_documents_python_setup
        README.md has a "Python Setup" section explaining the wrapper
        and the py launcher.

Exit code 0 = all 4 pass.

Run from project root:
    python tests/test_python_safety.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

TRACKER_PY  = os.path.join(_root, "tracker.py")
TRACKER_BAT = os.path.join(_root, "tracker.bat")
README_PATH = os.path.join(_root, "README.md")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_tracker_bat_exists():
    assert os.path.isfile(TRACKER_BAT), \
        "tracker.bat should exist at project root (the wrapper that uses py -3.14)"
    content = _read(TRACKER_BAT)
    assert "py -3.14" in content, \
        "tracker.bat should invoke the py launcher with -3.14"
    assert "tracker.py" in content, \
        "tracker.bat should ultimately launch tracker.py"


def t_tracker_py_version_check_present():
    src = _read(TRACKER_PY)
    assert "sys.version_info" in src, (
        "tracker.py should include a sys.version_info check to fail fast "
        "on the wrong Python"
    )
    # The check should reference 3.14 specifically
    assert re.search(r"\(\s*3\s*,\s*14\s*\)", src), (
        "tracker.py version check should target Python 3.14+"
    )
    # And should produce a helpful error mentioning the wrapper or py launcher
    assert "py -3.14" in src or "tracker.bat" in src, (
        "tracker.py version check should suggest 'py -3.14' or 'tracker.bat' "
        "in its error message so the user knows how to recover"
    )


def t_tracker_py_check_fires_before_imports():
    src = _read(TRACKER_PY)
    # Find the position of the version check and the first 3rd-party import
    # ("requests" is one of the first imports per the existing structure).
    check_pos  = src.find("sys.version_info")
    import_pos = src.find("import requests")
    assert check_pos != -1, "sys.version_info check missing entirely"
    assert import_pos != -1, "import requests missing — file structure changed?"
    assert check_pos < import_pos, (
        "Version check must appear BEFORE 'import requests' so wrong-Python "
        "fails with the helpful error instead of a cryptic ModuleNotFoundError. "
        f"check at char {check_pos}, import at char {import_pos}."
    )


def t_readme_documents_python_setup():
    src = _read(README_PATH)
    assert "Python Setup" in src or "Python setup" in src, (
        "README.md should have a 'Python Setup' section documenting the "
        "version requirement and the wrapper"
    )
    # Should reference both the wrapper and the py launcher
    assert "tracker.bat" in src, \
        "README should mention tracker.bat as the recommended launch method"
    assert "py -3.14" in src, \
        "README should mention 'py -3.14' as the explicit-Python alternative"


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(" v6.0.0 step 4.6 - Python version safety tests")
    print("=" * 70)

    tests = [
        ("tracker_bat_exists",                    t_tracker_bat_exists),
        ("tracker_py_version_check_present",      t_tracker_py_version_check_present),
        ("tracker_py_check_fires_before_imports", t_tracker_py_check_fires_before_imports),
        ("readme_documents_python_setup",         t_readme_documents_python_setup),
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
