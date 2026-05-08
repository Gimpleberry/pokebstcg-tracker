#!/usr/bin/env python3
"""
audit_v6_1_12_url_coverage.py

READ-ONLY URL audit for v6.1.12 trim decisions.

Reads tracker.py PRODUCTS and data/tcg_tracker.log. Classifies each product
by URL pattern and cross-references with log evidence (price observations
over the past N hours) to identify suspected-broken entries that should be
trimmed in v6.1.12.

Outputs:
  - Console report (categorized by retailer)
  - data/audit_v6_1_12_TIMESTAMP.txt (saved snapshot)

Usage:
    py -3.14 audit_v6_1_12_url_coverage.py
    py -3.14 audit_v6_1_12_url_coverage.py --hours 24       # change log window
    py -3.14 audit_v6_1_12_url_coverage.py --no-save        # console-only

NO modifications to tracker.py or any other file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from collections import defaultdict

# ----------------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------------
MIN_PYTHON_VERSION = (3, 14)
if sys.version_info[:2] < MIN_PYTHON_VERSION:
    print(f"ERROR: requires Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+")
    sys.exit(1)

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "tools" else _HERE
TRACKER = os.path.join(ROOT, "tracker.py")
LOG_FILE = os.path.join(ROOT, "data", "tcg_tracker.log")


# ----------------------------------------------------------------------------
# URL pattern classifiers
# ----------------------------------------------------------------------------
URL_CLASSIFIERS = [
    # (label, regex, status, note)
    ("target_p",       r"^https://www\.target\.com/p/",       "ok",      "Target /p/ - PDP, returns price JSON"),
    ("target_s",       r"^https://www\.target\.com/s/",       "broken",  "Target /s/ - search page, no price JSON"),
    ("target_other",   r"^https://www\.target\.com/",         "review",  "Target other path - needs review"),
    ("walmart_ip",     r"^https://www\.walmart\.com/ip/",     "ok",      "Walmart /ip/ - PDP standard"),
    ("walmart_other",  r"^https://www\.walmart\.com/",        "review",  "Walmart other path - needs review"),
    ("pokemoncenter",  r"^https://www\.pokemoncenter\.com/",  "ok",      "Pokemon Center"),
    ("bestbuy_site",   r"^https://www\.bestbuy\.com/site/",   "review",  "BB /site/ - 2 known Akamai-blocked"),
    ("bestbuy_other",  r"^https://www\.bestbuy\.com/",        "review",  "BB other path - needs review"),
    ("amazon",         r"^https://www\.amazon\.com/",         "ok",      "Amazon"),
    ("costco",         r"^https://www\.costco\.com/",         "ok",      "Costco"),
]


def classify_url(url: str) -> tuple[str, str, str]:
    """Return (label, status, note) for a URL."""
    for label, pattern, status, note in URL_CLASSIFIERS:
        if re.match(pattern, url):
            return label, status, note
    return ("unknown", "review", "Unknown URL pattern")


# ----------------------------------------------------------------------------
# tracker.py parsing
# ----------------------------------------------------------------------------
def parse_products(tracker_src: str) -> list[dict]:
    """
    Parse PRODUCTS list from tracker.py source.
    Returns list of dicts with keys: name, retailer, url, sku/item_id, section.
    """
    products = []
    block_match = re.search(r"^PRODUCTS\s*=\s*\[(.*?)^\]", tracker_src, re.M | re.S)
    if not block_match:
        return products
    block = block_match.group(1)

    # Track section comments to assign each entry to a logical group
    current_section = "pre-v6.1.8"
    pos = 0
    while pos < len(block):
        # Find next entry "{" or section comment "# ===... v6.1.8 ..."
        section_match = re.compile(r"#\s*===.*?v6\.1\.\d+.*?(TARGET|WALMART|TRIM)?", re.I)
        entry_match = re.compile(r"\{([^{}]*?)\}", re.S)

        s_match = section_match.search(block, pos)
        e_match = entry_match.search(block, pos)

        if e_match is None:
            break

        # If a section header appears before the next entry, update current_section
        if s_match and s_match.start() < e_match.start():
            line = block[s_match.start():block.find("\n", s_match.start())]
            if "v6.1.8" in line and "TARGET" in line.upper():
                current_section = "v6.1.8 TARGET"
            elif "v6.1.8" in line and "WALMART" in line.upper():
                current_section = "v6.1.8 WALMART"
            elif "v6.1.11" in line:
                current_section = "v6.1.11 TRIM"
            elif "v6.1.8" in line:
                current_section = "v6.1.8"
            pos = s_match.end()
            continue

        body = e_match.group(1)
        prod = {"section": current_section, "raw": body.strip()}

        for field in ("name", "retailer", "url", "sku", "item_id"):
            m = re.search(
                rf'"{field}"\s*:\s*"([^"]*)"',
                body,
            )
            prod[field] = m.group(1) if m else ""

        if prod.get("name") and prod.get("url"):
            label, status, note = classify_url(prod["url"])
            prod["url_label"] = label
            prod["url_status"] = status
            prod["url_note"] = note
            products.append(prod)

        pos = e_match.end()

    return products


# ----------------------------------------------------------------------------
# Log analysis - count price observations per product over last N hours
# ----------------------------------------------------------------------------
PRICE_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+\[INFO\].*?"
    r"(?:->|:)\s+(?:✅|❌|.+?)\s*(?:IN STOCK|out of stock|Out of stock).*?\|\s*(?P<price>\S+)",
    re.I,
)


def analyze_log(log_path: str, hours: int) -> dict:
    """
    Count price observations per product over last `hours`.
    Returns: {(retailer, name): {"total": N, "with_price": M, "n_a": K}}

    Names are derived from the line that *precedes* a "->" stock result
    (those lines say "Checking <name> (<retailer>)..." for non-batch flows).
    For batch lines we get only the result with name in the previous
    "[batch] N/M Pokemon ..." line.

    Simpler signal: per name, count how many times $price appeared vs N/A.
    """
    if not os.path.exists(log_path):
        return {}

    cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
    counts = defaultdict(lambda: {"total": 0, "with_price": 0, "n_a": 0})

    # Two patterns:
    # 1. "Checking NAME (retailer)..." then "  -> result | $price" (sequential PC flow)
    # 2. "[target_batch] N/M NAME" ... later "  -> result | $price" (batch flow)
    # Simpler: track product names from "Checking" or "[*_batch] N/M" lines,
    #          then attribute the next result line to the most recent name.
    # v6.1.13: anchor retailer group to known tokens. The prior
    # `\w+` was over-permissive and could capture any parenthetical
    # word as a retailer (e.g. warehouse codes, queue depths, retry
    # counters), polluting the pending_names FIFO and causing
    # per-cycle off-by-one drift in log-evidence counts.
    name_pattern = re.compile(
        r"Checking\s+(.+?)\s+"
        r"\((pokemoncenter|target|walmart|bestbuy|amazon|costco)\)"
    )
    batch_pattern = re.compile(r"\[(\w+)_batch\]\s+\d+/\d+\s+(.+?)$")
    result_pattern = re.compile(
        r"->\s+(?:✅|❌|.+?)\s*(?:IN STOCK|out of stock|Out of stock).*?\|\s*(\S+)",
        re.I,
    )
    walmart_pattern = re.compile(
        r"\[walmart_playwright\]\s+(.+?):\s+(?:in stock|out of stock).*?\|\s*(\S+)",
        re.I,
    )
    ts_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

    pending_names = []  # FIFO queue of (retailer, name) awaiting result

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ts_m = ts_pattern.match(line)
            if ts_m:
                try:
                    ts = dt.datetime.strptime(ts_m.group(1), "%Y-%m-%d %H:%M:%S")
                    if ts < cutoff:
                        continue
                except ValueError:
                    continue

            wm = walmart_pattern.search(line)
            if wm:
                name, price = wm.group(1).strip(), wm.group(2).strip()
                key = ("walmart", name)
                counts[key]["total"] += 1
                if price == "N/A":
                    counts[key]["n_a"] += 1
                else:
                    counts[key]["with_price"] += 1
                continue

            nm = name_pattern.search(line)
            if nm:
                pending_names.append((nm.group(2).lower(), nm.group(1).strip()))
                continue

            bm = batch_pattern.search(line)
            if bm:
                pending_names.append((bm.group(1).lower(), bm.group(2).strip()))
                continue

            rm = result_pattern.search(line)
            if rm and pending_names:
                price = rm.group(1).strip()
                retailer, name = pending_names.pop(0)
                key = (retailer, name)
                counts[key]["total"] += 1
                if price == "N/A":
                    counts[key]["n_a"] += 1
                else:
                    counts[key]["with_price"] += 1

    return counts


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------
def render_report(products: list[dict], counts: dict, hours: int) -> str:
    """Build the full report as a string."""
    lines = []
    w = lines.append

    w("=" * 70)
    w(f"  URL COVERAGE AUDIT — v6.1.12 candidate identification")
    w(f"  Generated:  {dt.datetime.now().isoformat(timespec='seconds')}")
    w(f"  Log window: last {hours}h ({LOG_FILE})")
    w(f"  Source:     {TRACKER}")
    w("=" * 70)
    w("")

    # Group by retailer
    by_retailer = defaultdict(list)
    for p in products:
        by_retailer[p.get("retailer", "?")].append(p)

    summary = {"ok": 0, "broken": 0, "review": 0}

    for retailer in sorted(by_retailer.keys()):
        retailer_products = by_retailer[retailer]
        w(f"─── {retailer.upper()} ({len(retailer_products)} products) " + "─" * 30)
        w("")

        # Sub-group by URL status
        by_status = defaultdict(list)
        for p in retailer_products:
            by_status[p["url_status"]].append(p)

        for status in ("broken", "review", "ok"):
            entries = by_status.get(status, [])
            if not entries:
                continue

            badge = {"ok": "✓ OK", "broken": "✗ BROKEN", "review": "⚠ REVIEW"}[status]
            w(f"  [{badge}] {len(entries)} entries")

            for p in entries:
                summary[status] += 1
                # Look up log evidence
                key = (retailer.lower(), p["name"])
                evidence = counts.get(key, {"total": 0, "with_price": 0, "n_a": 0})

                ev_str = ""
                if evidence["total"] > 0:
                    pct_price = (evidence["with_price"] / evidence["total"]) * 100
                    if evidence["with_price"] == 0:
                        ev_str = f"  [LOG: {evidence['total']}× checks, 0 with price → suspect]"
                    elif pct_price < 50:
                        ev_str = f"  [LOG: {evidence['with_price']}/{evidence['total']} with price ({pct_price:.0f}%)]"
                    else:
                        ev_str = f"  [LOG: {evidence['with_price']}/{evidence['total']} with price ({pct_price:.0f}%) ✓]"
                else:
                    ev_str = f"  [LOG: no observations in last {hours}h]"

                w(f"    • {p['name']}")
                w(f"        section: {p['section']}")
                w(f"        url:     {p['url']}")
                w(f"        note:    {p['url_note']}")
                w(f"        {ev_str.strip()}")
                w("")

        w("")

    w("=" * 70)
    w("  SUMMARY")
    w("=" * 70)
    w(f"  Total products tracked:      {len(products)}")
    w(f"  ✓ OK (URL pattern verified): {summary['ok']}")
    w(f"  ⚠ REVIEW (manual check):     {summary['review']}")
    w(f"  ✗ BROKEN (URL pattern bad):  {summary['broken']}")
    w("")
    w("  TRIM CANDIDATES for v6.1.12:")
    w("    1. Any [✗ BROKEN] entry — confirmed bad URL pattern")
    w("    2. Any [⚠ REVIEW] entry with [LOG: 0 with price]")
    w("       AND not currently in stock everywhere")
    w("       (N/A could mean either broken URL or genuinely OOS)")
    w("")
    w("  Use the log evidence to break ties:")
    w("    - Many checks, all N/A → URL likely broken")
    w("    - Many checks, mostly $price → URL works (probably just OOS)")
    w("    - Few/no checks → may be unreachable, investigate")
    w("=" * 70)

    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="v6.1.12 URL coverage audit")
    ap.add_argument("--hours", type=int, default=24,
                    help="log lookback window in hours (default 24)")
    ap.add_argument("--no-save", action="store_true",
                    help="don't save report to data/")
    args = ap.parse_args()

    if not os.path.exists(TRACKER):
        print(f"ERROR: tracker.py not found at {TRACKER}")
        sys.exit(1)

    print(f"[1/3] reading {TRACKER}...")
    with open(TRACKER, "r", encoding="utf-8") as f:
        tracker_src = f.read()
    products = parse_products(tracker_src)
    print(f"      parsed {len(products)} products")

    print(f"[2/3] analyzing {LOG_FILE} (last {args.hours}h)...")
    counts = analyze_log(LOG_FILE, args.hours)
    print(f"      indexed {len(counts)} unique product names")

    print(f"[3/3] building report...")
    report = render_report(products, counts, args.hours)
    print("")
    print(report)

    if not args.no_save:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = os.path.join(ROOT, "data")
        os.makedirs(outdir, exist_ok=True)
        outpath = os.path.join(outdir, f"audit_v6_1_12_{ts}.txt")
        with open(outpath, "w", encoding="utf-8", newline="") as f:
            f.write(report)
        print("")
        print(f"Report saved: {outpath}")


if __name__ == "__main__":
    main()
