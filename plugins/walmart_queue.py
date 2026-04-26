#!/usr/bin/env python3
"""
walmart_queue.py - Walmart Drop Monitor (#2)
Part of Keith's PokeBS plugin system.

Monitors ALL Walmart Pokemon TCG activity:
  - Wednesday drops (new product launches, 12 PM ET public)
  - Any-day restocks (tracked products flip IN_STOCK via main tracker)
  - Clearance pricing (significant discount from MSRP)
  - Rollback deals (temporary price reductions)
  - New untracked listings appearing on Walmart

On detection:
  - ntfy push notification with direct clickable URL
  - Opens browser to product page - YOU take action

No Walmart+ required. Public drop window targeted at 12 PM ET Wednesday.

SECURITY: Never adds to cart, never clicks purchase buttons, never
touches payment data. Browser opens to product page only.
"""

import requests
import json
import re
import os
import logging
import time
from datetime import datetime

# ── Path resolution - works from root or plugins/ folder ─────────────────────
import sys as _sys, os as _os
_here = _os.path.dirname(_os.path.abspath(__file__))
_root = _os.path.dirname(_here) if _os.path.basename(_here) == "plugins" else _here
if _root not in _sys.path:
    _sys.path.insert(0, _root)
if _here not in _sys.path:
    _sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────
from shared import (
    OUTPUT_DIR, HEADERS, HEADERS_JSON, get_msrp, parse_price,
    send_ntfy, open_browser, load_history, save_history
)

log = logging.getLogger(__name__)

HISTORY_FILE = "walmart_drop_history.json"


class WalmartQueueMonitor:
    """
    Monitors all Walmart Pokemon TCG activity.
    Registered as a plugin via plugins.py WalmartQueue_Plugin.
    """

    def __init__(self, config: dict, products: list):
        self.config           = config
        self.products         = products
        self.ntfy_topic       = config.get("ntfy_topic", "")
        self.history          = load_history(HISTORY_FILE)
        self.walmart_products = [p for p in products if p.get("retailer", "").lower() == "walmart"]
        self._wednesday_watch_active = False

    # ── Plugin lifecycle ──────────────────────────────────────────────

    def start(self, schedule) -> None:
        """Register all scheduled jobs."""
        # Wednesday drop window: alert 15 min early, stop at 2 PM
        schedule.every().wednesday.at("11:45").do(self._start_wednesday_watch)
        schedule.every().wednesday.at("14:00").do(self._stop_wednesday_watch)
        # New listing scans - twice daily
        schedule.every().day.at("07:00").do(self._scan_new_listings)
        schedule.every().day.at("13:00").do(self._scan_new_listings)
        # Clearance/rollback scans - twice daily
        schedule.every().day.at("09:00").do(self._scan_clearance)
        schedule.every().day.at("18:00").do(self._scan_clearance)
        log.info("[walmart_queue] All schedules registered")

    def on_stock_change(self, product: dict, status) -> None:
        """
        Called by plugins.notify_stock_change() when any Walmart product
        transitions to IN_STOCK via the main tracker check cycle.
        Fires alert + opens browser.
        """
        if not status.in_stock:
            return

        url  = product.get("url", "")
        name = product.get("name", "")
        price_str = getattr(status, "price", "N/A")

        # Suppress duplicate alerts for the same in-stock event
        key  = f"stock_{url}"
        prev = self.history.get(key, {})
        if prev.get("alerted_in_stock"):
            return

        comp     = None
        msrp     = get_msrp(name, "walmart")
        listed   = parse_price(price_str)
        price_note = ""
        if msrp and listed:
            savings = msrp - listed
            price_note = f" - AT MSRP" if listed <= msrp else f" - Above MSRP (${msrp:.2f})"

        log.info(f"[walmart_queue] DROP: {name} @ {price_str}{price_note}")

        send_ntfy(
            topic    = self.ntfy_topic,
            title    = f"Walmart IN STOCK: {name[:40]}",
            body     = f"{name}\nPrice: {price_str}{price_note}",
            url      = url,
            priority = "urgent",
            tags     = "rotating_light,shopping_cart",
        )
        open_browser(url, banner_title=f"Walmart Drop: {name[:40]}", banner_msg="Add to cart and checkout - YOU click Place Order")

        self.history[key] = {
            "alerted_in_stock": True,
            "alerted_at":       datetime.now().isoformat(),
            "price":            price_str,
        }
        save_history(HISTORY_FILE, self.history)

    # ── Wednesday watch ───────────────────────────────────────────────

    def _start_wednesday_watch(self):
        if self._wednesday_watch_active:
            return
        self._wednesday_watch_active = True
        log.info("[walmart_queue] Wednesday watch STARTED (public drop: 12 PM ET)")
        send_ntfy(
            topic    = self.ntfy_topic,
            title    = "Walmart Wednesday Watch OPEN",
            body     = "Drop window open - public 12 PM ET\nTracker polling every 3 min\nCheck your Walmart tracked products",
            url      = "https://www.walmart.com/search?q=pokemon+trading+card",
            priority = "high",
            tags     = "eyes,hourglass_flowing_sand",
        )

    def _stop_wednesday_watch(self):
        if not self._wednesday_watch_active:
            return
        self._wednesday_watch_active = False
        log.info("[walmart_queue] Wednesday watch ended")

    # ── New listing scanner ───────────────────────────────────────────

    def _scan_new_listings(self):
        """Search Walmart for Pokemon TCG products not in the tracked list."""
        search_terms = [
            "pokemon elite trainer box 2026",
            "pokemon booster bundle 2026",
            "pokemon mega evolution tcg",
        ]
        new_finds = []

        for term in search_terms:
            try:
                encoded = requests.utils.quote(term)
                r = requests.get(
                    f"https://www.walmart.com/search?q={encoded}",
                    headers=HEADERS, timeout=12
                )
                items = re.findall(
                    r'"name"\s*:\s*"([^"]{10,80})"[^}]*"price"\s*:\s*([\d.]+)',
                    r.text
                )
                for item_name, item_price_str in items[:5]:
                    if not any(kw in item_name.lower() for kw in ["pokemon", "pokmon", "tcg", "trading card"]):
                        continue
                    is_tracked = any(
                        item_name.lower()[:20] in p.get("name", "").lower()
                        for p in self.walmart_products
                    )
                    hist_key = f"new_{item_name[:30]}"
                    if not is_tracked and not self.history.get(hist_key, {}).get("alerted"):
                        listed = parse_price(item_price_str)
                        msrp   = get_msrp(item_name, "walmart")
                        if listed and msrp and listed <= msrp * 1.1:
                            new_finds.append({"name": item_name, "listed": listed, "msrp": msrp})
                            self.history[hist_key] = {"alerted": True, "alerted_at": datetime.now().isoformat()}
                time.sleep(2)
            except Exception as e:
                log.debug(f"[walmart_queue] New listing scan error ({term}): {e}")

        if new_finds:
            save_history(HISTORY_FILE, self.history)
            for find in new_finds[:3]:
                search_url = f"https://www.walmart.com/search?q={requests.utils.quote(find['name'][:40])}"
                msrp_note  = " (AT/BELOW MSRP!)" if find["listed"] <= find["msrp"] else ""
                send_ntfy(
                    topic    = self.ntfy_topic,
                    title    = f"New Walmart Listing: {find['name'][:35]}",
                    body     = f"NEW untracked product found\n{find['name']}\n${find['listed']:.2f}{msrp_note}",
                    url      = search_url,
                    priority = "high",
                    tags     = "new,shopping_cart",
                )
                open_browser(search_url, banner_title=find["name"][:40], banner_msg="New untracked listing found")
        log.debug(f"[walmart_queue] New listing scan: {len(new_finds)} new find(s)")

    # ── Clearance / rollback scanner ──────────────────────────────────

    def _scan_clearance(self):
        """Scan tracked Walmart products for clearance/rollback badges or >10% below MSRP."""
        clearance_finds = []

        for product in self.walmart_products:
            item_id = product.get("item_id", "")
            if not item_id:
                continue
            try:
                r = requests.get(
                    f"https://www.walmart.com/product/v2/pdpData?itemId={item_id}",
                    headers=HEADERS_JSON, timeout=10
                )
                data      = r.json()
                item_data = data.get("item", {})
                buying    = item_data.get("buyingOptions", {})
                price_info = item_data.get("priceInfo", {})

                availability = buying.get("availabilityStatus", "").upper()
                if availability != "IN_STOCK":
                    continue

                current_str = (
                    price_info.get("currentPrice", {}).get("priceString")
                    or price_info.get("unitPrice", {}).get("priceString")
                )
                was_str    = price_info.get("wasPrice", {}).get("priceString")
                rollback   = bool(item_data.get("badges", {}).get("rollback"))
                clearance  = bool(item_data.get("badges", {}).get("clearance"))

                current = parse_price(current_str)
                was     = parse_price(was_str)
                msrp    = get_msrp(product["name"], "walmart")

                if not current or not msrp:
                    continue

                is_deal = clearance or rollback or current <= msrp * 0.89
                if not is_deal:
                    continue

                hist_key = f"clearance_{item_id}_{round(current, 2)}"
                if self.history.get(hist_key, {}).get("alerted"):
                    continue

                deal_type = "CLEARANCE" if clearance else "ROLLBACK" if rollback else "PRICE DROP"
                clearance_finds.append({
                    "product":   product,
                    "current":   current,
                    "was":       was,
                    "msrp":      msrp,
                    "savings":   round(msrp - current, 2),
                    "deal_type": deal_type,
                    "hist_key":  hist_key,
                })
                time.sleep(1)
            except Exception as e:
                log.debug(f"[walmart_queue] Clearance check error ({product['name']}): {e}")

        for find in clearance_finds:
            p        = find["product"]
            url      = p.get("url", "https://www.walmart.com")
            was_note = f"Was: ${find['was']:.2f} - " if find["was"] else ""
            send_ntfy(
                topic    = self.ntfy_topic,
                title    = f"Walmart {find['deal_type']}: {p['name'][:35]}",
                body     = (
                    f"{find['deal_type']} FOUND\n"
                    f"{p['name']}\n"
                    f"{was_note}Now: ${find['current']:.2f} (MSRP ${find['msrp']:.2f} - Save ${find['savings']:.2f})"
                ),
                url      = url,
                priority = "urgent",
                tags     = "label,moneybag",
            )
            open_browser(url, banner_title=f"{find['deal_type']}: {p['name'][:35]}", banner_msg=f"${find['current']:.2f} - Save ${find['savings']:.2f} vs MSRP")
            self.history[find["hist_key"]] = {
                "alerted":    True,
                "alerted_at": datetime.now().isoformat(),
                "price":      find["current"],
                "deal_type":  find["deal_type"],
            }

        if clearance_finds:
            save_history(HISTORY_FILE, self.history)
            log.info(f"[walmart_queue] Clearance scan: {len(clearance_finds)} deal(s) found")
        else:
            log.debug("[walmart_queue] Clearance scan: no deals found")
