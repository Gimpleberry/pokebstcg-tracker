#!/usr/bin/env python3
"""
plugins.py — Plugin Coordinator for Keith's PokeBS Tracker

This is the single place to enable or disable optional features.
Each plugin is a self-contained module with a standard interface.
tracker.py calls load_plugins() once at startup.

TO DISABLE A FEATURE: comment out its line in ENABLED_PLUGINS below.
TO ADD A NEW FEATURE: write a module following the Plugin base class,
                      then add it to ENABLED_PLUGINS.

Plugin interface (all methods optional — only implement what you need):
    start(config, products, schedule)  — called once at startup
    on_stock_change(product, status)   — called when any product flips IN/OUT
    on_msrp_detected(product, price, msrp) — called when price <= MSRP
    stop()                             — called on clean shutdown
"""

import logging
import importlib
import sys
import os

log = logging.getLogger(__name__)

# Ensure plugins/ directory is on the path so submodule imports resolve
_here = os.path.dirname(os.path.abspath(__file__))
_plugins_dir = os.path.join(_here, "plugins")
if _plugins_dir not in sys.path:
    sys.path.insert(0, _plugins_dir)
# Also ensure root is on path for shared.py
if _here not in sys.path:
    sys.path.insert(0, _here)


# ─────────────────────────────────────────────
# ENABLED PLUGINS — comment out to disable
# ─────────────────────────────────────────────
ENABLED_PLUGINS = [
    "news_scraper_plugin",       # Daily news scraping (PokeBeach, CollectorStation)
    "msrp_alert_plugin",         # ntfy alert when price <= MSRP
    "cart_preloader_plugin",     # Auto-open browser to checkout on MSRP detection
    "store_inventory_plugin",    # Daily in-store stock check near your zip code
    "alt_retailer_plugin",       # Tue/Fri scan of Five Below, Marshalls, ALDI etc.
    "walmart_queue_plugin",      # Walmart Wednesday drop monitor + browser open
    "bestbuy_invites_plugin",    # Best Buy invite button monitor + auto-request (#4)
    "amazon_monitor_plugin",     # Amazon MSRP monitor — Amazon-sold only (#6)
    "restock_reminder_plugin",   # Daily 8:30 AM restock reminder (#8)
    "price_history_plugin",      # Hourly price logging to SQLite + Excel export (#5)
    "costco_tracker_plugin",     # Costco online + warehouse monitor Cherry Hill/Princeton NJ
]


# ─────────────────────────────────────────────
# BASE PLUGIN CLASS
# ─────────────────────────────────────────────
class Plugin:
    """
    Base class for all plugins. All methods are optional no-ops by default.
    Subclass this and override only what your plugin needs.
    """
    name = "unnamed_plugin"
    version = "1.0"
    description = ""

    def start(self, config: dict, products: list, schedule) -> None:
        """Called once at tracker startup. Register schedules here."""
        pass

    def on_stock_change(self, product: dict, status) -> None:
        """Called whenever a product transitions IN or OUT of stock."""
        pass

    def on_msrp_detected(self, product: dict, listed: float, msrp: float) -> None:
        """Called when a product is detected in stock at or below MSRP."""
        pass

    def stop(self) -> None:
        """Called on clean tracker shutdown."""
        pass


# ─────────────────────────────────────────────
# PLUGIN WRAPPERS
# Each wraps an existing module in the Plugin interface
# ─────────────────────────────────────────────

class NewsScraper_Plugin(Plugin):
    name = "news_scraper"
    version = "1.0"
    description = "Daily news scraping from PokeBeach, CollectorStation, Pokemon.com"

    def start(self, config, products, schedule):
        try:
            from news_scraper import run_news_scrape
            log.info("  [news_scraper] Running initial scrape...")
            run_news_scrape()
            schedule.every().day.at("06:00").do(run_news_scrape)
            log.info("  [news_scraper] Scheduled daily at 06:00")
        except Exception as e:
            log.warning(f"  [news_scraper] Failed to start: {e}")


class MSRPAlert_Plugin(Plugin):
    name = "msrp_alert"
    version = "1.0"
    description = "ntfy push alert when any product is detected in stock at or below MSRP"

    def start(self, config, products, schedule):
        try:
            from msrp_alert import check_msrp_prices
            self._check = check_msrp_prices
            self._config = config
            log.info("  [msrp_alert] Enabled — fires after every stock check")
        except Exception as e:
            log.warning(f"  [msrp_alert] Failed to start: {e}")
            self._check = None

    def on_post_check(self):
        """Called by tracker after every run_checks() cycle."""
        if hasattr(self, "_check") and self._check:
            try:
                self._check(self._config)
            except Exception as e:
                log.warning(f"  [msrp_alert] Check error: {e}")


class CartPreloader_Plugin(Plugin):
    name = "cart_preloader"
    version = "1.0"
    description = "Auto-opens browser to checkout page when MSRP price is detected"

    def start(self, config, products, schedule):
        try:
            from cart_preloader import patch_msrp_alert
            patch_msrp_alert(config)
            log.info("  [cart_preloader] Patched into MSRP alert pipeline")
        except Exception as e:
            log.warning(f"  [cart_preloader] Failed to start: {e}")


class StoreInventory_Plugin(Plugin):
    name = "store_inventory"
    version = "1.0"
    description = "Daily in-store stock check at Target and Walmart near your zip code"

    def start(self, config, products, schedule):
        try:
            from store_inventory import run_store_check
            schedule.every().day.at("08:00").do(
                lambda: run_store_check(products, config)
            )
            log.info("  [store_inventory] Scheduled daily at 08:00")
        except Exception as e:
            log.warning(f"  [store_inventory] Failed to start: {e}")


class AltRetailer_Plugin(Plugin):
    name = "alt_retailer"
    version = "1.0"
    description = "Tue/Fri scan of Five Below, Marshalls, TJ Maxx, Ollie's, ALDI, GameStop"

    def start(self, config, products, schedule):
        try:
            from alternative_retailers import run_alt_retailer_check
            schedule.every().tuesday.at("09:00").do(
                lambda: run_alt_retailer_check(config)
            )
            schedule.every().friday.at("09:00").do(
                lambda: run_alt_retailer_check(config)
            )
            log.info("  [alt_retailer] Scheduled Tue & Fri at 09:00")
        except Exception as e:
            log.warning(f"  [alt_retailer] Failed to start: {e}")


class WalmartQueue_Plugin(Plugin):
    name = "walmart_queue"
    version = "1.0"
    description = "Walmart Wednesday drop monitor + restock/clearance/rollback alerts with direct links"

    def start(self, config, products, schedule):
        try:
            from walmart_queue import WalmartQueueMonitor
            self._monitor = WalmartQueueMonitor(config, products)
            self._monitor.start(schedule)
            log.info("  [walmart_queue] Started — monitoring all Walmart drops, restocks, clearance")
        except Exception as e:
            log.warning(f"  [walmart_queue] Failed to start: {e}")

    def on_stock_change(self, product, status):
        if hasattr(self, "_monitor") and product.get("retailer", "").lower() == "walmart":
            try:
                self._monitor.on_stock_change(product, status)
            except Exception as e:
                log.warning(f"  [walmart_queue] on_stock_change error: {e}")

    def stop(self):
        log.info("  [walmart_queue] Stopped")


class BestBuyInvites_Plugin(Plugin):
    name = "bestbuy_invites"
    version = "1.0"
    description = "Best Buy invite button monitor — auto-requests invites and alerts on selection"

    def start(self, config, products, schedule):
        try:
            from bestbuy_invites import BestBuyInviteMonitor
            self._monitor = BestBuyInviteMonitor(config, products)
            self._monitor.start(schedule)
            log.info("  [bestbuy_invites] Started — monitoring invite buttons every 10 min")
        except Exception as e:
            log.warning(f"  [bestbuy_invites] Failed to start: {e}")

    def stop(self):
        log.info("  [bestbuy_invites] Stopped")


class AmazonMonitor_Plugin(Plugin):
    name = "amazon_monitor"
    version = "1.0"
    description = "Amazon MSRP monitor — alerts and opens browser when Amazon.com sells at MSRP"

    def start(self, config, products, schedule):
        try:
            from amazon_monitor import AmazonMSRPMonitor
            self._monitor = AmazonMSRPMonitor(config, products)
            self._monitor.start(schedule)
            log.info("  [amazon_monitor] Started — checking every 15 min (Amazon-sold only)")
        except Exception as e:
            log.warning(f"  [amazon_monitor] Failed to start: {e}")

    def stop(self):
        log.info("  [amazon_monitor] Stopped")


class RestockReminder_Plugin(Plugin):
    name = "restock_reminder"
    version = "1.0"
    description = "Daily 8:30 AM restock reminder with day-aware messaging"

    def start(self, config, products, schedule):
        try:
            from restock_reminder import RestockReminder
            self._reminder = RestockReminder(config)
            self._reminder.start(schedule)
            log.info("  [restock_reminder] Scheduled daily at 08:30")
        except Exception as e:
            log.warning(f"  [restock_reminder] Failed to start: {e}")

    def stop(self):
        log.info("  [restock_reminder] Stopped")


class PriceHistory_Plugin(Plugin):
    name = "price_history"
    version = "1.0"
    description = "Hourly price logging to SQLite + Excel export + 90-day retention"

    def start(self, config, products, schedule):
        try:
            from price_history import PriceHistoryTracker
            self._tracker = PriceHistoryTracker(config, products)
            self._tracker.start(schedule)
            log.info("  [price_history] Started -- logging prices hourly to SQLite")
        except Exception as e:
            log.warning(f"  [price_history] Failed to start: {e}")

    def stop(self):
        log.info("  [price_history] Stopped")


class CostcoTracker_Plugin(Plugin):
    name = "costco_tracker"
    version = "1.0"
    description = "Costco online + warehouse monitor for Cherry Hill NJ and Princeton NJ"

    def start(self, config, products, schedule):
        try:
            from costco_tracker import CostcoTracker
            self._tracker = CostcoTracker(config, products)
            self._tracker.start(schedule)
            log.info("  [costco_tracker] Started -- monitoring online + Cherry Hill/Princeton NJ")
        except Exception as e:
            log.warning(f"  [costco_tracker] Failed to start: {e}")

    def stop(self):
        if hasattr(self, "_tracker"):
            self._tracker.stop()


# ─────────────────────────────────────────────
# PLUGIN REGISTRY
# ─────────────────────────────────────────────
_PLUGIN_CLASSES = {
    "news_scraper_plugin":     NewsScraper_Plugin,
    "msrp_alert_plugin":       MSRPAlert_Plugin,
    "cart_preloader_plugin":   CartPreloader_Plugin,
    "store_inventory_plugin":  StoreInventory_Plugin,
    "alt_retailer_plugin":     AltRetailer_Plugin,
    "walmart_queue_plugin":    WalmartQueue_Plugin,
    "bestbuy_invites_plugin":  BestBuyInvites_Plugin,
    "amazon_monitor_plugin":   AmazonMonitor_Plugin,
    "restock_reminder_plugin": RestockReminder_Plugin,
    "price_history_plugin":    PriceHistory_Plugin,
    "costco_tracker_plugin":   CostcoTracker_Plugin,
}

_loaded_plugins: list[Plugin] = []


def load_plugins(config: dict, products: list, schedule) -> list[Plugin]:
    """
    Load and start all enabled plugins.
    Called once from tracker.py main().
    Returns list of loaded plugin instances.
    """
    global _loaded_plugins
    _loaded_plugins = []

    log.info(f"Loading {len(ENABLED_PLUGINS)} plugins...")

    for plugin_id in ENABLED_PLUGINS:
        cls = _PLUGIN_CLASSES.get(plugin_id)
        if not cls:
            log.warning(f"  Unknown plugin: {plugin_id} — skipping")
            continue
        try:
            instance = cls()
            instance.start(config, products, schedule)
            _loaded_plugins.append(instance)
            log.info(f"  ✅ {instance.name} v{instance.version} loaded")
        except Exception as e:
            log.warning(f"  ❌ {plugin_id} failed to load: {e}")

    log.info(f"Plugins loaded: {len(_loaded_plugins)}/{len(ENABLED_PLUGINS)}")
    return _loaded_plugins


def notify_stock_change(product: dict, status) -> None:
    """Broadcast a stock change event to all loaded plugins."""
    for plugin in _loaded_plugins:
        try:
            plugin.on_stock_change(product, status)
        except Exception as e:
            log.warning(f"Plugin {plugin.name} on_stock_change error: {e}")


def notify_post_check() -> None:
    """Called after every run_checks() — triggers MSRP check etc."""
    for plugin in _loaded_plugins:
        if hasattr(plugin, "on_post_check"):
            try:
                plugin.on_post_check()
            except Exception as e:
                log.warning(f"Plugin {plugin.name} on_post_check error: {e}")


def notify_msrp_detected(product: dict, listed: float, msrp: float) -> None:
    """Called when a product is detected at or below MSRP."""
    for plugin in _loaded_plugins:
        try:
            plugin.on_msrp_detected(product, listed, msrp)
        except Exception as e:
            log.warning(f"Plugin {plugin.name} on_msrp_detected error: {e}")


def stop_all() -> None:
    """Clean shutdown of all plugins."""
    for plugin in _loaded_plugins:
        try:
            plugin.stop()
        except Exception as e:
            log.warning(f"Plugin {plugin.name} stop error: {e}")
    log.info("All plugins stopped")


def plugin_status() -> list[dict]:
    """Return status of all plugins for dashboard/help page."""
    return [
        {
            "id": plugin_id,
            "name": _PLUGIN_CLASSES[plugin_id].name if plugin_id in _PLUGIN_CLASSES else plugin_id,
            "enabled": plugin_id in ENABLED_PLUGINS,
            "loaded": any(p.name == _PLUGIN_CLASSES[plugin_id].name
                          for p in _loaded_plugins
                          if plugin_id in _PLUGIN_CLASSES),
            "description": _PLUGIN_CLASSES[plugin_id].description if plugin_id in _PLUGIN_CLASSES else "",
        }
        for plugin_id in _PLUGIN_CLASSES
    ]
