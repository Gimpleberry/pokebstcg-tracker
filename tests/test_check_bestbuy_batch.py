#!/usr/bin/env python3
"""
tests/test_check_bestbuy_batch.py - Verify Step 4.7+4.8 BB batch architecture

NINE structural tests confirming the BB batch refactor + reliability
enhancements are wired correctly. These don't actually call Playwright
(would require a live browser); they verify the CODE structure ensures
correct behavior at runtime.

Step 4.7 tests:
  1.  inner_function_exists                    — _check_bestbuy_one(page, product)
  2.  batch_function_exists                    — check_bestbuy_batch(products)
  3.  batch_uses_daemon_thread                 — daemon=True threading
  4.  batch_launches_one_playwright_session    — exactly one sync_playwright()
  5.  run_checks_routes_bb_through_batch       — run_checks collects BB → batch
  6.  bestbuy_not_in_checker_map_or_routes_to_stub — CHECKER_MAP correctness

Step 4.8 tests (NEW):
  7.  batch_includes_prewarm_navigation        — homepage hit before product 1
  8.  batch_includes_per_product_retry         — retry-once on transient errors
  9.  batch_unroutes_before_close              — page.unroute called before close

Exit code 0 = all 9 pass.

Run from project root:
    python tests/test_check_bestbuy_batch.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

TRACKER_PY = os.path.join(_root, "tracker.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(src: str, name: str) -> str | None:
    """Extract a top-level function body (def NAME ... up to next top-level def or EOF)."""
    pattern = rf"^def {re.escape(name)}\b"
    m = re.search(pattern, src, re.MULTILINE)
    if not m:
        return None
    start = m.start()
    next_def = re.search(r"^def \w+\b", src[start + 1:], re.MULTILINE)
    if next_def:
        return src[start:start + 1 + next_def.start()]
    return src[start:]


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.7 TESTS (preserved from original)
# ─────────────────────────────────────────────────────────────────────────────

def t_inner_function_exists():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "_check_bestbuy_one")
    assert fn is not None, "tracker.py should define _check_bestbuy_one(page, product)"
    first_line = fn.split("\n", 1)[0]
    assert "page" in first_line and "product" in first_line, (
        f"_check_bestbuy_one signature should accept (page, product). Got: {first_line!r}"
    )


def t_batch_function_exists():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "tracker.py should define check_bestbuy_batch(products)"
    first_line = fn.split("\n", 1)[0]
    assert "products" in first_line, (
        f"check_bestbuy_batch signature should accept (products). Got: {first_line!r}"
    )


def t_batch_uses_daemon_thread():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    assert "threading.Thread" in fn, "check_bestbuy_batch must use threading.Thread"
    assert "daemon=True" in fn, "check_bestbuy_batch must set daemon=True"


def t_batch_launches_one_playwright_session():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    pw_starts = fn.count("sync_playwright()")
    assert pw_starts == 1, (
        f"check_bestbuy_batch should open exactly ONE sync_playwright() context. "
        f"Found {pw_starts}."
    )
    assert "for " in fn and "products" in fn, (
        "check_bestbuy_batch must loop over products inside the Playwright context"
    )


def t_run_checks_routes_bb_through_batch():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "run_checks")
    assert fn is not None, "run_checks function missing"
    assert "check_bestbuy_batch" in fn, (
        "run_checks must call check_bestbuy_batch (not per-product check_bestbuy)"
    )
    assert "bestbuy" in fn.lower(), (
        "run_checks must filter for bestbuy retailer to route to batch"
    )


def t_bestbuy_not_in_checker_map_or_routes_to_stub():
    src = _read(TRACKER_PY)
    m = re.search(r"CHECKER_MAP\s*=\s*\{([^}]*)\}", src, re.DOTALL)
    assert m, "CHECKER_MAP definition missing"
    map_body = m.group(1)
    if '"bestbuy"' in map_body or "'bestbuy'" in map_body:
        assert "check_bestbuy_batch" not in map_body, (
            "CHECKER_MAP['bestbuy'] should not point to the batch function"
        )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4.8 TESTS (new — reliability enhancements)
# ─────────────────────────────────────────────────────────────────────────────

def t_batch_includes_prewarm_navigation():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # Cold-start prewarm should navigate to BB homepage before products
    assert "bestbuy.com/" in fn or "bestbuy.com\"" in fn, (
        "check_bestbuy_batch must navigate to bestbuy.com homepage to prewarm "
        "the session (Step 4.8 cold-start fix)"
    )
    # And the prewarm should happen BEFORE the per-product loop
    homepage_pos = fn.find("bestbuy.com")
    loop_pos = fn.find("for i, product in enumerate(products)")
    if loop_pos == -1:
        loop_pos = fn.find("for product in products")
    assert homepage_pos != -1 and loop_pos != -1 and homepage_pos < loop_pos, (
        f"prewarm navigation must happen BEFORE the product loop "
        f"(homepage at char {homepage_pos}, loop at char {loop_pos})"
    )


def t_batch_includes_per_product_retry():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # Retry pattern: should call _check_bestbuy_one twice in proximity
    # (once initially, once on retry). Look for retry-related logic.
    assert "retry" in fn.lower(), (
        "check_bestbuy_batch must include retry logic (Step 4.8 transient error fix)"
    )
    # Should call _check_bestbuy_one at least twice (initial + retry path)
    one_calls = fn.count("_check_bestbuy_one(")
    assert one_calls >= 2, (
        f"check_bestbuy_batch should call _check_bestbuy_one at least 2 times "
        f"(initial + retry). Found {one_calls}."
    )


def t_batch_unroutes_before_close():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # Must call page.unroute before page.close to prevent asyncio cancellation noise
    assert "page.unroute" in fn, (
        "check_bestbuy_batch must call page.unroute before page.close to prevent "
        "asyncio CancelledError noise from route handlers (Step 4.8 cleanup fix)"
    )
    # Verify ordering: unroute appears before close in the function body
    unroute_pos = fn.find("page.unroute")
    close_pos = fn.find("page.close()")
    assert unroute_pos < close_pos, (
        f"page.unroute must be called BEFORE page.close. "
        f"unroute at char {unroute_pos}, close at char {close_pos}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(" v6.0.0 step 4.7+4.8 - Best Buy batch architecture tests")
    print("=" * 70)

    tests = [
        # Step 4.7
        ("inner_function_exists",                       t_inner_function_exists),
        ("batch_function_exists",                       t_batch_function_exists),
        ("batch_uses_daemon_thread",                    t_batch_uses_daemon_thread),
        ("batch_launches_one_playwright_session",       t_batch_launches_one_playwright_session),
        ("run_checks_routes_bb_through_batch",          t_run_checks_routes_bb_through_batch),
        ("bestbuy_not_in_checker_map_or_routes_to_stub", t_bestbuy_not_in_checker_map_or_routes_to_stub),
        # Step 4.8 (new)
        ("batch_includes_prewarm_navigation",           t_batch_includes_prewarm_navigation),
        ("batch_includes_per_product_retry",            t_batch_includes_per_product_retry),
        ("batch_unroutes_before_close",                 t_batch_unroutes_before_close),
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
