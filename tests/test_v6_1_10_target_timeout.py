#!/usr/bin/env python3
"""
tests/test_v6_1_10_target_timeout.py - Verify v6.1.10 timeout reduction.

3 structural tests against tracker.py source:

  1. helper_uses_4000ms_timeout    - _check_target_one has timeout=4000
  2. legacy_check_target_unchanged - legacy check_target still has 8000ms
                                     (preserves back-compat behavior)
  3. timeout_reduction_documented  - inline comment references v6.1.10

Run from project root:
    python tests/test_v6_1_10_target_timeout.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

TRACKER_PY = os.path.join(_root, "tracker.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(src, name):
    m = re.search(
        r"^def " + re.escape(name) + r"\(.*?(?=\n(?:def |class ))",
        src, re.DOTALL | re.MULTILINE,
    )
    return m.group(0) if m else None


def t_helper_uses_4000ms_timeout():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "_check_target_one")
    assert fn is not None, "v6.1.9 prerequisite missing - _check_target_one not found"
    assert "timeout=4000" in fn, (
        "v6.1.10: _check_target_one must use timeout=4000 for wait_for_selector "
        "(was 8000 in v6.1.9 - reduced to cut OOS detection lag)"
    )
    # Verify it's specifically on the wait_for_selector call, not page.goto
    # page.goto should still be timeout=20000
    assert "timeout=20000" in fn, (
        "page.goto timeout=20000 must be preserved"
    )


def t_legacy_check_target_unchanged():
    """Legacy check_target() is back-compat only; preserve 8000ms timeout."""
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_target")
    assert fn is not None, "check_target function missing"
    # Legacy check_target should still have wait_for_selector with 8000ms.
    # We deliberately did NOT touch this function - it's a safety net.
    assert "timeout=8000" in fn, (
        "Legacy check_target must keep timeout=8000 (back-compat safety net). "
        "v6.1.10 should only modify _check_target_one, not the legacy function."
    )


def t_timeout_reduction_documented():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "_check_target_one")
    assert fn is not None, "_check_target_one missing"
    assert "v6.1.10" in fn, (
        "v6.1.10: timeout reduction must include inline comment referencing "
        "the version for traceability"
    )


def main():
    print("=" * 70)
    print(" v6.1.10 target_batch wait_for_selector timeout tests")
    print("=" * 70)

    tests = [
        ("helper_uses_4000ms_timeout",     t_helper_uses_4000ms_timeout),
        ("legacy_check_target_unchanged",  t_legacy_check_target_unchanged),
        ("timeout_reduction_documented",   t_timeout_reduction_documented),
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
