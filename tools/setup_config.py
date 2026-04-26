#!/usr/bin/env python3
"""
tools/setup_config.py - One-time interactive setup for tcg_tracker local config.

Creates the local config file with your ntfy topic, home zip, and location anchors.
On Windows, the config lives at:
    %LOCALAPPDATA%\\tcg_tracker\\config.json

Safe to re-run - prompts before overwriting an existing config.

Usage:
    python tools/setup_config.py            # interactive
    python tools/setup_config.py --show     # print current config (topic masked)
    python tools/setup_config.py --validate # verify config without changing it
"""

import json
import os
import re
import sys

# Path resolution: works whether run from project root or tools/ folder
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tools" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)

from shared import (
    APPDATA_DIR,
    CONFIG_PATH,
    REQUIRED_CONFIG_KEYS,
    CONFIG_DEFAULTS,
    load_local_config,
    ConfigError,
)


def mask_topic(topic):
    """Mask a topic for safe display."""
    if len(topic) < 10:
        return "****"
    return topic[:8] + "..." + topic[-4:]


def prompt(label, default="", validator=None, secret=False):
    """Prompt for input with optional default and validator."""
    while True:
        prefix = "  " + label
        if default and not secret:
            prefix += " [" + default + "]"
        elif default and secret:
            prefix += " [" + mask_topic(default) + "]"
        prefix += ": "

        try:
            value = input(prefix).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nCancelled.\n")
            sys.exit(1)

        if not value and default:
            value = default

        if not value:
            print("    (required - please enter a value)")
            continue

        if validator:
            err = validator(value)
            if err:
                print("    " + err)
                continue

        return value


def validate_zip(z):
    if not re.fullmatch(r"\d{5}", z):
        return "Zip must be exactly 5 digits."
    return None


def validate_topic(t):
    if len(t) < 12:
        return "Topic too short - use at least 12 characters."
    if not re.fullmatch(r"[a-zA-Z0-9_\-]+", t):
        return "Topic must be alphanumeric, hyphens, or underscores only."
    risky = ["keith", "gimple", "1995", "1996", "1997", "1998", "1999"]
    lower = t.lower()
    for r in risky:
        if r in lower:
            print("    WARNING: topic contains '" + r + "' - guessable. Continue anyway? (y/N): ", end="")
            try:
                ans = input().strip().lower()
            except (KeyboardInterrupt, EOFError):
                return "Cancelled."
            if ans != "y":
                return "Pick a different topic."
            break
    return None


def validate_city(c):
    if len(c) < 3:
        return "City too short."
    return None


def show_current():
    """Print the existing config with the ntfy topic masked."""
    try:
        cfg = load_local_config(force_reload=True)
    except ConfigError as e:
        print("\n" + str(e) + "\n")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  Current config - " + CONFIG_PATH)
    print("=" * 60)
    for k, v in cfg.items():
        if k == "ntfy_topic":
            print("  " + k.ljust(25) + " = " + mask_topic(v))
        else:
            print("  " + k.ljust(25) + " = " + str(v))
    print("=" * 60 + "\n")


def validate_only():
    """Run load_local_config and report success or failure."""
    print("\nValidating config...")
    try:
        cfg = load_local_config(force_reload=True)
        print("  OK - config valid at " + CONFIG_PATH)
        print("  ntfy_topic: " + mask_topic(cfg["ntfy_topic"]))
        print("  home_zip:   " + cfg["home_zip"])
        print("  home_city:  " + cfg["home_city"])
        print("  anchors:    " + str(cfg.get("anchor_locations", [])) + "\n")
    except ConfigError as e:
        print("\n  FAIL - " + str(e) + "\n")
        sys.exit(1)


def interactive_setup():
    """Walk the user through creating or updating the config file."""
    print("\n" + "=" * 60)
    print("  TCG Tracker - Local Config Setup")
    print("=" * 60)
    print("  Target file: " + CONFIG_PATH)
    print("=" * 60 + "\n")

    existing = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                existing = json.load(f)
            print("  An existing config was found.  Showing current values as defaults.")
            print("  Press ENTER to keep a value, or type a new one to change it.\n")
        except Exception as e:
            print("  Existing config could not be parsed: " + str(e))
            print("  Proceeding with empty defaults.\n")
            existing = {}

    ntfy_topic = prompt(
        "ntfy topic (long random string)",
        default=existing.get("ntfy_topic", ""),
        validator=validate_topic,
        secret=True,
    )
    home_zip = prompt(
        "home zip (5 digits)",
        default=existing.get("home_zip", ""),
        validator=validate_zip,
    )
    home_city = prompt(
        "home city + state (e.g. Plainfield NJ)",
        default=existing.get("home_city", ""),
        validator=validate_city,
    )

    anchors_default = ", ".join(existing.get("anchor_locations", []))
    anchors_raw = prompt(
        "anchor locations (comma-separated, optional)",
        default=anchors_default or "(none)",
    )
    if anchors_raw == "(none)":
        anchors = []
    else:
        anchors = [a.strip() for a in anchors_raw.split(",") if a.strip()]

    cfg = {
        "_schema_version": 1,
        "_notes": "Local config. Never commit. Never sync to cloud.",
        "ntfy_topic":      ntfy_topic,
        "home_zip":        home_zip,
        "home_city":       home_city,
        "anchor_locations": anchors,
    }
    for k, default in CONFIG_DEFAULTS.items():
        if k.startswith("_"):
            continue
        cfg[k] = existing.get(k, default)

    print("\n" + "-" * 60)
    print("  Review:")
    print("-" * 60)
    for k, v in cfg.items():
        if k == "ntfy_topic":
            print("  " + k.ljust(25) + " = " + mask_topic(v))
        else:
            print("  " + k.ljust(25) + " = " + str(v))
    print("-" * 60)
    confirm = input("\n  Write to disk? (y/N): ").strip().lower()
    if confirm != "y":
        print("\n  Cancelled - no changes made.\n")
        sys.exit(0)

    os.makedirs(APPDATA_DIR, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("\n  FAIL - could not write " + CONFIG_PATH + ": " + str(e) + "\n")
        sys.exit(1)

    try:
        verify = load_local_config(force_reload=True)
        assert verify["ntfy_topic"] == ntfy_topic
        assert verify["home_zip"] == home_zip
    except (ConfigError, AssertionError) as e:
        print("\n  FAIL - config was written but failed validation: " + str(e) + "\n")
        sys.exit(1)

    browser_dir = os.path.join(APPDATA_DIR, "browser_profile")
    os.makedirs(browser_dir, exist_ok=True)

    print("\n" + "=" * 60)
    print("  OK - config written to " + CONFIG_PATH)
    print("  Browser profile dir ready: " + browser_dir)
    print("=" * 60)
    print("\n  Next steps:")
    print("    1. python tests/test_config.py     (verify config + ntfy)")
    print("    2. python tracker.py               (start the tracker)")
    print()


if __name__ == "__main__":
    if "--show" in sys.argv:
        show_current()
    elif "--validate" in sys.argv:
        validate_only()
    else:
        interactive_setup()