#!/usr/bin/env python3
"""
plugins.py — Plugin Coordinator for Keith's PokeBS Tracker (v6.0.0)

This is the single place to enable or disable optional features.
Each plugin is a self-contained module with a standard interface.
tracker.py calls load_plugins() once at startup.

TO DISABLE A FEATURE: comment out its line in ENABLED_PLUGINS below.
TO ADD A NEW FEATURE: write a module following the Plugin base class,
                      then add it to ENABLED_PLUGINS.

LIFECYCLE (v6.0.0 — see V6_0_0_SPEC.md for full design)

The Plugin base class supports two lifecycle styles. Both work; pick whichever
is appropriate for your plugin.

  NEW STYLE (v6.0.0+):
      def init(self, config, products) -> None
          Cold init: DB schemas, file system. Synchronous, fast (<100ms).
      def register(self, scheduler) -> None
          Declare jobs with the scheduler. No I/O. Fast (<10ms).
      def kickoff(self) -> None
          Optional explicit first-run. Most plugins use kickoff=True on
          register_job() instead and don't need this method.

  LEGACY STYLE (pre-v6.0.0, still fully supported):
      def start(self, config, products, schedule) -> None
          Called once at tracker startup. Register schedules + do first
          check inline. Used by all 14 wrapper classes in this file.
          The legacy signature will continue to work through all v6.x
          releases. Formal deprecation arrives in v7.0.

  EVENT HOOKS (both styles):
      def on_stock_change(self, product, status) -> None
      def on_msrp_detected(self, product, listed, msrp) -> None
      def on_post_check(self) -> None
      def stop(self) -> None

The lifecycle dispatch happens in load_plugins() below: plugins overriding
register() use the new path; plugins overriding only start() use the
back-compat shim. A plugin that overrides both will use register() (new
takes precedence).
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
    "walmart_playwright_plugin", # Walmart tracker (v6.1.1) - patchright + Chrome + off-screen
    "invest_store_plugin",
    "market_data_refresh_plugin",
    "api_server_plugin",
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

    # ── New lifecycle (v6.0.0+) — all optional, default to no-ops ─────────

    def init(self, config: dict, products: list) -> None:
        """Phase 0: cold init. DB schemas, file system. Synchronous, fast (<100ms)."""
        pass

    def register(self, scheduler) -> None:
        """Phase 1: declare jobs with the scheduler. No I/O. Fast (<10ms).

        Plugins overriding this method use the new lifecycle. Plugins
        overriding only the legacy start() method continue to work via
        the back-compat shim in load_plugins().
        """
        pass

    def kickoff(self) -> None:
        """Phase 3: explicit first-run. Most plugins use kickoff=True on
        scheduler.register_job() instead and don't need this method.
        Provided for plugins that need custom kickoff orchestration."""
        pass

    # ── Legacy lifecycle (pre-v6.0.0) — fully supported through v6.x ──────

    def start(self, config: dict, products: list, schedule) -> None:
        """LEGACY (pre-v6.0.0). Called once at tracker startup.

        Plugins still using this signature will continue to work
        unchanged. Formal deprecation arrives in v7.0. Migrating to
        the new init()/register() lifecycle is encouraged but not required.
        """
        pass

    # ── Event hooks (both styles) ─────────────────────────────────────────

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
# Each wraps an existing module in the Plugin interface.
# All 14 currently use the legacy start() lifecycle. They're untouched
# in v6.0.0; migrations to the new lifecycle happen in v6.0.0 Steps 4-6
# (the three boot-stall offenders) and in v6.1 (the remaining eight).
# ─────────────────────────────────────────────

class NewsScraper_Plugin(Plugin):
    name = "news_scraper"
    version = "1.1"  # v6.1.18: phased lifecycle (init + register)
    description = "Daily news scraping from PokeBeach, CollectorStation, Pokemon.com"

    def init(self, config, products):
        """Phase 0 (v6.1.18): cold init — hold a reference to the work fn.

        Legacy start() ran run_news_scrape() inline at boot, blocking
        plugin load for the duration of one scrape (~5-10s typical).
        The initial run is now deferred to a kickoff job at T+60s after
        boot_ready(), so the dashboard comes up first.
        """
        try:
            from news_scraper import run_news_scrape
            self._run = run_news_scrape
        except Exception as e:
            self._run = None
            log.warning(f"  [news_scraper] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 1 (v6.1.18): hand off to the unified Scheduler."""
        if self._run is None:
            return
        try:
            scheduler.register_job(
                name="news_scraper.run_news_scrape",
                fn=self._run,
                cadence="daily 06:00",
                kickoff=True,
                kickoff_delay=60,
                owner="news_scraper",
            )
            log.info("  [news_scraper] Registered (kickoff @ T+60s, then daily 06:00)")
        except Exception as e:
            log.warning(f"  [news_scraper] Failed to register: {e}")


class MSRPAlert_Plugin(Plugin):
    name = "msrp_alert"
    version = "1.1"  # v6.1.19: phased lifecycle (init + register)
    description = "ntfy push alert when any product is detected in stock at or below MSRP"

    def init(self, config, products):
        """Phase 0 (v6.1.19): cold init — instantiate check fn + hold config.

        MSRPAlert is event-driven (on_post_check), not scheduled. The
        init() body is functionally identical to legacy start(), minus
        the unused `schedule` argument. The work fn fires from
        on_post_check() below, called by tracker after every run cycle.
        """
        try:
            from msrp_alert import check_msrp_prices
            self._check = check_msrp_prices
            self._config = config
            log.info("  [msrp_alert] Enabled — fires after every stock check")
        except Exception as e:
            log.warning(f"  [msrp_alert] Failed to init: {e}")
            self._check = None

    def register(self, scheduler):
        """Phase 1 (v6.1.19): no periodic work to register.

        MSRPAlert reacts to tracker post-check events, not a schedule.
        The empty register() declares phased-lifecycle membership and
        prevents fall-through to the legacy start() shim. No-op by design.
        """
        pass

    def on_post_check(self):
        """Called by tracker after every run_checks() cycle."""
        if hasattr(self, "_check") and self._check:
            try:
                self._check(self._config)
            except Exception as e:
                log.warning(f"  [msrp_alert] Check error: {e}")


class CartPreloader_Plugin(Plugin):
    name = "cart_preloader"
    version = "1.1"  # v6.1.19: phased lifecycle (init + register)
    description = "Auto-opens browser to checkout page when MSRP price is detected"

    def init(self, config, products):
        """Phase 0 (v6.1.19): cold init — patch ourselves into the MSRP
        alert pipeline.

        CartPreloader has no periodic schedule and no event hook of its
        own. Its boot work is patch_msrp_alert(config), which monkey-
        patches the msrp_alert module to invoke cart_preloader on detection.
        Same call as legacy start(), now in init().
        """
        try:
            from cart_preloader import patch_msrp_alert
            patch_msrp_alert(config)
            log.info("  [cart_preloader] Patched into MSRP alert pipeline")
        except Exception as e:
            log.warning(f"  [cart_preloader] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 1 (v6.1.19): no periodic work to register.

        CartPreloader's interaction with the tracker is entirely through
        the runtime patch installed in init(). No scheduler involvement.
        The empty register() declares phased-lifecycle membership.
        """
        pass


class StoreInventory_Plugin(Plugin):
    name = "store_inventory"
    version = "1.1"  # v6.1.18: phased lifecycle (init + register)
    description = "Daily in-store stock check at Target and Walmart near your zip code"

    def init(self, config, products):
        """Phase 0 (v6.1.18): cold init — bind config+products into a closure.

        Legacy start() registered a lambda capturing both args at the
        schedule call site. Same closure pattern, just owned by self
        and consumed in register() instead.
        """
        try:
            from store_inventory import run_store_check
            self._fn = lambda: run_store_check(products, config)
        except Exception as e:
            self._fn = None
            log.warning(f"  [store_inventory] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 1 (v6.1.18): hand off to the unified Scheduler.
        No kickoff — legacy code never ran inline at boot, so we preserve
        that and only run on the daily cadence.
        """
        if self._fn is None:
            return
        try:
            scheduler.register_job(
                name="store_inventory.run_store_check",
                fn=self._fn,
                cadence="daily 08:00",
                owner="store_inventory",
            )
            log.info("  [store_inventory] Registered (daily 08:00)")
        except Exception as e:
            log.warning(f"  [store_inventory] Failed to register: {e}")


class AltRetailer_Plugin(Plugin):
    name = "alt_retailer"
    version = "1.1"  # v6.1.18: phased lifecycle (init + register)
    description = "Tue/Fri scan of Five Below, Marshalls, TJ Maxx, Ollie's, ALDI, GameStop"

    def init(self, config, products):
        """Phase 0 (v6.1.18): cold init — bind config into a closure.

        Both weekly schedules call the same fn with the same arg, so
        they share a single bound callable. Each becomes its own
        register_job() entry in register() below.
        """
        try:
            from alternative_retailers import run_alt_retailer_check
            self._fn = lambda: run_alt_retailer_check(config)
        except Exception as e:
            self._fn = None
            log.warning(f"  [alt_retailer] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 1 (v6.1.18): hand off to the unified Scheduler.

        Two distinct register_job() entries (one per weekday) so the
        introspection panel can show next_run independently for each.
        Same fn, same owner — just different names + cadences.
        """
        if self._fn is None:
            return
        try:
            scheduler.register_job(
                name="alt_retailer.run_tuesday",
                fn=self._fn,
                cadence="weekly tue 09:00",  # v6.1.18.1: short-day required by scheduler regex
                owner="alt_retailer",
            )
            scheduler.register_job(
                name="alt_retailer.run_friday",
                fn=self._fn,
                cadence="weekly fri 09:00",  # v6.1.18.1: short-day required by scheduler regex
                owner="alt_retailer",
            )
            log.info("  [alt_retailer] Registered (weekly Tue & Fri 09:00)")
        except Exception as e:
            log.warning(f"  [alt_retailer] Failed to register: {e}")


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
    version = "1.1"
    description = "Best Buy invite button monitor (v6.0.0 phased boot, no boot stall)"

    def init(self, config, products):
        try:
            from bestbuy_invites import BestBuyInviteMonitor
            self._monitor = BestBuyInviteMonitor(config, products)
            log.info("  [bestbuy_invites] Initialized")
        except Exception as e:
            log.warning(f"  [bestbuy_invites] Failed to init: {e}")
            self._monitor = None

    def register(self, scheduler):
        if self._monitor is None:
            log.warning("  [bestbuy_invites] Not initialized; skipping register")
            return
        try:
            self._monitor.register(scheduler)
            log.info("  [bestbuy_invites] Registered (kickoff @ T+30s, then every 10 min)")
        except Exception as e:
            log.warning(f"  [bestbuy_invites] Failed to register: {e}")

    def stop(self):
        log.info("  [bestbuy_invites] Stopped")


class AmazonMonitor_Plugin(Plugin):
    name = "amazon_monitor"
    version = "1.1"
    description = "Amazon MSRP monitor — alerts and opens browser when Amazon.com sells at MSRP"

    def init(self, config, products):
        """Phase 1 (v6.0.0 step 5): instantiate the monitor only.

        The legacy start() method ran a synchronous _check_all() which
        could block plugin loading for up to 5 minutes. In the new
        lifecycle, that initial check is deferred to a kickoff job that
        fires asynchronously after boot_ready().
        """
        try:
            from amazon_monitor import AmazonMSRPMonitor
            self._monitor = AmazonMSRPMonitor(config, products)
        except Exception as e:
            self._monitor = None
            log.warning(f"  [amazon_monitor] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 2 (v6.0.0 step 5): hand off to the unified Scheduler."""
        if self._monitor is None: return
        try:
            self._monitor.register(scheduler)
            log.info("  [amazon_monitor] Started — checking every 15 min (Amazon-sold only)")
        except Exception as e:
            log.warning(f"  [amazon_monitor] Failed to register: {e}")

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
    version = "1.1"
    description = "Costco online + warehouse monitor for Cherry Hill NJ and Princeton NJ"

    def init(self, config, products):
        """Phase 1 (v6.0.0 step 6): instantiate the tracker only.

        The legacy start() method ran a synchronous _check_all_online()
        which could block plugin loading for ~11 seconds. The initial
        check is now deferred to a kickoff job at T+150s.
        """
        try:
            from costco_tracker import CostcoTracker
            self._tracker = CostcoTracker(config, products)
        except Exception as e:
            self._tracker = None
            log.warning(f"  [costco_tracker] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 2 (v6.0.0 step 6): hand off to the unified Scheduler."""
        if self._tracker is None: return
        try:
            self._tracker.register(scheduler)
            log.info("  [costco_tracker] Started -- monitoring online + Cherry Hill/Princeton NJ")
        except Exception as e:
            log.warning(f"  [costco_tracker] Failed to register: {e}")

    def stop(self):
        if hasattr(self, "_tracker") and self._tracker is not None:
            self._tracker.stop()


class WalmartPlaywright_Plugin(Plugin):
    name = "walmart_playwright"
    version = "1.0"
    description = "Walmart Playwright tracker (v6.1.1) - patchright + Chrome + headful + off-screen"

    def init(self, config, products):
        """Phase 1 (v6.1.1): instantiate the tracker only.

        Replaces the urllib-based check_walmart() in tracker.py which was
        blocked by PerimeterX. See plugins/walmart_playwright.py docstring
        for the full anti-detection stack rationale.
        """
        try:
            from walmart_playwright import WalmartPlaywrightTracker
            self._tracker = WalmartPlaywrightTracker(config, products)
            log.info("  [walmart_playwright] Initialized")
        except Exception as e:
            self._tracker = None
            log.warning(f"  [walmart_playwright] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 2 (v6.1.1): hand off to the unified Scheduler."""
        if self._tracker is None:
            log.warning("  [walmart_playwright] Not initialized; skipping register")
            return
        try:
            self._tracker.register(scheduler)
            log.info("  [walmart_playwright] Registered (kickoff @ T+210s, then every 30 min)")
        except Exception as e:
            log.warning(f"  [walmart_playwright] Failed to register: {e}")

    def stop(self):
        if hasattr(self, "_tracker") and self._tracker is not None:
            self._tracker.stop()


class InvestStore_Plugin(Plugin):
    name = "invest_store"
    version = "1.1"  # v6.1.20: phased lifecycle (init + register)
    description = "SQLite-backed investment portfolio store (replaces localStorage)"

    def init(self, config, products):
        """Phase 0 (v6.1.20): cold init. Schema creation happens in
        InvestStore.__init__ via _init_schema() — idempotent and safe
        on every boot.

        InvestStore is a passive CRUD store called by api_server.py and
        plugins/market_data_refresh.py. No scheduled tasks. The init()
        body is functionally identical to legacy start(), minus the
        unused self._store.start(schedule) call (which was already a
        no-op log line).
        """
        try:
            from invest_store import InvestStore
            self._store = InvestStore(config, products)
            log.info("  [invest_store] Initialized -- invest.db ready at data/invest.db")
        except Exception as e:
            self._store = None
            log.warning(f"  [invest_store] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 1 (v6.1.20): no periodic work to register.

        InvestStore is a passive CRUD store. Its work is driven by
        synchronous calls from api_server.py and market_data_refresh.py.
        The empty register() declares phased-lifecycle membership and
        prevents fall-through to the legacy start() shim. No-op by design.
        """
        pass

    def stop(self):
        log.info("  [invest_store] Stopped")


class MarketDataRefresh_Plugin(Plugin):
    name = "market_data_refresh"
    version = "1.0"
    description = "12-hour market value refresh: pokemontcg.io + sealed MSRP estimates"

    def start(self, config, products, schedule):
        try:
            from market_data_refresh import MarketDataRefresh
            self._refresher = MarketDataRefresh(config, products)
            self._refresher.start(schedule)
            log.info("  [market_data_refresh] Started -- every 12h, weekly prune Mon 03:00")
        except Exception as e:
            log.warning(f"  [market_data_refresh] Failed to start: {e}")

    def stop(self):
        log.info("  [market_data_refresh] Stopped")


class ApiServer_Plugin(Plugin):
    name = "api_server"
    version = "1.1"  # v6.1.21: phased lifecycle (init + register)
    description = "Local HTTP API on 127.0.0.1:8765 -- serves the invest dashboard"

    def init(self, config, products):
        """Phase 0 (v6.1.21): cold init. Spawns the daemon HTTP server
        thread via ApiServer.__init__ — port bind happens inside the
        thread (run()), so this returns within ~ms.

        ApiServer is a service-style plugin: no scheduled jobs, runs an
        async daemon thread that serves the Invest dashboard endpoints.
        The init() body is functionally identical to legacy start(),
        minus the unused self._api.start(schedule) call (whose body has
        been merged into ApiServer.__init__ in v6.1.21).

        Boot order matters: api_server depends on invest_store and
        market_data_refresh schemas being on disk. Both must load before
        this plugin in ENABLED_PLUGINS — they do.
        """
        try:
            from api_server import ApiServer
            self._api = ApiServer(config, products)
            log.info("  [api_server] Initialized -- listening on http://127.0.0.1:8765")
        except Exception as e:
            self._api = None
            log.warning(f"  [api_server] Failed to init: {e}")

    def register(self, scheduler):
        """Phase 1 (v6.1.21): no periodic work to register.

        ApiServer runs as a daemon HTTP thread, not as scheduled jobs.
        The empty register() declares phased-lifecycle membership and
        prevents fall-through to the legacy start() shim. No-op by design.
        """
        pass

    def stop(self):
        if hasattr(self, "_api") and self._api is not None:
            self._api.stop()
        log.info("  [api_server] Stopped")


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
    "walmart_playwright_plugin":   WalmartPlaywright_Plugin,
    "invest_store_plugin":         InvestStore_Plugin,
    "market_data_refresh_plugin":  MarketDataRefresh_Plugin,
    "api_server_plugin":           ApiServer_Plugin,
}

_loaded_plugins: list[Plugin] = []


# ─────────────────────────────────────────────
# LIFECYCLE DISPATCH HELPERS
# ─────────────────────────────────────────────
def _overrides(instance: Plugin, method_name: str) -> bool:
    """True iff the plugin's class overrides the named method from the base Plugin."""
    method      = getattr(type(instance), method_name, None)
    base_method = getattr(Plugin, method_name, None)
    if method is None or base_method is None:
        return False
    return method is not base_method


def _resolve_schedule_lib(schedule_or_scheduler):
    """
    Accept either a Scheduler instance or the raw `schedule` library.
    Returns (scheduler_or_None, schedule_lib).

    Detection rule: anything with a `register_job` method is a Scheduler.
    Anything else is treated as a raw schedule library (legacy mode).

    This dual-mode shim is what lets v6.0.0 Step 2 ship without breaking
    the existing tracker.py call site. tracker.py keeps passing the raw
    `schedule` lib until Step 3 refactors it to construct a Scheduler.
    """
    if hasattr(schedule_or_scheduler, "register_job"):
        scheduler    = schedule_or_scheduler
        schedule_lib = scheduler._schedule
        return scheduler, schedule_lib
    return None, schedule_or_scheduler


# ─────────────────────────────────────────────
# LOAD PLUGINS (PHASED BOOT)
# ─────────────────────────────────────────────
def load_plugins(config: dict, products: list, schedule_or_scheduler) -> list[Plugin]:
    """
    Load and start all enabled plugins.
    Called once from tracker.py main().
    Returns list of loaded plugin instances.

    Accepts either a Scheduler instance (v6.0.0+) or the raw `schedule`
    library (legacy). When a Scheduler is provided, plugins overriding
    register() use the new phased lifecycle. Plugins overriding only
    start() always use the back-compat shim, regardless of which is passed.
    """
    global _loaded_plugins
    _loaded_plugins = []

    scheduler, schedule_lib = _resolve_schedule_lib(schedule_or_scheduler)
    mode_str = "phased (Scheduler)" if scheduler else "legacy (schedule lib)"
    log.info(f"Loading {len(ENABLED_PLUGINS)} plugins... (mode: {mode_str})")

    # ── PHASE 0: init() on every plugin (no-op default) ──
    instances: list[Plugin] = []
    for plugin_id in ENABLED_PLUGINS:
        cls = _PLUGIN_CLASSES.get(plugin_id)
        if not cls:
            log.warning(f"  Unknown plugin: {plugin_id} -- skipping")
            continue
        try:
            instance = cls()
            if _overrides(instance, "init"):
                instance.init(config, products)
            instances.append(instance)
        except Exception as e:
            log.warning(f"  X {plugin_id} failed in init phase: {e}")

    # ── PHASE 1: register() OR legacy start() shim ──
    for instance in instances:
        plugin_id = next(
            (pid for pid, c in _PLUGIN_CLASSES.items() if isinstance(instance, c)),
            instance.name,
        )
        try:
            if _overrides(instance, "register") and scheduler is not None:
                # New lifecycle: plugin declares jobs with the Scheduler.
                instance.register(scheduler)
            else:
                # Back-compat shim: hand the plugin the underlying schedule
                # library (whether we received a Scheduler or a raw schedule
                # lib makes no difference here -- we always pass the lib).
                instance.start(config, products, schedule_lib)
            _loaded_plugins.append(instance)
            log.info(f"  OK {instance.name} v{instance.version} loaded")
        except Exception as e:
            log.warning(f"  X {plugin_id} failed in register phase: {e}")

    log.info(f"Plugins loaded: {len(_loaded_plugins)}/{len(ENABLED_PLUGINS)}")
    return _loaded_plugins


# ─────────────────────────────────────────────
# EVENT BROADCAST + LIFECYCLE TEARDOWN
# ─────────────────────────────────────────────
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
