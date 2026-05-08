#!/usr/bin/env python3
"""
tools/audit_hygiene.py

Project-wide hygiene audit. Catches the kind of silent drift that
accumulates between patches and is invisible to per-patch verify()
hooks (which only look at the patched file).

Run weekly, or after every 3-5 patches, or whenever something feels
"off" about the project tree. Read-only by design \u2014 reports findings,
does not modify or delete.

CHECKS PERFORMED
----------------
1. Tracked files matching forbidden patterns (*.log, *.bak, _*.txt,
   *.tmp). These should NEVER be tracked \u2014 security + cruft risk.

2. .bak files anywhere in the working tree. Apply scripts' --finalize
   should have removed these. Surviving .bak files mean either a patch
   wasn't finalized, or pre-convention manual backups were left behind.

3. Root layout convention. PK convention says root contains only:
     tracker.py, tracker.bat, shared.py, plugins.py, scheduler.py,
     README.md, requirements.txt, BACKLOG.md, .gitignore, products_backup.txt
   Anything else tracked at root is convention drift.

4. PRODUCTS retailer count consistency. Counts entries in tracker.py
   PRODUCTS by retailer and compares against PROJECT_KNOWLEDGE.txt's
   architecture cycle table (if PK is present).

5. products_backup.txt freshness. Flags if more than 14 days have
   passed since "Last updated" in the file's header.

6. Gitignored cruft accumulation at root (debug dumps, stale runtime
   JSONs, orphan logs). Not a fail \u2014 just a count, with a hint to
   run cleanup_local_cruft.py if things have accumulated.

EXIT CODES
----------
0 = hygiene clean
1 = one or more findings present

USAGE
-----
    py -3.14 tools/audit_hygiene.py
    py -3.14 tools/audit_hygiene.py --quiet      # only print findings
    py -3.14 tools/audit_hygiene.py --no-color   # plain text output
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple

MIN_PYTHON_VERSION = (3, 14)
if sys.version_info[:2] < MIN_PYTHON_VERSION:
    print(f"ERROR: requires Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+; "
          f"got {sys.version_info[0]}.{sys.version_info[1]}")
    sys.exit(1)

# Resolve project root regardless of where the script is invoked from.
# This file lives in tools/, so root is one level up.
_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "tools" else _HERE


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Files allowed at project root per PK convention. Anything else tracked at
# root is convention drift.
ALLOWED_ROOT_FILES = {
    "tracker.py",
    "tracker.bat",
    "shared.py",
    "plugins.py",
    "scheduler.py",
    "README.md",
    "requirements.txt",
    "BACKLOG.md",
    ".gitignore",
    "products_backup.txt",
    # PK is gitignored by convention but lives at root
    "PROJECT_KNOWLEDGE.txt",
}

# File patterns that should never be tracked (security + cruft).
# Tested as glob-equivalents against tracked-file list.
FORBIDDEN_TRACKED_PATTERNS = [
    (re.compile(r"\.log$"),                 "log file (security \u2014 may contain runtime data)"),
    (re.compile(r"\.bak$"),                 "backup file (cruft \u2014 should be cleaned by --finalize)"),
    (re.compile(r"^_.*\.txt$"),             "debug dump (cruft)"),
    (re.compile(r"\.tmp$"),                 "temp file"),
]

# Cruft accumulation patterns at root (gitignored, not failures, just
# monitored). High counts here suggest cleanup_local_cruft.py should run.
CRUFT_ROOT_PATTERNS = [
    re.compile(r"^_.*\.txt$"),               # debug dumps
    re.compile(r"^.*\.bak$"),                # backup files
    re.compile(r"^.*\.bak_.*$"),             # apply-script-style .bak
    re.compile(r"^crashes\.txt$"),
    re.compile(r"^sweep_.*\.txt$"),
    re.compile(r"^actual_block\.txt$"),
    re.compile(r"^bb_productive\.txt$"),
    re.compile(r"^interval_state\.txt$"),
]

# Maximum age (days) for products_backup.txt before staleness is flagged.
PRODUCTS_BACKUP_MAX_AGE_DAYS = 14


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

class Out:
    def __init__(self, color: bool = True, quiet: bool = False):
        self.color = color
        self.quiet = quiet
        self.findings = 0

    def _wrap(self, code: str, s: str) -> str:
        if not self.color:
            return s
        return f"\033[{code}m{s}\033[0m"

    def green(self, s: str) -> str:  return self._wrap("32", s)
    def red(self, s: str) -> str:    return self._wrap("31", s)
    def yellow(self, s: str) -> str: return self._wrap("33", s)
    def bold(self, s: str) -> str:   return self._wrap("1", s)

    def section(self, n: int, title: str) -> None:
        if not self.quiet:
            print(f"\n[{n}] {self.bold(title)}")

    def ok(self, msg: str) -> None:
        if not self.quiet:
            print(f"    {self.green('OK')} \u2014 {msg}")

    def fail(self, msg: str, items: Optional[List[str]] = None) -> None:
        self.findings += 1
        print(f"    {self.red('FAIL')} \u2014 {msg}")
        if items:
            for item in items:
                print(f"      {item}")

    def warn(self, msg: str, items: Optional[List[str]] = None) -> None:
        self.findings += 1
        print(f"    {self.yellow('WARN')} \u2014 {msg}")
        if items:
            for item in items:
                print(f"      {item}")

    def info(self, msg: str) -> None:
        if not self.quiet:
            print(f"    {msg}")


# ---------------------------------------------------------------------------
# git helpers (best-effort \u2014 script still works without git)
# ---------------------------------------------------------------------------

def _git_ls_files() -> Optional[List[str]]:
    try:
        rc = subprocess.run(
            ["git", "ls-files"], capture_output=True, cwd=ROOT, text=True
        )
    except FileNotFoundError:
        return None
    if rc.returncode != 0:
        return None
    return [ln.strip().replace("\\", "/") for ln in rc.stdout.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Check 1: tracked files matching forbidden patterns
# ---------------------------------------------------------------------------

def check_forbidden_tracked(out: Out) -> None:
    out.section(1, "Tracked files matching forbidden patterns")
    tracked = _git_ls_files()
    if tracked is None:
        out.info("git not available \u2014 skipping")
        return

    findings = []
    for path in tracked:
        basename = os.path.basename(path)
        for pat, why in FORBIDDEN_TRACKED_PATTERNS:
            if pat.search(basename):
                findings.append(f"{path}  ({why})")

    if findings:
        out.fail(f"{len(findings)} tracked file(s) match forbidden patterns:", findings)
    else:
        out.ok("no tracked .log / .bak / _*.txt files")


# ---------------------------------------------------------------------------
# Check 2: .bak files anywhere in working tree
# ---------------------------------------------------------------------------

def check_bak_residue(out: Out) -> None:
    out.section(2, ".bak files (residual pre-finalize artifacts)")
    bak_files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # Don't recurse into hidden or vendored dirs
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in ("venv", "node_modules", "__pycache__")]
        for f in filenames:
            if ".bak" in f:
                rel = os.path.relpath(os.path.join(dirpath, f), ROOT)
                bak_files.append(rel.replace("\\", "/"))

    if bak_files:
        out.fail(f"{len(bak_files)} .bak file(s); should be cleaned by --finalize:",
                 sorted(bak_files))
    else:
        out.ok("no .bak files anywhere in working tree")


# ---------------------------------------------------------------------------
# Check 3: root layout convention
# ---------------------------------------------------------------------------

def check_root_layout(out: Out) -> None:
    out.section(3, "Root layout convention")
    tracked = _git_ls_files()
    if tracked is None:
        out.info("git not available \u2014 skipping")
        return

    drift = []
    for path in tracked:
        if "/" in path or "\\" in path:
            continue  # not at root
        if path not in ALLOWED_ROOT_FILES:
            drift.append(path)

    if drift:
        out.fail(
            f"{len(drift)} tracked file(s) at root not in allowed list "
            f"(consider moving to tools/ or removing):",
            drift,
        )
    else:
        out.ok("only allowed files tracked at root")


# ---------------------------------------------------------------------------
# Check 4: PRODUCTS retailer counts (tracker.py vs PK)
# ---------------------------------------------------------------------------

def _extract_products_block(src: str) -> Optional[str]:
    m = re.search(r"^PRODUCTS\s*=\s*(\[.*?^\])", src, re.M | re.S)
    return m.group(1) if m else None


def _strip_inline_comments(s: str) -> str:
    """Strip # comments outside strings, line by line. Naive but adequate
    for the PRODUCTS literal which has no fancy nested quotes."""
    out_lines = []
    for line in s.splitlines():
        in_str = False
        quote = None
        for i, c in enumerate(line):
            if c in ('"', "'"):
                if not in_str:
                    in_str, quote = True, c
                elif c == quote and (i == 0 or line[i-1] != "\\"):
                    in_str = False
            if c == "#" and not in_str:
                line = line[:i].rstrip()
                break
        out_lines.append(line)
    return "\n".join(out_lines)


def check_products_counts(out: Out) -> None:
    out.section(4, "PRODUCTS retailer count consistency")
    tracker_py = os.path.join(ROOT, "tracker.py")
    if not os.path.exists(tracker_py):
        out.info("tracker.py not found \u2014 skipping")
        return

    with open(tracker_py, "r", encoding="utf-8") as f:
        src = f.read()
    block = _extract_products_block(src)
    if block is None:
        out.fail("PRODUCTS = [...] block not found in tracker.py")
        return

    try:
        parsed = ast.literal_eval(_strip_inline_comments(block))
    except Exception as e:
        out.fail(f"PRODUCTS block did not parse: {e}")
        return

    counts = {}
    for entry in parsed:
        r = entry.get("retailer", "?")
        counts[r] = counts.get(r, 0) + 1
    total = sum(counts.values())

    out.info(f"tracker.py PRODUCTS counts: {dict(sorted(counts.items()))}")
    out.info(f"total: {total}")

    # Cross-reference with PROJECT_KNOWLEDGE.txt cycle table if present
    pk = os.path.join(ROOT, "PROJECT_KNOWLEDGE.txt")
    if not os.path.exists(pk):
        out.info("PROJECT_KNOWLEDGE.txt not present \u2014 skipping cross-reference")
        return

    with open(pk, "r", encoding="utf-8") as f:
        pk_src = f.read()

    # Parse cycle table lines like "Target (21)         \u2192 check_target_batch ..."
    cycle_rx = re.compile(
        r"(Pokemon Center|Best Buy|Target|Walmart)\s*\((\d+)\)",
        re.IGNORECASE,
    )
    pk_counts = {}
    for m in cycle_rx.finditer(pk_src):
        retailer_label = m.group(1).strip().lower()
        # Map PK labels to tracker.py retailer keys
        key_map = {
            "pokemon center": "pokemoncenter",
            "best buy":       "bestbuy",
            "target":         "target",
            "walmart":        "walmart",
        }
        key = key_map.get(retailer_label)
        if key:
            pk_counts[key] = int(m.group(2))

    if not pk_counts:
        out.info("PK cycle table not parseable \u2014 skipping cross-reference")
        return

    drift = []
    for retailer, pk_n in pk_counts.items():
        actual = counts.get(retailer, 0)
        if actual != pk_n:
            drift.append(f"{retailer}: PK says {pk_n}, tracker.py has {actual}")

    if drift:
        out.fail("PK / tracker.py retailer count mismatch:", drift)
    else:
        out.ok(f"PK declarations match tracker.py for {len(pk_counts)} retailer(s)")


# ---------------------------------------------------------------------------
# Check 5: products_backup.txt freshness
# ---------------------------------------------------------------------------

def check_backup_freshness(out: Out) -> None:
    out.section(5, "products_backup.txt freshness")
    path = os.path.join(ROOT, "products_backup.txt")
    if not os.path.exists(path):
        out.fail("products_backup.txt missing")
        return

    with open(path, "r", encoding="utf-8") as f:
        head = f.read(2048)

    m = re.search(r"Last updated:\s*(\w+\s+\d+,\s*\d{4})", head)
    if not m:
        out.warn("could not parse 'Last updated:' line in header")
        return

    try:
        last = dt.datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        out.warn(f"could not parse date: {m.group(1)!r}")
        return

    age_days = (dt.date.today() - last).days
    if age_days > PRODUCTS_BACKUP_MAX_AGE_DAYS:
        out.warn(f"backup is {age_days} days old (threshold: {PRODUCTS_BACKUP_MAX_AGE_DAYS}); "
                 f"consider regenerating from tracker.py")
    else:
        out.ok(f"last updated {last} ({age_days} days ago)")


# ---------------------------------------------------------------------------
# Check 6: cruft accumulation (informational)
# ---------------------------------------------------------------------------

def check_cruft(out: Out) -> None:
    out.section(6, "Gitignored cruft accumulation (informational)")
    try:
        root_files = [f for f in os.listdir(ROOT)
                      if os.path.isfile(os.path.join(ROOT, f))]
    except OSError as e:
        out.info(f"could not list root: {e}")
        return

    cruft = []
    for f in root_files:
        for pat in CRUFT_ROOT_PATTERNS:
            if pat.match(f):
                cruft.append(f)
                break

    if cruft:
        out.warn(f"{len(cruft)} cruft-pattern file(s) at root; "
                 f"consider running cleanup_local_cruft.py:",
                 sorted(cruft))
    else:
        out.ok("no debug-dump or .bak files at root")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    check_forbidden_tracked,
    check_bak_residue,
    check_root_layout,
    check_products_counts,
    check_backup_freshness,
    check_cruft,
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Project hygiene audit")
    ap.add_argument("--quiet", action="store_true",
                    help="only print findings (no OK lines)")
    ap.add_argument("--no-color", action="store_true",
                    help="plain text output (force ANSI off)")
    ap.add_argument("--color", action="store_true",
                    help="force ANSI color on (overrides auto-disable)")
    args = ap.parse_args()

    # Auto-disable ANSI on legacy Windows cmd.exe where escape codes render
    # as literal text. Windows Terminal sets WT_SESSION; modern PowerShell
    # 7+ also handles ANSI. --color forces on, --no-color forces off.
    if args.color:
        use_color = True
    elif args.no_color:
        use_color = False
    elif os.name == "nt" and "WT_SESSION" not in os.environ:
        use_color = False
    else:
        use_color = sys.stdout.isatty()

    out = Out(color=use_color, quiet=args.quiet)

    if not args.quiet:
        bar = "\u2550" * 63
        print(f"\u2550{bar}\u2550")
        print(f"  {out.bold('PROJECT HYGIENE AUDIT')}")
        print(f"  Root: {ROOT}")
        print(f"  Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"\u2550{bar}\u2550")

    for check in ALL_CHECKS:
        try:
            check(out)
        except Exception as e:
            out.fail(f"check {check.__name__} crashed: {type(e).__name__}: {e}")

    if not args.quiet:
        bar = "\u2550" * 63
        print(f"\n\u2550{bar}\u2550")
        if out.findings == 0:
            print(f"  {out.green('SUMMARY:')} hygiene clean (0 findings)")
        else:
            print(f"  {out.red('SUMMARY:')} {out.findings} finding(s) require attention")
        print(f"\u2550{bar}\u2550\n")

    return 0 if out.findings == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
