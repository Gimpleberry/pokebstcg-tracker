#!/usr/bin/env python3
"""
patch_nav_v3.py  --  Canonical nav patch (v3 -- adds Invest page)
=================================================================
Canonical nav order:
  Tracker | Set Info | Pricing | Binder | Calendar | Retail Drops |
  Local | Invest | Price History | Help

Run from tcg_tracker/ root:
    python patch_nav_v3.py

Or specify dashboard dir:
    python patch_nav_v3.py path/to/dashboard/
"""

import sys, os, re

DASHBOARD_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard"
)

NAV_LINKS = [
    ("dashboard.html",       "&#128225; Tracker"),
    ("info.html",            "&#128218; Set Info"),
    ("pricing.html",         "&#128176; Pricing"),
    ("binder.html",          "&#128194; Binder"),
    ("calendar.html",        "&#128197; Calendar"),
    ("retail-drops.html",    "&#128722; Retail Drops"),
    ("local.html",           "&#128506; Local"),
    ("invest.html",          "&#128200; Invest"),
    ("pricing-history.html", "Price History"),
    ("help.html",            "&#10067; Help"),
]

def build_nav(active_file):
    lines = ['<nav class="nav-bar">']
    for href, label in NAV_LINKS:
        cls = 'nav-link active' if href == active_file else 'nav-link'
        lines.append(f'  <a class="{cls}" href="{href}">{label}</a>')
    lines.append('</nav>')
    return '\n'.join(lines)

NAV_PATTERN = re.compile(
    r'<nav\s+class=["\']nav-bar["\']>.*?</nav>',
    re.DOTALL | re.IGNORECASE
)

results = []
pages = sorted(f for f in os.listdir(DASHBOARD_DIR) if f.endswith('.html'))

for fname in pages:
    fpath = os.path.join(DASHBOARD_DIR, fname)
    with open(fpath, encoding='utf-8') as f:
        content = f.read()

    original = content
    active   = 'calendar.html' if fname == 'future.html' else fname
    new_nav  = build_nav(active)

    match = NAV_PATTERN.search(content)
    if match:
        content = content[:match.start()] + new_nav + content[match.end():]
    else:
        results.append(f"  WARN    {fname} -- no nav-bar found, skipped")
        continue

    if content == original:
        results.append(f"  OK      {fname} -- already correct")
    else:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
        results.append(f"  PATCHED {fname}")

print("\npatch_nav_v3.py results:")
print(f"  Dashboard dir: {DASHBOARD_DIR}\n")
for r in results:
    print(r)
print("""
Canonical nav (v3):
  Tracker | Set Info | Pricing | Binder | Calendar | Retail Drops | Local | Invest | Price History | Help
""")
