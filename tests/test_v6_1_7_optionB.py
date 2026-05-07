#!/usr/bin/env python3
"""
tests/test_v6_1_7_optionB.py - Verify v6.1.7 Option B (gate unroute on had_errors)

4 structural tests against tracker.py source:

  1. had_errors_initialized   - `had_errors = False` declared in
                                check_bestbuy_batch
  2. had_errors_set_on_error  - `had_errors = True` set somewhere in
                                check_bestbuy_batch
  3. unroute_gated            - `if not had_errors:` gates the
                                page.unroute("**/*") call
  4. option_a_intact          - _cleanup_cycle_count regression check
                                (Option A periodic zombie cleanup
                                must remain present)

Exit code 0 = all 4 pass.

Run from project root:
    python tests/test_v6_1_7_optionB.py
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
    """Return the source of the named top-level function, or None."""
    m = re.search(
        r"^def " + re.escape(name) + r"\(.*?(?=\n(?:def |class ))",
        src, re.DOTALL | re.MULTILINE,
    )
    return m.group(0) if m else None


def t_had_errors_initialized():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    assert "had_errors = False" in fn, (
        "v6.1.7 Option B: check_bestbuy_batch must initialize "
        "`had_errors = False` before the per-product loop"
    )


def t_had_errors_set_on_error():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    assert "had_errors = True" in fn, (
        "v6.1.7 Option B: check_bestbuy_batch must set "
        "`had_errors = True` when a per-product error is detected"
    )


def t_unroute_gated():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # Gating string must appear before the unroute call
    gate_pos = fn.find("if not had_errors:")
    unroute_pos = fn.find('page.unroute("**/*")')
    assert gate_pos != -1, (
        "v6.1.7 Option B: `if not had_errors:` gating clause missing"
    )
    assert unroute_pos != -1, (
        "v6.1.7 Option B: page.unroute call missing entirely - this is "
        "a regression of v6.0.0 step 4.8 (unroute should still run on "
        "clean cycles for noise suppression)"
    )
    assert gate_pos < unroute_pos, (
        f"v6.1.7 Option B: `if not had_errors:` must appear BEFORE "
        f"page.unroute (gate at {gate_pos}, unroute at {unroute_pos})"
    )


def t_option_a_intact():
    """Regression: Option A periodic zombie cleanup must remain present.
    Option B is intended to be additive - we should never break the
    Option A safety net even after Option B succeeds."""
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    assert "_cleanup_cycle_count" in fn, (
        "v6.1.7 Option A regression: _cleanup_cycle_count missing - "
        "the periodic zombie cleanup must remain in place as a safety "
        "net even after Option B addresses the root cause"
    )
    assert "sweep_zombies_all_profiles" in fn, (
        "v6.1.7 Option A regression: sweep_zombies_all_profiles call "
        "missing - the periodic zombie cleanup invocation must remain"
    )


def main():
    print("=" * 70)
    print(" v6.1.7 Option B - error-gated unroute tests")
    print("=" * 70)

    tests = [
        ("had_errors_initialized",  t_had_errors_initialized),
        ("had_errors_set_on_error", t_had_errors_set_on_error),
        ("unroute_gated",           t_unroute_gated),
        ("option_a_intact",         t_option_a_intact),
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
