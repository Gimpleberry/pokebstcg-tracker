#!/usr/bin/env python3
"""
tests/test_shared_helpers_v6_1_2.py

Verifies the shared.py launch_chromium_with_fallback helper added in
v6.1.2 step 1. STRUCTURAL tests - they read shared.py as text and look
for expected code patterns. They DO NOT actually launch a browser
(integration testing is the diagnostic's job, see tools/diag_icu_bug.py).

Why a helper exists at all
--------------------------
Before v6.1.2, six runtime files called launch_persistent_context with
no channel= override - which routes to chrome-headless-shell, which is
broken (ICU data error) on Keith's machine. All six plugins were silently
failing. The helper centralizes the "try chrome -> msedge -> chromium"
fallback chain that walmart_playwright pioneered in v6.1.1, so step 2
can refactor each callsite to a one-line change.

The 7 tests
-----------
  1. helper_function_exists
       shared.py defines launch_chromium_with_fallback().

  2. channel_chain_constant_defined
       CHANNEL_CHAIN = ("chrome", "msedge", "chromium") at module level.
       Chrome-headless-shell is NEVER attempted - that's the whole point.

  3. helper_iterates_channel_chain
       Function body has 'for channel in ...' loop over the chain.

  4. helper_always_sets_channel_kwarg
       Inside the loop, kwargs['channel'] is set before each launch attempt.
       Skipping this would re-introduce the bug we're fixing.

  5. helper_raises_on_all_channels_fail
       Helper raises RuntimeError when no channel succeeds, rather than
       silently returning None.

  6. helper_creates_user_data_dir
       Calls os.makedirs(user_data_dir, exist_ok=True) so callers don't
       have to. Mirrors the pattern in shared.py:open_browser().

  7. helper_is_importable
       Smoke test - shared.launch_chromium_with_fallback can be imported
       and is callable. Catches syntax errors / typos that text matching
       would miss.

Exit code 0 = all 7 pass.

Run from project root:
    python tests/test_shared_helpers_v6_1_2.py
"""

from __future__ import annotations

import os
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

SHARED_PATH = os.path.join(_root, "shared.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(src: str, fn_name: str) -> str | None:
    """Return the body of a top-level function as a string, or None."""
    start = src.find(f"def {fn_name}(")
    if start < 0:
        return None
    # Find next top-level def OR class to bound the function
    next_def = src.find("\ndef ", start + 1)
    next_cls = src.find("\nclass ", start + 1)
    candidates = [c for c in (next_def, next_cls) if c > 0]
    end = min(candidates) if candidates else len(src)
    return src[start:end]


# ----------------------------------------------------------------------------
# TESTS
# ----------------------------------------------------------------------------

def t_helper_function_exists():
    src = _read(SHARED_PATH)
    assert "def launch_chromium_with_fallback(" in src, (
        "shared.py should define launch_chromium_with_fallback() - the "
        "centralized helper that adds channel fallback to dodge the "
        "chrome-headless-shell ICU bug"
    )


def t_channel_chain_constant_defined():
    src = _read(SHARED_PATH)
    assert "CHANNEL_CHAIN" in src, (
        "shared.py should define CHANNEL_CHAIN module-level constant"
    )
    # All three required channels must appear
    assert '"chrome"' in src, (
        "CHANNEL_CHAIN should include 'chrome' (real Chrome - best fingerprint, "
        "avoids the ICU bug per diag_icu_bug.py)"
    )
    assert '"msedge"' in src, (
        "CHANNEL_CHAIN should include 'msedge' (preinstalled on Windows, "
        "fallback if Chrome uninstalls)"
    )
    assert '"chromium"' in src, (
        "CHANNEL_CHAIN should include 'chromium' (bundled Chromium-for-Testing, "
        "last-resort fallback)"
    )


def t_helper_iterates_channel_chain():
    src = _read(SHARED_PATH)
    fn_body = _extract_function(src, "launch_chromium_with_fallback")
    assert fn_body is not None, "launch_chromium_with_fallback() not found"
    assert "for channel in" in fn_body, (
        "Helper should have 'for channel in ...' loop iterating the channel "
        "chain. Without iteration, no fallback - if the first channel fails, "
        "the helper just dies."
    )


def t_helper_always_sets_channel_kwarg():
    """The whole point of the helper is to AVOID chrome-headless-shell.
    Each launch attempt must explicitly pass channel=... or we'd re-introduce
    the bug we're trying to fix."""
    src = _read(SHARED_PATH)
    fn_body = _extract_function(src, "launch_chromium_with_fallback")
    assert fn_body is not None
    sets_channel = (
        'kwargs["channel"]' in fn_body
        or "kwargs['channel']" in fn_body
        or "channel=channel" in fn_body
    )
    assert sets_channel, (
        "Helper must set channel= on every launch attempt (either via "
        "kwargs['channel'] = channel or channel=channel kwarg). Skipping "
        "this would re-introduce the chrome-headless-shell bug."
    )


def t_helper_raises_on_all_channels_fail():
    src = _read(SHARED_PATH)
    fn_body = _extract_function(src, "launch_chromium_with_fallback")
    assert fn_body is not None
    assert "RuntimeError(" in fn_body or "raise RuntimeError" in fn_body, (
        "Helper should raise RuntimeError when ALL channels fail to launch. "
        "Silently returning None would let callers blunder ahead with no "
        "context, masking the real failure."
    )


def t_helper_creates_user_data_dir():
    src = _read(SHARED_PATH)
    fn_body = _extract_function(src, "launch_chromium_with_fallback")
    assert fn_body is not None
    assert "os.makedirs(" in fn_body, (
        "Helper should call os.makedirs(user_data_dir, exist_ok=True) so "
        "callers don't have to. Mirrors the pattern in shared.py:open_browser()."
    )


def t_helper_is_importable():
    """Smoke test - confirm the function can actually be imported.
    Catches syntax errors that text matching would miss."""
    if _root not in sys.path:
        sys.path.insert(0, _root)
    try:
        import shared  # type: ignore
    except Exception as e:
        raise AssertionError(f"shared.py failed to import: {type(e).__name__}: {e}")
    assert hasattr(shared, "launch_chromium_with_fallback"), (
        "shared.launch_chromium_with_fallback not exported"
    )
    assert callable(shared.launch_chromium_with_fallback), (
        "launch_chromium_with_fallback is not callable"
    )
    assert hasattr(shared, "CHANNEL_CHAIN"), (
        "shared.CHANNEL_CHAIN not exported"
    )
    chain = shared.CHANNEL_CHAIN
    assert isinstance(chain, tuple), "CHANNEL_CHAIN should be a tuple (immutable)"
    assert "chrome" in chain, "CHANNEL_CHAIN should include 'chrome'"
    assert "msedge" in chain, "CHANNEL_CHAIN should include 'msedge'"
    assert "chromium" in chain, "CHANNEL_CHAIN should include 'chromium'"


# ----------------------------------------------------------------------------
# RUNNER
# ----------------------------------------------------------------------------

def main():
    print("=" * 70)
    print(" v6.1.2 step 1 - shared.py launch_chromium_with_fallback helper")
    print("=" * 70)

    tests = [
        ("helper_function_exists",
            t_helper_function_exists),
        ("channel_chain_constant_defined",
            t_channel_chain_constant_defined),
        ("helper_iterates_channel_chain",
            t_helper_iterates_channel_chain),
        ("helper_always_sets_channel_kwarg",
            t_helper_always_sets_channel_kwarg),
        ("helper_raises_on_all_channels_fail",
            t_helper_raises_on_all_channels_fail),
        ("helper_creates_user_data_dir",
            t_helper_creates_user_data_dir),
        ("helper_is_importable",
            t_helper_is_importable),
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
