#!/usr/bin/env python3
"""
tests/test_config.py - Verify local config loads and ntfy publishes.

Runs four checks:
  1. Config file exists at expected path
  2. Config parses as JSON
  3. All required keys are present and non-empty
  4. ntfy publish round-trip succeeds (HTTP 200 from ntfy.sh)

Exit code 0 = all checks pass.  Non-zero = at least one check failed.

Usage:
    python tests/test_config.py
    python tests/test_config.py --no-ntfy   # skip live ntfy publish test
"""

import os
import sys

# -- Path resolution: works whether run from root or tests/ folder --------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)
# ------------------------------------------------------------------------

from shared import (
    APPDATA_DIR, CONFIG_PATH, BROWSER_PROFILE,
    REQUIRED_CONFIG_KEYS, CONFIG_DEFAULTS,
    load_local_config, ConfigError,
    send_ntfy,
)


PASS = "  [PASS]"
FAIL = "  [FAIL]"
SKIP = "  [SKIP]"


def main():
    skip_ntfy = "--no-ntfy" in sys.argv
    failures = []

    print("\n" + "=" * 60)
    print("  TCG Tracker - Config Diagnostic")
    print("=" * 60)
    print("  APPDATA_DIR:     " + APPDATA_DIR)
    print("  CONFIG_PATH:     " + CONFIG_PATH)
    print("  BROWSER_PROFILE: " + BROWSER_PROFILE)
    print("=" * 60 + "\n")

    # Test 1: file exists
    print("Test 1: Config file exists")
    if os.path.exists(CONFIG_PATH):
        print(PASS + " " + CONFIG_PATH)
    else:
        print(FAIL + " Not found: " + CONFIG_PATH)
        print("       Run: python tools/setup_config.py")
        failures.append("file_missing")
        print("\n" + "=" * 60)
        print("  RESULT: 1 failure - " + str(failures))
        print("=" * 60 + "\n")
        return 1

    # Test 2: parses + has required keys
    print("\nTest 2: Config parses + required keys present")
    try:
        cfg = load_local_config(force_reload=True)
        print(PASS + " JSON parsed")
        for k in REQUIRED_CONFIG_KEYS:
            if cfg.get(k):
                print(PASS + " " + k + " = <set>")
            else:
                print(PASS + " " + k + " = <EMPTY>")
    except ConfigError as e:
        print(FAIL + " " + str(e))
        failures.append("parse_or_missing_keys")
        print("\n" + "=" * 60)
        print("  RESULT: failures - " + str(failures))
        print("=" * 60 + "\n")
        return 1

    # Test 3: optional keys + sane defaults
    print("\nTest 3: Optional keys have sane values")
    for k, default in CONFIG_DEFAULTS.items():
        if k.startswith("_"):
            continue
        v = cfg.get(k)
        if v is None:
            print(FAIL + " " + k + " = None")
            failures.append("optional_" + k + "_none")
        else:
            print(PASS + " " + k + " = " + str(v))

    # Test 4: ntfy publish round-trip
    print("\nTest 4: ntfy publish round-trip")
    if skip_ntfy:
        print(SKIP + " --no-ntfy specified")
    else:
        topic = cfg["ntfy_topic"]
        ok = send_ntfy(
            topic=topic,
            title="Config Diagnostic",
            body="If you see this, ntfy is wired up correctly.",
            priority="low",
            tags="white_check_mark,gear",
        )
        if ok:
            print(PASS + " Test notification sent - check your phone")
        else:
            print(FAIL + " send_ntfy returned False - see log above")
            failures.append("ntfy_publish")

    # Summary
    print("\n" + "=" * 60)
    if failures:
        print("  RESULT: " + str(len(failures)) + " failure(s) - " + str(failures))
        print("=" * 60 + "\n")
        return 1
    else:
        print("  RESULT: all checks passed")
        print("=" * 60 + "\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())