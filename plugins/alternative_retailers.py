#!/usr/bin/env python3
"""
Alternative Retailer Monitor (#9)
Monitors non-traditional retailers for Pokemon TCG products —
often at or below MSRP, sometimes clearance pricing.

Retailers monitored:
  - Marshalls        (clearance — in-store reminder)
  - Burlington       (clearance — in-store reminder)
  - Ollie's          (overstock 30-50% below MSRP — in-store reminder)
  - GameStop         (exclusive bundles, new + pre-owned)
  - ALDI             (seasonal Finds — Mar/Apr + Oct/Nov)
  - Dollar General   (occasional $5 packs and blisters)

Schedule:   Mon / Wed / Fri at 09:00 (via plugins.py)
Run manual: python plugins/alternative_retailers.py

Improvements over v1:
  - Full current-set TCG keyword coverage
  - History-based deduplication (only alerts on NEW finds)
  - Burlington and Dollar General added
  - GameStop: two-URL strategy + pre-owned detection
  - ALDI: Wednesday drop awareness, specific season windows
  - Real finds and reminders separated in alerts
"""

import requests
import json
import os
import logging
import time
from datetime import datetime, date
from bs4 import BeautifulSoup

# ── Path resolution — works from root or plugins/ folder ─────────────────────
import sys as _sys, os as _os
_here = _os.path.dirname(_os.path.abspath(__file__))
_root = _os.path.dirname(_here) if _os.path.basename(_here) == "plugins" else _here
if _root not in _sys.path:
    _sys.path.insert(0, _root)
if _here not in _sys.path:
    _sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────
from shared import (
    DATA_DIR, HEADERS, get_msrp, parse_price,
    send_ntfy, load_history, save_history,
)

log = logging.getLogger(__name__)

ALT_HISTORY_FILE  = "alt_retailer_history.json"
ALT_OUTPUT_FILE   = os.path.join(DATA_DIR, "alt_retailers.json")

# ─────────────────────────────────────────────────────────────────────────────
# TCG KEYWORD LIST — all current sets + product types
# Update this list whenever a new set releases
# ─────────────────────────────────────────────────────────────────────────────
TCG_KEYWORDS = [
    # Generic identifiers
    "pokemon", "pokémon", "tcg", "trading card game",
    # Product types
    "booster", "elite trainer", "etb", "blister pack",
    "booster bundle", "booster box", "collection box",
    "mini tin", "tin", "build & battle", "sleeved booster",
    "display box", "poster collection",
    # Mega Evolution series (2025-2026)
    "mega evolution",
    "chaos rising",
    "phantasmal flames",
    "perfect order",
    "ascended heroes",
    "pitch black",
    # Scarlet & Violet series (2024-2025)
    "scarlet",
    "violet",
    "prismatic evolutions",
    "surging sparks",
    "stellar crown",
    "shrouded fable",
    "twilight masquerade",
    "destined rivals",
    "journey together",
    "black bolt",
    "white flare",
    "temporal forces",
    "paldean fates",
    "paradox rift",
    "obsidian flames",
    "151",
    # Older popular sets still in circulation
    "evolving skies",
    "hidden fates",
    "silver tempest",
    "crown zenith",
]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def is_pokemon_tcg(text: str) -> bool:
    """Return True if text matches any TCG keyword."""
    lower = text.lower()
    return any(kw in lower for kw in TCG_KEYWORDS)


def make_find(retailer: str, name: str, price_str: str, url: str,
              note: str = "", reminder: bool = False) -> dict:
    """Build a standardised finding dict."""
    price  = parse_price(price_str)
    msrp   = get_msrp(name) if not reminder else None
    deal   = bool(price and msrp and price < msrp * 0.99)
    return {
        "retailer":  retailer,
        "name":      name,
        "price":     price,
        "price_str": price_str if price_str else "N/A",
        "msrp":      msrp,
        "url":       url,
        "note":      note,
        "deal":      deal,
        "reminder":  reminder,
        "found_at":  datetime.now().isoformat(),
    }


def dedup_findings(findings: list, history: dict) -> tuple[list, list]:
    """
    Split findings into NEW (not seen before) and KNOWN (already alerted).
    Updates history in place with new finds.
    Returns (new_finds, all_finds).
    """
    new_finds = []
    for f in findings:
        if f.get("reminder"):
            continue                    # reminders never deduplicated
        key = f"{f['retailer']}|{f['name'].lower()[:60]}"
        if key not in history:
            history[key] = {
                "first_seen": f["found_at"],
                "last_seen":  f["found_at"],
                "price":      f.get("price_str"),
            }
            new_finds.append(f)
        else:
            history[key]["last_seen"] = f["found_at"]
    return new_finds, findings


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPERS
# ─────────────────────────────────────────────────────────────────────────────


def check_burlington() -> list:
    """
    Burlington Coat Factory occasionally stocks Pokemon TCG clearance.
    Closest location: Hamilton or Deptford NJ.
    """
    findings = []
    url = "https://www.burlington.com/search?q=pokemon+trading+card"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        found_online = False
        if r.status_code == 200 and len(r.text) > 3000:
            soup = BeautifulSoup(r.text, "html.parser")
            cards = (
                soup.select(".product-tile")
                or soup.select("[class*='product-item']")
                or soup.select("[class*='product-card']")
            )
            for card in cards[:20]:
                title_el  = card.select_one("h3, h2, .product-name, .title")
                price_el  = card.select_one(".price, .sale-price, [class*='price']")
                link_el   = card.select_one("a[href]")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not is_pokemon_tcg(title):
                    continue
                price_str = price_el.get_text(strip=True) if price_el else "Check in-store"
                href      = link_el["href"] if link_el else ""
                full_url  = (
                    "https://www.burlington.com" + href
                    if href.startswith("/") else href or url
                )
                findings.append(make_find(
                    retailer="Burlington",
                    name=title,
                    price_str=price_str,
                    url=full_url,
                    note="Burlington clearance — in-store stock varies by location.",
                ))
                log.info(f"Burlington: '{title}' @ {price_str}")
                found_online = True

        if not found_online:
            findings.append(make_find(
                retailer="Burlington",
                name="Check Burlington In-Store",
                price_str="Varies — deep clearance when found",
                url="https://stores.burlington.com/",
                note=(
                    "Burlington (Hamilton or Deptford NJ) occasionally gets Pokemon TCG "
                    "clearance, especially older sets at 40-60% below MSRP. "
                    "Worth checking on a Costco run to Cherry Hill."
                ),
                reminder=True,
            ))
    except Exception as e:
        log.warning(f"Burlington: {e}")

    return findings


def check_ollies() -> list:
    """
    Ollie's Bargain Outlet — overstock Pokemon TCG at 30-50% below MSRP.
    """
    findings = []
    url = "https://www.ollies.us/search?q=pokemon"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        found_online = False
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            cards = (
                soup.select(".product-item")
                or soup.select("[class*='product-tile']")
                or soup.select(".product")
            )
            for card in cards[:20]:
                title_el = (
                    card.select_one("h2, h3, .title, .name, .product-name")
                )
                price_el = (
                    card.select_one(".sale-price, .price, [class*='price']")
                )
                link_el  = card.select_one("a[href]")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not is_pokemon_tcg(title):
                    continue
                price_str = price_el.get_text(strip=True) if price_el else "N/A"
                href      = link_el["href"] if link_el else ""
                full_url  = (
                    "https://www.ollies.us" + href
                    if href.startswith("/") else href or url
                )
                findings.append(make_find(
                    retailer="Ollie's Bargain Outlet",
                    name=title,
                    price_str=price_str,
                    url=full_url,
                    note="Ollie's clearance — in-store only. Stock arrives in large unpredictable batches.",
                ))
                log.info(f"Ollie's: '{title}' @ {price_str}")
                found_online = True

        if not found_online:
            findings.append(make_find(
                retailer="Ollie's Bargain Outlet",
                name="Check Ollie's In-Store",
                price_str="30–50% below MSRP when available",
                url=url,
                note=(
                    "Ollie's gets Pokemon TCG overstock in unpredictable batches at deep "
                    "discounts. Don't make the trip without a real scanner alert — "
                    "but when they have it, it moves fast."
                ),
                reminder=True,
            ))
    except Exception as e:
        log.warning(f"Ollie's: {e}")

    return findings


def check_gamestop() -> list:
    """
    GameStop — new + pre-owned Pokemon TCG bundles and singles.
    Tries search page and Pokemon category page.
    """
    findings = []
    urls = [
        "https://www.gamestop.com/search/?q=pokemon+trading+card&lang=default",
        "https://www.gamestop.com/toys-and-collectibles/trading-card-games/products/",
    ]

    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200 or len(r.text) < 3000:
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            cards = (
                soup.select(".product-tile")
                or soup.select(".product-card")
                or soup.select("[class*='product-grid'] li")
            )

            for card in cards[:25]:
                title_el  = (
                    card.select_one(".product-tile__title")
                    or card.select_one("h3, h2, .title, .name")
                )
                price_el  = (
                    card.select_one(".final-sale, .product-tile__price")
                    or card.select_one(".price, [class*='price']")
                )
                link_el   = card.select_one("a[href]")
                preowned_el = card.select_one(".preowned, [class*='pre-owned'], .condition")

                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not is_pokemon_tcg(title):
                    continue

                price_str = price_el.get_text(strip=True) if price_el else "N/A"
                href      = link_el["href"] if link_el else ""
                full_url  = (
                    "https://www.gamestop.com" + href
                    if href.startswith("/") else href or url
                )
                is_preowned = bool(preowned_el) or "pre-owned" in title.lower()
                note = (
                    "GameStop pre-owned — inspect condition. Trade-in credit may offset cost."
                    if is_preowned else
                    "GameStop — sometimes has exclusive bundles. Pro members get early access."
                )

                findings.append(make_find(
                    retailer="GameStop",
                    name=title,
                    price_str=price_str,
                    url=full_url,
                    note=note,
                ))
                log.info(f"GameStop: '{title}' @ {price_str}{' [pre-owned]' if is_preowned else ''}")

        except Exception as e:
            log.warning(f"GameStop ({url}): {e}")
        time.sleep(1)

        if findings:
            break

    return findings


def check_dollar_general() -> list:
    """
    Dollar General occasionally stocks $5 Pokemon booster packs.
    Website has limited search — generates targeted in-store reminder.
    """
    findings = []
    url = "https://www.dollargeneral.com/search?query=pokemon+card"
    found_online = False

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and len(r.text) > 3000:
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("[class*='product']")
            for card in cards[:20]:
                title_el = card.select_one("h3, h2, .product-name, .title")
                price_el = card.select_one(".price, [class*='price']")
                link_el  = card.select_one("a[href]")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not is_pokemon_tcg(title):
                    continue
                price_str = price_el.get_text(strip=True) if price_el else "$5.00"
                href      = link_el["href"] if link_el else ""
                full_url  = (
                    "https://www.dollargeneral.com" + href
                    if href.startswith("/") else href or url
                )
                findings.append(make_find(
                    retailer="Dollar General",
                    name=title,
                    price_str=price_str,
                    url=full_url,
                    note="Dollar General — single packs at $5 or less. Check toy/gift section.",
                ))
                log.info(f"Dollar General: '{title}' @ {price_str}")
                found_online = True
    except Exception as e:
        log.warning(f"Dollar General: {e}")

    if not found_online:
        findings.append(make_find(
            retailer="Dollar General",
            name="Check Dollar General — $5 Pokemon Packs",
            price_str="$5.00 (single booster packs)",
            url="https://www.dollargeneral.com/search?query=pokemon+card",
            note=(
                "Dollar General carries $5 single booster packs of current sets. "
                "Look in the toy aisle near the seasonal/gift section. "
                "No specific drop schedule — check during any visit."
            ),
            reminder=True,
        ))

    return findings


def check_aldi() -> list:
    """
    ALDI Finds — seasonal Pokemon TCG promotions.
    Spring: Mar–Apr. Fall: Oct–Nov. New Finds drop Wednesday.

    Known upcoming: Chaos Rising mini tins expected Oct 2026 (fall window).
    """
    today = date.today()
    month = today.month
    day   = today.weekday()  # 0=Mon, 2=Wed

    # Active ALDI season windows
    spring_active = month in (3, 4)
    fall_active   = month in (10, 11)
    is_active     = spring_active or fall_active

    # Wednesday = new Finds drop day at ALDI
    wednesday_boost = "⚡ Today is Wednesday — new ALDI Finds drop today! Check early." if day == 2 else ""

    if is_active:
        season = "Spring" if spring_active else "Fall"
        status = (
            f"🟢 ALDI SEASON ACTIVE ({season}): Pokemon TCG Finds are dropping now. "
            f"Check your local ALDI mid-morning on Wednesdays. "
            f"Recent sightings: mini tins, blister packs, booster bundles. "
        )
        if wednesday_boost:
            status += wednesday_boost
        priority = "high"
    else:
        next_window = "March–April" if month > 11 or month < 3 else "October–November"
        status = (
            f"🔴 ALDI Off-Season: Next Pokemon Finds window expected {next_window}. "
            f"Watch for Wednesday drops when the season starts."
        )
        priority = "low"

    return [make_find(
        retailer="ALDI",
        name=f"ALDI Finds — Pokemon TCG {'[SEASON ACTIVE]' if is_active else '[OFF-SEASON]'}",
        price_str="$9.99–$19.99 typical",
        url="https://www.aldi.us/en/weekly-specials/this-weeks-aldi-finds/",
        note=status,
        reminder=True,
    )]


# ─────────────────────────────────────────────────────────────────────────────
# ALERT
# ─────────────────────────────────────────────────────────────────────────────

def send_alt_retailer_alert(new_finds: list, all_findings: list, ntfy_topic: str):
    """
    Send ntfy alert. Only alerts on NEW finds (deduped).
    Appends a brief reminder summary if ALDI season is active.
    """
    if not ntfy_topic:
        return

    # Real new finds
    real_new = [f for f in new_finds if not f.get("reminder")]
    # ALDI reminder if season active
    aldi_active = any(
        f.get("reminder") and "ALDI" in f.get("retailer", "") and "ACTIVE" in f.get("name", "")
        for f in all_findings
    )

    if not real_new and not aldi_active:
        log.info("Alt retailer: no new finds — skipping alert")
        return

    lines = []

    if real_new:
        lines.append(f"🏷️ {len(real_new)} NEW alt retailer find(s):\n")
        for f in real_new:
            savings = ""
            if f.get("price") and f.get("msrp") and f["price"] < f["msrp"]:
                savings = f" 💰 Save ${f['msrp'] - f['price']:.2f}"
            lines.append(f"• {f['retailer']}: {f['name']}")
            lines.append(f"  {f['price_str']}{savings}")
            lines.append(f"  {f['url']}")

    if aldi_active:
        lines.append("\n🟢 ALDI SEASON ACTIVE — check your local ALDI for Pokemon Finds.")

    send_ntfy(
        topic=ntfy_topic,
        title=f"Alt Retailer: {len(real_new)} new find(s)" if real_new else "ALDI Season Active",
        body="\n".join(lines),
        priority="high" if real_new else "default",
        tags="label,moneybag",
    )
    log.info(f"Alt retailer alert sent: {len(real_new)} new finds")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run_alt_retailer_check(config: dict) -> list:
    """Run all alternative retailer checks with deduplication."""
    ntfy_topic = config.get("ntfy_topic", "")
    log.info("Starting alternative retailer scan...")

    all_findings = []

    scrapers = [
        ("Burlington",     check_burlington),
        ("Ollie's",        check_ollies),
        ("GameStop",       check_gamestop),
        ("Dollar General", check_dollar_general),
        ("ALDI",           check_aldi),
    ]

    for label, fn in scrapers:
        log.info(f"Checking {label}...")
        try:
            results = fn()
            all_findings.extend(results)
            log.info(f"  {label}: {len(results)} item(s)")
        except Exception as e:
            log.warning(f"  {label} scraper failed: {e}")
        time.sleep(2)

    # Load history, deduplicate, save updated history
    history   = load_history(ALT_HISTORY_FILE)
    new_finds, _ = dedup_findings(all_findings, history)
    save_history(ALT_HISTORY_FILE, history)

    # Write full results to JSON for dashboard
    output = {
        "last_updated":    datetime.now().isoformat(),
        "total_found":     len(all_findings),
        "new_this_run":    len([f for f in new_finds if not f.get("reminder")]),
        "findings":        all_findings,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ALT_OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    log.info(f"Alt retailer scan complete: {len(all_findings)} total, {len(new_finds)} new")

    # Alert only on new real finds + active ALDI season
    send_alt_retailer_alert(new_finds, all_findings, ntfy_topic)

    return all_findings


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )
    # Load config from tracker if available, else use minimal stub
    try:
        from tracker import CONFIG
    except ImportError:
        CONFIG = {"ntfy_topic": "", "notify_push": False}

    findings = run_alt_retailer_check(CONFIG)

    real   = [f for f in findings if not f.get("reminder")]
    remind = [f for f in findings if f.get("reminder")]

    if real:
        print(f"\n🏷️  {len(real)} product(s) found:\n")
        for f in real:
            msrp_note = ""
            if f.get("msrp") and f.get("price") and f["price"] < f["msrp"]:
                msrp_note = f"  💰 Save ${f['msrp'] - f['price']:.2f} vs MSRP"
            print(f"  [{f['retailer']}] {f['name']}")
            print(f"  Price: {f['price_str']}" + (f"  MSRP: ${f['msrp']:.2f}" if f.get("msrp") else "") + msrp_note)
            print(f"  {f['url']}\n")
    else:
        print("\nNo new alt retailer products found.")

    if remind:
        print(f"📋 {len(remind)} in-store reminder(s):")
        for r in remind:
            active = "[ACTIVE]" in r.get("name", "")
            prefix = "  🟢" if active else "  🔴"
            print(f"{prefix} {r['retailer']}: {r['note'][:100]}...")
