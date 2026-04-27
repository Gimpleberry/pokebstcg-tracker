#!/usr/bin/env python3
"""
tests/test_check_bestbuy_threaded.py - Verify daemon-thread Playwright pattern

UPDATED v6.0.0 step 4.7: the threading moved from check_bestbuy() to
check_bestbuy_batch() when we batched all BB products through one session.
This test now verifies the threading pattern lives in check_bestbuy_batch().

Both functions are still expected to be present:
- check_bestbuy_batch(products): the actual threaded path
- check_bestbuy(product):         back-compat shim that routes through batch

Five tests:
  1. threading_imported_in_batch
       check_bestbuy_batch() imports/uses threading.

  2. daemon_thread_pattern_present
       check_bestbuy_batch() constructs a threading.Thread.

  3. result_capture_pattern_present
       check_bestbuy_batch() uses some mutable holder (list/dict) to
       capture results from inside the daemon thread.

  4. persistent_session_in_batch
       check_bestbuy_batch() opens ONE Playwright session for the whole
       batch (not one per product).

  5. thread_join_with_timeout
       check_bestbuy_batch() calls .join(timeout=...) so a hung session
       doesn't block forever.

Run from project root:
    python tests/test_check_bestbuy_threaded.py
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
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_threading_imported_in_batch():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing - Step 4.7 not applied"
    assert "threading" in fn, (
        "check_bestbuy_batch() should reference threading for the daemon "
        "thread that isolates Playwright from asyncio loop"
    )


def t_daemon_thread_pattern_present():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    assert "threading.Thread" in fn, (
        "check_bestbuy_batch() should construct a threading.Thread for the "
        "Playwright work (asyncio fix from Step 4.5, preserved in batch form)"
    )
    assert "daemon=True" in fn, (
        "check_bestbuy_batch() must set daemon=True so thread doesn't block shutdown"
    )


def t_result_capture_pattern_present():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # The batch should use a mutable holder (list or dict) to capture results
    # from inside the daemon thread, since thread targets can't return values.
    has_results_list = "results" in fn and (
        "results: list" in fn or "results = [" in fn or "results[" in fn
    )
    has_error_holder = "error" in fn.lower()
    assert has_results_list, (
        "check_bestbuy_batch() should use a results list to capture per-product "
        "ProductStatus from inside the daemon thread"
    )
    assert has_error_holder, (
        "check_bestbuy_batch() should track batch-level errors for circuit-breaker logic"
    )


def t_persistent_session_in_batch():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    pw_starts = fn.count("sync_playwright()")
    assert pw_starts == 1, (
        f"check_bestbuy_batch() should open EXACTLY ONE sync_playwright() context "
        f"for the entire batch (this is the whole point — warm session reuse). "
        f"Found {pw_starts}."
    )
    # Should also use launch_persistent_context for Akamai cookie reuse
    assert "launch_persistent_context" in fn, (
        "check_bestbuy_batch() should use launch_persistent_context for "
        "Akamai cookie persistence across products"
    )


def t_thread_join_with_timeout():
    src = _read(TRACKER_PY)
    fn = _extract_function(src, "check_bestbuy_batch")
    assert fn is not None, "check_bestbuy_batch missing"
    # Must call .join with a timeout
    assert re.search(r"\.join\s*\(\s*timeout\s*=", fn), (
        "check_bestbuy_batch() should call thread.join(timeout=...) so a hung "
        "Playwright session doesn't block the check loop indefinitely"
    )


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(" v6.0.0 step 4.7 - daemon-thread Playwright pattern (in batch fn)")
    print("=" * 70)

    tests = [
        ("threading_imported_in_batch",     t_threading_imported_in_batch),
        ("daemon_thread_pattern_present",   t_daemon_thread_pattern_present),
        ("result_capture_pattern_present",  t_result_capture_pattern_present),
        ("persistent_session_in_batch",     t_persistent_session_in_batch),
        ("thread_join_with_timeout",        t_thread_join_with_timeout),
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
