#!/usr/bin/env python3
"""
tools/recon_walmart_playwright.py - Walmart Playwright Reconnaissance (v6.1)
Part of Keith's PokeBS Tracker.

ONE-OFF DIAGNOSTIC. Run before designing plugins/walmart_playwright.py to:
  1. Confirm Playwright + persistent context get through PerimeterX
  2. Identify the Walmart product page structure
  3. Discover reliable selectors for IN_STOCK / Walmart-direct / price

Per the v6.0.0 workflow doc: one-off diagnostic scripts skip workflow
steps 4-7 (no apply script, no test wiring, no rollback machinery).

Self-contained: only depends on Playwright. Does NOT import from shared.py
to avoid pulling in the app's runtime dep chain (e.g. requests). This makes
the recon portable across Python environments.

USAGE:
    python tools/recon_walmart_playwright.py
    python tools/recon_walmart_playwright.py --url https://www.walmart.com/ip/...
    python tools/recon_walmart_playwright.py --url URL1 --url URL2

OUTPUTS (in data/):
    walmart_recon.txt          structured findings - read this!
    walmart_recon_sample.html  full HTML of the first un-blocked probe

REQUIRES (patchright is preferred, playwright+stealth as fallback):
    Patchright (recommended -- deeper anti-detection at browser level):
        py -3.14 -m pip install patchright
        py -3.14 -m patchright install chromium

    OR vanilla Playwright + stealth library:
        py -3.14 -m pip install playwright tf-playwright-stealth
        py -3.14 -m playwright install chromium

    Patchright is tried first; falls back to playwright if unavailable.

EXIT CODES:
    0  All probes ran (regardless of per-probe outcome)
    1  Hard error (Playwright not installed, can't write output, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime


# -- Self-contained config (no shared.py import) -----------------------------

# Resolve project root from script location (works whether invoked from
# project root or from inside tools/).
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tools" else _here

# data/ for outputs - matches the project's runtime-data convention
DATA_DIR = os.path.join(_root, "data")

# Browser profile - matches shared.py's BROWSER_PROFILE location.
# Per PROJECT_KNOWLEDGE.txt v5.8: lives in %LOCALAPPDATA%\tcg_tracker\.
_appdata = os.environ.get("LOCALAPPDATA", "")
if _appdata:
    BROWSER_PROFILE = os.path.join(_appdata, "tcg_tracker", "browser_profile")
else:
    # Fallback for non-Windows (recon should still work on any platform)
    BROWSER_PROFILE = os.path.join(_root, ".browser_profile")

# A modern Chrome UA. Walmart's anti-bot inspects UA so a stale UA can
# tip them off. Mirrors what shared.py uses as of v6.0.0.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# -- Defaults ----------------------------------------------------------------

# Two tracked-product URLs from products_backup.txt. The recon learns page
# structure, so the in-stock state of these doesn't matter for the structural
# findings - though if one happens to be in stock, the DOM diff is gold.
DEFAULT_URLS = [
    "https://www.walmart.com/ip/Pokemon-TCG-Mega-Evolution-Chaos-Rising-Elite-Trainer-Box/19939024731",
    "https://www.walmart.com/ip/Pokemon-Journey-Together-SV09-Elite-Trainer-Box/15156564532",
]

PERIMETERX_FINGERPRINTS = [
    "Robot or human",
    "Press & Hold",
    "press and hold",
    "_pxhd",
    "px-captcha",
    "perimeterx",
    "blocked by px",
]

OUTPUT_REPORT = os.path.join(DATA_DIR, "walmart_recon.txt")
OUTPUT_HTML   = os.path.join(DATA_DIR, "walmart_recon_sample.html")


# -- Stealth -----------------------------------------------------------------

def _apply_stealth(page) -> str:
    """
    Apply stealth evasions to a Playwright page.

    Tries tf-playwright-stealth (more current, actively maintained) first,
    then falls back to the original playwright-stealth. Returns the name
    of the package that worked. Raises ImportError if neither is installed.

    Stealth patches typically include:
      - Removes navigator.webdriver
      - Spoofs navigator.plugins / languages / permissions
      - Hides Chrome runtime / WebGL automation fingerprints
      - Patches a dozen-plus other automation tells PerimeterX inspects
    """
    try:
        from tf_playwright_stealth import stealth_sync  # type: ignore
        stealth_sync(page)
        return "tf-playwright-stealth"
    except ImportError:
        pass
    try:
        from playwright_stealth import stealth_sync  # type: ignore
        stealth_sync(page)
        return "playwright-stealth"
    except ImportError:
        pass
    raise ImportError(
        "No stealth library installed. Install one with:\n"
        "    py -3.14 -m pip install tf-playwright-stealth\n"
        "or fallback:\n"
        "    py -3.14 -m pip install playwright-stealth"
    )


# -- Probe -------------------------------------------------------------------

def probe_url(page, url: str) -> dict:
    """
    Navigate to a Walmart product URL and gather signals.
    Returns a dict with all observations. Never raises.
    """
    obs = {
        "url": url,
        "ok": False,
        "http_status": None,
        "title": None,
        "page_size": 0,
        "blocked_by_perimeterx": False,
        "perimeterx_signals": [],
        "has_next_data": False,
        "next_data_size": 0,
        "next_data_keys": [],
        "next_data_excerpt": None,
        "next_data_product_keys": [],
        "atc_selectors_matching": [],
        "oos_text_present": False,
        "marketplace_text_present": False,
        "walmart_direct_text_present": False,
        "extracted_price_via_attr": None,
        "extracted_price_via_itemprop": None,
        "extracted_price_via_regex": None,
        "errors": [],
    }

    try:
        # Navigate with a generous timeout - we're probing, not racing
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if response is not None:
            obs["http_status"] = response.status

        obs["title"] = page.title()
        content = page.content()
        obs["page_size"] = len(content)
        lc_content = content.lower()

        # __NEXT_DATA__ extraction (most reliable signal source for modern Walmart)
        nd_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            content,
            re.DOTALL,
        )
        if nd_match:
            obs["has_next_data"] = True
            nd_raw = nd_match.group(1)
            obs["next_data_size"] = len(nd_raw)
            try:
                nd_json = json.loads(nd_raw)
                obs["next_data_keys"] = list(nd_json.keys())[:10]
                # Walmart historically nests product data at:
                # props.pageProps.initialData.data.product
                initial_data = (
                    nd_json
                    .get("props", {})
                    .get("pageProps", {})
                    .get("initialData", {})
                    .get("data", {})
                )
                if initial_data:
                    obs["next_data_excerpt"] = json.dumps(
                        {k: type(v).__name__ for k, v in initial_data.items()},
                        indent=2,
                    )[:1000]
                    product_node = initial_data.get("product", {})
                    if isinstance(product_node, dict):
                        obs["next_data_product_keys"] = list(product_node.keys())[:25]
            except (json.JSONDecodeError, AttributeError) as e:
                obs["errors"].append(f"next_data parse: {e}")

        # PerimeterX detection - HIGH-PRECISION signals only.
        # Previous version did naive substring matching on "perimeterx" /
        # "_pxhd" which gave false positives because legitimate Walmart
        # pages embed PerimeterX tracking code in normal page furniture.
        # Two precise rules:
        #   1. Page title is a known challenge title
        #   2. Page is suspiciously thin AND lacks __NEXT_DATA__
        # Real Walmart product pages are 200-500KB with __NEXT_DATA__.
        # Challenge pages are ~15KB without __NEXT_DATA__.
        title_lc = (obs["title"] or "").lower()
        challenge_title_phrases = (
            "robot or human",
            "press and hold",
            "pardon our interruption",
            "access denied",
        )
        title_is_challenge = any(p in title_lc for p in challenge_title_phrases)
        thin_no_data = (obs["page_size"] < 50000 and not obs["has_next_data"])
        obs["blocked_by_perimeterx"] = title_is_challenge or thin_no_data
        if title_is_challenge:
            obs["perimeterx_signals"].append(
                f"challenge_title:{(obs['title'] or '')[:40]!r}"
            )
        if thin_no_data:
            obs["perimeterx_signals"].append(
                f"thin_page_no_next_data:{obs['page_size']}chars"
            )

        # ATC selector probing - try multiple known patterns
        candidate_selectors = [
            '[data-testid="add-to-cart-button"]',
            'button[data-automation-id="atc"]',
            'button:has-text("Add to cart")',
            'button:has-text("Add to Cart")',
            'button[type="submit"]:has-text("Add")',
            '[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]',
        ]
        for sel in candidate_selectors:
            try:
                el = page.query_selector(sel)
                if el is not None:
                    obs["atc_selectors_matching"].append(sel)
            except Exception:
                # selector syntax differences across Playwright versions
                pass

        # Stock-state text indicators
        obs["oos_text_present"] = any(
            phrase in lc_content
            for phrase in ("out of stock", "currently unavailable", "sold out")
        )
        obs["marketplace_text_present"] = any(
            phrase in lc_content
            for phrase in ("sold & shipped by", "sold and shipped by")
        )
        obs["walmart_direct_text_present"] = (
            "sold and shipped by walmart" in lc_content
            or "sold by walmart" in lc_content
            or "fulfilled by walmart" in lc_content
        )

        # Price probing - three independent strategies
        try:
            el = page.query_selector('[data-automation-id="buybox-price"]')
            if el:
                obs["extracted_price_via_attr"] = el.inner_text().strip()[:60]
        except Exception:
            pass
        try:
            el = page.query_selector('[itemprop="price"]')
            if el:
                obs["extracted_price_via_itemprop"] = (
                    el.get_attribute("content")
                    or el.inner_text().strip()[:60]
                )
        except Exception:
            pass
        m = re.search(r'\$([\d,]+\.\d{2})\b', content)
        if m:
            obs["extracted_price_via_regex"] = f"${m.group(1)}"

        obs["ok"] = True

    except Exception as e:
        obs["errors"].append(f"{type(e).__name__}: {e}")
        obs["errors"].append(traceback.format_exc(limit=3))

    return obs


# -- Run ---------------------------------------------------------------------

def run_recon(urls: list) -> int:
    """Launch Playwright/Patchright, probe each URL, write report."""
    # Try patchright first (drop-in replacement with deeper anti-detection
    # patches at browser-launch level). Falls back to vanilla playwright if
    # patchright isn't installed.
    impl = "playwright"
    try:
        from patchright.sync_api import sync_playwright  # type: ignore
        impl = "patchright"
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            print("FATAL: Neither patchright nor playwright is installed.", file=sys.stderr)
            print("       py -3.14 -m pip install patchright", file=sys.stderr)
            print("       py -3.14 -m patchright install chromium", file=sys.stderr)
            return 1

    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"[recon] BROWSER_PROFILE = {BROWSER_PROFILE}")
    print(f"[recon] DATA_DIR        = {DATA_DIR}")
    print(f"[recon] Engine          = {impl}")
    print(f"[recon] Probing {len(urls)} URL(s)...")

    results = []
    first_html_dumped = False
    stealth_lib = ""

    with sync_playwright() as p:
        # Patchright handles fingerprinting at the browser level and its
        # docs explicitly warn against passing custom args/UA -- they
        # can interfere with its own evasions. For vanilla playwright we
        # need the manual hardening.
        if impl == "patchright":
            # Patchright's #1 recommendation: real system Chrome
            # (channel="chrome") for maximum fingerprint authenticity.
            # Chromium-for-Testing has subtle fingerprint differences vs
            # real Chrome (install metadata, GPU strings, version stamps).
            # Fall back to msedge (ships with Windows by default), then
            # to the bundled Chromium-for-Testing as last resort.
            #
            # headless=False: 5 prior rungs (vanilla, +stealth, +warmed
            # profile, +patchright/chromium, +patchright/chrome) all
            # blocked. PerimeterX is fingerprinting headless mode below
            # the browser-launch level. Headful manual warmup proved
            # the page loads fine - this confirms headful works
            # programmatically. Operational invisibility (window
            # off-screen, cadence) is a plugin-spec concern, not recon.
            channel_used = None
            last_err = None
            for channel_attempt in ("chrome", "msedge", "chromium"):
                try:
                    context = p.chromium.launch_persistent_context(
                        BROWSER_PROFILE,
                        channel=channel_attempt,
                        headless=False,
                        args=[
                            # Push window off any reasonable monitor and
                            # keep it small. If this works, the plugin can
                            # run "headful" without disrupting the desktop.
                            # patchright docs warn against custom args, but
                            # window-position is purely cosmetic and should
                            # not interact with their evasion logic.
                            "--window-position=-2400,-2400",
                            "--window-size=400,300",
                        ],
                    )
                    channel_used = channel_attempt
                    break
                except Exception as e:
                    last_err = e
                    continue
            if channel_used is None:
                print(
                    f"FATAL: no chromium-family browser launchable via patchright. "
                    f"Last error: {last_err}",
                    file=sys.stderr,
                )
                return 1
            print(f"[recon] Channel         = {channel_used}")
            print(f"[recon] Mode            = headful, off-screen positioned")
        else:
            context = p.chromium.launch_persistent_context(
                BROWSER_PROFILE,
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
                user_agent=USER_AGENT,
            )
        page = context.new_page()

        # Stealth: patchright's built-in evasions replace stealth libs;
        # mixing them is counterproductive. Apply stealth ONLY for vanilla
        # playwright.
        if impl == "patchright":
            stealth_lib = "patchright (built-in)"
            print(f"[recon] Stealth         = built-in")
        else:
            try:
                stealth_lib = _apply_stealth(page)
                print(f"[recon] Stealth         = {stealth_lib}")
            except ImportError as e:
                print(f"FATAL: {e}", file=sys.stderr)
                return 1

        # Block heavy resources for speed
        page.route("**/*", lambda r: r.abort()
            if r.request.resource_type in ("image", "media", "font", "stylesheet")
            else r.continue_()
        )

        for i, url in enumerate(urls, 1):
            print(f"[recon]   [{i}/{len(urls)}] probing...")
            obs = probe_url(page, url)
            results.append(obs)
            status = (
                "BLOCKED" if obs["blocked_by_perimeterx"]
                else "ok" if obs["ok"]
                else "ERROR"
            )
            print(f"[recon]   [{i}/{len(urls)}] -> {status} (HTTP {obs['http_status']})")

            # Dump first un-blocked HTML for offline inspection
            if (obs["ok"] and not obs["blocked_by_perimeterx"]
                    and not first_html_dumped):
                try:
                    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
                        f.write(page.content())
                    first_html_dumped = True
                    print(f"[recon]     wrote sample HTML -> {OUTPUT_HTML}")
                except Exception as e:
                    print(f"[recon]     WARN: failed to dump HTML: {e}")

            time.sleep(2)  # be polite

        page.close()
        context.close()

    write_report(results, stealth_lib=stealth_lib, impl=impl)
    print(f"[recon] Report -> {OUTPUT_REPORT}")
    print(f"[recon] Done.")
    return 0


def write_report(results: list, stealth_lib: str = "", impl: str = "") -> None:
    """Render the recon results to a structured text report."""
    lines = []
    lines.append("=" * 72)
    lines.append("WALMART PLAYWRIGHT RECONNAISSANCE REPORT")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Probes:    {len(results)}")
    lines.append(f"Engine:    {impl or 'unknown'}")
    lines.append(f"Stealth:   {stealth_lib or 'none'}")
    lines.append(f"Profile:   {BROWSER_PROFILE}")
    lines.append("=" * 72)
    lines.append("")

    # Summary table
    lines.append("SUMMARY")
    lines.append("-" * 72)
    lines.append(
        f"{'#':<3} {'HTTP':<5} {'Blocked':<8} {'NEXT_DATA':<10} "
        f"{'ATC?':<5} {'Price?':<8} URL"
    )
    for i, r in enumerate(results, 1):
        atc = "yes" if r["atc_selectors_matching"] else "no"
        price = "yes" if any([
            r["extracted_price_via_attr"],
            r["extracted_price_via_itemprop"],
            r["extracted_price_via_regex"],
        ]) else "no"
        nd = "yes" if r["has_next_data"] else "no"
        blocked = "yes" if r["blocked_by_perimeterx"] else "no"
        http = str(r["http_status"]) if r["http_status"] else "ERR"
        url_short = r["url"]
        if len(url_short) > 50:
            url_short = url_short[:47] + "..."
        lines.append(
            f"{i:<3} {http:<5} {blocked:<8} {nd:<10} "
            f"{atc:<5} {price:<8} {url_short}"
        )
    lines.append("")

    # Per-probe detail
    for i, r in enumerate(results, 1):
        lines.append("=" * 72)
        lines.append(f"PROBE {i}: {r['url']}")
        lines.append("-" * 72)
        lines.append(f"  HTTP status:           {r['http_status']}")
        lines.append(f"  Page title:            {r['title']!r}")
        lines.append(f"  Page size (chars):     {r['page_size']:,}")
        lines.append(f"  Blocked by PerimeterX: {r['blocked_by_perimeterx']}")
        if r["perimeterx_signals"]:
            lines.append(f"    Signals matched:     {r['perimeterx_signals']}")
        lines.append(f"  __NEXT_DATA__ present: {r['has_next_data']}")
        if r["has_next_data"]:
            lines.append(f"    Size (chars):        {r['next_data_size']:,}")
            lines.append(f"    Top-level keys:      {r['next_data_keys']}")
            if r["next_data_excerpt"]:
                lines.append(f"    initialData.data shape:")
                for line in r["next_data_excerpt"].splitlines():
                    lines.append(f"      {line}")
            if r["next_data_product_keys"]:
                lines.append(f"    product.* keys:")
                for k in r["next_data_product_keys"]:
                    lines.append(f"      - {k}")
        lines.append(f"  ATC selectors matched: {len(r['atc_selectors_matching'])}")
        for sel in r["atc_selectors_matching"]:
            lines.append(f"    - {sel}")
        lines.append(f"  OOS text present:      {r['oos_text_present']}")
        lines.append(f"  Marketplace text:      {r['marketplace_text_present']}")
        lines.append(f"  Walmart-direct text:   {r['walmart_direct_text_present']}")
        lines.append(f"  Price (data-attr):     {r['extracted_price_via_attr']!r}")
        lines.append(f"  Price (itemprop):      {r['extracted_price_via_itemprop']!r}")
        lines.append(f"  Price (regex):         {r['extracted_price_via_regex']!r}")
        if r["errors"]:
            lines.append(f"  Errors:")
            for e in r["errors"]:
                # one error per line; truncate tracebacks
                for sub in e.splitlines()[:3]:
                    lines.append(f"    {sub}")
        lines.append("")

    # Recommendations
    lines.append("=" * 72)
    lines.append("RECOMMENDATIONS")
    lines.append("-" * 72)

    n_total   = len(results)
    n_blocked = sum(1 for r in results if r["blocked_by_perimeterx"])
    n_next    = sum(1 for r in results if r["has_next_data"])
    n_atc     = sum(1 for r in results if r["atc_selectors_matching"])

    if n_total == 0:
        lines.append("  No probes ran. Check command-line args.")
    elif n_blocked == n_total:
        sl = stealth_lib or "default"
        engine = impl or "playwright"
        lines.append(f"  >>> ALL probes blocked by PerimeterX even with {engine} + {sl}.")
        if engine == "patchright":
            lines.append("      Patchright is the deepest free anti-detection rung.")
            lines.append("      Free options effectively exhausted. Honest paths:")
            lines.append("      * Try headless=False (visible window during checks --")
            lines.append("        operationally annoying but works for sure based on warmup)")
            lines.append("      * Pivot Walmart out of v6.1, tackle Tier 1.2 / 1.3 instead")
            lines.append("      * Paid anti-bot service (out of character for hobby project)")
        else:
            lines.append("      Stealth alone was insufficient. Next escalation rungs:")
            lines.append("      * Install patchright (drop-in, deeper anti-detection)")
            lines.append("      * Try headless=False (visible browser window)")
            lines.append("      * Pivot Walmart to Tier 1.2 / 1.3 instead")
    elif n_blocked > 0:
        lines.append(f"  >>> {n_blocked}/{n_total} probes blocked. PerimeterX is intermittent.")
        lines.append("      Plugin should retry with a fresh context after a block")
        lines.append("      and may benefit from playwright-stealth-style evasions.")
    else:
        lines.append("  >>> Playwright gets through cleanly. Proceed with plugin spec.")

    if n_next == n_total and n_total > 0:
        lines.append("  >>> __NEXT_DATA__ present on all pages. Use as PRIMARY signal source")
        lines.append("      (more reliable than CSS selectors which churn frequently).")
    elif n_next > 0:
        lines.append(f"  >>> __NEXT_DATA__ present on {n_next}/{n_total} pages.")
    else:
        lines.append("  >>> No __NEXT_DATA__ found. Walmart may have moved to RSC streaming")
        lines.append("      or another framework. Plugin will need to rely on CSS selectors.")

    if n_atc > 0:
        lines.append(f"  >>> ATC selector(s) matched on {n_atc}/{n_total} probes.")
        lines.append("      Promising selectors (de-duplicated across probes):")
        seen = set()
        for r in results:
            for sel in r["atc_selectors_matching"]:
                if sel not in seen:
                    lines.append(f"        - {sel}")
                    seen.add(sel)
    else:
        lines.append("  >>> No ATC selectors matched. Either all probes were blocked,")
        lines.append("      all products are OOS (so no ATC button rendered), or")
        lines.append("      Walmart changed selectors. Inspect the sample HTML.")

    lines.append("")
    lines.append("Next step:")
    lines.append("  Send this report (data/walmart_recon.txt) back to Claude for")
    lines.append("  Step 2 (plugin spec). The recon findings will drive the selector")
    lines.append("  strategy and __NEXT_DATA__ extraction logic.")
    lines.append("=" * 72)

    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# -- Main --------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walmart Playwright reconnaissance for v6.1 plugin design",
    )
    parser.add_argument(
        "--url",
        action="append",
        help="Walmart product URL to probe (may be repeated). "
             "If not given, defaults to two tracked products.",
    )
    args = parser.parse_args()

    urls = args.url or DEFAULT_URLS
    return run_recon(urls)


if __name__ == "__main__":
    sys.exit(main())
