"""
test_v6_1_15_costco_resilience.py

Structural + behavior tests for v6.1.15 Costco resilience hardening.

Tests use the `def t_*` convention. Each test raises AssertionError on failure.
No Playwright dependency — tests don't actually fire HTTP/browser calls.
"""

import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

COSTCO_PATH = os.path.join(_ROOT, "plugins", "costco_tracker.py")


def _load_costco():
    """Import costco_tracker without executing __main__."""
    spec = importlib.util.spec_from_file_location(
        "costco_tracker_under_test", COSTCO_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_source():
    with open(COSTCO_PATH, "r", encoding="utf-8") as f:
        return f.read()


# Structural tests — module-level constants

def t_costco_resilience_dict_exists():
    mod = _load_costco()
    assert hasattr(mod, "COSTCO_RESILIENCE"), "COSTCO_RESILIENCE not defined"
    assert isinstance(mod.COSTCO_RESILIENCE, dict)


def t_costco_resilience_has_all_keys():
    mod = _load_costco()
    expected = {
        "product_nav_retries", "product_nav_backoff",
        "warehouse_api_retries", "warehouse_api_backoff",
        "session_retries", "session_backoff",
        "auth_consecutive_threshold", "auth_alert_dedupe_hours",
        "log_warning_on_exhaustion",
    }
    actual = set(mod.COSTCO_RESILIENCE.keys())
    missing = expected - actual
    assert not missing, f"COSTCO_RESILIENCE missing keys: {sorted(missing)}"


def t_costco_resilience_backoff_lengths_match():
    """Each *_retries must equal len(*_backoff)."""
    mod = _load_costco()
    cfg = mod.COSTCO_RESILIENCE
    assert cfg["product_nav_retries"] == len(cfg["product_nav_backoff"])
    assert cfg["warehouse_api_retries"] == len(cfg["warehouse_api_backoff"])
    assert cfg["session_retries"] == len(cfg["session_backoff"])


def t_auth_signal_constants_present():
    mod = _load_costco()
    assert hasattr(mod, "AUTH_FAILURE_URL_FRAGMENTS")
    assert hasattr(mod, "AUTH_FAILURE_CONTENT_PATTERNS")
    assert isinstance(mod.AUTH_FAILURE_URL_FRAGMENTS, tuple)
    assert isinstance(mod.AUTH_FAILURE_CONTENT_PATTERNS, tuple)
    assert len(mod.AUTH_FAILURE_URL_FRAGMENTS) >= 1
    assert len(mod.AUTH_FAILURE_CONTENT_PATTERNS) >= 1


# Structural tests — class methods

def t_class_has_resilience_methods():
    mod = _load_costco()
    cls = mod.CostcoTracker
    for name in ("_with_retry", "_record_auth_signal",
                 "_clear_auth_signal", "_alert_auth_failure",
                 "_looks_like_auth_failure"):
        assert hasattr(cls, name), f"CostcoTracker missing method {name!r}"


# Behavior tests — _looks_like_auth_failure
# These don't need a real instance; we can call as unbound

def _make_tracker_stub():
    """Build a minimal tracker instance for behavior testing.

    Bypasses the real __init__ which loads history files and registers
    products. We just need an object with the right method bindings.
    """
    mod = _load_costco()
    cls = mod.CostcoTracker
    obj = cls.__new__(cls)
    obj.config = {}
    obj.ntfy_topic = ""
    obj.history = {}
    obj.watch_list = []
    obj.active = []
    return obj, mod


def t_auth_detection_recognizes_logon_redirect():
    tracker, _ = _make_tracker_stub()
    assert tracker._looks_like_auth_failure(
        "https://www.costco.com/LogonForm?redirectUrl=...",
        "<html>Sign In</html>"
    ) is True


def t_auth_detection_recognizes_signin_title():
    tracker, _ = _make_tracker_stub()
    assert tracker._looks_like_auth_failure(
        "https://www.costco.com/some-product.html",
        "<title>Sign In | Costco</title><body>...</body>"
    ) is True


def t_auth_detection_clean_product_page_does_not_match():
    tracker, _ = _make_tracker_stub()
    assert tracker._looks_like_auth_failure(
        "https://www.costco.com/pokemon-prismatic.product.4000352232.html",
        "<title>Pokemon Prismatic Evolutions</title><body>add to cart</body>"
    ) is False


def t_auth_detection_handles_empty_inputs():
    tracker, _ = _make_tracker_stub()
    assert tracker._looks_like_auth_failure("", "") is False
    assert tracker._looks_like_auth_failure(None, None) is False


# Behavior tests — _with_retry

def t_with_retry_succeeds_first_try():
    tracker, _ = _make_tracker_stub()
    calls = [0]
    def fn():
        calls[0] += 1
        return "success"
    ok, result, exc = tracker._with_retry(
        fn, retries=2, backoff_s=[0, 0], label="test"
    )
    assert ok is True
    assert result == "success"
    assert exc is None
    assert calls[0] == 1, f"expected 1 call on success, got {calls[0]}"


def t_with_retry_succeeds_after_one_failure():
    tracker, _ = _make_tracker_stub()
    calls = [0]
    def fn():
        calls[0] += 1
        if calls[0] == 1:
            raise ValueError("transient")
        return "recovered"
    ok, result, exc = tracker._with_retry(
        fn, retries=2, backoff_s=[0, 0], label="test"
    )
    assert ok is True
    assert result == "recovered"
    assert calls[0] == 2


def t_with_retry_exhausts_and_returns_failure():
    tracker, _ = _make_tracker_stub()
    calls = [0]
    def fn():
        calls[0] += 1
        raise RuntimeError(f"fail {calls[0]}")
    ok, result, exc = tracker._with_retry(
        fn, retries=2, backoff_s=[0, 0], label="test"
    )
    assert ok is False
    assert result is None
    assert isinstance(exc, RuntimeError)
    assert calls[0] == 3, f"expected 3 attempts (1 + 2 retries), got {calls[0]}"


# Behavior tests — auth-signal state tracking

def t_record_auth_signal_increments_counter():
    tracker, _ = _make_tracker_stub()
    _, m = _make_tracker_stub()
    m.save_history = lambda *a, **kw: None

    # Suppress ntfy
    tracker._alert_auth_failure = lambda src, n: None

    tracker._record_auth_signal("test_source_1")
    assert tracker.history["_costco_health"]["consecutive_auth_failures"] == 1

    tracker._record_auth_signal("test_source_2")
    assert tracker.history["_costco_health"]["consecutive_auth_failures"] == 2


def t_clear_auth_signal_resets_counter():
    tracker, m = _make_tracker_stub()
    m.save_history = lambda *a, **kw: None

    tracker.history["_costco_health"] = {"consecutive_auth_failures": 5}
    tracker._clear_auth_signal()
    assert tracker.history["_costco_health"]["consecutive_auth_failures"] == 0


# Self-runner

if __name__ == "__main__":
    tests = sorted([n for n in dir() if n.startswith("t_") and callable(globals()[n])])
    passed = 0
    failed = []
    for name in tests:
        try:
            globals()[name]()
            passed += 1
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
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
