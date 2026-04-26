#!/usr/bin/env python3
"""
Alternative Retailer Monitor (#9)
Monitors non-traditional retailers for Pokemon TCG products -
often at or below MSRP, sometimes clearance pricing.

Retailers monitored:
  - ALDI (seasonal Pokemon products, mini tins, blisters)
  - Five Below ($5 packs, blister packs, mini tins)
  - Marshalls / TJ Maxx (clearance Pokemon products)
  - Ollie's Bargain Outlet (overstocked / clearance)
  - GameStop (exclusive bundles, trade-in credit)

Run manually:   python alternative_retailers.py
Scheduled:      runs automatically via tracker.py twice per week (Tue & Fri)
"""

import requests
import json
import re
import os
import logging
import time
from datetime import datetime
from bs4 import BeautifulSoup

# ── Path resolution - works from root or plugins/ folder ─────────────────────
import sys as _sys, os as _os
_here = _os.path.dirname(_os.path.abspath(__file__))
_root = _os.path.dirname(_here) if _os.path.basename(_here) == "plugins" else _here
if _root not in _sys.path:
    _sys.path.insert(0, _root)
if _here not in _sys.path:
    _sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────
from shared import OUTPUT_DIR, DATA_DIR, HEADERS, get_msrp, parse_price, send_ntfy, load_history, save_history

log = logging.getLogger(__name__)

ALT_HISTORY_FILE = "alt_retailer_history.json"

# Keywords that indicate a Pokemon TCG product
TCG_KEYWORDS = [
    "pokemon", "pokemon", "tcg", "trading card",
    "booster", "elite trainer", "etb", "blister",
    "scarlet", "violet", "mega evolution", "ascended heroes",
    "perfect order", "chaos rising", "prismatic evolutions",
    "destined rivals", "journey together",
]

# MSRP_REFERENCE removed - use shared.get_msrp() instead


def is_pokemon_tcg(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in TCG_KEYWORDS)


# get_msrp_estimate replaced by shared.get_msrp



# ─────────────────────────────────────────────
# SCRAPERS
# ─────────────────────────────────────────────

def check_five_below() -> list:
    """
    Five Below carries Pokemon packs, blisters, and mini tins.
    Often $5 single packs and $10-15 blisters.
    """
    findings = []
    try:
        search_url = "https://www.fivebelow.com/collections/pokemon"
        r = requests.get(search_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Product cards
        products = soup.select(".product-item, .grid__item, [data-product-id]")
        for p in products[:20]:
            title_el = p.select_one("h3, h2, .product-item__title, .card__heading")
            price_el = p.select_one(".price, .money, [class*='price']")
            link_el = p.select_one("a[href]")

            if not title_el:
                continue

            title = title_el.text.strip()
            if not is_pokemon_tcg(title):
                continue

            price_text = price_el.text.strip() if price_el else ""
            price = parse_price(price_text)
            link = "https://www.fivebelow.com" + link_el["href"] if link_el else search_url
            msrp = get_msrp(title)

            findings.append({
                "retailer": "Five Below",
                "name": title,
                "price": price,
                "price_str": price_text or "N/A",
                "msrp": msrp,
                "url": link,
                "note": "Check store availability - Five Below is primarily in-store",
            })
            log.info(f"Five Below: found '{title}' @ {price_text}")

        # Also check search
        if not findings:
            search_r = requests.get(
                "https://www.fivebelow.com/search?q=pokemon",
                headers=HEADERS, timeout=15
            )
            search_soup = BeautifulSoup(search_r.text, "html.parser")
            items = search_soup.select(".product-item, .grid__item")
            for item in items[:10]:
                title_el = item.select_one("h3, h2, .title")
                if title_el and is_pokemon_tcg(title_el.text):
                    price_el = item.select_one(".price, .money")
                    findings.append({
                        "retailer": "Five Below",
                        "name": title_el.text.strip(),
                        "price": parse_price(price_el.text if price_el else ""),
                        "price_str": price_el.text.strip() if price_el else "N/A",
                        "msrp": get_msrp(title_el.text),
                        "url": "https://www.fivebelow.com/search?q=pokemon",
                        "note": "Check store for availability",
                    })

    except Exception as e:
        log.warning(f"Five Below scrape error: {e}")
    return findings


def check_marshalls_tjmaxx() -> list:
    """
    Marshalls and TJ Maxx get overstocked Pokemon product at clearance prices.
    No online inventory - this monitors their websites for any listings
    and generates an in-store check reminder.
    """
    findings = []
    urls = [
        ("Marshalls", "https://www.marshalls.com/us/store/search.jsp?q=pokemon+card"),
        ("TJ Maxx", "https://www.tjmaxx.tjx.com/store/search.jsp?q=pokemon+card"),
    ]

    for retailer, url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")

            products = soup.select(".product-tile, .product-name, [class*='product']")
            found_any = False
            for p in products[:15]:
                text = p.get_text(separator=" ").strip()
                if is_pokemon_tcg(text) and len(text) > 5:
                    price_match = re.search(r'\$[\d.]+', text)
                    name_match = re.search(r'[Pp]ok[ee]mon[^$\n]{5,50}', text)
                    if name_match:
                        name = name_match.group(0).strip()[:60]
                        price_str = price_match.group(0) if price_match else "Check in-store"
                        price = parse_price(price_str)
                        findings.append({
                            "retailer": retailer,
                            "name": name,
                            "price": price,
                            "price_str": price_str,
                            "msrp": get_msrp(name),
                            "url": url,
                            "note": f"{retailer} has limited online presence - check your local store",
                        })
                        found_any = True
                        log.info(f"{retailer}: found '{name}' @ {price_str}")

            # Even if no products found online, remind to check in-store
            if not found_any:
                # Check if page loaded at all (not blocked)
                if len(r.text) > 5000:
                    findings.append({
                        "retailer": retailer,
                        "name": f"Check {retailer} In-Store (No Online Listings)",
                        "price": None,
                        "price_str": "Check in-store",
                        "msrp": None,
                        "url": url,
                        "note": f"{retailer} stocks Pokemon TCG irregularly - worth checking in-store mid-week. Look in the toy aisle and near registers.",
                        "reminder": True,
                    })

        except Exception as e:
            log.warning(f"{retailer} scrape error: {e}")

        time.sleep(2)

    return findings


def check_ollies() -> list:
    """
    Ollie's Bargain Outlet occasionally gets overstock Pokemon product
    at 30-50% below MSRP.
    """
    findings = []
    try:
        url = "https://www.ollies.us/search?q=pokemon"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        products = soup.select(".product-item, .product, [class*='product-tile']")
        for p in products[:15]:
            title_el = p.select_one("h2, h3, .title, .name")
            price_el = p.select_one(".price, .sale-price, [class*='price']")

            if not title_el:
                continue
            title = title_el.text.strip()
            if not is_pokemon_tcg(title):
                continue

            price_text = price_el.text.strip() if price_el else "N/A"
            price = parse_price(price_text)
            msrp = get_msrp(title)

            savings_note = ""
            if price and msrp and price < msrp:
                savings = msrp - price
                savings_note = f" - Save ${savings:.2f} vs MSRP!"

            findings.append({
                "retailer": "Ollie's Bargain Outlet",
                "name": title,
                "price": price,
                "price_str": price_text,
                "msrp": msrp,
                "url": url,
                "note": f"Ollie's clearance pricing{savings_note}. In-store only.",
            })
            log.info(f"Ollie's: found '{title}' @ {price_text}{savings_note}")

        if not findings:
            # Add a periodic reminder regardless
            findings.append({
                "retailer": "Ollie's Bargain Outlet",
                "name": "Check Ollie's In-Store (Clearance Checker)",
                "price": None,
                "price_str": "Varies",
                "msrp": None,
                "url": "https://www.ollies.us/search?q=pokemon",
                "note": "Ollie's gets Pokemon TCG overstock at 30-50% below MSRP. No online inventory - visit in-store. Stock is unpredictable.",
                "reminder": True,
            })

    except Exception as e:
        log.warning(f"Ollie's scrape error: {e}")
    return findings


def check_gamestop() -> list:
    """
    GameStop carries exclusive Pokemon TCG bundles and has trade-in programs.
    """
    findings = []
    try:
        url = "https://www.gamestop.com/search/?q=pokemon+trading+card&lang=default"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        products = soup.select(".product-tile, .product-name, [class*='product']")
        for p in products[:20]:
            title_el = p.select_one("h3, h2, .product-name a, .pdp-link a")
            price_el = p.select_one(".price, .price-section, [class*='price']")
            link_el = p.select_one("a[href]")

            if not title_el:
                continue
            title = title_el.text.strip()
            if not is_pokemon_tcg(title):
                continue

            price_text = price_el.text.strip() if price_el else "N/A"
            price = parse_price(price_text)
            link = "https://www.gamestop.com" + link_el["href"] if link_el and link_el["href"].startswith("/") else (link_el["href"] if link_el else url)
            msrp = get_msrp(title)

            findings.append({
                "retailer": "GameStop",
                "name": title,
                "price": price,
                "price_str": price_text,
                "msrp": msrp,
                "url": link,
                "note": "GameStop often has exclusive bundles. Pro members get early access. Trade-in credit can offset cost.",
            })
            log.info(f"GameStop: found '{title}' @ {price_text}")

    except Exception as e:
        log.warning(f"GameStop scrape error: {e}")
    return findings


def check_aldi() -> list:
    """
    ALDI runs Pokemon TCG seasonal promotions (typically spring/fall ALDI Finds).
    No online inventory - generates timed reminders based on known ALDI Find windows.
    """
    findings = []
    today = datetime.now()
    month = today.month

    # ALDI typically runs Pokemon Finds in spring (Mar-Apr) and fall (Oct-Nov)
    is_aldi_season = month in (3, 4, 10, 11)

    findings.append({
        "retailer": "ALDI",
        "name": "ALDI Finds - Pokemon TCG Check" if is_aldi_season else "ALDI Finds (Off-Season)",
        "price": None,
        "price_str": "$9.99-$19.99 typical",
        "msrp": None,
        "url": "https://www.aldi.us/en/weekly-specials/this-weeks-aldi-finds/",
        "note": (
            "🔴 ALDI SEASON ACTIVE: Pokemon TCG products appear as ALDI Finds Mar-Apr and Oct-Nov. "
            "Check your local ALDI starting Wednesday (new Finds drop Wednesday). "
            "Recent: Ascended Heroes Mini Tins spotted Apr 22 at select stores."
            if is_aldi_season else
            "ALDI runs Pokemon TCG promotions in spring (Mar-Apr) and fall (Oct-Nov) as ALDI Finds. "
            "Check back then for mini tins, blisters, and seasonal products at below-MSRP prices."
        ),
        "reminder": True,
        "in_season": is_aldi_season,
    })
    log.info(f"ALDI reminder added (in-season: {is_aldi_season})")
    return findings


# ─────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────

def send_alt_retailer_alert(findings: list, ntfy_topic: str):
    """Send push notification for alternative retailer finds."""
    if not ntfy_topic or ntfy_topic == "tcg-restock-MY-SECRET-TOPIC-123":
        return

    # Only alert on real finds, not reminders
    real_finds = [f for f in findings if not f.get("reminder") and f.get("price")]
    if not real_finds:
        return

    lines = [f"🏷️ Alt Retailer Finds - {datetime.now().strftime('%a %b %d')}"]
    for f in real_finds[:6]:
        msrp_note = ""
        if f.get("msrp") and f.get("price"):
            savings = f["msrp"] - f["price"]
            if savings > 0:
                msrp_note = f" (Save ${savings:.2f}!)"
        lines.append(f"• {f['retailer']}: {f['name']}")
        lines.append(f"  {f['price_str']}{msrp_note}")

    send_ntfy(
        topic=ntfy_topic,
        title="Pokemon TCG Alt Retailer Find!",
        body="\n".join(lines),
        priority="high",
        tags="label,moneybag",
    )
    log.info(f"Alt retailer alert sent: {len(real_finds)} finds")


def load_alt_history() -> dict:
    return load_history(ALT_HISTORY_FILE)


def save_alt_history(history: dict):
    save_history(ALT_HISTORY_FILE, history)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_alt_retailer_check(config: dict):
    """Run all alternative retailer checks."""
    ntfy_topic = config.get("ntfy_topic", "")
    log.info("Starting alternative retailer scan...")

    all_findings = []

    # Run all scrapers
    log.info("Checking Five Below...")
    all_findings.extend(check_five_below())
    time.sleep(2)

    log.info("Checking Marshalls / TJ Maxx...")
    all_findings.extend(check_marshalls_tjmaxx())
    time.sleep(2)

    log.info("Checking Ollie's Bargain Outlet...")
    all_findings.extend(check_ollies())
    time.sleep(2)

    log.info("Checking GameStop...")
    all_findings.extend(check_gamestop())
    time.sleep(2)

    log.info("Checking ALDI seasonal status...")
    all_findings.extend(check_aldi())

    # Save to JSON for dashboard
    output = {
        "last_updated": datetime.now().isoformat(),
        "findings": all_findings,
    }
    with open(os.path.join(DATA_DIR, "alt_retailers.json"), "w") as f:
        json.dump(output, f, indent=2)

    log.info(f"Alt retailer scan complete: {len(all_findings)} items found")

    # Alert on real finds
    send_alt_retailer_alert(all_findings, ntfy_topic)

    return all_findings


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )
    try:
        import sys
        sys.path.insert(0, OUTPUT_DIR)
        from tracker import CONFIG

        findings = run_alt_retailer_check(CONFIG)

        real = [f for f in findings if not f.get("reminder")]
        reminders = [f for f in findings if f.get("reminder")]

        if real:
            print(f"\n🏷️ Found {len(real)} alternative retailer products:\n")
            for f in real:
                msrp_note = ""
                if f.get("msrp") and f.get("price") and f["price"] < f["msrp"]:
                    msrp_note = f"  💰 Save ${f['msrp'] - f['price']:.2f} vs MSRP!"
                print(f"  {f['retailer']}: {f['name']}")
                print(f"  Price: {f['price_str']}  MSRP: ${f['msrp']:.2f}" if f.get("msrp") else f"  Price: {f['price_str']}")
                if msrp_note:
                    print(f" {msrp_note}")
                print(f"  {f['url']}\n")
        else:
            print("\nNo alternative retailer products found today.")

        if reminders:
            print(f"\n📋 In-Store Reminders ({len(reminders)}):\n")
            for r in reminders:
                print(f"  {r['retailer']}: {r['note']}\n")

    except ImportError as e:
        log.error(f"Run from the tcg_tracker directory: {e}")
