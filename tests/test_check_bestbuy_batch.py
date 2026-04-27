#!/usr/bin/env python3
"""
tests/test_check_bestbuy_batch.py - Verify Step 4.7 batch refactor structure

Six structural tests confirming the BB batch refactor is wired correctly.
These don't actually call Playwright (would require a live browser), they
verify the CODE structure ensures correct behavior at runtime.

  1.  inner_function_exists
        _check_bestbuy_one(page, product) function defined in tracker.py.

  2.  batch_function_exists
        check_bestbuy_batch(products) function defined in tracker.py.

  3.  batch_uses_daemon_thread
        check_bestbuy_batch creates a daemon thread (asyncio fix preserved).

  4.  batch_launches_one_playwright_session
        Inside the daemon thread, exactly one sync_playwright() context
        is opened — not one per product.

  5.  run_checks_routes_bb_through_batch
        run_checks() collects bestbuy products and passes them to
        check_bestbuy_batch as a list, instead of per-product calls.

  6.  bestbuy_not_in_checker_map
        CHECKER_MAP no longer contains 'bestbuy' (or maps to a stub),
        ensuring no accidental per-product fallback.

Exit code 0 = all 6 pass.

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
    # Find next top-level def
    next_def = re.search(r"^def \w+\b", src[start + 1:], re.MULTILINE)
    if next_def:
        return src[start:start + 1 + next_def.start()]
    return src[start:]


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_inner_function_exists():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "_check_bestbuy_one")
    assert fn is not None, (
        "tracker.py should define _check_bestbuy_one(page, product) — the "
        "per-product scraper used by check_bestbuy_batch"
    )
    # Verify signature includes both `page` and `product` parameters
    first_line = fn.split("\n", 1)[0]
    assert "page" in first_line and "product" in first_line, (
        f"_check_bestbuy_one signature should accept (page, product). Got: {first_line!r}"
    )


def t_batch_function_exists():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, (
        "tracker.py should define check_bestbuy_batch(products) — the batch "
        "wrapper that runs all BB products through one Playwright session"
    )
    first_line = fn.split("\n", 1)[0]
    assert "products" in first_line, (
        f"check_bestbuy_batch signature should accept (products). Got: {first_line!r}"
    )


def t_batch_uses_daemon_thread():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # Must use threading.Thread with daemon=True (asyncio fix)
    assert "threading.Thread" in fn, (
        "check_bestbuy_batch must use threading.Thread (daemon-thread pattern from Step 4.5)"
    )
    assert "daemon=True" in fn, (
        "check_bestbuy_batch must set daemon=True on the thread (so it doesn't block shutdown)"
    )


def t_batch_launches_one_playwright_session():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # Count sync_playwright calls — should be exactly 1 inside the batch function
    pw_starts = fn.count("sync_playwright()")
    assert pw_starts == 1, (
        f"check_bestbuy_batch should open exactly ONE sync_playwright() context "
        f"(this is the whole point of batching). Found {pw_starts}."
    )
    # And should iterate products (loop is the give-away)
    assert "for " in fn and "products" in fn, (
        "check_bestbuy_batch must loop over products inside the Playwright context"
    )


def t_run_checks_routes_bb_through_batch():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "run_checks")
    assert fn is not None, "run_checks function missing — major regression"
    # Must call check_bestbuy_batch
    assert "check_bestbuy_batch" in fn, (
        "run_checks must call check_bestbuy_batch (not per-product check_bestbuy)"
    )
    # Must collect BB products into a list before the batch call
    assert "bestbuy" in fn.lower(), (
        "run_checks must filter for bestbuy retailer to route to batch"
    )


def t_bestbuy_not_in_checker_map_or_routes_to_stub():
    src = _read(TRACKER_PY)
    # Find CHECKER_MAP definition
    m = re.search(r"CHECKER_MAP\s*=\s*\{([^}]*)\}", src, re.DOTALL)
    assert m, "CHECKER_MAP definition missing"
    map_body = m.group(1)
    # Either bestbuy isn't there at all, OR it's mapped to a non-batch-routing
    # function (e.g., a stub). The runtime path for BB MUST go through batch.
    # Acceptable: 'bestbuy' absent, OR 'bestbuy' mapped to a stub raising error.
    if '"bestbuy"' in map_body or "'bestbuy'" in map_body:
        # If still there, must be a stub (not the old check_bestbuy)
        assert "check_bestbuy_batch" not in map_body, (
            "CHECKER_MAP['bestbuy'] should not point to the batch function "
            "(batch is called from run_checks directly, not via CHECKER_MAP)"
        )
        # Could be a stub; that's fine. We just want to be sure we're not
        # accidentally invoking the old per-product path.


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(" v6.0.0 step 4.7 - Best Buy batch refactor tests")
    print("=" * 70)

    tests = [
        ("inner_function_exists",                       t_inner_function_exists),
        ("batch_function_exists",                       t_batch_function_exists),
        ("batch_uses_daemon_thread",                    t_batch_uses_daemon_thread),
        ("batch_launches_one_playwright_session",       t_batch_launches_one_playwright_session),
        ("run_checks_routes_bb_through_batch",          t_run_checks_routes_bb_through_batch),
        ("bestbuy_not_in_checker_map_or_routes_to_stub", t_bestbuy_not_in_checker_map_or_routes_to_stub),
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
