"""
test_v6_1_17_scheduler_panel.py

Structural tests for v6.1.17 dashboard scheduler health panel.
All tests are static text searches against dashboard/dashboard.html.
No JS engine required.
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DASHBOARD_PATH = os.path.join(_ROOT, "dashboard", "dashboard.html")


def _read():
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        return f.read()


# -- Markup tests ---------------------------------------------------------

def t_panel_id_present():
    src = _read()
    assert 'id="scheduler-panel"' in src, "scheduler-panel id missing"


def t_panel_body_id_present():
    src = _read()
    assert 'id="scheduler-panel-body"' in src, "scheduler-panel-body id missing"


def t_jobs_tbody_id_present():
    src = _read()
    assert 'id="scheduler-jobs-tbody"' in src, (
        "scheduler-jobs-tbody id missing (table body for job rows)"
    )


def t_panel_inserted_before_stats_bar():
    """Panel must be at top of main content, before stats-bar."""
    src = _read()
    panel_idx = src.find('id="scheduler-panel"')
    stats_idx = src.find('class="stats-bar"')
    assert panel_idx > 0, "scheduler-panel not found"
    assert stats_idx > 0, "stats-bar not found"
    assert panel_idx < stats_idx, (
        f"scheduler-panel ({panel_idx}) must precede stats-bar "
        f"({stats_idx}) in document"
    )


# -- CSS tests ------------------------------------------------------------

def t_status_pill_classes_defined():
    src = _read()
    for klass in (
        ".scheduler-status-pill",
        ".scheduler-status-pill.ok",
        ".scheduler-status-pill.error",
        ".scheduler-status-pill.never",
    ):
        assert klass in src, f"missing CSS class: {klass}"


def t_panel_table_class_defined():
    src = _read()
    assert ".scheduler-table" in src, ".scheduler-table CSS class missing"


# -- JS tests -------------------------------------------------------------

def t_health_endpoint_url_in_js():
    src = _read()
    assert "/api/scheduler/health" in src, (
        "/api/scheduler/health URL literal not found in JS"
    )


def t_refresh_interval_60s():
    """60000ms refresh interval. Confirms the cadence chosen in Q2."""
    src = _read()
    assert "60000" in src, "60000ms (60s) refresh interval missing"


def t_toggle_handler_defined():
    src = _read()
    assert "function toggleSchedulerPanel" in src, (
        "toggleSchedulerPanel function not defined"
    )


def t_init_function_called():
    src = _read()
    assert "initSchedulerPanel()" in src, (
        "initSchedulerPanel() not invoked"
    )


def t_existing_init_preserved():
    """Sanity: the patch did not break the existing init() function."""
    src = _read()
    assert "async function init()" in src, "existing async init() removed"
    assert re.search(r"^init\(\);\s*$", src, re.MULTILINE), (
        "existing init() invocation missing"
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
