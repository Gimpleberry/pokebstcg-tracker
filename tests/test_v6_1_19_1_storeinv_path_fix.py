"""
test_v6_1_19_1_storeinv_path_fix.py

Regression test for v6.1.19.1 — plugins/store_inventory.py path
migration miss. The v5.2 reorg moved runtime state to data/ (DATA_DIR),
but store_inventory.py:378 was still writing store_inventory.json to
OUTPUT_DIR (== ROOT_DIR, the legacy alias for the project root).

This test scans every plugins/*.py file for the antipattern:
    os.path.join(OUTPUT_DIR, "<filename>"), "w"
i.e. OUTPUT_DIR + filename + write-mode together. Pre-patch fails on
store_inventory.py:378. Post-patch all plugin writes go through DATA_DIR.

OUTPUT_DIR is still legitimate for sys.path manipulation
(sys.path.insert(0, OUTPUT_DIR)) — this test only flags the data-write
antipattern.
"""

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PLUGINS_DIR = os.path.join(_ROOT, "plugins")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# Antipattern: os.path.join(OUTPUT_DIR, ...), "w"
# Captures any context where OUTPUT_DIR is joined with a filename (any
# arguments) and the result is opened in write mode. DOTALL handles
# multi-line argument lists.
_BAD_PATTERN = re.compile(
    r'os\.path\.join\(\s*OUTPUT_DIR\s*,.*?\)\s*,\s*["\']w',
    re.DOTALL,
)


def t_no_plugin_writes_data_to_output_dir():
    """No plugin uses OUTPUT_DIR + filename + write-mode together.

    Banks the v5.2-reorg-miss lesson: OUTPUT_DIR is the legacy alias for
    ROOT_DIR (project root). Runtime data writes must go through DATA_DIR
    (the data/ subfolder) per the v5.2 storage convention. OUTPUT_DIR
    remains valid for sys.path manipulation in __main__ blocks.
    """
    bad = []
    if not os.path.isdir(PLUGINS_DIR):
        # Sandbox runs may not have plugins/ — don't fail spuriously
        return
    for fname in sorted(os.listdir(PLUGINS_DIR)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(PLUGINS_DIR, fname)
        try:
            src = _read(path)
        except OSError:
            continue
        for m in _BAD_PATTERN.finditer(src):
            line_no = src[:m.start()].count("\n") + 1
            bad.append(f"{fname}:{line_no}")

    assert not bad, (
        "Plugins write data via OUTPUT_DIR (should be DATA_DIR per v5.2 "
        f"reorg): {bad}"
    )


# Self-runner ----------------------------------------------------------------

if __name__ == "__main__":
    import sys
    tests = sorted(
        n for n in dir() if n.startswith("t_") and callable(globals()[n])
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
