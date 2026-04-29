#!/usr/bin/env python3
"""
tools/kill_chromium_zombies.py
==============================

Proactive cleanup for orphan chromium processes left behind by tracker
plugin timeouts (v6.1.5).

Why this exists
---------------
Some plugins (notably bestbuy_batch) run Playwright in a daemon thread
with a wall-clock timeout. When the timeout fires, `Thread.join(timeout=N)`
stops waiting but does NOT kill the underlying thread. The daemon
thread keeps running with chromium still launched, holding the profile
dir lock. The next cycle's chromium launch collides with the zombie
and fails with `Settings version is not 1`.

What this tool does
-------------------
At tracker.bat startup (BEFORE warm preflight + tracker.py launch),
this tool scans all chromium processes whose command line references
one of our isolated profile dirs. Found zombies are terminated.

Excluded from cleanup:
  - 'default' and 'walmart' (alias BROWSER_PROFILE - may hold
    legitimate live user sessions opened by open_browser)
  - The user's real Chrome browser (different user-data-dir entirely)
  - Target's persistent session (only matters mid-tracker-run; this
    tool runs at startup when tracker isn't yet alive, so any
    target-profile chromium IS a zombie too)

Tracker isn't running yet at this point in tracker.bat, so any chromium
process matching our profile paths must be a leftover from a previous
session.

USAGE
-----
  py -3.14 tools/kill_chromium_zombies.py
  py -3.14 tools/kill_chromium_zombies.py --dry-run
  py -3.14 tools/kill_chromium_zombies.py --verbose

Exit codes
----------
  0 = clean (no zombies, or all killed successfully)
  1 = at least one chromium process couldn't be terminated

PRECONDITIONS
-------------
  - Windows (uses WMI via PowerShell or wmic)
  - shared.py importable, BROWSER_PROFILES dict (v6.1.3 step 1)
  - tracker.py NOT running (this is a startup tool)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time


# ============================================================================
# COLORS (ANSI - cmd.exe in modern Windows supports these)
# ============================================================================

class C:
    OK     = "\033[92m"
    WARN   = "\033[93m"
    FAIL   = "\033[91m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    END    = "\033[0m"


def info(msg):  print(f"{C.BOLD}[*]{C.END} {msg}")
def good(msg):  print(f"{C.OK}[+]{C.END} {msg}")
def warn(msg):  print(f"{C.WARN}[!]{C.END} {msg}")
def fail(msg):  print(f"{C.FAIL}[X]{C.END} {msg}")


# ============================================================================
# CONFIG
# ============================================================================

# Profile dirs to scan for. Built dynamically from shared.BROWSER_PROFILES.
# Excluded keys: 'default' and 'walmart' (both alias BROWSER_PROFILE).
EXCLUDED_KEYS = ("default", "walmart")


# ============================================================================
# PROFILE DIRECTORIES
# ============================================================================

def get_isolated_profile_paths():
    """Return list of (key, abs_path) for profile dirs to scan.

    Excludes BROWSER_PROFILE aliases (default + walmart).
    Returns [] on failure (shared module not importable, etc.).
    """
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        import shared
    except ImportError as e:
        warn(f"Cannot import shared module: {e}")
        return []

    if not hasattr(shared, "BROWSER_PROFILES") or not hasattr(shared, "BROWSER_PROFILE"):
        warn("BROWSER_PROFILES dict not found in shared.py")
        return []

    base_norm = os.path.normcase(os.path.normpath(shared.BROWSER_PROFILE))
    isolated = []
    for key, path in shared.BROWSER_PROFILES.items():
        if key in EXCLUDED_KEYS:
            continue
        path_norm = os.path.normcase(os.path.normpath(path))
        if path_norm == base_norm:
            # Defensive: extra safety against future aliasing
            continue
        isolated.append((key, path))
    return isolated


# ============================================================================
# WMI PROCESS QUERY
# ============================================================================

def query_chromium_processes(verbose=False):
    """Return list of (pid, command_line) for all chrome.exe processes.

    Uses PowerShell + Get-CimInstance for reliable command-line capture.
    Falls back to wmic if PowerShell isn't available (rare on modern
    Windows but possible).
    """
    # PowerShell is preferred — it handles long command lines reliably.
    ps_cmd = (
        "Get-CimInstance Win32_Process "
        "-Filter \"Name='chrome.exe'\" "
        "| Select-Object ProcessId, CommandLine "
        "| ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            if verbose:
                warn(f"PowerShell query failed (exit {r.returncode}): "
                     f"{(r.stderr or '').strip()[:200]}")
            return []

        results = []
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            pid_str, _, cmdline = line.partition("|")
            try:
                pid = int(pid_str.strip())
            except ValueError:
                continue
            results.append((pid, cmdline))
        return results
    except subprocess.TimeoutExpired:
        warn("PowerShell process query timed out")
        return []
    except FileNotFoundError:
        warn("PowerShell not found - cannot scan processes")
        return []
    except Exception as e:
        warn(f"Process query error: {e}")
        return []


# ============================================================================
# ZOMBIE DETECTION
# ============================================================================

def find_zombies(profile_paths, verbose=False):
    """Return list of (pid, key, cmdline_preview) for chromium zombies.

    A chromium process is a zombie if its command line contains the
    absolute path of one of our isolated profile dirs.
    """
    if not profile_paths:
        return []

    all_chromes = query_chromium_processes(verbose=verbose)
    if verbose:
        info(f"Total chrome.exe processes found: {len(all_chromes)}")

    # Pre-compute lowercase profile paths for case-insensitive matching
    # (Windows paths are case-insensitive)
    profile_lc = [(key, path.lower()) for key, path in profile_paths]

    zombies = []
    for pid, cmdline in all_chromes:
        cmdline_lc = cmdline.lower()
        for key, path_lc in profile_lc:
            if path_lc in cmdline_lc:
                preview = cmdline[:100] + "..." if len(cmdline) > 100 else cmdline
                zombies.append((pid, key, preview))
                break  # one match is enough
    return zombies


def count_processes_using_profile(profile_path):
    """Return number of chrome.exe processes whose command line references
    `profile_path`. Used by check_bestbuy_batch as a runtime liveness
    probe (v6.1.6 replacement for the broken SingletonLock check).

    Returns 0 if scan fails (defensive - a runtime probe failure should
    NOT block the cycle).

    The returned count includes ALL chromium child processes (renderer,
    gpu-process, utility, crashpad-handler, etc.) attached to the
    profile. A typical live session is ~6-8 processes; a zombie cluster
    is similar. Any non-zero count means we should NOT launch a new
    chromium against that profile.
    """
    if not profile_path:
        return 0
    try:
        all_chromes = query_chromium_processes(verbose=False)
        path_lc = profile_path.lower()
        count = 0
        for pid, cmdline in all_chromes:
            if path_lc in cmdline.lower():
                count += 1
        return count
    except Exception:
        # Defensive: probe failure should not block tracker
        return 0


# ============================================================================
# KILL
# ============================================================================

def kill_pid(pid, verbose=False):
    """Kill a single PID. Returns True on success or already-gone, False on error."""
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
        # Exit codes:
        #   0  - killed successfully
        #   128 - process not found (already dead) - benign
        if r.returncode == 0:
            return True
        if r.returncode == 128:
            if verbose:
                info(f"  PID {pid}: already gone")
            return True
        if verbose:
            warn(f"  PID {pid}: taskkill exit {r.returncode} - "
                 f"{(r.stderr or r.stdout).strip()[:100]}")
        return False
    except subprocess.TimeoutExpired:
        warn(f"  PID {pid}: taskkill timed out")
        return False
    except Exception as e:
        warn(f"  PID {pid}: {e}")
        return False


# ============================================================================
# SWEEP (v6.1.7 - public function for runtime cleanup from tracker.py)
# ============================================================================

def sweep_zombies_all_profiles(
    cycle_count=0,
    threshold=3,
    bestbuy_batch_key="bestbuy_batch",
):
    """Sweep all isolated profiles, kill orphan chromium, log structured.

    v6.1.7 Option A: called periodically from tracker.py's
    check_bestbuy_batch._run() to clear zombies that v6.1.6's probe
    was just detecting. Without this, bestbuy_batch goes dormant
    after cycle 1.

    Reuses existing helpers (get_isolated_profile_paths, find_zombies,
    kill_pid) - no duplication.

    Visibility safeguards (v6.1.7 visibility design):
      Layer 1 - per-kill INFO log
      Layer 2 - sweep summary INFO log (always emitted)
      Layer 3 - threshold WARNING when non-bestbuy_batch profile
                accumulates threshold+ zombies in one sweep

    Args:
      cycle_count: caller-provided context for log readability
      threshold: zombie count on a non-bestbuy_batch profile that
                 triggers a WARNING (default 3)
      bestbuy_batch_key: profile key that is EXPECTED to accumulate
                         zombies (the known v6.1.6 issue) and thus
                         NOT subject to the threshold WARNING

    Returns:
      dict[profile_key, int_killed] - one entry per scanned profile,
      INCLUDING profiles with 0 kills, so caller can see which are clean.
    """
    import logging
    logger = logging.getLogger("tracker")

    profile_paths = get_isolated_profile_paths()
    if not profile_paths:
        logger.debug("[zombie_sweep] no isolated profile paths - skip")
        return {}

    zombies = find_zombies(profile_paths, verbose=False)

    # Bucket zombie PIDs by profile key
    pids_by_key = {key: [] for key, _ in profile_paths}
    for pid, key, _preview in zombies:
        if key in pids_by_key:
            pids_by_key[key].append(pid)

    # Layer 1: kill each, log per-kill
    killed_by_key = {key: 0 for key in pids_by_key}
    for key, pids in pids_by_key.items():
        for pid in pids:
            if kill_pid(pid, verbose=False):
                killed_by_key[key] += 1
                logger.info(
                    f"[zombie_sweep] killed pid={pid} profile={key}"
                )
            else:
                logger.warning(
                    f"[zombie_sweep] failed to kill pid={pid} profile={key}"
                )

    # Layer 2: summary line (always emitted, even if all clean)
    clean = sum(1 for v in killed_by_key.values() if v == 0)
    dirty = sum(1 for v in killed_by_key.values() if v > 0)
    totals_str = ", ".join(
        f"{k}={v}" for k, v in sorted(killed_by_key.items())
    )
    logger.info(
        f"[zombie_sweep] cycle={cycle_count} totals: {totals_str} "
        f"({clean} clean, {dirty} dirty)"
    )

    # Layer 3: threshold WARNING for non-bestbuy_batch profiles only.
    # bestbuy_batch is EXPECTED to accumulate zombies (the known
    # v6.1.6 issue we are sweeping); accumulation in OTHER profiles
    # signals a new hang somewhere and warrants attention.
    for key, count in killed_by_key.items():
        if key == bestbuy_batch_key:
            continue
        if count >= threshold:
            logger.warning(
                f"[zombie_sweep] WARNING: {key} accumulated {count} "
                f"zombies in single sweep - possible new hang"
            )

    return killed_by_key


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Kill orphan chromium processes left behind by tracker plugins (v6.1.5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show zombies but do not kill them",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print detailed status",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress all output unless something is killed or fails. "
             "Used by tracker.bat preflight.",
    )
    args = parser.parse_args()

    profile_paths = get_isolated_profile_paths()
    if not profile_paths:
        if not args.quiet:
            warn("No isolated profile paths found - skipping zombie scan")
        return 0

    if args.verbose:
        info(f"Scanning for zombies attached to {len(profile_paths)} profile(s):")
        for key, path in profile_paths:
            info(f"  {key:20s} -> {path}")

    zombies = find_zombies(profile_paths, verbose=args.verbose)

    if not zombies:
        if not args.quiet:
            good(f"[zombie-check] no orphan chromium processes "
                 f"({len(profile_paths)} profile(s) scanned)")
        return 0

    # Found zombies - print a one-line summary even in --quiet mode
    print(f"{C.WARN}[zombie-check]{C.END} found {len(zombies)} orphan chromium "
          f"process(es) from previous run(s):")
    if args.verbose or len(zombies) <= 12:
        for pid, key, preview in zombies:
            print(f"  PID {pid:6d}  ({key})")

    if args.dry_run:
        print(f"{C.WARN}[zombie-check]{C.END} --dry-run: not killing.")
        return 0

    # Kill them
    failed = []
    for pid, key, preview in zombies:
        if not kill_pid(pid, verbose=args.verbose):
            failed.append(pid)

    # Brief settle period - chromium child cleanup
    time.sleep(0.5)

    if failed:
        fail(f"[zombie-check] could not kill {len(failed)} process(es): "
             f"{', '.join(str(p) for p in failed)}")
        return 1

    good(f"[zombie-check] killed {len(zombies)} orphan chromium process(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
