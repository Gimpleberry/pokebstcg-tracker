"""
test_v6_1_16_scheduler_health.py

Structural + behavior tests for v6.1.16 Step A scheduler introspection
endpoint (/api/scheduler/health).

Tests use the `def t_*` convention. Each test raises AssertionError on failure.
No real HTTP server — behavior tests dispatch directly through the real
_ApiHandler._dispatch_get() bound to a mock handler instance.

Module loaded via importlib.util.spec_from_file_location to avoid the
`import plugins.X` pathology (works in Linux namespace-package sandboxes
but breaks on Windows + Py3.14 without __init__.py).
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_PLUGINS_DIR = os.path.join(_ROOT, "plugins")
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

API_SERVER_PATH = os.path.join(_ROOT, "plugins", "api_server.py")
TRACKER_PATH = os.path.join(_ROOT, "tracker.py")


def _load_api_server():
    """Import api_server.py via spec_from_file_location.
    Each call returns a fresh module with _scheduler reset to None."""
    spec = importlib.util.spec_from_file_location(
        "api_server_under_test", API_SERVER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# -- Structural tests -----------------------------------------------------

def t_set_scheduler_function_exists():
    mod = _load_api_server()
    assert hasattr(mod, "set_scheduler"), "set_scheduler not defined"
    assert callable(mod.set_scheduler), "set_scheduler not callable"


def t_module_scheduler_var_defaults_none():
    mod = _load_api_server()
    assert hasattr(mod, "_scheduler"), "_scheduler module attr missing"
    assert mod._scheduler is None, (
        f"_scheduler should default to None, got {mod._scheduler!r}"
    )


def t_datetime_imported():
    src = _read_text(API_SERVER_PATH)
    assert "from datetime import datetime" in src, (
        "datetime import missing"
    )


def t_scheduler_health_route_in_source():
    src = _read_text(API_SERVER_PATH)
    assert '/api/scheduler/health' in src, (
        "/api/scheduler/health route literal missing from source"
    )


def t_scheduler_health_in_endpoints_list():
    """Path string appears at minimum twice post-apply: once in the
    /api/health endpoint listing, once in the route comparison."""
    src = _read_text(API_SERVER_PATH)
    occurrences = src.count('"/api/scheduler/health"')
    assert occurrences >= 2, (
        f"expected >=2 occurrences of \"/api/scheduler/health\", "
        f"got {occurrences}"
    )


def t_tracker_wires_scheduler():
    src = _read_text(TRACKER_PATH)
    assert "_api_server_mod.set_scheduler(scheduler)" in src, (
        "tracker.py does not wire _api_server_mod.set_scheduler(scheduler)"
    )


# -- Behavior tests -------------------------------------------------------

class _MockHandler:
    """Captures _send_json calls for inspection."""
    def __init__(self, path):
        self.path = path
        self.responses = []

    def _send_json(self, status, payload, origin=""):
        self.responses.append((status, payload))


class _StubScheduler:
    """Stand-in for the real Scheduler. Configurable jobs() return."""
    def __init__(self, jobs_value=None, raises=None, ready=True):
        self._jobs_value = jobs_value if jobs_value is not None else []
        self._raises = raises
        self.is_ready = ready

    def jobs(self):
        if self._raises:
            raise self._raises
        return list(self._jobs_value)


def _dispatch_get(mod, handler):
    """Bind the real _dispatch_get to a mock handler and call."""
    mod._ApiHandler._dispatch_get(handler, "")


def t_set_scheduler_stores_ref():
    mod = _load_api_server()
    stub = _StubScheduler()
    mod.set_scheduler(stub)
    assert mod._scheduler is stub, "set_scheduler did not store ref"


def t_health_endpoint_503_when_unwired():
    mod = _load_api_server()
    handler = _MockHandler("/api/scheduler/health")
    _dispatch_get(mod, handler)
    assert len(handler.responses) == 1, (
        f"expected 1 response, got {len(handler.responses)}"
    )
    status, payload = handler.responses[0]
    assert status == 503, f"expected 503, got {status}"
    assert payload.get("ok") is False
    assert "error" in payload


def t_health_endpoint_200_with_stub_scheduler():
    mod = _load_api_server()
    stub = _StubScheduler(jobs_value=[
        {
            "name": "amazon_monitor.check_all",
            "owner": "amazon_monitor",
            "cadence": "every 15 minutes",
            "kickoff": True,
            "kickoff_delay": 90,
            "next_run": None,
            "last_run": None,
            "last_status": None,
        },
    ])
    mod.set_scheduler(stub)
    handler = _MockHandler("/api/scheduler/health")
    _dispatch_get(mod, handler)
    assert len(handler.responses) == 1
    status, payload = handler.responses[0]
    assert status == 200, f"expected 200, got {status}"
    assert payload.get("ok") is True
    assert payload.get("ready") is True
    assert payload.get("job_count") == 1
    assert isinstance(payload.get("jobs"), list)
    assert len(payload["jobs"]) == 1


def t_health_endpoint_jobs_exception_returns_500():
    mod = _load_api_server()
    stub = _StubScheduler(raises=RuntimeError("simulated failure"))
    mod.set_scheduler(stub)
    handler = _MockHandler("/api/scheduler/health")
    _dispatch_get(mod, handler)
    assert len(handler.responses) == 1
    status, payload = handler.responses[0]
    assert status == 500, f"expected 500, got {status}"
    assert payload.get("ok") is False
    detail = payload.get("detail", "")
    assert "RuntimeError" in detail, (
        f"expected RuntimeError in detail, got: {detail!r}"
    )


def t_health_endpoint_payload_has_all_keys():
    mod = _load_api_server()
    stub = _StubScheduler(jobs_value=[])
    mod.set_scheduler(stub)
    handler = _MockHandler("/api/scheduler/health")
    _dispatch_get(mod, handler)
    status, payload = handler.responses[0]
    expected_keys = {"ok", "ready", "generated_at", "job_count", "jobs"}
    actual_keys = set(payload.keys())
    missing = expected_keys - actual_keys
    assert not missing, f"missing keys: {sorted(missing)}"


def t_main_health_endpoint_lists_scheduler_health():
    mod = _load_api_server()
    handler = _MockHandler("/api/health")
    _dispatch_get(mod, handler)
    assert len(handler.responses) == 1
    status, payload = handler.responses[0]
    assert status == 200, f"expected 200, got {status}"
    endpoints = payload.get("endpoints", [])
    assert "/api/scheduler/health" in endpoints, (
        f"/api/scheduler/health missing from endpoints list: {endpoints}"
    )


# -- Self-runner ----------------------------------------------------------

if __name__ == "__main__":
    tests = sorted(
        [n for n in dir() if n.startswith("t_") and callable(globals()[n])]
    )
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
