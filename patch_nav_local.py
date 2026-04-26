#!/usr/bin/env python3
"""
patch_nav_local.py
Adds the Local page nav link to all dashboard HTML files.

Run from your tcg_tracker/ root:
    python patch_nav_local.py

Or from anywhere with the path argument:
    python patch_nav_local.py path/to/dashboard/
"""

import sys
import os

DASHBOARD_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard"
)

LOCAL_LINK = '<a class="nav-link" href="local.html">🗺️ Local</a>'

# Each entry: (filename, find_string, insert_after_this_link)
# We insert LOCAL_LINK immediately after the retail-drops.html link in each file.
PATCHES = [
    # dashboard.html — insert before Price History
    (
        "dashboard.html",
        '<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>',
        '<a class="nav-link" href="pricing-history.html">Price History</a>',
    ),
    # info.html — insert before Help
    (
        "info.html",
        '<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>',
        '<a class="nav-link" href="help.html">❓ Help</a>',
    ),
    # pricing.html — insert before Help
    (
        "pricing.html",
        '<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>',
        '<a class="nav-link" href="help.html">❓ Help</a>',
    ),
    # binder.html — insert before Help
    (
        "binder.html",
        '<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>',
        '<a class="nav-link" href="help.html">❓ Help</a>',
    ),
    # future.html — insert after retail-drops (Help may be missing in this nav)
    (
        "future.html",
        '<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>',
        None,  # append immediately after anchor
    ),
    # retail-drops.html — active link, insert before Help
    (
        "retail-drops.html",
        '<a class="nav-link active" href="retail-drops.html">🛒 Retail Drops</a>',
        '<a class="nav-link" href="help.html">❓ Help</a>',
    ),
    # pricing-history.html — insert before active Price History
    (
        "pricing-history.html",
        '<a class="nav-link" href="retail-drops.html">Retail Drops</a>',
        '<a class="nav-link active" href="pricing-history.html">Price History</a>',
    ),
    # help.html — insert before Price History (also fix malformed <a <a tag)
    (
        "help.html",
        None,  # special case — handled below
        None,
    ),
]

LOCAL_LINK_NL = f'\n  {LOCAL_LINK}'

results = []

for entry in PATCHES:
    fname, anchor, before = entry
    fpath = os.path.join(DASHBOARD_DIR, fname)

    if not os.path.exists(fpath):
        results.append(f"  SKIP   {fname} — file not found at {fpath}")
        continue

    with open(fpath, encoding="utf-8") as f:
        content = f.read()

    # Skip if already patched
    if 'href="local.html"' in content:
        results.append(f"  SKIP   {fname} — already has local.html link")
        continue

    original = content

    if fname == "help.html":
        # Fix the known malformed nav ('<a <a') and insert local link
        content = content.replace(
            '<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>\n  <a <a class="nav-link" href="pricing-history.html">Price History</a>',
            f'<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>\n  {LOCAL_LINK}\n  <a class="nav-link" href="pricing-history.html">Price History</a>',
        )
        if content == original:
            # Try without the malformed tag (already fixed)
            content = content.replace(
                '<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>',
                f'<a class="nav-link" href="retail-drops.html">🛒 Retail Drops</a>\n  {LOCAL_LINK}',
                1,
            )

    elif before is None:
        # Simple append after anchor
        content = content.replace(anchor, f"{anchor}{LOCAL_LINK_NL}", 1)

    else:
        # Insert local link between anchor and before
        old_seq = f"{anchor}\n  {before}"
        new_seq = f"{anchor}\n  {LOCAL_LINK}\n  {before}"
        if old_seq in content:
            content = content.replace(old_seq, new_seq, 1)
        else:
            # Fallback: just insert after anchor
            content = content.replace(anchor, f"{anchor}{LOCAL_LINK_NL}", 1)

    if content == original:
        results.append(f"  WARN   {fname} — no match found, file unchanged")
    else:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        results.append(f"  PATCHED {fname}")

print("\npatch_nav_local.py results:")
print(f"  Dashboard dir: {DASHBOARD_DIR}\n")
for r in results:
    print(r)
print("\nDone. Reload your browser to see the updated nav.")
