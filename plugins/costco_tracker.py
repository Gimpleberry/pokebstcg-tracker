#!/usr/bin/env python3
"""
costco_tracker.py -- Costco Pokemon TCG Tracker
Plugin for Keith's PokeBS tracker system.

Monitors Costco.com for Pokemon TCG products and tracks
in-warehouse availability at two specific locations:
  - Cherry Hill, NJ (Warehouse #1142)
  - Princeton, NJ  (Warehouse #0482)

WHY COSTCO IS DIFFERENT:
  - Sells exclusive bundles (2-packs, UPCs) not at other retailers
  - Online drops typically ~11 AM ET and ~5 PM ET
  - Uses a queue system for high-demand drops (20-min window to join)
  - Blocks plain HTTP requests -- requires Playwright
  - No fixed weekly restock day -- tied to warehouse shipments
  - Membership required to purchase (not to browse)

ON DETECTION:
  - Online in-stock: urgent ntfy alert + direct URL + browser opens
  - Queue detected: urgent alert -- you have 20 minutes to join
  - In-store at Cherry Hill or Princeton: high priority ntfy alert

SECURITY:
  - Never purchases anything
  - Never clicks queue join or checkout buttons automatically
  - Browser opens to product page -- YOU take action
  - Costco login not required for stock checking

SETUP:
  Log into Costco in the browser profile to enable faster queue access:
    python plugins/cart_preloader.py --setup --retailer costco
"""

import os
import re
import sys
import time
import logging
from datetime import datetime

# -- Path resolution ----------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "plugins" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)
if _here not in sys.path:
    sys.path.insert(0, _here)
# ----------------------------------------------------------------------------

from shared import (
    DATA_DIR, BROWSER_PROFILE, HEADERS,
    get_msrp, parse_price, send_ntfy,
    open_browser, load_history, save_history,
)

log = logging.getLogger(__name__)

HISTORY_FILE = "costco_tracker_history.json"

# -- Warehouse IDs for your two locations ------------------------------------
# Cherry Hill, NJ -- Costco Warehouse #1142
# Princeton, NJ   -- Costco Warehouse #0482
WAREHOUSE_IDS = {
    "Cherry Hill, NJ": "1142",
    "Princeton, NJ":   "0482",
}

# -- Products to monitor -----------------------------------------------------
# Item numbers from costco.com URLs: .product.ITEMNUMBER.html
# Update this list as Costco adds new Pokemon TCG products.
# Costco typically carries exclusive 2-pack bundles and UPCs.
COSTCO_PRODUCTS = [
    {
        "name":    "Pokemon Prismatic Evolutions SPC 2-Pack Bundle",
        "item":    "4000352232",
        "url":     "https://www.costco.com/pokemon-prismatic-evolutions-spc-2-pack-bundle.product.4000352232.html",
        "msrp":    None,  # Costco bundles -- shared.get_msrp handles estimation
        "notes":   "Exclusive 2-pack. Sells out in minutes online.",
    },
    {
        "name":    "Pokemon Mega Charizard UPC 2-Pack Bundle",
        "item":    "4000351890",
        "url":     "https://www.costco.com/pokemon-mega-charizard-upc-2-pack-bundle.product.4000351890.html",
        "msrp":    None,
        "notes":   "Exclusive bundle. Check for queue system on launch day.",
    },
    {
        "name":    "Pokemon Chaos Rising ETB 2-Pack Bundle",
        "item":    "",  # Update when Costco lists Chaos Rising
        "url":     "https://www.costco.com/trading-cards.html",
        "msrp":    None,
        "notes":   "Expected for Chaos Rising launch May 22, 2026. Update item# when listed.",
    },
    {
        "name":    "Pokemon TCG Paldea Partners Tins 3-Pack",
        "item":    "4000352232",
        "url":     "https://www.costco.com/pokemon-3-pack-paldea-partners-tins.product.4000352232.html",
        "msrp":    None,
        "notes":   "Limit 2 per membership per day.",
    },
]

# Costco availability signals
AVAILABLE_SIGNALS = [
    "add to cart",
    "add-to-cart",
    "addtocart",
    "in stock",
    "available",
    '"availability":"InStock"',
    'availability.*InStock',
]

OOS_SIGNALS = [
    "out of stock",
    "sold out",
    "currently unavailable",
    "check back",
    "expected to be in stock",
    "not available",
    '"availability":"OutOfStock"',
]

QUEUE_SIGNALS = [
    "join the queue",
    "join queue",
    "queue is open",
    "get in line",
    "virtual queue",
]


# -- Core checker -------------------------------------------------------------

class CostcoTracker:
    """
    Monitors Costco.com for Pokemon TCG products.
    Checks online availability + in-warehouse stock at
    Cherry Hill NJ and Princeton NJ locations.
    """

    def __init__(self, config: dict, products: list):
        self.config     = config
        self.ntfy_topic = config.get("ntfy_topic", "")
        self.history    = load_history(HISTORY_FILE)
        # Merge any costco products from main PRODUCTS list
        self.watch_list = list(COSTCO_PRODUCTS)
        for p in products:
            if p.get("retailer", "").lower() == "costco" and p.get("item"):
                if not any(w["item"] == p["item"] for w in self.watch_list):
                    self.watch_list.append({
                        "name":  p["name"],
                        "item":  p["item"],
                        "url":   p["url"],
                        "msrp":  None,
                        "notes": "",
                    })
        # Only check products with valid item numbers
        self.active = [p for p in self.watch_list if p.get("item")]
        log.info(f"[costco] Monitoring {len(self.active)} products")

    def start(self, schedule) -> None:
        """Register scheduled checks."""
        # Check every 15 minutes -- Costco drops are unpredictable
        # but tend to cluster around 11 AM and 5 PM ET
        schedule.every(15).minutes.do(self._check_all_online)

        # In-warehouse check once per day at 9 AM
        # (warehouses stock shelves shortly after opening)
        schedule.every().day.at("09:00").do(self._check_warehouses)

        # Extra online check at known drop windows
        schedule.every().day.at("10:45").do(self._check_all_online)
        schedule.every().day.at("16:45").do(self._check_all_online)

        self._check_all_online()
        log.info("[costco] Scheduled -- 15-min online checks, daily warehouse check at 09:00")

    def stop(self) -> None:
        log.info("[costco] Stopped")

    # -- Online check ---------------------------------------------------------

    def _check_all_online(self) -> None:
        """Check all tracked Costco products for online availability."""
        if not self.active:
            return

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            log.warning("[costco] Playwright not installed")
            return

        log.debug(f"[costco] Checking {len(self.active)} products online...")

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    BROWSER_PROFILE,
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--blink-settings=imagesEnabled=false",
                    ],
                    user_agent=HEADERS["User-Agent"],
                )

                page = context.new_page()
                page.route("**/*", lambda r: r.abort()
                    if r.request.resource_type in ("image", "media", "font", "stylesheet")
                    else r.continue_()
                )

                for product in self.active:
                    try:
                        self._check_single(page, product)
                        time.sleep(3)
                    except Exception as e:
                        log.debug(f"[costco] Error checking {product['name']}: {e}")

                page.close()
                context.close()

        except Exception as e:
            log.warning(f"[costco] Playwright session error: {e}")

    def _check_single(self, page, product: dict) -> None:
        """Check one Costco product page for availability."""
        from playwright.sync_api import TimeoutError as PWTimeout

        url  = product["url"]
        name = product["name"]
        key  = f"costco_{product['item']}"
        prev = self.history.get(key, {})

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_selector(
                    ".add-to-cart-btn, #add-to-cart-btn, "
                    "[automation-id='addToCartButton'], .e-com-product-actions",
                    timeout=7000
                )
            except PWTimeout:
                pass

            content = page.content().lower()

            # Check for queue system first (highest priority)
            is_queue = any(s in content for s in QUEUE_SIGNALS)

            # Check availability
            is_available = any(s in content for s in AVAILABLE_SIGNALS)
            is_oos       = any(s in content for s in OOS_SIGNALS)

            # Refine: OOS overrides available if both present
            if is_oos:
                is_available = False

            # Try to get price
            price = "N/A"
            price_match = re.search(r'\$\s*(\d+(?:\.\d{2})?)', page.content())
            if price_match:
                price = f"${price_match.group(1)}"

            log.debug(
                f"[costco] {name}: available={is_available} "
                f"queue={is_queue} oos={is_oos} price={price}"
            )

            # -- Queue detected -- urgent alert, 20-minute window
            if is_queue and not prev.get("queue_alerted"):
                log.info(f"[costco] QUEUE DETECTED: {name}")
                self._alert_queue(product, price)
                self.history[key] = {
                    **prev,
                    "queue_alerted": True,
                    "queue_at":      datetime.now().isoformat(),
                    "last_checked":  datetime.now().isoformat(),
                }
                save_history(HISTORY_FILE, self.history)

            # -- Available online -- alert if state changed
            elif is_available and not prev.get("online_alerted"):
                log.info(f"[costco] IN STOCK ONLINE: {name} @ {price}")
                self._alert_online(product, price)
                self.history[key] = {
                    **prev,
                    "online_alerted":  True,
                    "online_at":       datetime.now().isoformat(),
                    "last_checked":    datetime.now().isoformat(),
                    "queue_alerted":   False,
                    "last_price":      price,
                }
                save_history(HISTORY_FILE, self.history)

            # -- No longer available -- reset alert flags
            elif is_oos and (prev.get("online_alerted") or prev.get("queue_alerted")):
                self.history[key] = {
                    **prev,
                    "online_alerted": False,
                    "queue_alerted":  False,
                    "last_checked":   datetime.now().isoformat(),
                }
                save_history(HISTORY_FILE, self.history)

            else:
                self.history[key] = {
                    **prev,
                    "last_checked": datetime.now().isoformat(),
                    "last_price":   price if price != "N/A" else prev.get("last_price"),
                }
                save_history(HISTORY_FILE, self.history)

        except Exception as e:
            log.debug(f"[costco] {name} check error: {e}")

    # -- Warehouse check ------------------------------------------------------

    def _check_warehouses(self) -> None:
        """
        Check in-warehouse availability at Cherry Hill and Princeton.
        Uses Costco's warehouse inventory endpoint.
        """
        import requests as req

        for location, warehouse_id in WAREHOUSE_IDS.items():
            for product in self.active:
                item_id = product["item"]
                if not item_id:
                    continue

                try:
                    # Costco warehouse availability endpoint
                    r = req.get(
                        f"https://www.costco.com/AjaxWarehouseInventoryCmd"
                        f"?warehouseId={warehouse_id}&productId={item_id}",
                        headers=HEADERS,
                        timeout=10,
                    )

                    if r.status_code != 200:
                        log.debug(
                            f"[costco] Warehouse API {r.status_code} for "
                            f"{product['name']} at {location}"
                        )
                        continue

                    data = r.json() if r.headers.get("content-type", "").startswith("application") else {}
                    text = r.text.lower()

                    in_warehouse = (
                        data.get("inWarehouse", False) or
                        '"inwarehouse":true' in text or
                        "in stock" in text
                    )

                    key = f"costco_wh_{item_id}_{warehouse_id}"
                    prev = self.history.get(key, {})

                    if in_warehouse and not prev.get("alerted"):
                        log.info(
                            f"[costco] IN WAREHOUSE: {product['name']} "
                            f"at {location}"
                        )
                        self._alert_warehouse(product, location)
                        self.history[key] = {
                            "alerted":     True,
                            "alerted_at":  datetime.now().isoformat(),
                            "location":    location,
                        }
                        save_history(HISTORY_FILE, self.history)

                    elif not in_warehouse and prev.get("alerted"):
                        # Reset so we alert again on next restock
                        self.history[key] = {
                            "alerted": False,
                            "last_checked": datetime.now().isoformat(),
                        }
                        save_history(HISTORY_FILE, self.history)

                    time.sleep(1)

                except Exception as e:
                    log.debug(
                        f"[costco] Warehouse check error "
                        f"{product['name']} at {location}: {e}"
                    )

    # -- Alerts ---------------------------------------------------------------

    def _alert_online(self, product: dict, price: str) -> None:
        """Alert and open browser for online availability."""
        name = product["name"]
        url  = product["url"]
        msrp = get_msrp(name) or product.get("msrp")

        price_note = ""
        if msrp and price != "N/A":
            listed = parse_price(price)
            if listed:
                price_note = f" (MSRP ~${msrp:.2f})" if listed > msrp else " - AT/BELOW MSRP!"

        send_ntfy(
            topic=self.ntfy_topic,
            title=f"Costco IN STOCK: {name[:45]}",
            body=(
                f"Costco online has this NOW\n"
                f"{name}\n"
                f"Price: {price}{price_note}\n"
                f"Act fast - sells out in minutes"
            ),
            url=url,
            priority="urgent",
            tags="rotating_light,shopping_cart",
        )
        open_browser(
            url,
            banner_title=f"Costco: {name[:40]}",
            banner_msg="In stock online - add to cart and checkout NOW",
        )

    def _alert_queue(self, product: dict, price: str) -> None:
        """Alert for queue system -- 20-minute window."""
        name = product["name"]
        url  = product["url"]

        send_ntfy(
            topic=self.ntfy_topic,
            title=f"Costco QUEUE OPEN: {name[:40]}",
            body=(
                f"QUEUE IS OPEN - 20 MINUTES TO JOIN\n"
                f"{name}\n"
                f"Price: {price}\n"
                f"Open NOW and join the queue immediately"
            ),
            url=url,
            priority="urgent",
            tags="rotating_light,hourglass_flowing_sand",
        )
        open_browser(
            url,
            banner_title=f"COSTCO QUEUE: {name[:35]}",
            banner_msg="20-MINUTE WINDOW - Join the queue NOW",
        )

    def _alert_warehouse(self, product: dict, location: str) -> None:
        """Alert for in-warehouse stock."""
        name = product["name"]
        url  = product["url"]

        send_ntfy(
            topic=self.ntfy_topic,
            title=f"Costco In-Store: {location}",
            body=(
                f"In-warehouse stock detected\n"
                f"{name}\n"
                f"Location: {location}\n"
                f"Warehouses stock shelves shortly after opening - go early"
            ),
            url=url,
            priority="high",
            tags="department_store,tada",
        )

    def get_status_summary(self) -> list[dict]:
        """Return current status for all watched products."""
        results = []
        for product in self.watch_list:
            item  = product.get("item", "")
            key   = f"costco_{item}"
            entry = self.history.get(key, {})
            wh_status = {}
            for loc, wid in WAREHOUSE_IDS.items():
                wh_key = f"costco_wh_{item}_{wid}"
                wh_status[loc] = self.history.get(wh_key, {}).get("alerted", False)
            results.append({
                "name":          product["name"],
                "item":          item,
                "url":           product["url"],
                "notes":         product.get("notes", ""),
                "last_checked":  entry.get("last_checked", "never"),
                "online_stock":  entry.get("online_alerted", False),
                "queue_open":    entry.get("queue_alerted", False),
                "last_price":    entry.get("last_price", "N/A"),
                "warehouses":    wh_status,
            })
        return results


# -- Standalone diagnostic ----------------------------------------------------

def run_diagnostics(config: dict, products: list) -> None:
    """
    Check all Costco products and print status.
    Usage: python plugins/costco_tracker.py
    """
    print("\n" + "=" * 60)
    print("  Costco Tracker -- Diagnostic")
    print("=" * 60)
    print(f"\n  Monitoring locations:")
    for loc, wid in WAREHOUSE_IDS.items():
        print(f"    {loc} (Warehouse #{wid})")
    print()

    tracker = CostcoTracker(config, products)

    print(f"  Checking {len(tracker.active)} products online...\n")
    tracker._check_all_online()
    tracker._check_warehouses()

    summary = tracker.get_status_summary()
    print(f"  {'Product':<45} {'Online':>8} {'Queue':>7} {'Cherry Hill':>12} {'Princeton':>10}")
    print("  " + "-" * 85)
    for r in summary:
        online = "YES" if r["online_stock"]  else "no"
        queue  = "OPEN" if r["queue_open"]   else "no"
        ch     = "YES" if r["warehouses"].get("Cherry Hill, NJ") else "no"
        pr     = "YES" if r["warehouses"].get("Princeton, NJ")   else "no"
        print(
            f"  {r['name'][:44]:<45} {online:>8} {queue:>7} "
            f"{ch:>12} {pr:>10}"
        )
        if r["notes"]:
            print(f"  {'':45} {r['notes'][:40]}")

    print("\n  Add new products by editing COSTCO_PRODUCTS in this file.")
    print("  Update item numbers from Costco URLs: .product.ITEMNUMBER.html")
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )
    try:
        from tracker import CONFIG, PRODUCTS
        run_diagnostics(CONFIG, PRODUCTS)
    except ImportError:
        log.error("Run from tcg_tracker/ directory: python plugins/costco_tracker.py")
