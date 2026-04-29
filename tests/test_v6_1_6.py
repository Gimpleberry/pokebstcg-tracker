#!/usr/bin/env python3
"""
tests/test_v6_1_6.py

STRUCTURAL tests for v6.1.6 (process-based liveness probe).

v6.1.5 used a SingletonLock filesystem check that doesn't work on
Windows (Chromium-on-Windows uses kernel mutex, not lockfile, and
even the `lockfile` file in profile dir is created once and never
updated). v6.1.6 replaces that probe with a process scan.

Verifies:
  1. tools/kill_chromium_zombies.py exposes count_processes_using_profile()
  2. tracker.py:check_bestbuy_batch._run() uses the new probe
  3. Old SingletonLock probe is removed from check_bestbuy_batch
  4. The new probe still uses the 'profile_locked_by_previous_run' marker
     (so the existing locked-skip handler in v6.1.5 still works)

Run from project root:
    python tests/test_v6_1_6.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback


_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

KILL_TOOL_PATH    = os.path.join(_root, "tools", "kill_chromium_zombies.py")
TRACKER_PY_PATH   = os.path.join(_root, "tracker.py")


def _read(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="replace")


# ----------------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------------

def t_kill_tool_exposes_count_helper():
    """tools/kill_chromium_zombies.py must expose count_processes_using_profile."""
    src = _read(KILL_TOOL_PATH)
    assert "def count_processes_using_profile(" in src, (
        "kill_chromium_zombies.py is missing count_processes_using_profile() "
        "function. v6.1.6 should have added it."
    )


def t_check_bestbuy_batch_uses_process_probe():
    """check_bestbuy_batch._run() must call count_processes_using_profile()."""
    src = _read(TRACKER_PY_PATH)

    func_match = re.search(r"def\s+check_bestbuy_batch\s*\(", src)
    assert func_match, "check_bestbuy_batch function not found"
    func_start = func_match.start()
    func_body = src[func_start:func_start + 8000]

    assert "count_processes_using_profile" in func_body, (
        "check_bestbuy_batch._run() does not call "
        "count_processes_using_profile(). v6.1.6 should have replaced "
        "the broken SingletonLock check with the process-based probe."
    )


def t_old_singletonlock_probe_removed():
    """The broken v6.1.5 SingletonLock probe must be gone from check_bestbuy_batch.

    SingletonLock can still appear in comments (referencing the old approach)
    but should not appear in any os.path.join() or active code path.
    """
    src = _read(TRACKER_PY_PATH)

    func_match = re.search(r"def\s+check_bestbuy_batch\s*\(", src)
    assert func_match, "check_bestbuy_batch function not found"
    func_start = func_match.start()
    # Look in the first ~5000 chars (where the probe was)
    func_body = src[func_start:func_start + 5000]

    # The string SingletonLock should not appear in os.path.join calls
    # within check_bestbuy_batch. Check by looking for the active pattern.
    # If it shows up only in comments, that's fine - we look for the
    # active code marker instead.
    has_active_singleton = bool(re.search(
        r'os\.path\.join\([^)]*SingletonLock',
        func_body,
    ))
    assert not has_active_singleton, (
        "check_bestbuy_batch still has active SingletonLock-based probe. "
        "v6.1.6 should have replaced it with count_processes_using_profile."
    )


def t_locked_skip_marker_preserved():
    """The 'profile_locked_by_previous_run' marker must still trigger
    the locked-skip path (no CB increment) added in v6.1.5."""
    src = _read(TRACKER_PY_PATH)

    # Marker present
    assert "profile_locked_by_previous_run" in src, (
        "profile_locked_by_previous_run marker is missing - v6.1.5's "
        "locked-skip handling depends on this string."
    )

    # Should appear at least 2 times: once in the probe (setting it),
    # once in the error handler (checking for it)
    occurrences = src.count("profile_locked_by_previous_run")
    assert occurrences >= 2, (
        f"profile_locked_by_previous_run only appears {occurrences} time(s); "
        f"expected at least 2 (set in probe, checked in error handler)"
    )


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" v6.1.6 - process-based liveness probe")
    print("=" * 70)

    tests = [
        ("kill_tool_exposes_count_helper",        t_kill_tool_exposes_count_helper),
        ("check_bestbuy_batch_uses_process_probe", t_check_bestbuy_batch_uses_process_probe),
        ("old_singletonlock_probe_removed",       t_old_singletonlock_probe_removed),
        ("locked_skip_marker_preserved",          t_locked_skip_marker_preserved),
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
