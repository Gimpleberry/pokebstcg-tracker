#!/usr/bin/env python3
"""
tests/test_v6_1_4_step2a.py

STRUCTURAL tests for v6.1.4 step 2a (auto-warm infrastructure).

Verifies:
  - tools/warm_browser_profiles.py exposes a --check-or-warm flag
  - The new mode has a silent fast-path when all profiles are already warm
  - tracker.bat invokes the warm tool BEFORE tracker.py
  - tracker.bat hard-fails (with non-zero exit) if warming fails

These are static checks - they read the source files as text. They do
NOT actually run the .bat or launch chromium.

Tests:
  1. warm_tool_has_check_or_warm_flag
  2. warm_tool_check_or_warm_uses_silent_fast_path
  3. tracker_bat_calls_warm_tool_before_tracker
  4. tracker_bat_hard_fails_on_warming_error
  5. tracker_bat_preserves_arg_passthrough

Exit code 0 = all 5 pass.

Run from project root:
    python tests/test_v6_1_4_step2a.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback


_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

WARM_TOOL_PATH = os.path.join(_root, "tools", "warm_browser_profiles.py")
TRACKER_BAT_PATH = os.path.join(_root, "tracker.bat")


def _read(path: str) -> str:
    # tracker.bat may use cp1252 or utf-8; read as bytes and decode
    # leniently so we can still run regex over it.
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------------

def t_warm_tool_has_check_or_warm_flag():
    """warm_browser_profiles.py must define a --check-or-warm flag."""
    src = _read(WARM_TOOL_PATH)
    # The argparse add_argument call should mention check-or-warm
    assert '"--check-or-warm"' in src or "'--check-or-warm'" in src, (
        "warm_browser_profiles.py is missing --check-or-warm flag definition. "
        "Step 2a should have added it via argparse.add_argument."
    )


def t_warm_tool_check_or_warm_uses_silent_fast_path():
    """When --check-or-warm sees all profiles warm, output should be minimal.

    We can't easily test runtime behavior here (would launch chromium),
    so instead we look for the architectural shape: a fast-path branch
    in main() that checks all profiles upfront before falling through
    to warm_all().
    """
    src = _read(WARM_TOOL_PATH)
    # The fast-path should reference is_profile_warm and BROWSER_PROFILES
    # in main() in conjunction with check_or_warm. Approximate check:
    # the source should contain a check that uses both symbols when
    # check_or_warm is enabled.
    assert "check_or_warm" in src, (
        "Tool source should reference check_or_warm internally"
    )
    assert "is_profile_warm" in src, (
        "Tool should use is_profile_warm() to detect already-warm profiles"
    )

    # Ensure the silent path exists - we expect a print line that
    # mentions "ready" or similar when all are warm. Loose check.
    # The exact wording is flexible, but it should be a single short line.
    has_silent_marker = (
        "all profiles ready" in src.lower()
        or "all profiles warm" in src.lower()
        or "profiles ok" in src.lower()
        or "profiles already warm" in src.lower()
    )
    assert has_silent_marker, (
        "Expected a short success line for check-or-warm fast path "
        "(e.g. 'all profiles ready'). None found - silent mode may not "
        "be wired up."
    )


def t_tracker_bat_calls_warm_tool_before_tracker():
    """tracker.bat must invoke the warm tool BEFORE invoking tracker.py.

    Order matters: if tracker.py runs first, the storm could happen
    before warming gets a chance.

    Note: tracker.bat's header comments mention "tracker.py" - we must
    match the actual invocation line (`py -3.14 ... tracker.py`), not
    string mentions in REM comments.
    """
    src = _read(TRACKER_BAT_PATH)

    # Match the actual invocation - py launcher with tracker.py as arg.
    # NOTE: tracker.py is preceded by `%~dp0` (the dirname-relative prefix)
    # which means there's NO word boundary before 'tracker' in the actual
    # text - `0tracker.py` has 0 (word char) directly adjacent to t (word
    # char). So we cannot use \btracker\.py here. Match tracker\.py
    # without leading boundary; trailing \b is fine because `.py` is
    # followed by `"` (non-word).
    invocation_re = re.compile(
        r"^[^\r\n]*\bpy\b[^\r\n]*tracker\.py\b",
        re.MULTILINE | re.IGNORECASE,
    )
    invocations = list(invocation_re.finditer(src))
    assert invocations, (
        "tracker.bat has no detectable `py ... tracker.py` invocation. "
        "Cannot verify ordering."
    )
    # Use the LAST invocation (after step 2a, the only invocation should
    # be at the bottom; multiple matches would suggest the patch went wrong)
    tracker_idx = invocations[-1].start()

    # Match the actual warm tool invocation (not comment mentions)
    warm_re = re.compile(
        r"^[^\r\n]*\bpy\b[^\r\n]*warm_browser_profiles",
        re.MULTILINE | re.IGNORECASE,
    )
    warm_match = warm_re.search(src)
    assert warm_match, (
        "tracker.bat does not invoke warm_browser_profiles. "
        "Step 2a should have added a preflight invocation."
    )
    warm_idx = warm_match.start()

    assert warm_idx < tracker_idx, (
        f"tracker.bat invokes tracker.py (line at offset {tracker_idx}) BEFORE "
        f"warm_browser_profiles (line at offset {warm_idx}). The warm "
        f"preflight must run first, or it cannot prevent the boot storm."
    )


def t_tracker_bat_hard_fails_on_warming_error():
    """If warming fails, tracker.bat must NOT proceed to tracker.py.

    Per design decision Q2: hard fail. Surface the problem visibly
    instead of silently regressing to the storm.
    """
    src = _read(TRACKER_BAT_PATH)

    # Look for an errorlevel check after the warm invocation. Common
    # patterns:
    #   if errorlevel 1 ( ... exit /b 1 ... )
    #   if %errorlevel% neq 0 ( ... )
    # Be lenient about exact form.
    has_errorlevel_check = (
        re.search(r"if\s+errorlevel\s+1", src, re.IGNORECASE) is not None
        or re.search(r"if\s+%errorlevel%\s+neq\s+0", src, re.IGNORECASE) is not None
        or re.search(r"if\s+errorlevel\s+\d+", src, re.IGNORECASE) is not None
    )
    assert has_errorlevel_check, (
        "tracker.bat must check errorlevel after invoking warm tool. "
        "Without the check, warming failures silently fall through to "
        "tracker.py and could trigger the boot storm."
    )

    # The error path should contain `exit /b` to actually leave the
    # script (not just print an error and continue).
    assert re.search(r"exit\s+/b\s+\d+", src, re.IGNORECASE), (
        "tracker.bat error path should call `exit /b <code>` to actually "
        "stop the script. Without it, control falls through to tracker.py."
    )


def t_tracker_bat_preserves_arg_passthrough():
    """tracker.bat must still pass user args through to tracker.py.

    The original tracker.bat ended with `py -3.14 ... tracker.py %*`
    so users can do `tracker.bat debug` and have 'debug' reach tracker.py.
    Step 2a's patch must not break that.
    """
    src = _read(TRACKER_BAT_PATH)
    # Look for tracker.py invocation followed by %* (or with args between)
    # Loose check: tracker.py and %* on the same line, %* AFTER tracker.py
    tracker_line_match = re.search(
        r"tracker\.py.*%\*", src
    )
    assert tracker_line_match, (
        "tracker.bat tracker.py invocation no longer passes %* (user args). "
        "Step 2a's patch removed the arg passthrough - users will lose "
        "the ability to do `tracker.bat debug` etc."
    )


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" v6.1.4 step 2a - auto-warm infrastructure")
    print("=" * 70)

    tests = [
        ("warm_tool_has_check_or_warm_flag", t_warm_tool_has_check_or_warm_flag),
        ("warm_tool_check_or_warm_uses_silent_fast_path", t_warm_tool_check_or_warm_uses_silent_fast_path),
        ("tracker_bat_calls_warm_tool_before_tracker", t_tracker_bat_calls_warm_tool_before_tracker),
        ("tracker_bat_hard_fails_on_warming_error", t_tracker_bat_hard_fails_on_warming_error),
        ("tracker_bat_preserves_arg_passthrough", t_tracker_bat_preserves_arg_passthrough),
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
