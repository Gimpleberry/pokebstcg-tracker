#!/usr/bin/env python3
"""
tests/test_v6_1_1_step4.py - Verify v6.1.1 step 4 (walmart warning silencing)

2 structural tests against tracker.py source:

  1. walmart_skip_present   - the new walmart skip line exists
  2. warnings_path_intact   - "No checker for retailer:" warning path
                              still intact for unrelated misconfigurations

Run from project root:
    python tests/test_v6_1_1_step4.py
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


def _extract_run_checks_body(src):
    """Return the body of run_checks() as a string, or None if not found."""
    m = re.search(r"^def run_checks\(\):.*?(?=\n(?:def |class ))", src, re.DOTALL | re.MULTILINE)
    return m.group(0) if m else None


def t_walmart_skip_present():
    src = _read(TRACKER_PY)
    body = _extract_run_checks_body(src)
    assert body is not None, "Could not locate run_checks() function in tracker.py"
    # The walmart skip should be in the run_checks body
    assert 'if retailer == "walmart":' in body, (
        "v6.1.1 step 4: run_checks() must skip walmart retailer "
        "(walmart_playwright plugin handles those products)"
    )
    # And it should reference walmart_playwright in a comment
    assert "walmart_playwright" in body, (
        "v6.1.1 step 4: comment explaining the skip should reference "
        "walmart_playwright plugin"
    )


def t_warnings_path_intact():
    src = _read(TRACKER_PY)
    body = _extract_run_checks_body(src)
    assert body is not None, "Could not locate run_checks() function in tracker.py"
    # The safety-net "No checker for retailer:" warning should still exist
    # for genuinely-missing retailers. We did not remove the safety net.
    assert 'log.warning(f"No checker for retailer:' in body, (
        "v6.1.1 step 4: the 'No checker for retailer:' warning path "
        "must be preserved as a safety net for misconfigured retailers"
    )


def main():
    print("=" * 70)
    print(" v6.1.1 step 4 - silence walmart warnings tests")
    print("=" * 70)

    tests = [
        ("walmart_skip_present",   t_walmart_skip_present),
        ("warnings_path_intact",   t_warnings_path_intact),
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
