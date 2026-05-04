#!/usr/bin/env python3
"""
tests/test_v6_1_12.py - Verify v6.1.12 (PC concurrent batch)

4 structural tests against tracker.py source:

  1. one_helper_present     - _check_pokemoncenter_one extracted
  2. batch_function_present - check_pokemoncenter_batch defined
  3. checker_map_clean      - "pokemoncenter" removed from CHECKER_MAP
  4. run_checks_wired       - pokemoncenter_products collector + skip + dispatch

Run from project root:
    python tests/test_v6_1_12.py
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


def t_one_helper_present():
    src = _read(TRACKER_PY)
    assert "def _check_pokemoncenter_one(" in src, (
        "v6.1.12: _check_pokemoncenter_one helper must be defined "
        "(extracted from legacy check_pokemoncenter)"
    )


def t_batch_function_present():
    src = _read(TRACKER_PY)
    assert "def check_pokemoncenter_batch(" in src, (
        "v6.1.12: check_pokemoncenter_batch dispatcher must be defined"
    )
    # Verify it uses ThreadPoolExecutor
    assert "ThreadPoolExecutor" in src, (
        "v6.1.12: check_pokemoncenter_batch must use ThreadPoolExecutor "
        "for concurrent dispatch"
    )


def t_checker_map_clean():
    src = _read(TRACKER_PY)
    m = re.search(r"CHECKER_MAP\s*=\s*\{([^}]*)\}", src, re.DOTALL)
    assert m, "CHECKER_MAP definition missing from tracker.py"
    body = m.group(1)
    # Quoted "pokemoncenter" should not be present as a key
    assert '"pokemoncenter"' not in body, (
        "v6.1.12: \"pokemoncenter\" still in CHECKER_MAP - cutover incomplete"
    )


def t_run_checks_wired():
    src = _read(TRACKER_PY)
    # Find run_checks body - until next top-level def
    m = re.search(r"^def run_checks\(\):.*?(?=\n(?:def |class ))", src, re.DOTALL | re.MULTILINE)
    assert m, "Could not locate run_checks() in tracker.py"
    body = m.group(0)

    # Collector exists
    assert "pokemoncenter_products = []" in body, (
        "v6.1.12: pokemoncenter_products collector must be in run_checks"
    )
    # Skip block exists
    assert 'if retailer == "pokemoncenter":' in body, (
        "v6.1.12: pokemoncenter skip block must be in run_checks"
    )
    # Dispatch call exists
    assert "check_pokemoncenter_batch(" in body, (
        "v6.1.12: check_pokemoncenter_batch dispatch must be called in run_checks"
    )


def main():
    print("=" * 70)
    print(" v6.1.12 - Pokemon Center concurrent batch tests")
    print("=" * 70)

    tests = [
        ("one_helper_present",     t_one_helper_present),
        ("batch_function_present", t_batch_function_present),
        ("checker_map_clean",      t_checker_map_clean),
        ("run_checks_wired",       t_run_checks_wired),
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
