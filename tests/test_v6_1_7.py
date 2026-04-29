#!/usr/bin/env python3
"""
tests/test_v6_1_7.py - Verify v6.1.7 Option A (periodic zombie cleanup)

7 structural tests against the actual source files:

  1.  tracker_inserts_cleanup_before_probe
        tracker.py contains the v6.1.7 cleanup block AND it appears
        BEFORE the v6.1.6 probe block in check_bestbuy_batch._run().

  2.  tracker_uses_function_attribute_pattern
        Cycle counter uses getattr(check_bestbuy_batch,
        "_cleanup_cycle_count", 0) - matches existing _circuit pattern.

  3.  tracker_gates_cleanup_on_modulo_5
        The cleanup block is conditional on n_cycle % 5 == 0.

  4.  sweep_function_defined
        tools/kill_chromium_zombies.py defines
        def sweep_zombies_all_profiles(...).

  5.  sweep_returns_dict
        Function body builds and returns a dict[key, int_killed].

  6.  sweep_emits_three_log_layers
        Function source contains all three log signature strings:
        "killed pid=", "totals:", "WARNING:".

  7.  sweep_threshold_skips_bestbuy_batch_key
        Threshold WARNING block has a `continue` for bestbuy_batch_key,
        so the known-issue zombies do not trigger spurious warnings.

Exit code 0 = all 7 pass.

Run from project root:
    python tests/test_v6_1_7.py
"""

from __future__ import annotations

import os
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

TRACKER_PY = os.path.join(_root, "tracker.py")
KILL_PY    = os.path.join(_root, "tools", "kill_chromium_zombies.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# -----------------------------------------------------------------------------
# TESTS
# -----------------------------------------------------------------------------

def t_tracker_inserts_cleanup_before_probe():
    src = _read(TRACKER_PY)
    cleanup_marker = "v6.1.7 Option A: periodic zombie cleanup"
    probe_marker   = "v6.1.6: liveness probe"
    assert cleanup_marker in src, (
        "tracker.py missing v6.1.7 cleanup block marker"
    )
    cleanup_pos = src.find(cleanup_marker)
    probe_pos   = src.find(probe_marker)
    assert probe_pos > 0, "tracker.py missing v6.1.6 probe marker"
    assert cleanup_pos < probe_pos, (
        f"v6.1.7 cleanup must appear BEFORE v6.1.6 probe "
        f"(cleanup at {cleanup_pos}, probe at {probe_pos})"
    )


def t_tracker_uses_function_attribute_pattern():
    src = _read(TRACKER_PY)
    assert 'getattr(check_bestbuy_batch, "_cleanup_cycle_count"' in src, (
        "Cycle counter must use the function-attribute pattern matching "
        "the existing _circuit pattern: "
        'getattr(check_bestbuy_batch, "_cleanup_cycle_count", 0)'
    )
    assert "check_bestbuy_batch._cleanup_cycle_count = " in src, (
        "Cycle counter must be written back as a function attribute"
    )


def t_tracker_gates_cleanup_on_modulo_5():
    src = _read(TRACKER_PY)
    assert "n_cycle % 5 == 0" in src, (
        "Cleanup must be gated on n_cycle % 5 == 0 (every 5 cycles)"
    )


def t_sweep_function_defined():
    src = _read(KILL_PY)
    assert "def sweep_zombies_all_profiles(" in src, (
        "tools/kill_chromium_zombies.py must define "
        "sweep_zombies_all_profiles(...)"
    )


def t_sweep_returns_dict():
    src = _read(KILL_PY)
    # Isolate the function body via substring slicing
    body_start = src.find("def sweep_zombies_all_profiles(")
    body_end = src.find("\ndef main():", body_start)
    assert body_start >= 0 and body_end > body_start, (
        "Could not isolate sweep_zombies_all_profiles body"
    )
    body = src[body_start:body_end]
    assert "killed_by_key = {" in body, (
        "Function body must build a killed_by_key dict"
    )
    assert "return killed_by_key" in body, (
        "Function body must return killed_by_key (a dict)"
    )


def t_sweep_emits_three_log_layers():
    src = _read(KILL_PY)
    body_start = src.find("def sweep_zombies_all_profiles(")
    body_end   = src.find("\ndef main():", body_start)
    body = src[body_start:body_end]
    # Layer 1: per-kill log
    assert "killed pid=" in body, (
        "Function must emit Layer 1 (per-kill INFO log: 'killed pid=')"
    )
    # Layer 2: sweep summary
    assert "totals:" in body, (
        "Function must emit Layer 2 (sweep summary INFO log: 'totals:')"
    )
    # Layer 3: threshold warning
    assert "WARNING:" in body, (
        "Function must emit Layer 3 (threshold WARNING)"
    )


def t_sweep_threshold_skips_bestbuy_batch_key():
    src = _read(KILL_PY)
    body_start = src.find("def sweep_zombies_all_profiles(")
    body_end   = src.find("\ndef main():", body_start)
    body = src[body_start:body_end]
    # Look for the threshold loop with a continue gating on bestbuy_batch_key
    assert "if key == bestbuy_batch_key:" in body, (
        "Threshold block must check 'if key == bestbuy_batch_key:'"
    )
    # The continue must follow the check (within ~50 chars)
    check_pos    = body.find("if key == bestbuy_batch_key:")
    continue_pos = body.find("continue", check_pos)
    assert 0 < continue_pos - check_pos < 80, (
        "After 'if key == bestbuy_batch_key:' there must be a 'continue' "
        "to skip the WARNING for the known-issue profile"
    )


# -----------------------------------------------------------------------------
# RUNNER
# -----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" v6.1.7 Option A - periodic zombie cleanup tests")
    print("=" * 70)

    tests = [
        ("tracker_inserts_cleanup_before_probe",      t_tracker_inserts_cleanup_before_probe),
        ("tracker_uses_function_attribute_pattern",   t_tracker_uses_function_attribute_pattern),
        ("tracker_gates_cleanup_on_modulo_5",         t_tracker_gates_cleanup_on_modulo_5),
        ("sweep_function_defined",                    t_sweep_function_defined),
        ("sweep_returns_dict",                        t_sweep_returns_dict),
        ("sweep_emits_three_log_layers",              t_sweep_emits_three_log_layers),
        ("sweep_threshold_skips_bestbuy_batch_key",   t_sweep_threshold_skips_bestbuy_batch_key),
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
