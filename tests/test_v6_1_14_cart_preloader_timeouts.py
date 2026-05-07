"""
test_v6_1_14_cart_preloader_timeouts.py

Structural tests for v6.1.14 cart_preloader timeout centralization.

Tests use the `def t_*` convention. Each test raises AssertionError on failure.
No Playwright dependency — these are static structural tests against the
source file, not behavior tests.
"""

import importlib.util
import os
import re
import sys

# Add project root to path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CART_PRELOADER_PATH = os.path.join(_ROOT, "plugins", "cart_preloader.py")


def _load_cart_module():
    """Import cart_preloader without executing __main__."""
    spec = importlib.util.spec_from_file_location(
        "cart_preloader_under_test", CART_PRELOADER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_source():
    with open(CART_PRELOADER_PATH, "r", encoding="utf-8") as f:
        return f.read()


# Expected keys in CART_TIMEOUTS dict (the contract this patch establishes)
EXPECTED_KEYS = {
    "product_goto_ms",
    "checkout_goto_ms",
    "login_goto_ms",
    "post_product_load_ms",
    "post_atc_click_ms",
    "post_checkout_nav_ms",
}


def t_cart_timeouts_dict_exists():
    """CART_TIMEOUTS must be defined at module level as a dict."""
    mod = _load_cart_module()
    assert hasattr(mod, "CART_TIMEOUTS"), "CART_TIMEOUTS constant not defined"
    assert isinstance(mod.CART_TIMEOUTS, dict), "CART_TIMEOUTS must be a dict"


def t_cart_timeouts_has_all_expected_keys():
    """Every documented timeout key must be present."""
    mod = _load_cart_module()
    actual = set(mod.CART_TIMEOUTS.keys())
    missing = EXPECTED_KEYS - actual
    assert not missing, f"CART_TIMEOUTS missing keys: {sorted(missing)}"


def t_cart_timeouts_values_are_positive_ints():
    """Every value must be a positive integer (milliseconds)."""
    mod = _load_cart_module()
    for k, v in mod.CART_TIMEOUTS.items():
        assert isinstance(v, int), f"{k}: value {v!r} is not an int"
        assert v > 0, f"{k}: value {v} is not positive"


def t_cart_timeouts_values_in_sane_bounds():
    """Sanity check: nothing wildly out of range (1ms..120s)."""
    mod = _load_cart_module()
    for k, v in mod.CART_TIMEOUTS.items():
        assert 1 <= v <= 120000, f"{k}: value {v}ms out of sane bounds [1, 120000]"


def t_no_hardcoded_main_path_static_sleeps():
    """
    No `wait_for_timeout(NNNN)` literal int calls outside of the user-wait
    pattern. The 3 happy-path sleeps must route through CART_TIMEOUTS.

    Note: `wait_for_event("close", timeout=0)` is allowed and unrelated.
    """
    src = _read_source()
    # Match wait_for_timeout(<integer literal>) — the bug pattern
    pattern = re.compile(r"wait_for_timeout\(\s*(\d+)\s*\)")
    matches = pattern.findall(src)
    assert not matches, (
        f"hardcoded wait_for_timeout literals still present: {matches} "
        f"— all should route through CART_TIMEOUTS"
    )


def t_all_main_path_gotos_route_through_dict():
    """
    Every `page.goto(...)` call should use timeout=CART_TIMEOUTS[...] not
    a literal integer.
    """
    src = _read_source()
    # Match: page.goto(...timeout=NNNN...) where NNNN is digits
    pattern = re.compile(
        r"page\.goto\([^)]*timeout\s*=\s*(\d+)[^)]*\)"
    )
    matches = pattern.findall(src)
    assert not matches, (
        f"hardcoded page.goto timeouts still present: {matches} "
        f"— all should route through CART_TIMEOUTS"
    )


def t_user_wait_blocks_untouched():
    """
    The wait_for_event("close", timeout=0) blocks are intentional and
    must NOT be modified by this patch.
    """
    src = _read_source()
    user_wait_count = src.count('wait_for_event("close", timeout=0)')
    assert user_wait_count >= 2, (
        f"expected at least 2 user-wait blocks, found {user_wait_count} "
        f"— v6.1.14 must NOT touch these"
    )


# Self-runner — used by the apply script's verification phase
if __name__ == "__main__":
    tests = [name for name in dir() if name.startswith("t_") and callable(globals()[name])]
    passed = 0
    failed = []
    for name in tests:
        try:
            globals()[name]()
            passed += 1
        except AssertionError as e:
            failed.append((name, str(e)))
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))

    if failed:
        print(f"FAIL: {len(failed)}/{len(tests)} test(s) failed")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print(f"OK: {passed}/{len(tests)} tests passed")
        sys.exit(0)
