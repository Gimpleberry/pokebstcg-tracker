#!/usr/bin/env python3
"""
tools/warm_browser_profiles.py
==============================

One-time profile pre-warming tool for the v6.1.x lock-contention fix.

Why this exists
---------------
v6.1.3 step 2 migrated each headless-scrape plugin to its own dedicated
profile dir. Profile dirs were created lazily on first chromium launch,
which caused a boot-time storm: 4 plugins simultaneously initializing
fresh chromium profiles overwhelmed Windows IPC infrastructure (Crashpad
handlers, Mojo brokers, Defender scanning), hanging Target's existing
session.

This tool launches chromium SEQUENTIALLY against each isolated profile
dir, with a delay between launches to let Windows fully clean up. After
running once, every profile dir has its `Default/`, `Cache/`, etc.
subtrees pre-built. Subsequent runtime chromium launches against those
dirs are fast — no first-init storm.

What it does
------------
For each key in BROWSER_PROFILES that's NOT an alias to BROWSER_PROFILE
(i.e., the isolated plugin profiles: target, amazon, bestbuy_batch,
bestbuy_invites, costco):
  1. Check if the profile dir already has a Default/ subdir
     - If yes and --force not passed: skip
     - Else: proceed
  2. Launch chromium against the profile dir using launch_chromium_with_fallback
  3. Open a blank page, close it, close the context
  4. Sleep 3 seconds (let Windows clean up Crashpad / file handles)
  5. Move to next profile

USAGE
-----
  py -3.14 tools/warm_browser_profiles.py              # warm only un-warmed profiles
  py -3.14 tools/warm_browser_profiles.py --force      # re-warm all profiles
  py -3.14 tools/warm_browser_profiles.py --dry-run    # show plan, do nothing
  py -3.14 tools/warm_browser_profiles.py --verbose    # show chromium chatter

EXIT CODES
----------
  0 = all profiles warmed (or already warm) successfully
  1 = at least one profile failed to warm

PRECONDITIONS
-------------
  - Python 3.14
  - patchright/playwright importable
  - shared.py has BROWSER_PROFILES dict (v6.1.3 step 1 applied)
  - shared.py has launch_chromium_with_fallback (v6.1.2 step 1 applied)

OPERATIONAL NOTES
-----------------
  - Idempotent. Safe to re-run anytime.
  - Tool does NOT modify any tracker source files.
  - Tool does NOT run during normal tracker.bat startup unless v6.1.4 step 2
    has been applied (which adds an auto-invoke check to tracker.bat).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback


# Ensure project root is on the path so we can `import shared`
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "tools" else _HERE
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================================
# CONFIGURATION
# ============================================================================

# Sleep between profile warmings - lets Windows clean up Crashpad/file handles
# Tuned conservatively. Can be lowered if subsequent QC shows it's safe.
SLEEP_BETWEEN_PROFILES_SEC = 3.0

# What we consider "already warm": presence of a Default/ subdir, which
# chromium creates on first launch and populates with state files.
WARM_MARKER_SUBDIR = "Default"

# Args passed to chromium during warming. Minimal - just the headless basics.
# We deliberately do NOT pass anti-detection flags - this is profile init,
# not real scraping. Plain vanilla chromium is faster to spin up.
WARM_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


# ============================================================================
# COLORS (Windows console ANSI - works on Win10+ default cmd)
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
def step(msg):  print(f"\n{C.BOLD}=== {msg} ==={C.END}")


# ============================================================================
# CORE LOGIC
# ============================================================================

def get_isolated_profile_keys(browser_profiles: dict, browser_profile: str) -> list[str]:
    """Return profile keys whose path is NOT BROWSER_PROFILE itself.

    Aliases (default, walmart) point to BROWSER_PROFILE which is the
    pre-warmed PerimeterX-trusted profile - no need to warm it.
    """
    isolated = []
    for key, path in browser_profiles.items():
        if path != browser_profile:
            isolated.append(key)
    return isolated


def is_profile_warm(profile_dir: str) -> bool:
    """A profile is 'warm' if its Default/ subdir exists.

    Chromium creates Default/ on first launch and populates it with
    state files (Cookies, History, Local State, etc). If it doesn't
    exist, the profile is fresh and the next launch will trigger
    full first-init.
    """
    return os.path.isdir(os.path.join(profile_dir, WARM_MARKER_SUBDIR))


def warm_one_profile(key: str, profile_dir: str, verbose: bool = False) -> bool:
    """Launch chromium against profile_dir, open about:blank, close cleanly.

    Returns True on success, False on any failure.
    """
    try:
        from shared import launch_chromium_with_fallback
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        fail(f"  Cannot import required modules: {e}")
        return False

    os.makedirs(profile_dir, exist_ok=True)

    try:
        with sync_playwright() as p:
            ctx = launch_chromium_with_fallback(
                p,
                profile_dir,
                headless=True,
                args=WARM_LAUNCH_ARGS,
                log_prefix=f"warm_{key}",
            )
            try:
                page = ctx.new_page()
                page.goto("about:blank", timeout=15000)
                page.close()
            finally:
                ctx.close()
        return True
    except Exception as e:
        fail(f"  Launch failed for {key}: {type(e).__name__}: {e}")
        if verbose:
            traceback.print_exc()
        return False


def warm_all(force: bool = False, dry_run: bool = False, verbose: bool = False) -> int:
    """Main entry. Returns process exit code (0 = success)."""

    step("PRECONDITIONS")

    # Detect prerequisites
    try:
        import shared
    except ImportError as e:
        fail(f"Cannot import shared module: {e}")
        fail(f"Run from project root or check PYTHONPATH.")
        return 1
    good(f"shared module importable from {os.path.dirname(shared.__file__)}")

    if not hasattr(shared, "BROWSER_PROFILES"):
        fail("shared.BROWSER_PROFILES not found - apply v6.1.3 step 1 first")
        return 1
    good(f"BROWSER_PROFILES dict found ({len(shared.BROWSER_PROFILES)} entries)")

    if not hasattr(shared, "BROWSER_PROFILE"):
        fail("shared.BROWSER_PROFILE not found - shared.py looks broken")
        return 1
    good(f"BROWSER_PROFILE base path: {shared.BROWSER_PROFILE}")

    if not hasattr(shared, "launch_chromium_with_fallback"):
        fail("shared.launch_chromium_with_fallback not found - apply v6.1.2 step 1 first")
        return 1
    good("launch_chromium_with_fallback helper available")

    # Probe playwright
    try:
        import playwright  # noqa: F401
    except ImportError:
        fail("playwright/patchright not importable")
        return 1
    good("playwright module importable")

    # ---- Identify which profiles need warming ----

    step("PLAN")

    isolated_keys = get_isolated_profile_keys(
        shared.BROWSER_PROFILES, shared.BROWSER_PROFILE
    )

    if not isolated_keys:
        warn("No isolated profile keys found in BROWSER_PROFILES.")
        warn("(All keys alias BROWSER_PROFILE - nothing to warm.)")
        return 0

    info(f"Found {len(isolated_keys)} isolated profile(s) in BROWSER_PROFILES:")
    plan = []
    for key in isolated_keys:
        path = shared.BROWSER_PROFILES[key]
        already_warm = is_profile_warm(path)

        if already_warm and not force:
            status = "skip (already warm)"
            should_warm = False
        elif already_warm and force:
            status = "re-warm (--force)"
            should_warm = True
        else:
            status = "warm (fresh)"
            should_warm = True

        info(f"  {key:18s} -> {status}")
        info(f"  {'':18s}    {path}")
        plan.append((key, path, should_warm))

    to_warm = sum(1 for _, _, w in plan if w)
    to_skip = len(plan) - to_warm

    info(f"Summary: {to_warm} to warm, {to_skip} to skip")

    if dry_run:
        good("\n--dry-run: stopping here. No chromium launches.")
        return 0

    if to_warm == 0:
        good("\nAll profiles already warm. Nothing to do.")
        return 0

    # ---- Sequential warming ----

    step("WARMING")

    succeeded = 0
    failed = 0
    skipped = 0

    for i, (key, path, should_warm) in enumerate(plan, 1):
        if not should_warm:
            info(f"[{i}/{len(plan)}] {key}: skip (already warm)")
            skipped += 1
            continue

        info(f"[{i}/{len(plan)}] {key}: warming...")
        t0 = time.time()
        ok = warm_one_profile(key, path, verbose=verbose)
        elapsed = time.time() - t0

        if ok:
            good(f"  {key}: ready ({elapsed:.1f}s)")
            succeeded += 1
        else:
            fail(f"  {key}: FAILED ({elapsed:.1f}s)")
            failed += 1

        # Always sleep between launches (except after last) to let
        # Windows clean up. Even on failure - especially on failure -
        # because failed launches may leave Crashpad processes lingering.
        is_last = (i == len(plan))
        if not is_last:
            time.sleep(SLEEP_BETWEEN_PROFILES_SEC)

    # ---- Summary ----

    step("RESULT")

    info(f"  succeeded: {succeeded}")
    info(f"  failed:    {failed}")
    info(f"  skipped:   {skipped}")

    if failed == 0:
        good(f"\nAll {succeeded + skipped}/{len(plan)} profiles ready.")
        return 0
    else:
        fail(f"\n{failed} profile(s) failed to warm.")
        warn("Tracker may still hit boot-time storm on next start.")
        warn("Re-run with --verbose for tracebacks, or --force to retry.")
        return 1


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Pre-warm per-plugin chromium profile dirs to avoid "
                    "boot-time launch storm (v6.1.4)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-warm profiles even if they appear already warm",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be warmed but launch no chromium",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print full tracebacks on failure",
    )
    parser.add_argument(
        "--check-or-warm", action="store_true",
        help="Quick check: if all profiles warm, exit silently (0); "
             "otherwise warm what's missing. For tracker.bat preflight.",
    )
    args = parser.parse_args()

    # --check-or-warm fast-path: silent exit if all profiles already warm.
    # This is what tracker.bat invokes as a preflight - normally a no-op
    # in the millisecond range. If anything is missing, fall through to
    # the normal warm_all() flow with full chatter.
    if args.check_or_warm:
        try:
            import shared
        except ImportError as e:
            print(f"[warm-check] ERROR: cannot import shared: {e}", file=sys.stderr)
            return 1
        if not (hasattr(shared, "BROWSER_PROFILES") and hasattr(shared, "BROWSER_PROFILE")):
            print("[warm-check] ERROR: BROWSER_PROFILES not in shared.py "
                  "- apply v6.1.3 step 1 first", file=sys.stderr)
            return 1
        isolated = get_isolated_profile_keys(
            shared.BROWSER_PROFILES, shared.BROWSER_PROFILE,
        )
        all_warm = all(
            is_profile_warm(shared.BROWSER_PROFILES[k]) for k in isolated
        )
        if all_warm:
            print(f"[warm-check] all profiles ready ({len(isolated)} checked)")
            return 0
        # Something is missing - fall through with normal output to surface
        # the work being done.

    print(f"\n{C.BOLD}warm_browser_profiles.py{C.END} (v6.1.4)")
    if args.force:
        print(f"  Mode: FORCE (re-warm all)")
    elif args.dry_run:
        print(f"  Mode: DRY-RUN")
    elif args.check_or_warm:
        print(f"  Mode: CHECK-OR-WARM (warming missing profiles)")
    else:
        print(f"  Mode: warm un-warmed profiles only")
    print()

    return warm_all(
        force=args.force,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
