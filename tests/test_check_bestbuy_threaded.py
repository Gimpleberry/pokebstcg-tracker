#!/usr/bin/env python3
"""
tests/test_check_bestbuy_threaded.py - Verify check_bestbuy() runs in a daemon thread

Runs 5 structural checks against tracker.py to confirm Step 4.5's migration
is in place. These are STRUCTURAL tests — they read tracker.py as text and
look for expected code patterns. They don't actually execute check_bestbuy()
because that requires Playwright + real network + Best Buy cookies.

  1.  threading_imported_in_check_bestbuy
        check_bestbuy() contains 'import threading' (scoped to the function,
        not at the top of tracker.py).

  2.  daemon_thread_pattern_present
        threading.Thread is constructed with daemon=True and a recognizable
        name for diagnostics.

  3.  result_dict_pattern_present
        The function uses a result dict to capture in_stock/price/error
        from inside the daemon thread.

  4.  persistent_session_attrs_removed
        check_bestbuy._pw and check_bestbuy._context are no longer assigned
        anywhere in tracker.py — the persistent-session optimization was
        dropped because Playwright objects aren't safe to share across
        threads.

  5.  thread_join_with_timeout
        The daemon thread is joined with a timeout, and the timeout case
        is handled (otherwise a hung Playwright session could block the
        check loop forever).

Exit code 0 = all 5 pass. Non-zero = at least one failed.

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
TRACKER_PATH = os.path.join(_root, "tracker.py")


def _read_tracker() -> str:
    with open(TRACKER_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _check_bestbuy_body() -> str:
    """Extract just the body of check_bestbuy() from tracker.py."""
    src = _read_tracker()
    # Match from `def check_bestbuy(` until the next top-level def or assignment
    m = re.search(
        r"^def check_bestbuy\(.*?(?=\n^def |\nCHECKER_MAP\s*=)",
        src,
        re.M | re.S,
    )
    if not m:
        raise RuntimeError(
            "Could not locate check_bestbuy() function in tracker.py. "
            "The function structure may have changed."
        )
    return m.group(0)


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_threading_imported_in_check_bestbuy():
    body = _check_bestbuy_body()
    assert "import threading" in body, (
        "check_bestbuy() should contain 'import threading' for the daemon "
        "thread wrapper that isolates Playwright from tracker.py's asyncio loop"
    )


def t_daemon_thread_pattern_present():
    body = _check_bestbuy_body()
    assert "threading.Thread" in body, (
        "check_bestbuy() should construct a threading.Thread for the "
        "Playwright work"
    )
    assert "daemon=True" in body, (
        "Daemon thread is required (matches amazon_monitor / costco_tracker / "
        "bestbuy_invites pattern)"
    )
    assert 'name="bestbuy_check"' in body or "name='bestbuy_check'" in body, (
        "Thread should have a recognizable name for diagnostics"
    )


def t_result_dict_pattern_present():
    body = _check_bestbuy_body()
    # Looking for a result dict that captures values across the thread boundary
    assert re.search(r'result\s*=\s*\{', body), (
        "check_bestbuy() should use a result dict to capture in_stock/price/"
        "error from inside the daemon thread"
    )
    # Specifically: expects in_stock and price keys, plus an error sentinel
    assert '"in_stock"' in body or "'in_stock'" in body, \
        "result dict should track in_stock"
    assert '"price"' in body or "'price'" in body, \
        "result dict should track price"
    assert '"error"' in body or "'error'" in body, \
        "result dict should track error (for circuit breaker handling)"


def t_persistent_session_attrs_removed():
    body = _check_bestbuy_body()
    # The persistent-session optimization is incompatible with thread isolation
    assert "check_bestbuy._pw" not in body, (
        "check_bestbuy._pw is no longer used (Playwright sessions can't safely "
        "be shared across threads). The migration is incomplete if this string "
        "is still present."
    )
    assert "check_bestbuy._context" not in body, (
        "check_bestbuy._context is no longer used (same reason as ._pw)."
    )


def t_thread_join_with_timeout():
    body = _check_bestbuy_body()
    assert re.search(r"\.join\(timeout\s*=", body), (
        "check_bestbuy() should call thread.join(timeout=...) so a hung "
        "Playwright session doesn't block the check loop forever"
    )
    assert "is_alive()" in body, (
        "check_bestbuy() should check t.is_alive() after join to detect "
        "the timeout case (and trip the circuit breaker if so)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(" v6.0.0 step 4.5 - check_bestbuy() daemon thread migration tests")
    print("=" * 70)

    tests = [
        ("threading_imported_in_check_bestbuy",  t_threading_imported_in_check_bestbuy),
        ("daemon_thread_pattern_present",        t_daemon_thread_pattern_present),
        ("result_dict_pattern_present",          t_result_dict_pattern_present),
        ("persistent_session_attrs_removed",     t_persistent_session_attrs_removed),
        ("thread_join_with_timeout",             t_thread_join_with_timeout),
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
