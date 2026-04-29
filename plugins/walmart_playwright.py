#!/usr/bin/env python3
"""
plugins/walmart_playwright.py - Walmart Playwright Tracker (v6.1.1)
Part of Keith's PokeBS Tracker.

Replaces the urllib-based check_walmart() in tracker.py. Uses patchright +
real Chrome + headful + off-screen window to bypass PerimeterX (which had
blocked urllib at every endpoint as of v5.x). All five layers of the stack
are required - removing any one re-introduces blocking:

    patchright (NOT vanilla playwright; deeper anti-detection)
      + channel="chrome" (real system Chrome; falls back to msedge then
        the bundled Chromium-for-Testing if Chrome isn't installed)
      + headless=False (NOT headless; PerimeterX fingerprints headless mode
        below the browser-launch level - no JS-layer trick can hide it)
      + args=["--window-position=-2400,-2400", "--window-size=400,300"]
        (pushes window off any reasonable monitor; operationally invisible)
      + warmed BROWSER_PROFILE (existing session cookies seed PerimeterX
        with behavioral trust)

Architecture:
  - v6.0.0 phased lifecycle (init, register)
  - Daemon-thread wrapping around sync_playwright (avoids asyncio conflict
    with the tracker's main loop, same pattern as costco/amazon/bestbuy)
  - Patchright import with fallback to vanilla playwright
  - Channel chain: chrome -> msedge -> chromium
  - Stagger kickoff at T+210s (after costco at T+150s)

Stock detection (in priority order):
  1. __NEXT_DATA__.props.pageProps.initialData.data.product.primaryOffer
     (296-316KB JSON tree; richest signal source per recon)
  2. button[data-automation-id="atc"] CSS selector (fallback)
  3. itemprop="price" for price extraction (fallback)

Marketplace suppression (carried from urllib version):
  - Reject if seller is not Walmart-direct (sellerName / sellerType checks)
  - Reject if listed_price > MSRP * 2.0 (>100% over MSRP = scalper)
  - Reject if URL contains "conditionGroupCode" (used / refurb listing)

Self-handles all downstream notifications (per V6_1_1_SPEC.md Q1 = Option A):
  - send_ntfy directly when a product transitions to in-stock
  - cart_preloader.trigger_cart_preload directly for any in-stock listing
    surviving the 2.0x MSRP filter (Keith's threshold: any walmart-direct
    deal up to 2x MSRP is worth staging a cart for)
  - plugins.notify_stock_change so walmart_queue.on_stock_change fires

OPERATIONAL REQUIREMENT:
  tracker.bat must run in a logged-in Windows session. Headful Chrome
  cannot render in non-interactive contexts. Will NOT work as a Windows
  Service or unattended Task Scheduler job (documented in README.md).
"""

import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from types import SimpleNamespace

# -- Path resolution - works from root or plugins/ folder ---------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "plugins" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)
if _here not in sys.path:
    sys.path.insert(0, _here)
# -----------------------------------------------------------------------------

from shared import (
    BROWSER_PROFILE,
    get_msrp,
    parse_price,
    send_ntfy,
    load_history,
    save_history,
)

log = logging.getLogger(__name__)

HISTORY_FILE = "walmart_playwright_history.json"

# Off-screen window positioning. Pushes window off any reasonable monitor.
# patchright docs warn against custom args; window-position is purely cosmetic
# and recon confirmed it doesn't interfere with their evasion logic.
OFFSCREEN_ARGS = [
    "--window-position=-2400,-2400",
    "--window-size=400,300",
]

# Per-product polite delay between page loads
DELAY_BETWEEN_PRODUCTS_SEC = 3

# Max time for one full check cycle before we kill the thread
CHECK_CYCLE_TIMEOUT_SEC = 300

# Page load timeout
PAGE_GOTO_TIMEOUT_MS = 30000

# PerimeterX challenge title phrases (high-precision detector)
CHALLENGE_TITLE_PHRASES = (
    "robot or human",
    "press and hold",
    "pardon our interruption",
    "access denied",
)

# Chromium-family channel fallback chain (chrome best, chromium last)
CHANNEL_CHAIN = ("chrome", "msedge", "chromium")


# -- Engine resolution --------------------------------------------------------

def _import_sync_playwright():
    """
    Try patchright first (deeper anti-detection at browser-launch level),
    fall back to vanilla playwright. Returns (sync_playwright_callable,
    impl_name) or (None, None) if neither is installed.
    """
    try:
        from patchright.sync_api import sync_playwright  # type: ignore
        return sync_playwright, "patchright"
    except ImportError:
        pass
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        return sync_playwright, "playwright"
    except ImportError:
        return None, None


# -- Plugin class -------------------------------------------------------------

class WalmartPlaywrightTracker:
    """
    Walmart product tracker using patchright + headful Chrome + off-screen
    window. Registered as a plugin via plugins.py WalmartPlaywright_Plugin.

    See module docstring for the full anti-detection stack rationale.
    """

    def __init__(self, config: dict, products: list):
        self.config = config
        self.ntfy_topic = config.get("ntfy_topic", "")
        self.history = load_history(HISTORY_FILE)
        self.walmart_products = [
            p for p in products
            if p.get("retailer", "").lower() == "walmart"
            and p.get("item_id")
        ]
        log.info(
            f"[walmart_playwright] Monitoring {len(self.walmart_products)} "
            f"Walmart products"
        )

    # -- Lifecycle ------------------------------------------------------------

    def register(self, scheduler) -> None:
        """Register jobs with the scheduler (v6.0.0 phased boot).

        Kickoff at T+210s, staggered behind:
          T+30s  bestbuy_invites
          T+90s  amazon_monitor
          T+150s costco_tracker
          T+210s walmart_playwright   <- this plugin
        """
        scheduler.register_job(
            name="walmart_playwright.check_all",
            fn=self._check_all,
            cadence="every 30 minutes",
            kickoff=True,
            kickoff_delay=210,
            owner="walmart_playwright",
        )
        log.info(
            "[walmart_playwright] Registered - kickoff @ T+210s, "
            "then every 30 min"
        )

    def stop(self) -> None:
        log.info("[walmart_playwright] Stopped")

    # -- Check cycle ----------------------------------------------------------

    def _check_all(self) -> None:
        """Check all Walmart products. Daemon-thread wrapped to avoid
        sync_playwright conflict with tracker.py's asyncio event loop.
        Same pattern as costco_tracker / amazon_monitor / bestbuy_invites."""
        if not self.walmart_products:
            return

        sync_playwright, impl = _import_sync_playwright()
        if sync_playwright is None:
            log.warning(
                "[walmart_playwright] Neither patchright nor playwright "
                "installed - skipping. Run: py -3.14 -m pip install patchright"
            )
            return

        if impl != "patchright":
            log.warning(
                "[walmart_playwright] Falling back to vanilla playwright. "
                "Patchright is recommended for PerimeterX bypass; without it "
                "Walmart checks will likely be blocked."
            )

        def _run():
            log.debug(
                f"[walmart_playwright] Checking "
                f"{len(self.walmart_products)} products via {impl}..."
            )
            try:
                with sync_playwright() as p:
                    context = self._launch_context(p, impl)
                    if context is None:
                        return

                    page = context.new_page()
                    # Block heavy resources for speed
                    page.route("**/*", lambda r: r.abort()
                        if r.request.resource_type in (
                            "image", "media", "font", "stylesheet"
                        )
                        else r.continue_()
                    )

                    for product in self.walmart_products:
                        try:
                            status = self._check_single(page, product)
                            self._process_status(product, status)
                            time.sleep(DELAY_BETWEEN_PRODUCTS_SEC)
                        except Exception as e:
                            log.warning(
                                f"[walmart_playwright] Error checking "
                                f"{product['name']}: {e}"
                            )

                    page.close()
                    context.close()
            except Exception as e:
                log.warning(f"[walmart_playwright] Session error: {e}")

        t = threading.Thread(
            target=_run,
            daemon=True,
            name="walmart_playwright_check_all",
        )
        t.start()
        t.join(timeout=CHECK_CYCLE_TIMEOUT_SEC)

    def _launch_context(self, p, impl: str):
        """Try chrome -> msedge -> chromium until one launches."""
        last_err = None
        # Patchright: cosmetic args only (window-position). Vanilla playwright:
        # add the AutomationControlled hardening even though it likely won't
        # be enough on its own for PerimeterX.
        if impl == "patchright":
            launch_args = list(OFFSCREEN_ARGS)
        else:
            launch_args = list(OFFSCREEN_ARGS) + [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]

        for channel in CHANNEL_CHAIN:
            try:
                context = p.chromium.launch_persistent_context(
                    BROWSER_PROFILE,
                    channel=channel,
                    headless=False,
                    args=launch_args,
                )
                log.debug(
                    f"[walmart_playwright] launched via channel={channel}"
                )
                return context
            except Exception as e:
                last_err = e
                continue

        log.warning(
            f"[walmart_playwright] No chromium-family browser launchable: "
            f"{last_err}"
        )
        return None

    # -- Per-product check ----------------------------------------------------

    def _check_single(self, page, product: dict):
        """Navigate to product page, return a status SimpleNamespace.

        Returns SimpleNamespace with: name, retailer, url, in_stock, price,
        checked_at. Compatible with notify_stock_change's duck-typed
        expectations.
        """
        url = product["url"]
        in_stock = False
        price = "N/A"

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_GOTO_TIMEOUT_MS,
            )
            content = page.content()

            # Challenge detection - if PerimeterX threw a challenge page,
            # bail out cleanly rather than try to extract junk
            if self._is_challenge_page(page, content):
                log.warning(
                    f"[walmart_playwright] PerimeterX challenge for "
                    f"{product['name']} - check warmup state"
                )
                return self._build_status(product, False, "N/A")

            # Primary signal: __NEXT_DATA__
            nd_result = self._extract_from_next_data(content, product)
            if nd_result is not None:
                in_stock, price, is_walmart_direct = nd_result
                if not is_walmart_direct:
                    in_stock = False
            else:
                # Fallback: CSS selectors
                in_stock, price = self._extract_via_selectors(page)

            # Sanity: price > 2x MSRP -> treat as marketplace scalper.
            # Suppress in_stock so no alert and no cart_preloader fires.
            # 2x = "100% over MSRP" per Keith's threshold; anything above
            # that isn't a genuine retail listing.
            if in_stock:
                msrp = get_msrp(product["name"], "walmart")
                listed = parse_price(price)
                if msrp and listed and listed > msrp * 2.0:
                    log.debug(
                        f"[walmart_playwright] {product['name']}: "
                        f"${listed:.2f} is {listed/msrp:.1f}x MSRP - "
                        f"suppressing as marketplace (threshold: 2.0x)"
                    )
                    in_stock = False

        except Exception as e:
            log.warning(
                f"[walmart_playwright] Check error for {product['name']}: {e}"
            )

        return self._build_status(product, in_stock, price)

    def _is_challenge_page(self, page, content: str) -> bool:
        """High-precision PerimeterX challenge detector.
        Avoids the substring false-positive that bit us in recon."""
        try:
            title = (page.title() or "").lower()
        except Exception:
            title = ""
        if any(p in title for p in CHALLENGE_TITLE_PHRASES):
            return True
        # Real product pages are 200-500KB with __NEXT_DATA__.
        # Challenge pages are ~15KB without.
        if len(content) < 50000 and "__NEXT_DATA__" not in content:
            return True
        return False

    # -- __NEXT_DATA__ extraction --------------------------------------------

    def _extract_from_next_data(self, content: str, product: dict):
        """
        Parse __NEXT_DATA__ JSON and extract (in_stock, price, walmart_direct).
        Returns None if extraction fails (caller falls back to selectors).

        Defensive: never raises. Logs warnings on missing keys but continues.
        """
        nd_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            content,
            re.DOTALL,
        )
        if not nd_match:
            return None

        try:
            nd = json.loads(nd_match.group(1))
        except json.JSONDecodeError as e:
            log.debug(f"[walmart_playwright] NEXT_DATA parse error: {e}")
            return None

        product_node = (
            nd.get("props", {})
              .get("pageProps", {})
              .get("initialData", {})
              .get("data", {})
              .get("product", {})
        ) or {}
        if not product_node:
            return None

        primary_offer = product_node.get("primaryOffer", {}) or {}

        # -- Stock signal --
        availability_status = (
            primary_offer.get("availabilityStatus")
            or product_node.get("availabilityStatus")
            or ""
        )
        in_stock = (str(availability_status).upper() == "IN_STOCK")

        # showAtc is the buy-button visibility flag - cross-check
        show_atc = product_node.get("showAtc")
        if show_atc is False and in_stock:
            in_stock = False

        # -- Seller validation --
        seller = primary_offer.get("seller", {}) or {}
        seller_name = (
            primary_offer.get("sellerName")
            or seller.get("name")
            or ""
        )
        seller_type = (
            primary_offer.get("sellerType")
            or seller.get("type")
            or primary_offer.get("offerType")
            or ""
        )
        wm_fulfillment = (
            primary_offer.get("walmartItemFulfillment")
            or product_node.get("walmartItemFulfillment")
        )

        # Walmart-direct heuristic - any of these positive AND no negatives
        url_has_marketplace_marker = (
            "conditionGroupCode" in product.get("url", "")
        )
        is_walmart_direct = (
            ("walmart" in str(seller_name).lower()
             or wm_fulfillment is True)
            and str(seller_type).upper() != "EXTERNAL_SELLER"
            and not url_has_marketplace_marker
        )

        # -- Price extraction --
        price_info = primary_offer.get("priceInfo", {}) or {}
        current_price = price_info.get("currentPrice", {}) or {}
        price_value = (
            current_price.get("priceString")
            or current_price.get("price")
            or primary_offer.get("price")
        )
        price_str = self._normalize_price(price_value)

        return in_stock, price_str, is_walmart_direct

    @staticmethod
    def _normalize_price(value) -> str:
        """Coerce a price value to '$X.YY' format. Returns 'N/A' on failure."""
        if value is None:
            return "N/A"
        if isinstance(value, str):
            return value if value.strip() else "N/A"
        if isinstance(value, (int, float)):
            return f"${float(value):.2f}"
        return "N/A"

    # -- Selector fallback ---------------------------------------------------

    def _extract_via_selectors(self, page) -> tuple:
        """Fallback when __NEXT_DATA__ is missing or unparseable.
        Per recon: button[data-automation-id="atc"] is the most stable
        ATC selector; [itemprop="price"] is the most stable price selector."""
        in_stock = False
        price = "N/A"
        try:
            atc = page.query_selector('button[data-automation-id="atc"]')
            if atc is not None:
                in_stock = True
        except Exception:
            pass
        try:
            price_el = page.query_selector('[itemprop="price"]')
            if price_el is not None:
                price = (
                    price_el.get_attribute("content")
                    or price_el.inner_text().strip()
                    or "N/A"
                )
        except Exception:
            pass
        return in_stock, price

    # -- Status object + downstream pipeline ---------------------------------

    @staticmethod
    def _build_status(product: dict, in_stock: bool, price: str):
        """Build a SimpleNamespace duck-typed for notify_stock_change.
        Avoids importing ProductStatus from tracker.py (would create cycle)."""
        return SimpleNamespace(
            name=product["name"],
            retailer="Walmart",
            url=product["url"],
            in_stock=in_stock,
            price=price,
            checked_at=datetime.now().isoformat(),
            was_in_stock=None,
        )

    def _process_status(self, product: dict, status) -> None:
        """Per-product post-check: detect transition, fire downstream hooks,
        update history."""
        url = product["url"]
        prev = self.history.get(url, {})
        was_in_stock = prev.get("in_stock", None)
        status.was_in_stock = was_in_stock

        # Detect out-of-stock -> in-stock transition (or first-time-in-stock)
        is_new_stock = (
            (status.in_stock and was_in_stock is False)
            or (status.in_stock and was_in_stock is None)
        )

        if is_new_stock:
            self._send_alert(product, status)
            self._maybe_trigger_cart_preloader(product, status)
            self._notify_stock_change(product, status)

        # Update history regardless
        self.history[url] = {
            "in_stock": status.in_stock,
            "price": status.price,
            "last_checked": status.checked_at,
            "name": status.name,
            "retailer": status.retailer,
        }
        save_history(HISTORY_FILE, self.history)

        log.info(
            f"[walmart_playwright] {status.name}: "
            f"{'IN STOCK' if status.in_stock else 'out of stock'} | "
            f"{status.price}"
        )

    def _send_alert(self, product: dict, status) -> None:
        """Send ntfy push when a Walmart product transitions to in-stock."""
        if not self.ntfy_topic:
            return
        try:
            send_ntfy(
                topic=self.ntfy_topic,
                title=f"WALMART: {status.name}",
                body=f"In stock @ {status.price}\n{status.url}",
                url=status.url,
                priority="high",
                tags="shopping_cart,fire",
            )
        except Exception as e:
            log.warning(f"[walmart_playwright] ntfy send error: {e}")

    def _maybe_trigger_cart_preloader(self, product: dict, status) -> None:
        """Fire cart_preloader directly for any in-stock transition that
        survived the upstream 2x MSRP marketplace filter. Keith's threshold:
        anything within 2x MSRP is worth staging the cart for, not just
        at-or-below MSRP.

        Replicates the auto-trigger that msrp_alert + cart_preloader monkey-
        patch do for products in run_checks(). Walmart no longer goes through
        run_checks() so we wire this manually."""
        msrp = get_msrp(product["name"], "walmart")
        listed = parse_price(status.price)
        if msrp is None or listed is None:
            # Can't compute - cart_preloader requires both for its labeling.
            # Skip rather than pass garbage values.
            return
        # Note: NO `if listed > msrp` gate here. The upstream 2x MSRP filter
        # in _check_single already set in_stock=False for prices > 2x MSRP,
        # so anything reaching this point is guaranteed listed <= 2x MSRP.
        try:
            from cart_preloader import trigger_cart_preload
            trigger_cart_preload(product, listed, msrp, self.config)
        except ImportError:
            log.debug(
                "[walmart_playwright] cart_preloader not available - skipping"
            )
        except Exception as e:
            log.warning(
                f"[walmart_playwright] cart_preloader trigger error: {e}"
            )

    def _notify_stock_change(self, product: dict, status) -> None:
        """Route stock change to walmart_queue.on_stock_change (and any other
        plugin listening for stock transitions). Mirrors what tracker.run_checks
        does for products still in CHECKER_MAP."""
        try:
            import plugins as _ps
            _ps.notify_stock_change(product, status)
        except Exception as e:
            log.debug(
                f"[walmart_playwright] notify_stock_change error: {e}"
            )
