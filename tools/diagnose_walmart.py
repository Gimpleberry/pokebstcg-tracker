#!/usr/bin/env python3
"""
tools/diagnose_walmart.py - Walmart API diagnostic (v6.1 step 1)

WHY THIS SCRIPT EXISTS:
  As of late Apr 2026, every Walmart product check in tracker.py returns
  404 from walmart.com/product/v2/pdpData?itemId=<id>. Walmart appears
  to have changed something about their public product API. Before we
  can write a fix, we need to know WHAT changed.

  This script does NOT fix anything. It probes Walmart with five
  carefully-targeted requests and writes a report to stdout (and
  optionally to data/walmart_diagnostic.txt) so we can pick a
  remediation strategy with evidence.

WHAT IT PROBES:
  1. Current broken endpoint (baseline 404 confirmation)
  2. Canonical product page HTML (does the page still load? does it
     embed inventory JSON in a <script> tag like other modern e-commerce?)
  3. Walmart GraphQL endpoint (orchestra/v1/...)
  4. Walmart REST v3 endpoint
  5. Original endpoint + browser-like headers (in case it's a header filter)

USAGE:
    python tools/diagnose_walmart.py
    python tools/diagnose_walmart.py --report data/walmart_diagnostic.txt

SAFETY:
  - 5 sequential requests, ~3 seconds apart  (15 seconds total)
  - Read-only - no POSTs, no mutations
  - Truncates response bodies to 500 chars in the report
  - Never logs any auth headers (we don't send any)
  - Standard polite browser-like User-Agent
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any


# ─── Probe definitions ─────────────────────────────────────────────────────────
# A small, well-known item ID. We use a single item for all probes so we get
# comparable signals. Picked one of the user's actual tracked products
# (Pokemon SV9 Journey Together ETB on Walmart) since we know it currently 404s.
PROBE_ITEM_ID = "15156564532"

# Browser-like headers. Walmart's anti-bot stack is fingerprint-aware so we
# present a complete plausible browser identity. NOT a perfect impersonation
# (we don't run JS, no cookies) but a baseline that rules out trivial UA blocks.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",  # don't request gzip - keeps body readable
    "DNT": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Older minimal headers - what the tracker uses now. If THIS works in this
# script but fails in tracker.py, the issue is something else in the request
# building pipeline (cookies, timing, etc.).
MINIMAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible)",
    "Accept": "application/json",
}

PROBES = [
    {
        "name":    "1. Current broken endpoint (baseline)",
        "url":     f"https://www.walmart.com/product/v2/pdpData?itemId={PROBE_ITEM_ID}",
        "headers": MINIMAL_HEADERS,
        "what_it_means": (
            "200 = endpoint actually works and bug is elsewhere | "
            "404 = endpoint is dead, baseline confirmed | "
            "403 = anti-bot block on this UA"
        ),
    },
    {
        "name":    "2. Product page HTML (current behavior of live site)",
        "url":     f"https://www.walmart.com/ip/{PROBE_ITEM_ID}",
        "headers": BROWSER_HEADERS,
        "what_it_means": (
            "200 + product HTML = page works, inventory likely in <script> JSON | "
            "200 + captcha page = anti-bot block | "
            "404 = product genuinely doesn't exist | "
            "5xx = Walmart server-side issue"
        ),
    },
    {
        "name":    "3. Walmart GraphQL (orchestra) endpoint",
        "url":     "https://www.walmart.com/orchestra/home/graphql",
        "headers": BROWSER_HEADERS,
        # No body — we just want to see the response shape on a bare GET
        "what_it_means": (
            "405 Method Not Allowed = endpoint exists but needs POST (good!) | "
            "404 = no GraphQL at this path | "
            "200 = unexpected, inspect body"
        ),
    },
    {
        "name":    "4. Walmart REST v3 candidate path",
        "url":     f"https://www.walmart.com/api/v3/items/{PROBE_ITEM_ID}",
        "headers": BROWSER_HEADERS,
        "what_it_means": (
            "200 + JSON = found new endpoint, big win | "
            "404 = path doesn't exist | "
            "401 = exists but needs auth"
        ),
    },
    {
        "name":    "5. Original endpoint with browser-like headers",
        "url":     f"https://www.walmart.com/product/v2/pdpData?itemId={PROBE_ITEM_ID}",
        "headers": BROWSER_HEADERS,
        "what_it_means": (
            "200 = simple header fix unblocks tracker | "
            "404 = endpoint genuinely dead, header isn't the issue | "
            "403 = anti-bot block beyond header level (cookies, fingerprint)"
        ),
    },
]


# ─── Probe execution ───────────────────────────────────────────────────────────

def probe(name: str, url: str, headers: dict, timeout: float = 15.0) -> dict:
    """Send a single GET request and return a structured result."""
    started = time.time()
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = time.time() - started
            body_bytes = resp.read(8192)  # at most 8KB
            try:
                body_text = body_bytes.decode("utf-8", errors="replace")
            except Exception:
                body_text = repr(body_bytes[:500])
            return {
                "name":         name,
                "url":          url,
                "ok":           True,
                "status":       resp.status,
                "elapsed_ms":   int(elapsed * 1000),
                "content_type": resp.headers.get("Content-Type", ""),
                "body_excerpt": body_text[:500],
                "body_length":  len(body_bytes),
                "fingerprints": _fingerprint(body_text),
                "error":        None,
            }
    except urllib.error.HTTPError as e:
        elapsed = time.time() - started
        try:
            body_bytes = e.read(8192)
            body_text  = body_bytes.decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        return {
            "name":         name,
            "url":          url,
            "ok":           False,
            "status":       e.code,
            "elapsed_ms":   int(elapsed * 1000),
            "content_type": e.headers.get("Content-Type", "") if e.headers else "",
            "body_excerpt": body_text[:500],
            "body_length":  len(body_text) if body_text else 0,
            "fingerprints": _fingerprint(body_text),
            "error":        f"HTTPError {e.code}: {e.reason}",
        }
    except Exception as e:
        elapsed = time.time() - started
        return {
            "name":         name,
            "url":          url,
            "ok":           False,
            "status":       0,
            "elapsed_ms":   int(elapsed * 1000),
            "content_type": "",
            "body_excerpt": "",
            "body_length":  0,
            "fingerprints": [],
            "error":        f"{type(e).__name__}: {e}",
        }


def _fingerprint(body: str) -> list[str]:
    """Look for telltale strings that hint at what the response actually is."""
    if not body:
        return []
    found = []
    body_lower = body.lower()
    # Anti-bot / blocked-page indicators
    if "captcha" in body_lower or "are you a robot" in body_lower:
        found.append("CAPTCHA_PAGE")
    if "access denied" in body_lower or "blocked" in body_lower and "robot" in body_lower:
        found.append("ACCESS_DENIED_PAGE")
    if "perimeterx" in body_lower or "px-captcha" in body_lower:
        found.append("PERIMETER_X_BLOCK")
    if "akamai" in body_lower and ("blocked" in body_lower or "denied" in body_lower):
        found.append("AKAMAI_BLOCK")
    # Useful structural indicators
    if "__next_data__" in body or "__NEXT_DATA__" in body:
        found.append("NEXTJS_EMBEDDED_DATA")
    if '"availabilityStatus"' in body or '"inStock"' in body:
        found.append("HAS_AVAILABILITY_FIELD")
    if '"price":' in body or '"currentPrice":' in body:
        found.append("HAS_PRICE_FIELD")
    if "graphql" in body_lower:
        found.append("MENTIONS_GRAPHQL")
    if "<title" in body_lower:
        m = re.search(r"<title[^>]*>([^<]{1,100})</title>", body, flags=re.IGNORECASE)
        if m:
            found.append(f"PAGE_TITLE: {m.group(1).strip()[:80]!r}")
    # JSON shape hints
    if body.lstrip().startswith("{") and '"errors"' in body:
        found.append("JSON_ERROR_RESPONSE")
    if body.lstrip().startswith("{") and '"data"' in body:
        found.append("JSON_DATA_RESPONSE")
    return found


# ─── Report generation ─────────────────────────────────────────────────────────

def render_report(results: list[dict]) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append(" WALMART API DIAGNOSTIC")
    lines.append(f" Run: {datetime.now().isoformat()}")
    lines.append(f" Probe item ID: {PROBE_ITEM_ID}")
    lines.append("=" * 78)
    lines.append("")

    for r in results:
        lines.append(f"{r['name']}")
        lines.append(f"  URL:         {r['url']}")
        if r["error"]:
            lines.append(f"  Result:      ERROR - {r['error']}")
        else:
            lines.append(f"  Result:      HTTP {r['status']} in {r['elapsed_ms']}ms")
        lines.append(f"  Content-Type: {r['content_type'] or '(none)'}")
        lines.append(f"  Body length: {r['body_length']} bytes")
        if r["fingerprints"]:
            lines.append(f"  Fingerprints: {', '.join(r['fingerprints'])}")
        else:
            lines.append(f"  Fingerprints: (none detected)")
        if r["body_excerpt"]:
            excerpt = r["body_excerpt"].replace("\n", " ")[:300]
            lines.append(f"  Body excerpt: {excerpt!r}")
        lines.append("")

    lines.append("─" * 78)
    lines.append(" READING THE REPORT")
    lines.append("─" * 78)
    lines.append("")
    lines.append("The hypothesis Walmart took (one of these is now most likely):")
    lines.append("")
    lines.append("  A. Endpoint just moved")
    lines.append("     SIGNAL: Probe 4 returns 200 with JSON | Probe 1 returns 404")
    lines.append("     FIX:    Update tracker.py URL to whatever responded with 200")
    lines.append("     EFFORT: Low (~30 min)")
    lines.append("")
    lines.append("  B. Inventory now embedded in HTML page")
    lines.append("     SIGNAL: Probe 2 returns 200 with NEXTJS_EMBEDDED_DATA fingerprint")
    lines.append("             AND has HAS_AVAILABILITY_FIELD or HAS_PRICE_FIELD")
    lines.append("     FIX:    Switch Walmart check from API to HTML+regex/JSON-extract")
    lines.append("             (similar pattern to costco_tracker)")
    lines.append("     EFFORT: Medium (1-2 hours)")
    lines.append("")
    lines.append("  C. Walmart switched to GraphQL")
    lines.append("     SIGNAL: Probe 3 returns 405 Method Not Allowed (endpoint exists,")
    lines.append("             needs POST) OR returns JSON_DATA_RESPONSE")
    lines.append("     FIX:    Rewrite Walmart request layer to send GraphQL POST queries")
    lines.append("     EFFORT: Medium (2-3 hours)")
    lines.append("")
    lines.append("  D. Anti-bot block")
    lines.append("     SIGNAL: ANY probe returns CAPTCHA_PAGE, ACCESS_DENIED_PAGE,")
    lines.append("             PERIMETER_X_BLOCK, or AKAMAI_BLOCK fingerprint")
    lines.append("     FIX:    Switch to Playwright (like bestbuy_invites). Significant")
    lines.append("             rewrite, but unblocks the entire retailer cleanly.")
    lines.append("     EFFORT: High (4-6 hours)")
    lines.append("")
    lines.append("  E. Header fix is enough")
    lines.append("     SIGNAL: Probe 5 returns 200 (browser-headers + same URL works)")
    lines.append("             where Probe 1 returned 404")
    lines.append("     FIX:    Add the BROWSER_HEADERS dict to shared.py and use it for")
    lines.append("             Walmart requests. Trivial change.")
    lines.append("     EFFORT: Low (~15 min)")
    lines.append("")
    lines.append("  F. Inconclusive")
    lines.append("     SIGNAL: All probes 404 with no useful fingerprints")
    lines.append("     NEXT:   Add more probes (different endpoints, with cookies, etc.)")
    lines.append("             OR open a real browser DevTools session and watch the")
    lines.append("             network tab when loading a Walmart product page")
    lines.append("     EFFORT: Variable")
    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines)


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Walmart API diagnostic")
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Path to write report to (in addition to stdout)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds between probes (default 3, be polite)",
    )
    args = parser.parse_args()

    print(f"Running {len(PROBES)} probes against walmart.com...")
    print(f"(probe item ID: {PROBE_ITEM_ID}, delay between probes: {args.delay}s)")
    print()

    results = []
    for i, p in enumerate(PROBES, start=1):
        print(f"  [{i}/{len(PROBES)}] {p['name']}", flush=True)
        result = probe(p["name"], p["url"], p["headers"])
        if result["error"]:
            print(f"        -> {result['error']}")
        else:
            print(f"        -> HTTP {result['status']} in {result['elapsed_ms']}ms"
                  f" ({result['body_length']} bytes)"
                  + (f" [{', '.join(result['fingerprints'])}]"
                     if result['fingerprints'] else ""))
        results.append(result)
        if i < len(PROBES):
            time.sleep(args.delay)

    print()
    report = render_report(results)
    print(report)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nReport also written to: {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
