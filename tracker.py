#!/usr/bin/env python3
"""
TCG Restock Tracker
Monitors Target, Walmart, and Best Buy for TCG bundles, boxes, and assortments.
Sends alerts via email, SMS (Twilio), or push notification (ntfy.sh).
"""
# ─────────────────────────────────────────────
# Python version check (v6.0.0 step 4.6)
# Fail fast with a helpful error if launched on the wrong Python.
# Bare `python` may resolve to a different install than `py -3.14`.
# See README.md "🐍 Python Setup" section.
# ─────────────────────────────────────────────
import sys
if sys.version_info < (3, 14):
    sys.exit(
        f"ERROR: tracker.py requires Python 3.14+ "
        f"(you have {sys.version_info.major}.{sys.version_info.minor}).\n"
        f"On Windows, run: py -3.14 tracker.py  (or use tracker.bat)"
    )

import requests
import json
import time
import schedule
import logging
import logging.handlers
import os
import re
from datetime import datetime
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict, field
from typing import Optional
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─────────────────────────────────────────────
# CONFIGURATION - Edit this section
# ─────────────────────────────────────────────
# -- CONFIG ----------------------------------------------------------------
# Sensitive values (ntfy topic, location, email credentials) live in
# %LOCALAPPDATA%\tcg_tracker\config.json - NOT in this source file.
#
# To set up the local config:    python tools/setup_config.py
# To rotate any sensitive value: edit config.json directly, then restart.
# To enable email notifications: edit config.json to fill in
#                                 email_sender, email_password,
#                                 email_recipient, then set
#                                 notify_email below to True.
# --------------------------------------------------------------------------

from shared import load_local_config, ConfigError, DATA_DIR

try:
    _local_cfg = load_local_config()
except ConfigError as e:
    print("\n" + "=" * 60)
    print("  TCG Tracker - Configuration Error")
    print("=" * 60)
    print("\n" + str(e) + "\n")
    print("=" * 60 + "\n")
    raise SystemExit(1)

CONFIG = {
    # -- Operational settings (overridable via config.json) -------------
    "check_interval_minutes": _local_cfg["check_interval_minutes"],
    "request_timeout":        15,
    "delay_between_requests": 3,
    "history_file":           "restock_history.json",

    # -- Log file (absolute path, runnable from any working directory) --
    "log_file": os.path.join(DATA_DIR, "tcg_tracker.log"),

    # -- Notification methods --------------------------------------------
    "notify_push":  _local_cfg["notify_push"],
    "notify_email": False,   # Set True after filling email_* in config.json

    # -- ntfy.sh push (sensitive: from config.json) ----------------------
    "ntfy_topic":   _local_cfg["ntfy_topic"],

    # -- Email settings --------------------------------------------------
    # Operational (inline defaults, not sensitive)
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    # Sensitive (from config.json - empty until you fill them in)
    "email_sender":    _local_cfg.get("email_sender",    ""),
    "email_password":  _local_cfg.get("email_password",  ""),
    "email_recipient": _local_cfg.get("email_recipient", ""),

    # -- Location (from config.json) -------------------------------------
    # Currently informational; plugins will migrate to read these in
    # future sessions instead of using their own hardcoded constants.
    "home_zip":         _local_cfg["home_zip"],
    "home_city":        _local_cfg["home_city"],
    "anchor_locations": _local_cfg["anchor_locations"],
}

# NOTE: Do NOT add hardcoded fallbacks for sensitive values.  If config
# is missing, load_local_config() raises and we exit cleanly above.
# Silent fallbacks would mask configuration mistakes and re-introduce
# hardcoded secrets.

# ─────────────────────────────────────────────
# TCG PRODUCTS TO TRACK
# Add or remove products here.
# For each product provide a name and the product URL from the retailer.
# ─────────────────────────────────────────────
PRODUCTS = [

    # ══════════════════════════════════════════
    # TARGET - Elite Trainer Boxes
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV9 Journey Together ETB",
        "retailer": "target",
        "url": "https://www.target.com/p/2025-pok-233-mon-scarlet-violet-s9-elite-trainer-box/-/A-93803439",
        "sku": "93803439",
    },
    {
        "name": "Pokemon Mega Evolution Ascended Heroes ETB",
        "retailer": "target",
        "url": "https://www.target.com/p/2025-pok-me-2-5-elite-trainer-box/-/A-95082118",
        "sku": "95082118",
    },
    {
        "name": "Pokemon Mega Evolution Perfect Order ETB",
        "retailer": "target",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-mega-evolution-perfect-order-elite-trainer-box/-/A-95230445",
        "sku": "95230445",
    },
    {
        "name": "Pokemon Mega Evolution Phantasmal Flames ETB",
        "retailer": "target",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-mega-evolution-8212-phantasmal-flames-elite-trainer-box/-/A-94860231",
        "sku": "94860231",
    },
    {
        "name": "Pokemon Prismatic Evolutions ETB",
        "retailer": "target",
        "url": "https://www.target.com/p/2024-pok-scarlet-violet-s8-5-elite-trainer-box/-/A-93954435",
        "sku": "93954435",
    },

    # ══════════════════════════════════════════
    # TARGET - Booster Bundles
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV9 Journey Together Booster Bundle",
        "retailer": "target",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-scarlet-38-violet-8212-journey-together-booster-bundle/-/A-94300074",
        "sku": "94300074",
    },
    {
        "name": "Pokemon SV10 Destined Rivals Booster Bundle",
        "retailer": "target",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-scarlet-38-violet-8212-destined-rivals-booster-bundle/-/A-94681770",
        "sku": "94681770",
    },
    {
        "name": "Pokemon Mega Evolution Booster Bundle",
        "retailer": "target",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-mega-evolution-booster-bundle/-/A-94681782",
        "sku": "94681782",
    },
    {
        "name": "Pokemon SV8.5 Prismatic Evolutions Booster Bundle",
        "retailer": "target",
        "url": "https://www.target.com/p/pok-233-mon-trading-card-game-scarlet-38-violet-prismatic-evolutions-booster-bundle/-/A-93954446",
        "sku": "93954446",
    },

    # ══════════════════════════════════════════
    # WALMART - Elite Trainer Boxes
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV9 Journey Together ETB",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/Pokemon-Journey-Together-SV09-Elite-Trainer-Box/15156564532",
        "item_id": "15156564532",
    },
    {
        "name": "Pokemon SV10 Destined Rivals ETB",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/Pok-mon-TCG-Scarlet-Violet-Destined-Rivals-Pok-mon-Center-Elite-Trainer-Box/15718673510",
        "item_id": "15718673510",
    },
    {
        "name": "Pokemon SV10.5 Black Bolt ETB",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/Pokemon-TCG-Scarlet-Violet-10-5-Black-Bolt-Elite-Trainer-Box-9-Packs-Promo-Card/16498668973",
        "item_id": "16498668973",
    },
    {
        "name": "Pokemon SV10.5 White Flare ETB",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/Pokemon-TCG-Scarlet-Violet-10-5-White-Flare-Elite-Trainer-Box-9-Packs-Promo-Card/16446322202",
        "item_id": "16446322202",
    },
    {
        "name": "Pokemon Prismatic Evolutions ETB",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/Pokemon-Scarlet-Violet-Prismatic-Evolutions-Elite-Trainer-Box/13816151308",
        "item_id": "13816151308",
        "alt_item_ids": ["15160152062", "15116619982"],  # Walmart relists under multiple IDs
    },

    # ══════════════════════════════════════════
    # WALMART - Booster Bundles
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV10 Destined Rivals Booster Bundle",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/Pokemon-TCG-Scarlet-Violet-Destined-Rivals-Booster-Bundle-6-Packs/16019713971",
        "item_id": "16019713971",
    },
    {
        "name": "Pokemon SV10 Destined Rivals Booster Bundle (Alt)",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/TCG-Scarlet-Violet-Destined-Rivals-Booster-Bundle-6-Packs/15700422581",
        "item_id": "15700422581",
    },

    # ══════════════════════════════════════════
    # BEST BUY - Elite Trainer Boxes
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV9 Journey Together ETB",
        "retailer": "bestbuy",
        "url": "https://www.bestbuy.com/product/pokemon-trading-card-game-scarlet-violet-journey-together-elite-trainer-box/JJG2TLCFTX",
        "sku": "6614267",
    },
    {
        "name": "Pokemon SV10 Destined Rivals ETB",
        "retailer": "bestbuy",
        "url": "https://www.bestbuy.com/product/pokemon-trading-card-game-scarlet-violet-destined-rivals-elite-trainer-box/JJG2TL22PF",
        "sku": "6629999",
    },
    {
        "name": "Pokemon Prismatic Evolutions ETB",
        "retailer": "bestbuy",
        "url": "https://www.bestbuy.com/product/pokemon-trading-card-game-scarlet-violet-prismatic-evolutions-elite-trainer-box/JJG2TLCW3L",
        "sku": "6606082",
    },

    # ══════════════════════════════════════════
    # BEST BUY - Booster Bundles
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV9 Journey Together Booster Bundle 6pk",
        "retailer": "bestbuy",
        "url": "https://www.bestbuy.com/site/pokemon-trading-card-game-scarlet-violet-journey-together-booster-bundle-6-pk/6614264.p",
        "sku": "6614264",
    },
    {
        "name": "Pokemon SV10.5 Black Bolt Booster Bundle",
        "retailer": "bestbuy",
        "url": "https://www.bestbuy.com/product/pokemon-trading-card-game-scarlet-violet-black-bolt-booster-bundle/JJG2TLX84Q",
        "sku": "6629998",
    },
    {
        "name": "Pokemon SV8.5 Prismatic Evolutions Booster Bundle",
        "retailer": "bestbuy",
        "url": "https://www.bestbuy.com/site/pokemon-trading-card-game-scarlet-violet-prismatic-evolutions-booster-bundle/6608206.p",
        "sku": "6608206",
    },

    # ══════════════════════════════════════════
    # POKEMON CENTER - Elite Trainer Boxes
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV9 Journey Together PC Elite Trainer Box",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/100-10356/pokemon-tcg-scarlet-and-violet-journey-together-pokemon-center-elite-trainer-box",
    },
    {
        "name": "Pokemon SV10 Destined Rivals PC Elite Trainer Box",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/100-10653/pokemon-tcg-scarlet-and-violet-destined-rivals-pokemon-center-elite-trainer-box",
    },
    {
        "name": "Pokemon Mega Evolution Ascended Heroes PC Elite Trainer Box",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/10-10315-108/pokemon-tcg-mega-evolution-ascended-heroes-pokemon-center-elite-trainer-box",
    },
    {
        "name": "Pokemon Mega Evolution Perfect Order PC Elite Trainer Box",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/10-10372-109/pokemon-tcg-mega-evolution-perfect-order-pokemon-center-elite-trainer-box",
    },
    {
        "name": "Pokemon Mega Evolution Gardevoir PC Elite Trainer Box",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/10-10047-120/pokemon-tcg-mega-evolution-pokemon-center-elite-trainer-box-mega-gardevoir",
    },
    {
        "name": "Pokemon SV10.5 Black Bolt PC Elite Trainer Box",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/10-10037-118/pokemon-tcg-scarlet-and-violet-black-bolt-pokemon-center-elite-trainer-box",
    },

    # ══════════════════════════════════════════
    # POKEMON CENTER - Booster Bundles
    # ══════════════════════════════════════════
    {
        "name": "Pokemon SV9 Journey Together Booster Bundle 6pk",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/100-10341/pokemon-tcg-scarlet-and-violet-journey-together-booster-bundle-6-packs",
    },
    {
        "name": "Pokemon SV10 Destined Rivals Booster Bundle 6pk",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/100-10638/pokemon-tcg-scarlet-and-violet-destined-rivals-booster-bundle-6-packs",
    },
    {
        "name": "Pokemon SV10 Destined Rivals Booster Display Box 36pk",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/10-10157-101/pokemon-tcg-scarlet-and-violet-destined-rivals-booster-display-box-36-packs",
    },
    {
        "name": "Pokemon SV9 Journey Together Enhanced Display Box 36pk",
        "retailer": "pokemoncenter",
        "url": "https://www.pokemoncenter.com/product/10-10125-102/pokemon-tcg-scarlet-and-violet-journey-together-enhanced-booster-display-box-36-packs-and-1-promo-card",
    },
]

# ─────────────────────────────────────────────
# KEYWORD SEARCH - auto-discover new TCG drops
# These terms are used to search each retailer for new listings
# ─────────────────────────────────────────────
SEARCH_TERMS = [
    "pokemon booster box",
    "pokemon elite trainer box",
    "pokemon bundle",
    "one piece card game booster",
    "yugioh booster box",
    "magic the gathering bundle",
    "lorcana booster box",
    "dragon ball super card game",
    "digimon card game booster",
]

# ─────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG["log_file"]),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

from shared import launch_chromium_with_fallback  # v6.1.2 step 2: ICU bug fix
from shared import BROWSER_PROFILES  # v6.1.4 step 2b: per-plugin profile dirs
from shared import (
    HEADERS, HEADERS_JSON, ROOT_DIR, OUTPUT_DIR, DATA_DIR, BROWSER_PROFILE,
    send_ntfy as _shared_send_ntfy,
    save_json, load_history as _shared_load_history,
    save_history as _shared_save_history,
)

# Add plugins/ subfolder to import path so plugin modules resolve correctly
import sys as _sys
_plugins_dir = os.path.join(ROOT_DIR, "plugins")
if _plugins_dir not in _sys.path:
    _sys.path.insert(0, _plugins_dir)


# ─────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────
@dataclass
class ProductStatus:
    name: str
    retailer: str
    url: str
    in_stock: bool
    price: Optional[str]
    checked_at: str
    was_in_stock: Optional[bool] = None  # previous state


# ─────────────────────────────────────────────
# History (persist stock state across runs)
# ─────────────────────────────────────────────
def load_history() -> dict:
    return _shared_load_history(CONFIG["history_file"])


def save_history(history: dict):
    _shared_save_history(CONFIG["history_file"], history)


# ─────────────────────────────────────────────
# Retailer checkers
# ─────────────────────────────────────────────

def debug_target(product: dict):
    """Run: python tracker.py debug"""
    url = product["url"]
    print(f"\n{'='*60}")
    print(f"DEBUG TARGET: {product['name']}")
    print(f"URL: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"Status: {r.status_code}")
        text = r.text

        avail = re.findall(r'"availability_status"\s*:\s*"([A-Z_]+)"', text)
        buttons = re.findall(r'"buttonLabel"\s*:\s*"([^"]+)"', text)
        atc = re.findall(r'"addToCartButtonText"\s*:\s*"([^"]+)"', text)
        price = re.findall(r'"formatted_current_price"\s*:\s*"([^"]+)"', text)
        preloaded = bool(re.search(r'window\.__PRELOADED_STATE__', text))

        print(f"  availability_status values : {list(set(avail))}")
        print(f"  buttonLabel values         : {list(set(buttons))}")
        print(f"  addToCartButtonText values : {list(set(atc))}")
        print(f"  formatted_current_price    : {list(set(price))[:3]}")
        print(f"  Has __PRELOADED_STATE__     : {preloaded}")
        print(f"  Contains 'Unavailable'     : {'Unavailable' in text}")
        print(f"  Contains 'Add to cart'     : {'Add to cart' in text}")
        print(f"  Contains 'Out of stock'    : {'Out of stock' in text}")
        print(f"  HTML size                  : {len(text):,} chars")
    except Exception as e:
        print(f"  ERROR: {e}")
    print(f"{'='*60}\n")


def check_target(product: dict) -> ProductStatus:
    """
    Target is fully client-side rendered - uses Playwright.
    Reuses the global browser session if available to avoid
    launching a new browser for every product check.
    """
    in_stock, price = False, "N/A"
    url = product["url"]

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

        # ── Reuse global browser session if already running ──
        global _playwright_instance, _playwright_browser, _playwright_context

        if not hasattr(check_target, "_pw") or check_target._pw is None:
            check_target._pw = sync_playwright().start()
            os.makedirs(BROWSER_PROFILE, exist_ok=True)
            check_target._context = launch_chromium_with_fallback(
                check_target._pw,
                BROWSER_PROFILES["target"],
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",               # No GPU needed for headless
                    "--disable-images",            # Don't load images - faster
                    "--disable-extensions",
                    "--blink-settings=imagesEnabled=false",
                ],
                user_agent=HEADERS["User-Agent"],
                log_prefix="check_target",
            )
            log.debug("Playwright: launched persistent browser session")

        context = check_target._context

        # Block images, fonts, and media to reduce CPU/memory load
        def block_unnecessary(route):
            if route.request.resource_type in ("image", "media", "font", "stylesheet"):
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        page.route("**/*", block_unnecessary)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_selector(
                    "[data-test='fulfillment-cell'], [data-test='add-to-cart-button'], "
                    "[data-test='orderPickup']",
                    timeout=8000
                )
            except PWTimeout:
                pass  # Unavailable items hide the fulfillment section

            rendered = page.content()

            # Check rendered Add to Cart button state
            atc_btn = page.query_selector("[data-test='add-to-cart-button']:not([disabled])")
            unavail_btn = page.query_selector("[data-test='add-to-cart-button'][disabled]")

            if atc_btn and not unavail_btn:
                in_stock = True
            elif unavail_btn:
                in_stock = False

            # Scan rendered JSON for availability_status
            avail_statuses = re.findall(r'"availability_status"\s*:\s*"([A-Z_]+)"', rendered)
            if avail_statuses:
                unavail_set = {"UNAVAILABLE", "OUT_OF_STOCK", "SOLD_OUT"}
                avail_set = {"IN_STOCK", "LIMITED_STOCK", "AVAILABLE_TO_PROMISE"}
                status_set = set(avail_statuses)
                if status_set & unavail_set:
                    in_stock = False
                elif status_set & avail_set and not (status_set & unavail_set):
                    in_stock = True

            # Check button label text
            if re.search(r'"buttonLabel"\s*:\s*"Unavailable"', rendered):
                in_stock = False

            # Price
            price_match = re.search(r'"formatted_current_price"\s*:\s*"([^"]+)"', rendered)
            if price_match:
                price = price_match.group(1)
            else:
                price_el = page.query_selector("[data-test='product-price']")
                if price_el:
                    price = price_el.inner_text().strip()

            log.debug(f"Target {product['name']}: in_stock={in_stock} price={price}")

        finally:
            page.close()  # Close page but keep browser alive

    except Exception as e:
        log.warning(f"Target check error for {product['name']}: {e}")
        # Reset browser session on error so it relaunches next cycle
        try:
            if hasattr(check_target, "_pw") and check_target._pw:
                check_target._context.close()
                check_target._pw.stop()
        except Exception:
            pass
        check_target._pw = None
        in_stock = False

    return ProductStatus(
        name=product["name"],
        retailer="Target",
        url=url,
        in_stock=in_stock,
        price=price,
        checked_at=datetime.now().isoformat(),
    )


def _scrape_target_fallback(url: str):
    """Legacy fallback - kept for compatibility but check_target no longer calls this."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=CONFIG["request_timeout"])
        text = r.text
        avail_matches = re.findall(r'"availability_status"\s*:\s*"([A-Z_]+)"', text)
        unavail = {"UNAVAILABLE", "OUT_OF_STOCK", "SOLD_OUT"}
        avail = {"IN_STOCK", "LIMITED_STOCK", "AVAILABLE_TO_PROMISE"}
        in_stock = False
        if set(avail_matches) & avail and not (set(avail_matches) & unavail):
            in_stock = True
        price_match = re.search(r'"formatted_current_price"\s*:\s*"([^"]+)"', text)
        price = price_match.group(1) if price_match else "N/A"
        return in_stock, price
    except Exception as e:
        log.warning(f"Target fallback error: {e}")
        return False, "N/A"


def check_walmart(product: dict) -> ProductStatus:
    """
    Uses Walmart's product API.
    Tries primary item_id plus any alt_item_ids defined in the product.
    Strictly requires IN_STOCK and Walmart-sold (not marketplace).
    """
    item_id = product.get("item_id", "")
    # Support alternate item IDs for products like Prismatic Evolutions ETB
    # that Walmart relists under multiple IDs
    alt_ids = product.get("alt_item_ids", [])
    all_ids = [item_id] + alt_ids

    in_stock, price = False, "N/A"

    for try_id in all_ids:
        if not try_id:
            continue
        try:
            r = requests.get(
                f"https://www.walmart.com/product/v2/pdpData?itemId={try_id}",
                headers={**HEADERS, "Accept": "application/json"},
                timeout=CONFIG["request_timeout"],
            )
            r.raise_for_status()
            data = r.json()
            item_data = data.get("item", {})
            buying    = item_data.get("buyingOptions", {})
            availability = buying.get("availabilityStatus", "").upper()
            offer_type   = buying.get("offerType", "").upper()

            # ── Seller validation ──────────────────────────────────────────
            # Only alert on Walmart-direct listings. Reject:
            #   - EXTERNAL_SELLER (3rd party marketplace)
            #   - conditionGroupCode in URL (used/refurb/marketplace condition)
            #   - offerType not from Walmart
            is_external = offer_type == "EXTERNAL_SELLER"
            is_marketplace_url = "conditionGroupCode" in product.get("url", "")

            # walmartItemFulfillment is the clearest Walmart-direct signal
            is_walmart_direct = buying.get("walmartItemFulfillment", False)

            # If key is absent entirely, accept only if not flagged as external
            if "walmartItemFulfillment" not in str(buying):
                is_walmart_direct = not is_external and not is_marketplace_url

            item_in_stock = (
                availability == "IN_STOCK"
                and is_walmart_direct
                and not is_external
                and not is_marketplace_url
            )

            # ── Price extraction ───────────────────────────────────────────
            price_info = item_data.get("priceInfo", {})
            item_price = (
                price_info.get("currentPrice", {}).get("priceString")
                or price_info.get("wasPrice", {}).get("priceString")
                or price_info.get("unitPrice", {}).get("priceString")
            )
            if not item_price:
                raw_price = data.get("product", {}).get("priceInfo", {}).get("currentPrice", {}).get("price")
                if raw_price:
                    item_price = f"${float(raw_price):.2f}"

            # ── Price sanity check ─────────────────────────────────────────
            # If price is more than 3.5x MSRP it's a marketplace scalper price
            # Suppress in-stock flag to avoid false MSRP alerts
            if item_price and item_in_stock:
                from shared import get_msrp, parse_price
                msrp = get_msrp(product["name"], "walmart")
                listed = parse_price(item_price)
                if msrp and listed and listed > msrp * 3.5:
                    log.debug(
                        f"Walmart {product['name']}: price ${listed:.2f} is "
                        f"{listed/msrp:.1f}x MSRP - suppressing as marketplace price"
                    )
                    item_in_stock = False

            log.debug(
                f"Walmart {product['name']} (id={try_id}): "
                f"availability={availability} offer={offer_type} "
                f"walmart_direct={is_walmart_direct} in_stock={item_in_stock} price={item_price}"
            )

            if item_price and item_price != "N/A":
                price = item_price

            if item_in_stock:
                in_stock = True
                break

        except Exception as e:
            log.warning(f"Walmart API error for {product['name']} (id={try_id}): {e}")
            if try_id == all_ids[-1]:
                in_stock, fb_price = _scrape_walmart_fallback(product["url"])
                if fb_price != "N/A":
                    price = fb_price

        if not in_stock and try_id != all_ids[-1]:
            time.sleep(CONFIG["delay_between_requests"])

    return ProductStatus(
        name=product["name"],
        retailer="Walmart",
        url=product["url"],
        in_stock=in_stock,
        price=price,
        checked_at=datetime.now().isoformat(),
    )


def _scrape_walmart_fallback(url: str):
    """Fallback: check for Add to cart and absence of out-of-stock signals."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=CONFIG["request_timeout"])
        has_add = "Add to cart" in r.text
        has_oos = bool(re.search(r"out of stock|sold out|currently unavailable", r.text, re.I))
        in_stock = has_add and not has_oos
        price_match = re.search(r'\$[\d,]+\.\d{2}', r.text)
        price = price_match.group(0) if price_match else "N/A"
        return in_stock, price
    except Exception as e:
        log.warning(f"Walmart scrape fallback error: {e}")
        return False, "N/A"


def _check_bestbuy_one(page, product: dict) -> tuple:
    """
    Scrape ONE Best Buy product using a pre-existing Playwright page.

    Returns a tuple (in_stock: bool, price: str, error: Optional[Exception]).
    Raises nothing — all errors caught and returned for the batch wrapper
    to handle uniformly.

    The page's persistent context is reused across products, preserving
    Akamai cookies and avoiding cold-start handshakes per product.
    """
    from playwright.sync_api import TimeoutError as PWTimeout

    url = product.get("url", "")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=18000)
        try:
            page.wait_for_selector(
                ".add-to-cart-button, [data-button-state], "
                ".fulfillment-add-to-cart-button",
                timeout=7000,
            )
        except PWTimeout:
            pass

        content = page.content()

        # Read rendered button state
        btn_el = page.query_selector(
            ".add-to-cart-button, [data-button-state]"
        )
        btn_state = ""
        btn_text  = ""
        if btn_el:
            btn_state = btn_el.get_attribute("data-button-state") or ""
            btn_text  = btn_el.inner_text().strip().upper()

        # Scan rendered JSON fragments
        states = re.findall(r'data-button-state=["\']([^"\']+)["\']', content)
        states += re.findall(r'"buttonState"\s*:\s*"([A-Z_]+)"', content)

        avail_states  = {"ADD_TO_CART"}
        unavail_states = {
            "SOLD_OUT", "COMING_SOON", "PRE_ORDER",
            "CHECK_STORES", "UNAVAILABLE", "NOT_AVAILABLE",
        }

        in_stock = False
        if btn_state in avail_states:
            in_stock = True
        elif btn_state in unavail_states:
            in_stock = False
        elif "ADD TO CART" in btn_text:
            in_stock = True
        elif set(states) & avail_states:
            in_stock = True
        elif set(states) & unavail_states:
            in_stock = False

        # Price
        price = "N/A"
        price_match = re.search(r'"currentPrice"\s*:\s*([\d.]+)', content)
        if price_match:
            price = f"${float(price_match.group(1)):.2f}"
        else:
            price_el = page.query_selector(
                ".priceView-customer-price span, "
                ".priceView-hero-price span"
            )
            if price_el:
                price = price_el.inner_text().strip()

        log.debug(
            f"Best Buy {product['name']}: "
            f"btn_state={btn_state} btn_text={btn_text} "
            f"in_stock={in_stock} price={price}"
        )
        return (in_stock, price, None)

    except Exception as e:
        return (False, "N/A", e)


def check_bestbuy_batch(products: list) -> list:
    """
    Check all Best Buy products in a single daemon thread sharing one
    Playwright session (v6.0.0 step 4.7, enhanced step 4.8).

    Step 4.8 enhancements over 4.7:
      - Cold-start prewarm: navigate to bestbuy.com homepage once before
        product 1, so Chromium + Akamai handshake + HTTP/2 pool are
        already warm. Eliminates the ~18s timeout on the first product.
      - Per-product retry: on transient errors (HTTP/2 protocol error,
        chrome-error pages, sporadic timeout), wait 2s and retry once
        on the same warm page.
      - page.unroute cleanup: tear down route handlers cleanly before
        page.close() to prevent asyncio cancellation noise on shutdown.

    Returns a list of ProductStatus in the same order as input products.

    Per-product errors are isolated — one failing product does not abort
    the batch; remaining products are still checked. Whole-batch failure
    (e.g., browser launch error) marks all products as failed and trips
    the circuit breaker.

    Circuit breaker: shared across the batch. 3 consecutive batch
    failures triggers a 30-minute backoff. State stored on
    check_bestbuy_batch._circuit (function attribute, persists across calls).
    """
    import threading

    if not products:
        return []

    # ── Circuit breaker ──────────────────────────────────────────
    cb = getattr(check_bestbuy_batch, "_circuit",
                 {"failures": 0, "open_until": 0})
    check_bestbuy_batch._circuit = cb

    if cb["failures"] >= 3 and time.time() < cb["open_until"]:
        mins_left = int((cb["open_until"] - time.time()) / 60)
        log.debug(
            f"Best Buy circuit open - skipping batch of {len(products)} "
            f"product(s) ({mins_left} min remaining)"
        )
        return [
            ProductStatus(
                name=p["name"], retailer="Best Buy",
                url=p.get("url", ""), in_stock=False, price="N/A",
                checked_at=datetime.now().isoformat(),
            )
            for p in products
        ]

    log.info(f"[bestbuy_batch] Starting batch check of {len(products)} product(s)...")

    # ── Run all BB products in ONE daemon thread, ONE Playwright session ──
    results: list = [None] * len(products)
    batch_error: list = [None]  # Mutable holder for batch-level error

    def _run():
        # v6.1.6: liveness probe - skip if previous cycle's chromium still
        # has live processes attached to profile dir. Replaced v6.1.5's
        # SingletonLock approach because Chromium-on-Windows doesn't
        # create that file; instead it uses a kernel mutex that's not
        # filesystem-visible. Process-based detection works for all OSes.
        # Reuses the same WMI scan as tools/kill_chromium_zombies.py.
        try:
            tools_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "tools"
            )
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)
            from kill_chromium_zombies import count_processes_using_profile
            n_alive = count_processes_using_profile(
                BROWSER_PROFILES["bestbuy_batch"]
            )
            if n_alive > 0:
                log.warning(
                    f"[bestbuy_batch] {n_alive} chrome.exe still attached "
                    f"to profile dir - previous cycle still alive, "
                    f"skipping this cycle silently"
                )
                batch_error[0] = RuntimeError(
                    "profile_locked_by_previous_run"
                )
                return
        except Exception:
            # Non-fatal - if probe itself fails (import error, WMI query
            # error, etc.), fall through to normal launch. Chromium will
            # hit Settings version is not 1 if a real zombie is present,
            # which is no worse than pre-v6.1.5 behavior.
            pass

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("Playwright not installed - Best Buy checks disabled")
            batch_error[0] = ImportError("playwright not installed")
            return

        try:
            with sync_playwright() as p:
                os.makedirs(BROWSER_PROFILE, exist_ok=True)
                context = launch_chromium_with_fallback(
                    p,
                    BROWSER_PROFILES["bestbuy_batch"],
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--blink-settings=imagesEnabled=false",
                    ],
                    user_agent=HEADERS["User-Agent"],
                    log_prefix="bestbuy_batch",
                )

                page = context.new_page()
                page.route("**/*", lambda r: r.abort()
                    if r.request.resource_type in ("image", "media", "font", "stylesheet")
                    else r.continue_()
                )

                try:
                    # ── Cold-start prewarm (v6.0.0 step 4.8) ──────────
                    # Navigate to BB homepage once before product 1 so
                    # Chromium + Akamai cookies + HTTP/2 pool are warm.
                    # Use 30s timeout for this single cold navigation.
                    # Failure here is non-fatal — products will still
                    # try to load (and may succeed if the homepage
                    # navigation got partway through).
                    try:
                        log.debug("[bestbuy_batch] prewarming session via homepage...")
                        page.goto(
                            "https://www.bestbuy.com/",
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                        page.wait_for_timeout(1500)  # Let JS settle
                        log.debug("[bestbuy_batch] prewarm complete")
                    except Exception as e:
                        log.debug(f"[bestbuy_batch] prewarm failed (continuing): {e}")

                    # ── Per-product check loop with retry ─────────────
                    for i, product in enumerate(products):
                        log.info(f"  [bestbuy_batch] {i+1}/{len(products)} {product['name']}")
                        in_stock, price, err = _check_bestbuy_one(page, product)

                        # Retry once on transient errors (v6.0.0 step 4.8).
                        # Common transient: HTTP/2 protocol error, chrome-
                        # error page, sporadic 18s timeout. Warm session
                        # is preserved, just give it 2s to recover.
                        if err is not None:
                            log.debug(
                                f"  [bestbuy_batch] retry {product['name']} "
                                f"after error: {err}"
                            )
                            time.sleep(2)
                            in_stock, price, err = _check_bestbuy_one(page, product)
                            if err is not None:
                                log.warning(
                                    f"  [bestbuy_batch] error on {product['name']} "
                                    f"(after retry): {err}"
                                )
                            else:
                                log.debug(f"  [bestbuy_batch] retry succeeded for {product['name']}")

                        results[i] = ProductStatus(
                            name=product["name"],
                            retailer="Best Buy",
                            url=product.get("url", ""),
                            in_stock=in_stock,
                            price=price,
                            checked_at=datetime.now().isoformat(),
                        )
                finally:
                    # Tear down route handlers cleanly to prevent asyncio
                    # cancellation noise (v6.0.0 step 4.8). page.unroute
                    # waits for in-flight handlers to complete before
                    # closing — eliminates "Exception in callback" spam.
                    try:
                        page.unroute("**/*")
                    except Exception:
                        pass
                    page.close()
                    context.close()

        except Exception as e:
            batch_error[0] = e

    # Total batch wall-clock budget: 6 products * 25s + ~10s startup +
    # prewarm 30s + retries = ~190s base. v6.1.5 bumped to 360 to
    # accommodate Akamai-retry overhead + chromium teardown observed
    # in production after v6.1.4 unblocked the batch path.
    BATCH_TIMEOUT_SEC = 360  # v6.1.5: was 240

    t = threading.Thread(target=_run, daemon=True, name="bestbuy_batch")
    t.start()
    t.join(timeout=BATCH_TIMEOUT_SEC)

    if t.is_alive():
        log.warning(
            f"Best Buy batch timed out (>{BATCH_TIMEOUT_SEC}s) - "
            f"marking all {len(products)} product(s) as failed"
        )
        cb["failures"] += 1
        if cb["failures"] >= 3:
            cb["open_until"] = time.time() + (30 * 60)
            log.warning(
                "Best Buy circuit breaker OPEN - "
                "backing off 30 minutes after 3 consecutive failures"
            )
        return [
            ProductStatus(
                name=p["name"], retailer="Best Buy",
                url=p.get("url", ""), in_stock=False, price="N/A",
                checked_at=datetime.now().isoformat(),
            )
            for p in products
        ]

    if batch_error[0] is not None:
        # v6.1.5 locked-skip: previous cycle still running. Don't increment
        # circuit breaker (it's not a failure - it's the previous cycle
        # still in progress). Return failure-status products so this cycle
        # still produces output, but spare the CB counter.
        if str(batch_error[0]) == "profile_locked_by_previous_run":
            return [
                ProductStatus(
                    name=p["name"], retailer="Best Buy",
                    url=p.get("url", ""), in_stock=False, price="N/A",
                    checked_at=datetime.now().isoformat(),
                )
                for p in products
            ]
        log.warning(f"Best Buy batch error: {batch_error[0]}")
        cb["failures"] += 1
        if cb["failures"] >= 3:
            cb["open_until"] = time.time() + (30 * 60)
            log.warning(
                "Best Buy circuit breaker OPEN - "
                "backing off 30 minutes after 3 consecutive failures"
            )
        return [
            ProductStatus(
                name=p["name"], retailer="Best Buy",
                url=p.get("url", ""), in_stock=False, price="N/A",
                checked_at=datetime.now().isoformat(),
            )
            for p in products
        ]

    # Success — reset circuit breaker and log summary
    cb["failures"] = 0
    in_stock_count = sum(1 for r in results if r and r.in_stock)
    log.info(
        f"[bestbuy_batch] Batch complete: {in_stock_count}/{len(products)} in stock"
    )

    # Defensive: replace any None results (shouldn't occur) with failure status
    final_results = []
    for i, r in enumerate(results):
        if r is None:
            log.warning(f"  [bestbuy_batch] missing result for {products[i]['name']}")
            final_results.append(ProductStatus(
                name=products[i]["name"], retailer="Best Buy",
                url=products[i].get("url", ""), in_stock=False, price="N/A",
                checked_at=datetime.now().isoformat(),
            ))
        else:
            final_results.append(r)
    return final_results


# Stub kept in CHECKER_MAP for back-compat — the real BB path is the batch
# function called directly from run_checks(). This stub catches any code
# that accidentally calls the per-product path and routes it through batch.
def check_bestbuy(product: dict) -> ProductStatus:
    """
    DEPRECATED in v6.0.0 step 4.7 — use check_bestbuy_batch() instead.

    Kept as a thin shim for back-compat: any direct caller is routed
    through the batch function with a single-element list.
    """
    log.debug(
        f"[bestbuy] direct check_bestbuy() call for {product['name']} - "
        f"routing through batch (1-element)"
    )
    results = check_bestbuy_batch([product])
    return results[0] if results else ProductStatus(
        name=product["name"], retailer="Best Buy",
        url=product.get("url", ""), in_stock=False, price="N/A",
        checked_at=datetime.now().isoformat(),
    )


def _scrape_bestbuy_fallback(url: str):
    """Legacy fallback - kept for compatibility but check_bestbuy no longer calls this."""
    return False, "N/A"


# ─────────────────────────────────────────────
# Search for new TCG drops
# ─────────────────────────────────────────────

def search_bestbuy_new_drops():
    """Search Best Buy for new TCG listings."""
    results = []
    for term in SEARCH_TERMS[:3]:  # limit to avoid rate limiting
        try:
            encoded = requests.utils.quote(term)
            url = f"https://www.bestbuy.com/site/searchpage.jsp?st={encoded}&categoryId=pcmcat232300050013"
            r = requests.get(url, headers=HEADERS, timeout=CONFIG["request_timeout"])
            soup = BeautifulSoup(r.text, "html.parser")
            items = soup.select(".sku-item")[:5]
            for item in items:
                title_el = item.select_one(".sku-title a")
                price_el = item.select_one(".priceView-customer-price span")
                add_btn = item.select_one(".add-to-cart-button")
                if title_el:
                    results.append({
                        "name": title_el.text.strip(),
                        "url": "https://www.bestbuy.com" + title_el.get("href", ""),
                        "price": price_el.text.strip() if price_el else "N/A",
                        "in_stock": add_btn is not None and "disabled" not in add_btn.attrs,
                        "retailer": "Best Buy",
                    })
            time.sleep(CONFIG["delay_between_requests"])
        except Exception as e:
            log.warning(f"Best Buy search error: {e}")
    return results


# ─────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────

def _notify_push(product: ProductStatus):
    """
    Adapts ProductStatus to shared.send_ntfy signature.
    Handles Click action + Buy Now button automatically.
    """
    _shared_send_ntfy(
        topic=CONFIG["ntfy_topic"],
        title=f"IN STOCK: {product.retailer}",
        body=f"{product.name} is IN STOCK!\nPrice: {product.price}",
        url=product.url,
        priority="urgent",
        tags="tada,rotating_light",
    )
    log.info(f"ntfy sent for {product.name}")


def send_email(product: ProductStatus):
    """Send email alert."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🃏 TCG RESTOCK: {product.name} at {product.retailer}"
        msg["From"] = CONFIG["email_sender"]
        msg["To"] = CONFIG["email_recipient"]
        html = f"""
        <h2>🎉 TCG Item Back In Stock!</h2>
        <p><strong>{product.name}</strong> is now available at <strong>{product.retailer}</strong></p>
        <p>Price: {product.price}</p>
        <p><a href="{product.url}" style="background:#e53935;color:white;padding:10px 20px;text-decoration:none;border-radius:4px;">Buy Now</a></p>
        <p><small>Checked at: {product.checked_at}</small></p>
        """
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(CONFIG["smtp_host"], CONFIG["smtp_port"]) as server:
            server.starttls()
            server.login(CONFIG["email_sender"], CONFIG["email_password"])
            server.sendmail(CONFIG["email_sender"], CONFIG["email_recipient"], msg.as_string())
        log.info(f"Email sent for {product.name}")
    except Exception as e:
        log.error(f"Email error: {e}")


def send_sms(product: ProductStatus):
    """Send SMS via Twilio."""
    try:
        from twilio.rest import Client
        client = Client(CONFIG["twilio_account_sid"], CONFIG["twilio_auth_token"])
        client.messages.create(
            body=f"🃏 TCG RESTOCK! {product.name} at {product.retailer} - {product.price}\n{product.url}",
            from_=CONFIG["twilio_from"],
            to=CONFIG["twilio_to"],
        )
        log.info(f"SMS sent for {product.name}")
    except Exception as e:
        log.error(f"SMS error: {e}")


def notify(product: ProductStatus):
    """Dispatch all enabled notifications."""
    log.info(f"RESTOCK ALERT: {product.name} @ {product.retailer} - {product.price}")
    # Hardened CONFIG access (v6.0.0 step 4.8) — all three notification
    # channels now use .get() with default False to prevent KeyError if
    # any key is missing from config.json.
    if CONFIG.get("notify_push", False):
        _notify_push(product)
    if CONFIG.get("notify_email", False):
        send_email(product)
    if CONFIG.get("notify_sms", False):
        send_sms(product)


# ─────────────────────────────────────────────
# Main check loop
# ─────────────────────────────────────────────

def check_pokemoncenter(product: dict) -> ProductStatus:
    """
    Scrapes Pokemon Center product pages.
    Uses JSON-LD structured data as the primary source of truth.
    Only falls back to HTML signals when JSON-LD is missing entirely.

    IMPORTANT: Method 3 (addToCart string search) has been removed because
    Pokemon Center embeds "addToCart" in their JavaScript bundle regardless
    of stock status, causing false positive in-stock detections.
    """
    in_stock, price = False, "N/A"
    ld_found = False

    try:
        r = requests.get(
            product["url"],
            headers=HEADERS,
            timeout=CONFIG["request_timeout"],
        )
        r.raise_for_status()
        text = r.text

        # Method 1: JSON-LD structured data (most reliable)
        # Pokemon Center embeds availability in <script type="application/ld+json">
        ld_matches = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            text, re.DOTALL
        )
        for ld_raw in ld_matches:
            try:
                ld = json.loads(ld_raw)
                offers = ld.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                availability = offers.get("availability", "")

                if not availability:
                    continue

                ld_found = True
                if "InStock" in availability:
                    in_stock = True
                elif any(s in availability for s in
                         ("OutOfStock", "SoldOut", "PreOrder", "Discontinued")):
                    in_stock = False

                offer_price = offers.get("price", None)
                if offer_price:
                    try:
                        price = f"${float(offer_price):.2f}"
                    except (ValueError, TypeError):
                        pass
            except Exception:
                continue

        # Method 2: Explicit SOLD OUT text in HTML
        # Pokemon Center renders this server-side - reliable OOS signal
        if re.search(r'SOLD\s*OUT|sold-out|soldOut', text):
            in_stock = False

        # Method 3: REMOVED - "addToCart" exists in JS bundle regardless of stock
        # Using it caused false positive in-stock on sold-out products like
        # Pokemon SV10 Destined Rivals ETB

        # Method 3 replacement: Only trust HTML add-to-cart if JSON-LD was absent
        # AND we see very specific button markup (not just JS variable names)
        if not ld_found and not in_stock:
            # Look for actual button markup, not JS variables
            has_atc_button = bool(re.search(
                r'<button[^>]+(?:add-to-cart|addToCart)[^>]*>',
                text, re.I
            ))
            has_sold_out = bool(re.search(
                r'SOLD\s*OUT|soldout|sold_out|outOfStock', text, re.I
            ))
            if has_atc_button and not has_sold_out:
                in_stock = True
                log.debug(f"Pokemon Center {product['name']}: in_stock via button markup fallback")

        log.debug(
            f"Pokemon Center {product['name']}: "
            f"in_stock={in_stock} price={price} ld_found={ld_found}"
        )

    except Exception as e:
        log.warning(f"Pokemon Center scrape error for {product['name']}: {e}")

    return ProductStatus(
        name=product["name"],
        retailer="Pokemon Center",
        url=product["url"],
        in_stock=in_stock,
        price=price,
        checked_at=datetime.now().isoformat(),
    )


CHECKER_MAP = {
    "target": check_target,
    "walmart": check_walmart,
    "bestbuy": check_bestbuy,
    "pokemoncenter": check_pokemoncenter,
}


def run_checks():
    log.info("=" * 60)
    log.info(f"Running TCG stock check - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    history = load_history()
    results = []
    bestbuy_products = []  # Collected for batch processing (v6.0.0 step 4.7)

    for product in PRODUCTS:
        retailer = product["retailer"].lower()

        # Best Buy products are batched at the end of the cycle (v6.0.0 step 4.7)
        # for warm-session perf — see check_bestbuy_batch() docstring.
        if retailer == "bestbuy":
            bestbuy_products.append(product)
            continue

        checker = CHECKER_MAP.get(retailer)
        if not checker:
            log.warning(f"No checker for retailer: {retailer}")
            continue

        log.info(f"Checking {product['name']} ({retailer})...")
        status = checker(product)

        prev = history.get(product["url"], {})
        was_in_stock = prev.get("in_stock", None)
        status.was_in_stock = was_in_stock

        # Alert only when transitioning OUT->IN stock (or first run in stock)
        is_new_stock = (status.in_stock and was_in_stock is False) or \
                       (status.in_stock and was_in_stock is None)
        if is_new_stock:
            notify(status)
            try:
                import plugins as _ps
                _ps.notify_stock_change(product, status)
            except Exception:
                pass

        history[product["url"]] = {
            "in_stock": status.in_stock,
            "price": status.price,
            "last_checked": status.checked_at,
            "name": status.name,
            "retailer": status.retailer,
        }
        results.append(status)
        log.info(
            f"  -> {'✅ IN STOCK' if status.in_stock else '❌ Out of stock'} | {status.price}"
        )
        time.sleep(CONFIG["delay_between_requests"])

    # ── Best Buy batch (v6.0.0 step 4.7) ───────────────────────────
    # Run all BB products through ONE Playwright session for warm-session
    # perf. Each batch reuses Akamai cookies + browser process across all
    # products instead of paying cold-start cost per product.
    if bestbuy_products:
        log.info(
            f"Checking {len(bestbuy_products)} Best Buy product(s) in batch..."
        )
        bb_results = check_bestbuy_batch(bestbuy_products)
        for product, status in zip(bestbuy_products, bb_results):
            prev = history.get(product["url"], {})
            was_in_stock = prev.get("in_stock", None)
            status.was_in_stock = was_in_stock

            is_new_stock = (status.in_stock and was_in_stock is False) or \
                           (status.in_stock and was_in_stock is None)
            if is_new_stock:
                notify(status)
                try:
                    import plugins as _ps
                    _ps.notify_stock_change(product, status)
                except Exception:
                    pass

            history[product["url"]] = {
                "in_stock": status.in_stock,
                "price": status.price,
                "last_checked": status.checked_at,
                "name": status.name,
                "retailer": status.retailer,
            }
            results.append(status)
            log.info(
                f"  -> {'✅ IN STOCK' if status.in_stock else '❌ Out of stock'} | {status.price}"
            )

    save_history(history)

    save_json("status_snapshot.json", [asdict(r) for r in results])  # -> data/status_snapshot.json

    log.info(f"Check complete. {sum(r.in_stock for r in results)}/{len(results)} items in stock.")



def main():
    log.info("🃏 TCG Restock Tracker starting...")
    log.info(f"Tracking {len(PRODUCTS)} products across Target, Walmart, Best Buy")
    log.info(f"Checking every {CONFIG['check_interval_minutes']} minutes")
    topic = CONFIG.get("ntfy_topic", "")
    masked_topic = topic[:4] + "****" + topic[-4:] if len(topic) > 8 else "****"
    log.info(f"Push notifications: {'✅' if CONFIG['notify_push'] else '❌'} | "
             f"ntfy topic: {masked_topic}")
    log.info(f"Data directory: {DATA_DIR}")
    log.info(f"Snapshot will write to: {os.path.join(DATA_DIR, 'status_snapshot.json')}")

    # ── Load all plugins via plugin coordinator ──
    import plugins as plugin_system
    from scheduler import Scheduler
    scheduler = Scheduler(schedule)
    loaded = plugin_system.load_plugins(CONFIG, PRODUCTS, scheduler)
    scheduler.boot_ready()

    # ── Wrap run_checks to broadcast events to plugins ──
    _original_run_checks = run_checks

    def run_checks_with_plugins():
        _original_run_checks()
        # Notify plugins that a check cycle completed
        plugin_system.notify_post_check()

    globals()["run_checks"] = run_checks_with_plugins

    # ── Stock checks with adaptive scheduling ──
    def adaptive_run_checks():
        """
        Adjust check frequency based on time of day and day of week.
        Drops almost always happen at specific windows - no need to
        hammer APIs at 4 AM on a Tuesday.
        """
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()  # 0=Mon, 1=Tue, ..., 6=Sun

        # ── Drop windows - check at full 3-min frequency ──
        # Target: overnight 2-4 AM any day (new drops) + Fri 3-6 PM (restocks)
        # Walmart: Wed 9 AM - 2 PM (Wednesday drops)
        # Pokemon Center: 9-11 AM any weekday
        is_target_overnight = 1 <= hour <= 5          # 1-5 AM any day
        is_target_restock = weekday == 4 and 14 <= hour <= 19  # Fri 2-7 PM ET
        is_walmart_wednesday = weekday == 2 and 8 <= hour <= 15  # Wed 8 AM-3 PM
        is_pc_morning = 8 <= hour <= 12                # 8 AM-noon any weekday

        is_hot_window = any([
            is_target_overnight,
            is_target_restock,
            is_walmart_wednesday,
            is_pc_morning,
        ])

        # ── Dead hours - slow way down ──
        # No drops ever happen 10 PM - 1 AM
        is_dead_hours = 22 <= hour or hour == 0

        if is_hot_window:
            interval = CONFIG["check_interval_minutes"]  # Full speed (3 min)
        elif is_dead_hours:
            interval = 15  # Very slow during quiet hours
        else:
            interval = 8   # Normal background pace

        # Reschedule if interval changed
        if not hasattr(adaptive_run_checks, "_last_interval") or \
                adaptive_run_checks._last_interval != interval:
            schedule.clear("stock_check")
            schedule.every(interval).minutes.do(adaptive_run_checks).tag("stock_check")
            adaptive_run_checks._last_interval = interval
            log.info(f"Check interval adjusted to {interval} min "
                     f"({'hot window' if is_hot_window else 'dead hours' if is_dead_hours else 'normal'})")

        run_checks()

    run_checks()
    schedule.every(CONFIG["check_interval_minutes"]).minutes.do(adaptive_run_checks).tag("stock_check")

    # ── Graceful shutdown handler ──
    import signal

    def _shutdown(signum, frame):
        log.info("Shutdown signal received - closing cleanly...")

        # Stop all plugins
        try:
            plugin_system.stop_all()
        except Exception as e:
            log.debug(f"Plugin shutdown error: {e}")

        # Close Playwright browser sessions if open
        for checker_fn, label in [
            (check_target, "Target"),
            (check_bestbuy, "Best Buy"),
        ]:
            try:
                if hasattr(checker_fn, "_pw") and checker_fn._pw:
                    log.info(f"Closing {label} Playwright session...")
                    checker_fn._context.close()
                    checker_fn._pw.stop()
                    checker_fn._pw = None
            except Exception as e:
                log.debug(f"{label} Playwright shutdown error: {e}")

        log.info("Tracker stopped. Goodbye.")
        raise SystemExit(0)

    # Handle Ctrl+C and CMD window close (SIGTERM on Windows)
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Tracker running - press Ctrl+C to stop cleanly")

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except SystemExit:
            break
        except KeyboardInterrupt:
            _shutdown(None, None)


if __name__ == "__main__":
    import sys
    # Run: python tracker.py debug
    # to print raw API responses for the first Target product showing false positive
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        target_products = [p for p in PRODUCTS if p.get("retailer") == "target" and p.get("sku")]
        for p in target_products[:3]:
            debug_target(p)
    else:
        main()
