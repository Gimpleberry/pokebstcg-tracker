#!/usr/bin/env python3
"""
tests/test_v6_1_1_step3.py - Verify v6.1.1 step 3 (Walmart cutover)

3 structural tests against tracker.py source.

Run from project root:
    python tests/test_v6_1_1_step3.py
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


def t_walmart_not_in_checker_map():
    src = _read(TRACKER_PY)
    m = re.search(r"CHECKER_MAP\s*=\s*\{([^}]*)\}", src, re.DOTALL)
    assert m, "CHECKER_MAP definition missing from tracker.py"
    body = m.group(1)
    assert '"walmart"' not in body, (
        "v6.1.1 step 3 cutover incomplete: \"walmart\" still in CHECKER_MAP"
    )
    assert "check_walmart" not in body, (
        "v6.1.1 step 3 cutover incomplete: check_walmart still referenced "
        "in CHECKER_MAP"
    )


def t_check_walmart_function_removed():
    src = _read(TRACKER_PY)
    assert "def check_walmart(" not in src, (
        "v6.1.1 step 3 cutover incomplete: def check_walmart() still present"
    )


def t_scrape_fallback_function_removed():
    src = _read(TRACKER_PY)
    assert "def _scrape_walmart_fallback(" not in src, (
        "v6.1.1 step 3 cutover incomplete: def _scrape_walmart_fallback() "
        "still present"
    )


def main():
    print("=" * 70)
    print(" v6.1.1 step 3 - Walmart cutover tests")
    print("=" * 70)

    tests = [
        ("walmart_not_in_checker_map",       t_walmart_not_in_checker_map),
        ("check_walmart_function_removed",   t_check_walmart_function_removed),
        ("scrape_fallback_function_removed", t_scrape_fallback_function_removed),
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
