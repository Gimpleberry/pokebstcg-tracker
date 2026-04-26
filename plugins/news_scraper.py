#!/usr/bin/env python3
"""
TCG News Scraper — runs once daily
Scrapes PokeBeach, CollectorStation, and TCG news sources for:
  - Upcoming set release dates and product waves → future_events.json
  - Retail drop intelligence and timing → retail_drops.json

Run manually:  python news_scraper.py
Run daily:     schedule handles it automatically when imported by tracker.py
               OR use Task Scheduler / cron to run standalone
"""

import requests
import json
import re
import logging
import os
from datetime import datetime, date, timedelta
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


# ── Path resolution — works from root or plugins/ folder ─────────────────────
import sys as _sys, os as _os
_here = _os.path.dirname(_os.path.abspath(__file__))
_root = _os.path.dirname(_here) if _os.path.basename(_here) == "plugins" else _here
if _root not in _sys.path:
    _sys.path.insert(0, _root)
if _here not in _sys.path:
    _sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────
from shared import OUTPUT_DIR, DATA_DIR, HEADERS, save_json, load_json

FUTURE_FILE = os.path.join(DATA_DIR, "future_events.json")
DROPS_FILE  = os.path.join(DATA_DIR, "retail_drops.json")

# ─────────────────────────────────────────────────────────────────────
# BASELINE DATA
# Static confirmed events — these are always present as a foundation.
# Scraped content merges on top of this.
# ─────────────────────────────────────────────────────────────────────
BASELINE_EVENTS = [
    {
        "id": "asc-launch",
        "date": "2026-01-30",
        "title": "Ascended Heroes — Launch Day",
        "desc": "295-card Mega Evolution set. Largest English set at time of release. Features Mega Gengar ex, Pikachu ex, Mega Dragonite ex.",
        "cats": ["set"],
        "tags": ["Set Release", "Confirmed"],
        "msrp": "",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokemon.com",
        "past": True,
    },
    {
        "id": "asc-etb",
        "date": "2026-02-20",
        "title": "Ascended Heroes ETB & Product Wave",
        "desc": "Elite Trainer Box ($49.99), PC ETB ($59.99), Mini Tins, Premium Poster Collections.",
        "cats": ["product"],
        "tags": ["Product Wave", "Confirmed"],
        "msrp": "$49.99",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokemon.com",
        "past": True,
    },
    {
        "id": "asc-fp-collection",
        "date": "2026-03-20",
        "title": "First Partner Illustration Collection (Kanto/Sinnoh/Alola)",
        "desc": "Chikorita, Tepig, Totodile foil promos + Enamel Pin + 5 ASC packs.",
        "cats": ["collection", "promo"],
        "tags": ["Collection", "Confirmed"],
        "msrp": "$39.99",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokemon.com",
        "past": True,
    },
    {
        "id": "por-launch",
        "date": "2026-03-27",
        "title": "Perfect Order — Launch Day",
        "desc": "130-card Mega Evolution mini-set. Stars Mega Zygarde ex, Mega Starmie ex, Mega Clefable ex.",
        "cats": ["set"],
        "tags": ["Set Release", "Confirmed"],
        "msrp": "",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokemon.com",
        "past": True,
    },
    {
        "id": "asc-mega-ex-boxes",
        "date": "2026-04-24",
        "title": "Ascended Heroes Mega ex Boxes",
        "desc": "3 variants: Mega Meganium ex, Mega Emboar ex, Mega Feraligatr ex. Each includes foil promo + lenticular card + 4 packs.",
        "cats": ["product"],
        "tags": ["Product Wave", "Confirmed"],
        "msrp": "$21.99",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "cr-prerelease-start",
        "date": "2026-05-09",
        "title": "Chaos Rising — Prerelease Begins",
        "desc": "Build & Battle Boxes at LGS. Promos: Delphox, Ampharos, Crobat, Goodra stamped promo cards.",
        "cats": ["prerelease", "event"],
        "tags": ["Prerelease", "Confirmed"],
        "msrp": "$14.99",
        "retailers": ["lgs"],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "cr-prerelease-end",
        "date": "2026-05-17",
        "title": "Chaos Rising — Prerelease Ends",
        "desc": "Last day of prerelease events at participating Play! Pokémon stores.",
        "cats": ["prerelease", "event"],
        "tags": ["Prerelease", "Confirmed"],
        "msrp": "",
        "retailers": ["lgs"],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "cr-tcglive",
        "date": "2026-05-21",
        "title": "Chaos Rising — TCG Live Digital Release",
        "desc": "Chaos Rising cards available on Pokémon TCG Live, one day before physical retail.",
        "cats": ["event"],
        "tags": ["Digital", "Confirmed"],
        "msrp": "",
        "retailers": [],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "cr-launch",
        "date": "2026-05-22",
        "title": "Chaos Rising — Launch Day 🔥",
        "desc": "122-card Mega Evolution set. Stars Mega Greninja ex, Mega Pyroar ex, Mega Floette ex, Mega Dragalge ex. ETB features Mega Greninja on cover.",
        "cats": ["set"],
        "tags": ["Set Release", "Confirmed"],
        "msrp": "",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "cr-etb",
        "date": "2026-05-22",
        "title": "Chaos Rising ETB",
        "desc": "9 packs + Fennekin Illustration Rare promo + accessories. PC ETB has 11 packs + stamped promo.",
        "cats": ["product"],
        "tags": ["Product Wave", "Confirmed"],
        "msrp": "$49.99",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "cr-bundle",
        "date": "2026-05-22",
        "title": "Chaos Rising Booster Bundle",
        "desc": "6-pack booster bundle. $26.99 at major retailers.",
        "cats": ["product"],
        "tags": ["Product Wave", "Confirmed"],
        "msrp": "$26.99",
        "retailers": ["target", "walmart", "bestbuy"],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "cr-blister",
        "date": "2026-05-22",
        "title": "Chaos Rising 3-Pack Blister",
        "desc": "Charmeleon foil promo + 3 booster packs. $14.99.",
        "cats": ["product"],
        "tags": ["Product Wave", "Confirmed"],
        "msrp": "$14.99",
        "retailers": ["target", "walmart", "bestbuy"],
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "fp-series2",
        "date": "2026-06-19",
        "title": "First Partner Illustration Collection Series 2",
        "desc": "Johto, Unova, Galar starters with Illustration Rare style promos.",
        "cats": ["collection", "promo"],
        "tags": ["Collection", "Confirmed"],
        "msrp": "$14.99",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "tcgradar.eu",
        "past": False,
    },
    {
        "id": "pb-prerelease",
        "date": "2026-07-04",
        "title": "Pitch Black — Prerelease Begins",
        "desc": "Pitch Black (Mega Darkrai ex) prereleases begin at local game stores.",
        "cats": ["prerelease", "event"],
        "tags": ["Prerelease", "Confirmed"],
        "msrp": "$14.99",
        "retailers": ["lgs"],
        "source": "pokebeach.com",
        "past": False,
    },
    {
        "id": "pb-launch",
        "date": "2026-07-17",
        "title": "Pitch Black — Launch Day 🔥",
        "desc": "5th Mega Evolution set adapting Japan's Abyss Eye. Stars Mega Darkrai ex. Features Dark Bell Trainer + new Malamar.",
        "cats": ["set"],
        "tags": ["Set Release", "Confirmed"],
        "msrp": "",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokebeach.com",
        "past": False,
    },
    {
        "id": "storm-rumored",
        "date": "2026-08-01",
        "title": "Storm Emerald (Mega Rayquaza ex) — Expected",
        "desc": "RUMORED: Based on Japan's Storm Emeralda (Jul 31 JP). Mega Rayquaza ex expected as headliner. No English date confirmed.",
        "cats": ["set", "rumored"],
        "tags": ["Rumored", "Set Release"],
        "msrp": "",
        "retailers": [],
        "source": "tcgradar.eu",
        "past": False,
    },
    {
        "id": "30th-jp",
        "date": "2026-09-16",
        "title": "30th Celebration Set — Japan Launch",
        "desc": "Japanese release of 30th Anniversary set. English worldwide launch follows Sep 18.",
        "cats": ["set", "event"],
        "tags": ["Set Release", "Confirmed", "Milestone"],
        "msrp": "",
        "retailers": [],
        "source": "collectorstation.com",
        "past": False,
    },
    {
        "id": "30th-worldwide",
        "date": "2026-09-18",
        "title": "30th Celebration Set — Worldwide Launch 🎉",
        "desc": "First ever simultaneous worldwide Pokémon TCG release. All-foil 6-card packs. New rarity type. Premium Deck Set Espeon & Umbreon releases same day.",
        "cats": ["set", "event"],
        "tags": ["Set Release", "Confirmed", "Milestone"],
        "msrp": "",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "collectorstation.com / pokebeach.com",
        "past": False,
    },
    {
        "id": "30th-card-sets",
        "date": "2026-10-16",
        "title": "30th Celebration Card Sets (x9)",
        "desc": "Nine card sets featuring all 27 Starter Pokémon.",
        "cats": ["collection"],
        "tags": ["Collection", "Confirmed"],
        "msrp": "",
        "retailers": ["target", "walmart", "bestbuy", "pc"],
        "source": "pokebeach.com",
        "past": False,
    },
    {
        "id": "aura-seeker-rumored",
        "date": "2026-11-01",
        "title": "Aura Seeker (Mega Lucario Z?) — Rumored",
        "desc": "RUMORED: Possible late 2026 set. No official announcement. Based on Japanese set rumor data.",
        "cats": ["set", "rumored"],
        "tags": ["Rumored", "Set Release"],
        "msrp": "",
        "retailers": [],
        "source": "community rumor",
        "past": False,
    },
]

BASELINE_DROPS = [
    {
        "id": "target-cr-preorder",
        "retailer": "target",
        "title": "Chaos Rising ETB — Target Preorder",
        "date": "2026-04-23",
        "time": "~3:00 AM ET",
        "confidence": "confirmed",
        "desc": "Target preorder links went live overnight. ETB, Booster Bundle, Blister, and Mega Zygarde Box all listed simultaneously.",
        "source": "@PokemonRestocks",
        "past": True,
    },
    {
        "id": "walmart-asc-bundle",
        "retailer": "walmart",
        "title": "Ascended Heroes Booster Bundle Drop",
        "date": "2026-04-23",
        "time": "Wednesday ~12 PM ET",
        "confidence": "confirmed",
        "desc": "Walmart Wednesday drop included Ascended Heroes Booster Bundles at MSRP $26.99.",
        "source": "TrackaLacker",
        "past": True,
    },
    {
        "id": "target-asc-mega-boxes",
        "retailer": "target",
        "title": "Ascended Heroes Mega ex Boxes",
        "date": "2026-04-24",
        "time": "~3:00 AM ET",
        "confidence": "confirmed",
        "desc": "Mega Meganium ex, Mega Emboar ex, Mega Feraligatr ex boxes went live overnight at Target.",
        "source": "pokemon.com",
        "past": False,
    },
    {
        "id": "aldi-mini-tins",
        "retailer": "other",
        "title": "Ascended Heroes Mini Tins at ALDI",
        "date": "2026-04-22",
        "time": "In-Store Only",
        "confidence": "confirmed",
        "desc": "Ascended Heroes Mini Tins spotted at ALDI stores starting April 22. 2 packs + art card + sticker sheet.",
        "source": "@pokemontcgrestocks Threads",
        "past": True,
    },
    {
        "id": "target-cr-launch",
        "retailer": "target",
        "title": "Chaos Rising Full Launch — Target",
        "date": "2026-05-22",
        "time": "~3:00 AM ET",
        "confidence": "confirmed",
        "desc": "Chaos Rising ETB, Booster Bundle, and 3-Pack Blister expected live overnight. Preorder links already confirmed.",
        "source": "@PokemonRestocks / TrackaLacker",
        "past": False,
    },
    {
        "id": "walmart-cr-wednesday",
        "retailer": "walmart",
        "title": "Chaos Rising — Walmart Wednesday",
        "date": "2026-05-27",
        "time": "~12:00 PM ET (9 AM for Walmart+)",
        "confidence": "likely",
        "desc": "Walmart Wednesday drop expected week of May 22. ETB + Bundle + Blister. Walmart+ members get 3-hr early access.",
        "source": "TrackaLacker pattern data",
        "past": False,
    },
    {
        "id": "bestbuy-cr-app",
        "retailer": "bestbuy",
        "title": "Chaos Rising ETB — Best Buy App Invite",
        "date": "2026-05-22",
        "time": "App notification",
        "confidence": "likely",
        "desc": "Best Buy historically uses app invites for high-demand ETBs. Download the Best Buy app and enable notifications before May 22.",
        "source": "@PokemonRestocks",
        "past": False,
    },
    {
        "id": "pc-cr-etb",
        "retailer": "pc",
        "title": "Chaos Rising PC ETB Drop",
        "date": "2026-05-22",
        "time": "~10:00 AM ET",
        "confidence": "confirmed",
        "desc": "Pokémon Center exclusive ETB with 11 packs + stamped Fennekin promo. MSRP $64.99. Expect virtual queue.",
        "source": "pokemoncenter.com preorder",
        "past": False,
    },
    {
        "id": "pc-cr-display",
        "retailer": "pc",
        "title": "Chaos Rising Booster Display Box 36pk",
        "date": "2026-05-22",
        "time": "~10:00 AM ET",
        "confidence": "confirmed",
        "desc": "36-pack display box at Pokémon Center. $159.99. Sells out within minutes. Have the page queued at 10 AM.",
        "source": "pokemoncenter.com",
        "past": False,
    },
    {
        "id": "target-cr-blister-restock",
        "retailer": "target",
        "title": "Chaos Rising 3-Pack Blister Restock",
        "date": "2026-06-06",
        "time": "~3:00 PM ET Friday",
        "confidence": "likely",
        "desc": "Blisters typically see a restock wave 1–2 weeks after initial launch. Watch Friday afternoon windows at Target.",
        "source": "TrackaLacker historical pattern",
        "past": False,
    },
    {
        "id": "walmart-pb-preorder",
        "retailer": "walmart",
        "title": "Pitch Black (Mega Darkrai ex) — Preorder Expected",
        "date": "2026-06-20",
        "time": "TBD",
        "confidence": "rumored",
        "desc": "Walmart typically lists preorders 4–6 weeks before launch. Pitch Black releases July 17 — watch for preorder listings around mid-June.",
        "source": "Pattern analysis",
        "past": False,
    },
    {
        "id": "target-pb-launch",
        "retailer": "target",
        "title": "Pitch Black Full Launch — Target",
        "date": "2026-07-17",
        "time": "~3:00 AM ET",
        "confidence": "confirmed",
        "desc": "Pitch Black (Mega Darkrai ex) launch day. ETB, Bundle, Blister expected. Prereleases Jul 4–12 at LGS.",
        "source": "pokebeach.com",
        "past": False,
    },
    {
        "id": "pc-30th-drop",
        "retailer": "pc",
        "title": "30th Celebration Set — PC Drop",
        "date": "2026-09-18",
        "time": "~10:00 AM ET",
        "confidence": "confirmed",
        "desc": "Historic worldwide simultaneous launch. All-foil 6-card packs. Virtual queue almost certain. Biggest TCG drop of 2026.",
        "source": "pokebeach.com / collectorstation.com",
        "past": False,
    },
]


# ─────────────────────────────────────────────────────────────────────
# SCRAPERS
# ─────────────────────────────────────────────────────────────────────

def scrape_pokebeach():
    """Scrape PokeBeach news for new set announcements and release dates."""
    new_events = []
    try:
        r = requests.get(
            "https://www.pokebeach.com/category/news",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("article.entry")[:10]

        for article in articles:
            title_el = article.select_one("h2.entry-title a, h1.entry-title a")
            date_el = article.select_one("time.entry-date")
            excerpt_el = article.select_one(".entry-summary, .entry-content p")

            if not title_el:
                continue

            title = title_el.text.strip()
            url = title_el.get("href", "")
            pub_date = date_el.get("datetime", "")[:10] if date_el else ""
            excerpt = excerpt_el.text.strip()[:200] if excerpt_el else ""

            # Look for release date patterns in title/excerpt
            date_match = re.search(
                r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(202\d)',
                title + " " + excerpt, re.I
            )

            # Only add if it mentions a new set or product
            keywords = ["release", "expansion", "set", "etb", "collection", "launch", "prerelease"]
            if any(kw in title.lower() or kw in excerpt.lower() for kw in keywords):
                event_date = ""
                if date_match:
                    month_map = {
                        "january":"01","february":"02","march":"03","april":"04",
                        "may":"05","june":"06","july":"07","august":"08",
                        "september":"09","october":"10","november":"11","december":"12"
                    }
                    m = month_map.get(date_match.group(1).lower(), "01")
                    d = date_match.group(2).zfill(2)
                    y = date_match.group(3)
                    event_date = f"{y}-{m}-{d}"

                new_events.append({
                    "id": f"pb-scraped-{hash(title) % 100000}",
                    "date": event_date or pub_date,
                    "title": f"[PokeBeach] {title[:80]}",
                    "desc": excerpt or "New article from PokeBeach.",
                    "cats": ["set" if "expansion" in title.lower() or "set" in title.lower() else "product"],
                    "tags": ["News", "PokeBeach"],
                    "msrp": "",
                    "retailers": [],
                    "source": f"pokebeach.com — {url}",
                    "past": False,
                    "scraped": True,
                })
        log.info(f"PokeBeach: scraped {len(new_events)} articles")
    except Exception as e:
        log.warning(f"PokeBeach scrape failed: {e}")
    return new_events


def scrape_collectorstation():
    """Scrape CollectorStation for TCG release schedule updates."""
    new_events = []
    try:
        r = requests.get(
            "https://collectorstation.com/pokemon-tcg-schedule-upcoming-sets",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")

        # Look for date/set mentions in article content
        content = soup.get_text()
        # Find patterns like "July 17, 2026" or "September 18, 2026"
        date_patterns = re.findall(
            r'([A-Z][a-z]+ \d{1,2},? 202\d)[^\n]*?([A-Z][^.]+(?:ex|ETB|release|launch|set)[^.]*)\.',
            content
        )

        for dp in date_patterns[:5]:
            try:
                date_str_raw = dp[0].replace(",", "")
                parsed = datetime.strptime(date_str_raw.strip(), "%B %d %Y")
                event_date = parsed.strftime("%Y-%m-%d")
                event_title = dp[1].strip()[:80]
                new_events.append({
                    "id": f"cs-scraped-{hash(event_title) % 100000}",
                    "date": event_date,
                    "title": f"[Schedule] {event_title}",
                    "desc": "Sourced from CollectorStation TCG release schedule.",
                    "cats": ["set"],
                    "tags": ["News", "CollectorStation"],
                    "msrp": "",
                    "retailers": [],
                    "source": "collectorstation.com",
                    "past": False,
                    "scraped": True,
                })
            except Exception:
                continue

        log.info(f"CollectorStation: scraped {len(new_events)} events")
    except Exception as e:
        log.warning(f"CollectorStation scrape failed: {e}")
    return new_events


def scrape_pokemon_news():
    """Scrape official Pokemon.com news for product announcements."""
    new_events = []
    try:
        r = requests.get(
            "https://www.pokemon.com/us/pokemon-news/",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select(".news-item, article, .content-item")[:8]

        for article in articles:
            title_el = article.select_one("h2, h3, .title, a")
            date_el = article.select_one("time, .date")
            excerpt_el = article.select_one("p, .excerpt, .summary")

            if not title_el:
                continue

            title = title_el.text.strip()
            pub_date = date_el.text.strip() if date_el else ""
            excerpt = excerpt_el.text.strip()[:200] if excerpt_el else ""

            if not title or len(title) < 5:
                continue

            keywords = ["release", "expansion", "tcg", "etb", "collection", "product", "set"]
            if any(kw in title.lower() or kw in excerpt.lower() for kw in keywords):
                new_events.append({
                    "id": f"pkm-scraped-{hash(title) % 100000}",
                    "date": "",
                    "title": f"[Pokemon.com] {title[:80]}",
                    "desc": excerpt or "New announcement from Pokemon.com.",
                    "cats": ["product"],
                    "tags": ["Official", "Pokemon.com"],
                    "msrp": "",
                    "retailers": [],
                    "source": "pokemon.com/us/pokemon-news",
                    "past": False,
                    "scraped": True,
                })

        log.info(f"Pokemon.com: scraped {len(new_events)} articles")
    except Exception as e:
        log.warning(f"Pokemon.com news scrape failed: {e}")
    return new_events


def scrape_retail_drops():
    """Scrape TrackaLacker for recent drop intelligence."""
    new_drops = []
    try:
        r = requests.get(
            "https://www.trackalacker.com/articles/news/walmart-wednesday-pokemon-card-drops",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.get_text()

        # Look for drop-related mentions with times
        drop_patterns = re.findall(
            r'((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[^.]*(?:\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*ET)[^.]*)\.',
            content, re.I
        )

        for dp in drop_patterns[:3]:
            new_drops.append({
                "id": f"tl-scraped-{hash(dp) % 100000}",
                "retailer": "walmart" if "walmart" in dp.lower() else "target" if "target" in dp.lower() else "other",
                "title": f"[TrackaLacker] Drop Pattern Update",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": re.search(r'\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*ET', dp, re.I).group(0) if re.search(r'\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*ET', dp, re.I) else "TBD",
                "confidence": "likely",
                "desc": dp.strip()[:200],
                "source": "trackalacker.com",
                "past": False,
                "scraped": True,
            })
        log.info(f"TrackaLacker: scraped {len(new_drops)} drop patterns")
    except Exception as e:
        log.warning(f"TrackaLacker scrape failed: {e}")
    return new_drops


# ─────────────────────────────────────────────────────────────────────
# MERGE & DEDUPLICATE
# ─────────────────────────────────────────────────────────────────────

def merge_events(baseline, scraped):
    """Merge scraped events into baseline, avoiding duplicates by ID."""
    existing_ids = {e["id"] for e in baseline}
    merged = list(baseline)
    added = 0
    for event in scraped:
        if event["id"] not in existing_ids:
            # Only add if it has a date or meaningful content
            if event.get("date") or len(event.get("desc", "")) > 20:
                merged.append(event)
                existing_ids.add(event["id"])
                added += 1
    log.info(f"Merged {added} new events from scraping")
    return sorted(merged, key=lambda e: e.get("date") or "9999")


def merge_drops(baseline, scraped):
    """Merge scraped drops into baseline."""
    existing_ids = {d["id"] for d in baseline}
    merged = list(baseline)
    added = 0
    for drop in scraped:
        if drop["id"] not in existing_ids:
            merged.append(drop)
            existing_ids.add(drop["id"])
            added += 1
    log.info(f"Merged {added} new drops from scraping")
    return sorted(merged, key=lambda d: d.get("date") or "9999")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def run_news_scrape():
    """Run all scrapers and write output JSON files."""
    log.info("=" * 50)
    log.info(f"News scrape started — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Scrape all sources
    scraped_events = []
    scraped_events += scrape_pokebeach()
    scraped_events += scrape_collectorstation()
    scraped_events += scrape_pokemon_news()

    scraped_drops = []
    scraped_drops += scrape_retail_drops()

    # Merge with baseline
    all_events = merge_events(BASELINE_EVENTS, scraped_events)
    all_drops = merge_drops(BASELINE_DROPS, scraped_drops)

    # Write future_events.json
    output_events = {
        "last_updated": datetime.now().isoformat(),
        "source_urls": [
            "pokebeach.com",
            "collectorstation.com",
            "pokemon.com",
            "tcgradar.eu",
        ],
        "events": all_events,
    }
    with open(FUTURE_FILE, "w") as f:
        json.dump(output_events, f, indent=2)
    log.info(f"Wrote {len(all_events)} events → future_events.json")

    # Write retail_drops.json
    output_drops = {
        "last_updated": datetime.now().isoformat(),
        "source_urls": [
            "trackalacker.com",
            "@PokemonRestocks",
            "pokebeach.com",
        ],
        "drops": all_drops,
    }
    with open(DROPS_FILE, "w") as f:
        json.dump(output_drops, f, indent=2)
    log.info(f"Wrote {len(all_drops)} drops → retail_drops.json")
    log.info("News scrape complete")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )
    run_news_scrape()
