#!/usr/bin/env python3
"""
fix_encoding.py
Fixes UTF-8 encoding on local.html and patches missing Local nav link
in any dashboard HTML files that still don't have it.

Run from tcg_tracker/ root:
    python fix_encoding.py
"""

import os

DASHBOARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
LOCAL_LINK = '<a class="nav-link" href="local.html">\U0001f5fa\ufe0f Local</a>'

FILES = [
    "local.html",
    "dashboard.html",
    "info.html",
    "pricing.html",
    "binder.html",
    "future.html",
    "retail-drops.html",
    "pricing-history.html",
    "help.html",
]

for fname in FILES:
    fpath = os.path.join(DASHBOARD, fname)
    if not os.path.exists(fpath):
        print(f"  SKIP   {fname} — not found")
        continue

    # Try reading as cp1252 (Windows default), re-save as UTF-8
    try:
        raw = open(fpath, encoding="cp1252").read()
        # Check if it looks corrupted (Windows-1252 read of a UTF-8 file shows these artifacts)
        if "ΓÇö" in raw or "≡ƒ" in raw or "ΓÇ" in raw:
            # It was cp1252-read of UTF-8 — read as latin-1 and decode properly
            raw = open(fpath, encoding="latin-1").read()
            raw = raw.encode("latin-1").decode("utf-8", errors="replace")
            open(fpath, "w", encoding="utf-8").write(raw)
            print(f"  FIXED  {fname} — re-encoded to UTF-8")
        else:
            # Already clean — just ensure UTF-8 save
            open(fpath, "w", encoding="utf-8").write(raw)
            print(f"  OK     {fname} — encoding fine")
    except Exception as e:
        print(f"  ERROR  {fname} — {e}")
        continue

    # Now patch nav if local.html link is missing
    content = open(fpath, encoding="utf-8").read()
    if 'href="local.html"' in content:
        continue  # already has it

    # Insert after retail-drops link
    for anchor in [
        '<a class="nav-link active" href="retail-drops.html">\U0001f6d2 Retail Drops</a>',
        '<a class="nav-link" href="retail-drops.html">\U0001f6d2 Retail Drops</a>',
        '<a class="nav-link" href="retail-drops.html">Retail Drops</a>',
    ]:
        if anchor in content:
            content = content.replace(anchor, anchor + "\n  " + LOCAL_LINK, 1)
            open(fpath, "w", encoding="utf-8").write(content)
            print(f"  PATCHED {fname} — added Local nav link")
            break
    else:
        print(f"  WARN   {fname} — could not find retail-drops anchor to patch")

print("\nDone. Hard refresh your browser (Ctrl+Shift+R).")
