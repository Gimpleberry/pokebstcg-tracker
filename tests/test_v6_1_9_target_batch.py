#!/usr/bin/env python3
"""
tests/test_v6_1_9_target_batch.py - Verify v6.1.9 target_batch architecture.

8 structural tests against tracker.py source (mirror test_check_bestbuy_batch.py
which passed for the equivalent BB architecture):

  1. helper_function_exists           - _check_target_one defined
  2. batch_function_exists            - check_target_batch defined
  3. batch_uses_daemon_thread         - threading.Thread daemon=True
  4. batch_launches_one_playwright    - exactly one sync_playwright()
  5. run_checks_routes_target         - run_checks collects target_products
  6. batch_includes_prewarm           - target.com homepage prewarm present
  7. batch_includes_per_product_retry - retry logic + 2x _check_target_one calls
  8. batch_unroutes_gated_on_errors   - Option B had_errors gating present

Run from project root:
    python tests/test_v6_1_9_target_batch.py
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
    """Return source of named top-level function, or None."""
    m = re.search(
        r"^def " + re.escape(name) + r"\(.*?(?=\n(?:def |class ))",
        src, re.DOTALL | re.MULTILINE,
    )
    return m.group(0) if m else None


def t_helper_function_exists():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "_check_target_one")
    assert fn is not None, (
        "v6.1.9: _check_target_one helper missing - this is the per-product "
        "function that operates on an already-open warm page"
    )
    assert "page.goto" in fn, "_check_target_one must call page.goto"
    assert "in_stock" in fn, "_check_target_one must determine in_stock"


def t_batch_function_exists():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_target_batch")
    assert fn is not None, "check_target_batch function missing"
    assert "products: list" in fn, "must accept products list arg"
    assert "ProductStatus" in fn, "must return ProductStatus list"


def t_batch_uses_daemon_thread():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_target_batch")
    assert fn is not None, "check_target_batch missing"
    assert "threading.Thread" in fn, (
        "check_target_batch must wrap _run in a daemon thread (mirrors "
        "v6.0.0 step 4.7 pattern)"
    )
    assert "daemon=True" in fn, "thread must be daemon=True"
    assert re.search(r"\.join\s*\(\s*timeout\s*=", fn), (
        "must call thread.join(timeout=...) so a hung Playwright doesn't "
        "block the check loop"
    )


def t_batch_launches_one_playwright():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_target_batch")
    assert fn is not None, "check_target_batch missing"
    pw_starts = fn.count("sync_playwright()")
    assert pw_starts == 1, (
        f"check_target_batch should open EXACTLY ONE sync_playwright() "
        f"context for the entire batch (warm session reuse). Found {pw_starts}."
    )
    assert "launch_chromium_with_fallback" in fn, (
        "check_target_batch must use launch_chromium_with_fallback for "
        "consistent profile handling"
    )
    assert 'BROWSER_PROFILES["target"]' in fn, (
        "check_target_batch must use the isolated target profile"
    )


def t_run_checks_routes_target():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "run_checks")
    assert fn is not None, "run_checks function missing"
    assert "target_products" in fn, (
        "run_checks must collect target_products for batch processing"
    )
    assert "check_target_batch" in fn, (
        "run_checks must call check_target_batch after the main loop"
    )
    # The collector pattern should mirror the bestbuy one
    assert 'if retailer == "target":' in fn, (
        "run_checks must check retailer == target to route to batch"
    )
    assert "target_products.append(product)" in fn, (
        "run_checks must append target products to target_products list"
    )


def t_batch_includes_prewarm():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_target_batch")
    assert fn is not None, "check_target_batch missing"
    # Cold-start prewarm should navigate to target.com homepage before products
    assert "target.com" in fn, (
        "check_target_batch must navigate to target.com homepage to prewarm "
        "the session (mirrors v6.0.0 step 4.8 cold-start fix)"
    )
    # And the prewarm should happen BEFORE the per-product loop
    homepage_pos = fn.find("target.com")
    loop_pos = fn.find("for i, product in enumerate(products)")
    assert homepage_pos != -1 and loop_pos != -1 and homepage_pos < loop_pos, (
        f"prewarm navigation must happen BEFORE the product loop "
        f"(homepage at char {homepage_pos}, loop at char {loop_pos})"
    )


def t_batch_includes_per_product_retry():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_target_batch")
    assert fn is not None, "check_target_batch missing"
    assert "retry" in fn.lower(), (
        "check_target_batch must include retry logic (transient error fix)"
    )
    one_calls = fn.count("_check_target_one(")
    assert one_calls >= 2, (
        f"check_target_batch should call _check_target_one at least 2 times "
        f"(initial + retry path). Found {one_calls}."
    )


def t_batch_unroutes_gated_on_errors():
    """v6.1.7 Option B pattern: error-gated unroute prevents zombie leak."""
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_target_batch")
    assert fn is not None, "check_target_batch missing"
    assert "had_errors = False" in fn, (
        "v6.1.7 Option B: check_target_batch must initialize had_errors = False"
    )
    assert "had_errors = True" in fn, (
        "v6.1.7 Option B: check_target_batch must set had_errors = True on error"
    )
    gate_pos = fn.find("if not had_errors:")
    unroute_pos = fn.find('page.unroute("**/*")')
    assert gate_pos != -1, (
        "v6.1.7 Option B: 'if not had_errors:' gating clause missing"
    )
    assert unroute_pos != -1, (
        "v6.1.7 Option B: page.unroute call missing entirely"
    )
    assert gate_pos < unroute_pos, (
        f"v6.1.7 Option B: gating must precede unroute call "
        f"(gate at {gate_pos}, unroute at {unroute_pos})"
    )


def main():
    print("=" * 70)
    print(" v6.1.9 target_batch architecture tests")
    print("=" * 70)

    tests = [
        ("helper_function_exists",            t_helper_function_exists),
        ("batch_function_exists",             t_batch_function_exists),
        ("batch_uses_daemon_thread",          t_batch_uses_daemon_thread),
        ("batch_launches_one_playwright",     t_batch_launches_one_playwright),
        ("run_checks_routes_target",          t_run_checks_routes_target),
        ("batch_includes_prewarm",            t_batch_includes_prewarm),
        ("batch_includes_per_product_retry",  t_batch_includes_per_product_retry),
        ("batch_unroutes_gated_on_errors",    t_batch_unroutes_gated_on_errors),
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
