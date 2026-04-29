#!/usr/bin/env python3
"""
tools/extract_callsites_v6_1_2.py - Extract exact bytes of each
                                    launch_persistent_context call
                                    for v6.1.2 step 2 patch authoring.

Why this exists
---------------
v6.1.2 step 2 will refactor 7 callsites across 6 files to use the new
launch_chromium_with_fallback() helper. Each refactor is a surgical
str_replace, which means the apply script needs the EXACT bytes of
each launch call as its 'find' anchor.

Rather than guess at whitespace, indentation, and arg ordering across
files I haven't fully seen, this tool reads each affected file and
prints the exact bytes of every launch_persistent_context call it finds.
You paste the output back to Claude, and Claude writes step 2 with
zero anchor-mismatch risk.

What it does
------------
For each file in AFFECTED_FILES:
  1. Reads the file as bytes (preserves CRLF/LF)
  2. Finds every occurrence of `launch_persistent_context`
  3. For each, walks backward to the start of the assignment statement
     and forward to the matching closing paren
  4. Prints the exact bytes of that statement, with a header showing
     the file path, call number, and line number

Usage
-----
    py -3.14 tools/extract_callsites_v6_1_2.py

Or save to a file for easier copy-paste:
    py -3.14 tools/extract_callsites_v6_1_2.py > callsites_v6_1_2.txt

Then paste the output (or the file contents) back into the conversation.

This tool is DIAGNOSTIC ONLY. It writes nothing. Safe to run anytime.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files affected by the chrome-headless-shell ICU bug (per diag_icu_bug.py)
AFFECTED_FILES = [
    "tracker.py",
    "shared.py",
    "plugins/amazon_monitor.py",
    "plugins/bestbuy_invites.py",
    "plugins/cart_preloader.py",
    "plugins/costco_tracker.py",
]

PATTERN = "launch_persistent_context"


def find_all_offsets(content: str, pattern: str):
    """Return all character positions where `pattern` starts in `content`."""
    offsets = []
    i = 0
    while True:
        i = content.find(pattern, i)
        if i < 0:
            break
        offsets.append(i)
        i += len(pattern)
    return offsets


def find_statement_start(content: str, call_offset: int) -> int:
    """Walk backward from `call_offset` to find the start of the
    enclosing assignment statement.

    Logic:
      - Step 1: find the start of the line containing call_offset
      - Step 2: if the line starts with whitespace and the previous
        line ends with `\\` (continuation) or with `(` `[` or `,`
        (multi-line call), include earlier lines too
      - Stop when we find an assignment, function call, or open paren
        that starts the statement.
    For our use case, the call always sits on a single line that
    starts with `<varname> = ...launch_persistent_context(`, so we
    simply find the start of THAT line.
    """
    line_start = content.rfind("\n", 0, call_offset) + 1
    return line_start


def find_matching_close_paren(content: str, open_paren_offset: int) -> int:
    """Walk forward from the `(` after `launch_persistent_context` to
    find the matching `)`. Returns position AFTER the closing paren.

    Naive scan — doesn't handle string literals containing parens.
    For our actual launch_persistent_context calls there are no such
    embedded parens, so this is safe.
    """
    depth = 1
    pos = open_paren_offset + 1
    while pos < len(content) and depth > 0:
        c = content[pos]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        pos += 1
    return pos


def extract_one_call(content: str, call_offset: int) -> tuple[int, int, str]:
    """Given the offset where `launch_persistent_context` starts,
    return (statement_start, statement_end, statement_bytes)."""
    stmt_start = find_statement_start(content, call_offset)
    open_paren = content.find("(", call_offset)
    if open_paren < 0:
        return (stmt_start, call_offset + len(PATTERN),
                content[stmt_start:call_offset + len(PATTERN)])
    stmt_end = find_matching_close_paren(content, open_paren)
    return (stmt_start, stmt_end, content[stmt_start:stmt_end])


def line_number_at(content: str, offset: int) -> int:
    """1-indexed line number of `offset` in `content`."""
    return content.count("\n", 0, offset) + 1


def find_enclosing_function(content: str, offset: int) -> str | None:
    """Walk backward from `offset` to find the enclosing `def NAME(...)`.
    Returns the function name or None."""
    # Find all `def ` occurrences before offset
    pos = offset
    while True:
        pos = content.rfind("\ndef ", 0, pos)
        if pos < 0:
            # Check if file starts with `def `
            if content.startswith("def "):
                pos = 0
            else:
                return None
        if pos == 0 or content[pos] == "\n":
            # Found a def. Extract name.
            actual_def_start = pos + 1 if content[pos] == "\n" else pos
            name_start = actual_def_start + len("def ")
            name_end = content.find("(", name_start)
            if name_end > 0:
                return content[name_start:name_end].strip()
            return None
        pos -= 1


def find_imports_from_shared(content: str) -> list[tuple[int, int, str]]:
    """Find any `from shared import ...` lines.
    Returns list of (line_start, line_end, line_text)."""
    results = []
    for line_no_idx, line in enumerate(content.split("\n"), start=1):
        if line.lstrip().startswith("from shared import"):
            # Find this line's char offset
            line_start = 0
            for _ in range(line_no_idx - 1):
                line_start = content.find("\n", line_start) + 1
            line_end = content.find("\n", line_start)
            if line_end < 0:
                line_end = len(content)
            results.append((line_start, line_end, content[line_start:line_end]))
    return results


def main():
    print("=" * 80)
    print(" v6.1.2 step 2 - callsite extraction")
    print(f" Project root: {ROOT}")
    print(f" Pattern: {PATTERN}")
    print("=" * 80)
    print()
    print("Paste this entire output back to Claude. Each callsite block below")
    print("contains the exact bytes Claude will use as a str_replace anchor.")
    print()
    print("-" * 80)

    total_calls = 0

    for rel_path in AFFECTED_FILES:
        path = ROOT / rel_path
        if not path.is_file():
            print(f"\n## {rel_path}: FILE NOT FOUND")
            continue

        raw = path.read_bytes()
        if b"\r\n" in raw:
            content = raw.replace(b"\r\n", b"\n").decode("utf-8")
            line_endings = "CRLF"
        else:
            content = raw.decode("utf-8")
            line_endings = "LF"

        offsets = find_all_offsets(content, PATTERN)

        # Locate any existing `from shared import ...` lines
        shared_imports = find_imports_from_shared(content)

        print(f"\n## {rel_path}")
        print(f"   line_endings: {line_endings}")
        print(f"   total {PATTERN} calls: {len(offsets)}")

        if shared_imports:
            print(f"   existing 'from shared import' lines:")
            for line_start, _, line_text in shared_imports:
                line_no = line_number_at(content, line_start)
                print(f"     line {line_no}: {line_text!r}")
        else:
            print(f"   (no 'from shared import' lines)")

        if not offsets:
            print(f"   (no calls found)")
            continue

        for i, off in enumerate(offsets, start=1):
            stmt_start, stmt_end, stmt_bytes = extract_one_call(content, off)
            line_no = line_number_at(content, stmt_start)
            fn_name = find_enclosing_function(content, off)

            print()
            print(f"### {rel_path} :: call #{i} :: line {line_no} "
                  f":: enclosing fn: {fn_name or '<module-level>'}")
            print("```python")
            print(stmt_bytes)
            print("```")
            total_calls += 1

    print()
    print("-" * 80)
    print(f"# Total: {total_calls} call(s) across {len(AFFECTED_FILES)} file(s)")
    print("-" * 80)


if __name__ == "__main__":
    main()
