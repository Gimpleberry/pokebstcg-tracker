#!/usr/bin/env python3
"""
tools/diag_icu_bug.py - Diagnostic for chrome-headless-shell ICU bug
                       (v6.1.2 prep, one-off)

Why this exists
---------------
During v6.1.1 (walmart playwright migration), recon turned up that
chrome-headless-shell.exe on this machine crashes with an ICU data
error during launch. We worked around it for walmart_playwright by
forcing channel="chrome" (real system Chrome).

But review of the v6.1.1 boot log found the SAME ICU crash hitting
target, amazon_monitor, bestbuy_invites, and costco_tracker. They've
been silently failing for some time - ALL their checks return "out
of stock" because the browser can't even launch. The structural tests
pass because they only check code shape, not live launches.

This diagnostic answers four questions:

  Q1. Which files in the codebase call launch_persistent_context
      WITHOUT a channel= override? (those are the broken ones)

  Q2. Does the chrome-headless-shell ICU crash actually reproduce
      on this machine, in isolation?

  Q3. Does channel="chrome" / "msedge" / "chromium" fix it for
      vanilla playwright (no patchright dependency)?

  Q4. Does the same fix work in headless mode? (bestbuy/amazon/costco
      can't run headful - they have no PerimeterX requirement and
      were doing fine in headless before this regression)

Output
------
- Console: real-time table as cells run
- File:    data/diag_icu_bug_report.txt (timestamped report)

Usage
-----
  py -3.14 tools/diag_icu_bug.py            # full diagnostic (~3-4 min)
  py -3.14 tools/diag_icu_bug.py --scan-only  # just the codebase scan
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

# -- Path resolution ---------------------------------------------------------
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "tools" else HERE
DIAG_PROFILE_BASE = ROOT / "data" / "_diag_profile"
REPORT_PATH = ROOT / "data" / "diag_icu_bug_report.txt"

# Ensure diag profile dir exists and is clean
def _fresh_profile_dir(suffix: str) -> Path:
    """Each test cell gets its own profile dir so they don't fight."""
    p = DIAG_PROFILE_BASE / suffix
    if p.exists():
        try:
            shutil.rmtree(p)
        except Exception:
            pass
    p.mkdir(parents=True, exist_ok=True)
    return p


# -- Test cell definitions ---------------------------------------------------

TEST_CELLS = [
    # Cell 1: reproduce the bug (what currently-broken plugins do)
    {
        "name": "vanilla_playwright + chrome-headless-shell + headless",
        "engine": "playwright",
        "channel": None,
        "headless": True,
        "expected": "FAIL_ICU",
        "interpretation": "This is what target/amazon/bestbuy/costco currently do. Should fail.",
    },

    # Cell 2-4: vanilla playwright + channel switch (cheapest fix)
    {
        "name": "vanilla_playwright + channel=chrome + headless",
        "engine": "playwright",
        "channel": "chrome",
        "headless": True,
        "expected": "PASS",
        "interpretation": "Cheapest fix - just add channel='chrome' to launch call.",
    },
    {
        "name": "vanilla_playwright + channel=msedge + headless",
        "engine": "playwright",
        "channel": "msedge",
        "headless": True,
        "expected": "PASS",
        "interpretation": "Fallback if Chrome isn't installed. Edge ships with Windows.",
    },
    {
        "name": "vanilla_playwright + channel=chromium + headless",
        "engine": "playwright",
        "channel": "chromium",
        "headless": True,
        "expected": "PASS_OR_FAIL",
        "interpretation": "Bundled Chromium-for-Testing. Sometimes affected by same bug.",
    },

    # Cell 5: patchright with default channel (broader test)
    {
        "name": "patchright + chrome-headless-shell + headless",
        "engine": "patchright",
        "channel": None,
        "headless": True,
        "expected": "PASS_OR_FAIL",
        "interpretation": "Does patchright avoid the bug on its own?",
    },

    # Cell 6: patchright + channel=chrome + headless (best of both)
    {
        "name": "patchright + channel=chrome + headless",
        "engine": "patchright",
        "channel": "chrome",
        "headless": True,
        "expected": "PASS",
        "interpretation": "Maximum compatibility for headless workflows.",
    },

    # Cell 7: patchright + channel=chrome + headful (walmart_playwright stack)
    {
        "name": "patchright + channel=chrome + headful (walmart stack)",
        "engine": "patchright",
        "channel": "chrome",
        "headless": False,
        "expected": "PASS",
        "interpretation": "Confirms the v6.1.1 walmart stack still works.",
    },
]


# -- Test runner --------------------------------------------------------------

def run_test_cell(cell: dict) -> dict:
    """Launch browser per cell config, navigate to about:blank, clean shutdown.

    Returns dict with status, error_class, error_msg, duration_sec.
    """
    name = cell["name"]
    engine = cell["engine"]
    channel = cell["channel"]
    headless = cell["headless"]

    started = time.perf_counter()

    # Import engine
    try:
        if engine == "patchright":
            from patchright.sync_api import sync_playwright  # type: ignore
        else:
            from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as e:
        return {
            "status": "SKIP_IMPORT",
            "error_class": "ImportError",
            "error_msg": str(e),
            "duration_sec": time.perf_counter() - started,
        }

    profile_dir = _fresh_profile_dir(
        name.replace(" ", "_").replace("+", "and").replace("=", "_")
    )

    # Build launch kwargs
    launch_kwargs = {"headless": headless}
    if channel:
        launch_kwargs["channel"] = channel
    if not headless:
        # Off-screen window args (same as walmart_playwright)
        launch_kwargs["args"] = [
            "--window-position=-2400,-2400",
            "--window-size=400,300",
        ]

    try:
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    **launch_kwargs,
                )
            except Exception as e:
                err_str = str(e)
                err_class = type(e).__name__
                if "ICU" in err_str or "icu_util" in err_str:
                    return {
                        "status": "FAIL_ICU",
                        "error_class": err_class,
                        "error_msg": "chrome-headless-shell ICU bug",
                        "duration_sec": time.perf_counter() - started,
                    }
                if ("Executable doesn't exist" in err_str
                        or "not found" in err_str.lower()):
                    return {
                        "status": "SKIP_NOTINSTALLED",
                        "error_class": err_class,
                        "error_msg": (f"channel '{channel}' not installed"
                                      if channel else "default browser not installed"),
                        "duration_sec": time.perf_counter() - started,
                    }
                return {
                    "status": "FAIL_OTHER",
                    "error_class": err_class,
                    "error_msg": err_str[:300],
                    "duration_sec": time.perf_counter() - started,
                }

            try:
                page = ctx.new_page()
                page.goto("about:blank", timeout=10000)
                page.close()
                ctx.close()
            except Exception as e:
                return {
                    "status": "FAIL_NAV",
                    "error_class": type(e).__name__,
                    "error_msg": str(e)[:300],
                    "duration_sec": time.perf_counter() - started,
                }

        return {
            "status": "PASS",
            "error_class": None,
            "error_msg": None,
            "duration_sec": time.perf_counter() - started,
        }

    except Exception as e:
        return {
            "status": "FAIL_HARNESS",
            "error_class": type(e).__name__,
            "error_msg": str(e)[:300],
            "duration_sec": time.perf_counter() - started,
        }


# -- Codebase scan ------------------------------------------------------------

def scan_codebase() -> dict:
    """Find all .py files using launch_persistent_context. Flag those without
    a channel= override - those are the ones broken by the ICU bug."""
    affected = []
    safe = []
    skip_dirs = {".patches_archive", "__pycache__", "data", ".git",
                 "node_modules", "_diag_profile", "tests"}

    for f in ROOT.rglob("*.py"):
        # Skip if any parent is in skip_dirs
        if any(part in skip_dirs for part in f.parts):
            continue
        # Skip self
        if f.name == "diag_icu_bug.py":
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if "launch_persistent_context" not in content:
            continue

        # Heuristic: does the file specify channel= anywhere?
        uses_channel = ("channel=\"" in content
                        or "channel='" in content
                        or 'channel = "' in content)
        # Heuristic: does it import patchright?
        uses_patchright = ("patchright" in content)

        rel = f.relative_to(ROOT)
        entry = {
            "file": str(rel).replace("\\", "/"),
            "uses_channel": uses_channel,
            "uses_patchright": uses_patchright,
        }
        if uses_channel:
            safe.append(entry)
        else:
            affected.append(entry)

    return {"affected": affected, "safe": safe}


# -- Reporting ----------------------------------------------------------------

def print_table(rows: list[dict]) -> None:
    """Render the test results table to stdout."""
    print()
    print("=" * 80)
    print(" RESULTS")
    print("=" * 80)
    headers = [("CELL", 50), ("STATUS", 18), ("TIME", 7)]
    for h, w in headers:
        print(f"{h:<{w}}", end=" ")
    print()
    print("-" * 80)
    for r in rows:
        print(f"{r['name'][:50]:<50}", end=" ")
        print(f"{r['status']:<18}", end=" ")
        print(f"{r['duration_sec']:>5.1f}s", end=" ")
        print()
        if r['error_msg']:
            print(f"  -> {r['error_msg'][:75]}")


def write_report(scan: dict, results: list[dict]) -> None:
    """Write the human-readable report to data/diag_icu_bug_report.txt."""
    REPORT_PATH.parent.mkdir(exist_ok=True, parents=True)
    now = datetime.datetime.now().isoformat(timespec="seconds")

    lines = []
    lines.append("=" * 80)
    lines.append(" CHROME-HEADLESS-SHELL ICU BUG DIAGNOSTIC")
    lines.append(f" Generated: {now}")
    lines.append("=" * 80)
    lines.append("")

    # Codebase scan
    lines.append("PHASE 1 - CODEBASE SCAN")
    lines.append("-" * 80)
    lines.append(f"Files calling launch_persistent_context: "
                 f"{len(scan['affected']) + len(scan['safe'])}")
    lines.append("")
    if scan['affected']:
        lines.append(f"AFFECTED ({len(scan['affected'])}) - no channel= override, "
                     f"will hit chrome-headless-shell:")
        for a in scan['affected']:
            ptag = " [uses patchright]" if a['uses_patchright'] else ""
            lines.append(f"  - {a['file']}{ptag}")
        lines.append("")
    if scan['safe']:
        lines.append(f"SAFE ({len(scan['safe'])}) - has channel= override:")
        for s in scan['safe']:
            ptag = " [uses patchright]" if s['uses_patchright'] else ""
            lines.append(f"  - {s['file']}{ptag}")
        lines.append("")

    # Test cells
    lines.append("PHASE 2 - LAUNCH TESTS")
    lines.append("-" * 80)
    for r in results:
        lines.append(f"[{r['status']:<18}] {r['name']}")
        lines.append(f"  duration: {r['duration_sec']:.1f}s")
        lines.append(f"  expected: {r['expected']}")
        if r['error_msg']:
            lines.append(f"  error:    {r['error_class']}: {r['error_msg'][:200]}")
        lines.append(f"  meaning:  {r['interpretation']}")
        lines.append("")

    # Recommendation block
    lines.append("PHASE 3 - INTERPRETATION & RECOMMENDATION")
    lines.append("-" * 80)
    by_name = {r["name"]: r for r in results}

    bug_reproduces = (by_name.get(
        "vanilla_playwright + chrome-headless-shell + headless", {}
    ).get("status") == "FAIL_ICU")

    chrome_works = (by_name.get(
        "vanilla_playwright + channel=chrome + headless", {}
    ).get("status") == "PASS")

    msedge_works = (by_name.get(
        "vanilla_playwright + channel=msedge + headless", {}
    ).get("status") == "PASS")

    chromium_works = (by_name.get(
        "vanilla_playwright + channel=chromium + headless", {}
    ).get("status") == "PASS")

    if bug_reproduces:
        lines.append("CONFIRMED: chrome-headless-shell ICU bug reproduces on this machine.")
        lines.append("           The plugins listed in PHASE 1 'AFFECTED' are silently failing.")
        lines.append("")
    else:
        lines.append("UNEXPECTED: ICU bug did NOT reproduce. Investigate further -")
        lines.append("            something in the launch may differ from the plugins'.")
        lines.append("")

    if chrome_works:
        lines.append("FIX TEMPLATE 1 (cheapest): vanilla playwright + channel='chrome'")
        lines.append("  Just add channel='chrome' to launch_persistent_context() calls.")
        lines.append("  No new dependencies. Works in headless.")
        lines.append("")
    if msedge_works:
        lines.append("FIX TEMPLATE 2 (fallback): channel='msedge'")
        lines.append("  Edge is preinstalled on Windows so this is bulletproof if Chrome")
        lines.append("  uninstalls. Use as fallback after channel='chrome' raises.")
        lines.append("")
    if chromium_works:
        lines.append("FIX TEMPLATE 3 (last resort): channel='chromium'")
        lines.append("  Bundled Chromium-for-Testing. Use only if Chrome and Edge both fail.")
        lines.append("")

    if chrome_works and msedge_works:
        lines.append("RECOMMENDED v6.1.2 PATCH:")
        lines.append("  Wrap launch_persistent_context calls with the same channel chain")
        lines.append("  walmart_playwright uses (chrome -> msedge -> chromium). The four")
        lines.append("  affected plugins (target, amazon_monitor, bestbuy_invites,")
        lines.append("  costco_tracker) ALL use vanilla playwright + headless and have no")
        lines.append("  PerimeterX requirement, so they don't need patchright - the channel")
        lines.append("  switch alone fixes them.")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to: {REPORT_PATH}")


# -- Main ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose chrome-headless-shell ICU bug across plugins.",
    )
    parser.add_argument("--scan-only", action="store_true",
                        help="Only scan codebase, skip launch tests")
    args = parser.parse_args()

    print(f"\n{'=' * 80}")
    print(" CHROME-HEADLESS-SHELL ICU BUG DIAGNOSTIC")
    print(f" Project root: {ROOT}")
    print(f" Started: {datetime.datetime.now().isoformat(timespec='seconds')}")
    print("=" * 80)

    # Phase 1: codebase scan
    print("\nPhase 1: scanning codebase for launch_persistent_context calls...")
    scan = scan_codebase()
    print(f"  Total files: {len(scan['affected']) + len(scan['safe'])}")
    print(f"  AFFECTED ({len(scan['affected'])}) - no channel= override:")
    for a in scan['affected']:
        ptag = " [patchright]" if a['uses_patchright'] else ""
        print(f"    - {a['file']}{ptag}")
    print(f"  SAFE ({len(scan['safe'])}) - has channel= override:")
    for s in scan['safe']:
        ptag = " [patchright]" if s['uses_patchright'] else ""
        print(f"    - {s['file']}{ptag}")

    if args.scan_only:
        # Write minimal report
        write_report(scan, [])
        return 0

    # Phase 2: launch tests
    print("\nPhase 2: running launch tests (~3-4 min total)...")
    print("Each cell gets its own profile dir so they don't interfere.")
    print()

    results = []
    for i, cell in enumerate(TEST_CELLS, 1):
        print(f"[{i}/{len(TEST_CELLS)}] {cell['name']}...")
        result = run_test_cell(cell)
        merged = {**cell, **result}
        results.append(merged)
        marker = {
            "PASS": "OK ",
            "FAIL_ICU": "ICU",
            "FAIL_OTHER": "ERR",
            "FAIL_NAV": "NAV",
            "FAIL_HARNESS": "HRN",
            "SKIP_IMPORT": "SKP",
            "SKIP_NOTINSTALLED": "NIN",
        }.get(result["status"], "???")
        print(f"     [{marker}] {result['status']} ({result['duration_sec']:.1f}s)")
        if result["error_msg"]:
            print(f"          {result['error_msg'][:75]}")

    # Phase 3: report
    print_table(results)
    write_report(scan, results)

    # Cleanup diag profiles
    try:
        shutil.rmtree(DIAG_PROFILE_BASE)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
