#!/usr/bin/env python3
"""
bestbuy_invites.py - Best Buy Invite Monitor (#4)
Plugin for Keith's PokeBS tracker system.

Best Buy uses a two-phase invite system for high-demand Pokemon TCG products:

  PHASE 1 - Request Invite:
    When the "Request Invite" button appears on a product page, this monitor:
    - Sends an ntfy alert with direct link
    - Opens a browser and clicks the Invite button automatically
    - Registers you as early as possible (early registration likely improves
      Best Buy's algorithm score based on account behaviour)

  PHASE 2 - Invite Received:
    When your status changes from "Requested" to "Selected/Accepted", this monitor:
    - Sends an URGENT ntfy alert with direct checkout link
    - Opens browser directly to checkout page
    - You have 24 hours to complete purchase - YOU click Buy

IMPORTANT NOTES:
  - Best Buy's algorithm considers purchase history, membership, and browser
    behaviour when selecting invitees. Using a real logged-in browser session
    (via .browser_profile/) helps signal you are a legitimate human buyer.
  - My Best Buy+ / Total members may receive priority invite consideration.
  - This monitor checks every 10 minutes during active invite windows and
    every 30 minutes otherwise to minimise CPU load.

SECURITY:
  - Never clicks any purchase or payment button
  - Never reads or stores payment data
  - Never enters credentials
  - Browser opens to checkout after invite - YOU click Place Order

SETUP:
  Log into Best Buy in the cart preloader browser first:
    python cart_preloader.py --setup --retailer bestbuy
"""

import os
import re
import time
import json
import logging
from datetime import datetime

import sys
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)  # plugins/ -> tcg_tracker/
if _root not in sys.path:
    sys.path.insert(0, _root)


# ── Path resolution - works from root or plugins/ folder ─────────────────────
import sys as _sys, os as _os
_here = _os.path.dirname(_os.path.abspath(__file__))
_root = _os.path.dirname(_here) if _os.path.basename(_here) == "plugins" else _here
if _root not in _sys.path:
    _sys.path.insert(0, _root)
if _here not in _sys.path:
    _sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────
from shared import launch_chromium_with_fallback  # v6.1.2 step 2: ICU bug fix
from shared import BROWSER_PROFILES  # v6.1.4 step 2b: per-plugin profile dirs
from shared import (
    DATA_DIR, BROWSER_PROFILE, send_ntfy,
    open_browser, load_history, save_history,
)

log = logging.getLogger(__name__)

HISTORY_FILE = "bestbuy_invite_history.json"

# ── Invite page button states ──────────────────────────────────────────────
# These are the rendered button states Best Buy uses on product pages
INVITE_BUTTON_STATES = {
    # Button text / data-button-state values that mean invite is available
    "requestable": [
        "REQUEST_INVITE",
        "Request Invite",
        "request invite",
        "REQUEST INVITE",
    ],
    # Button states meaning you already requested
    "requested": [
        "INVITE_REQUESTED",
        "Invite Requested",
        "invite requested",
        "Request Submitted",
    ],
    # Button states meaning you have been selected - ACT NOW
    "selected": [
        "INVITE_ACCEPTED",
        "Add to Cart",          # After invite accepted, button becomes Add to Cart
        "ADD_TO_CART",
        "Invite Accepted",
    ],
    # Out of stock / not yet live
    "unavailable": [
        "SOLD_OUT",
        "COMING_SOON",
        "Sold Out",
        "Coming Soon",
        "Check Stores",
    ],
}

# Checkout URL
BB_CART_URL = "https://www.bestbuy.com/cart"


class BestBuyInviteMonitor:
    """
    Monitors all tracked Best Buy products for invite button availability
    and invite selection notifications.
    Registered as a plugin via plugins.py BestBuyInvites_Plugin.
    """

    def __init__(self, config: dict, products: list):
        self.config        = config
        self.ntfy_topic    = config.get("ntfy_topic", "")
        self.history       = load_history(HISTORY_FILE)
        self.bb_products   = [
            p for p in products
            if p.get("retailer", "").lower() in ("bestbuy", "best buy")
            and p.get("sku")
        ]
        log.info(f"[bestbuy_invites] Monitoring {len(self.bb_products)} Best Buy products")

    # ── Plugin lifecycle ─────────────────────────────────────────────────────

    def register(self, scheduler) -> None:
        """Register jobs with the scheduler (v6.0.0 phased boot).

        Replaces the legacy start(schedule) signature. The first check used
        to run synchronously inside start(), blocking the plugin loader for
        ~60s. Now it's queued as a kickoff job that fires at T+30s after
        boot_ready(), in a daemon thread, so the dashboard comes up first.
        """
        scheduler.register_job(
            name="bestbuy_invites.check_all_products",
            fn=self._check_all_products,
            cadence="every 10 minutes",
            kickoff=True,
            kickoff_delay=30,
            owner="bestbuy_invites",
        )
        log.info("[bestbuy_invites] Registered - kickoff @ T+30s, then every 10 min")

    # ── Core check ───────────────────────────────────────────────────────────

    def _check_all_products(self) -> None:
        """Check all tracked Best Buy products. Runs in a daemon thread (v6.0.0)
        to avoid sync_playwright conflict with tracker.py's asyncio event loop.
        Same pattern as amazon_monitor and costco_tracker."""
        import threading

        if not self.bb_products:
            return

        def _run():
            log.debug(f"[bestbuy_invites] Checking {len(self.bb_products)} products...")
            for product in self.bb_products:
                try:
                    state = self._get_invite_state(product)
                    self._handle_state_change(product, state)
                    time.sleep(3)  # Polite delay between products
                except Exception as e:
                    log.warning(f"[bestbuy_invites] Error checking {product['name']}: {e}")

        t = threading.Thread(target=_run, daemon=True, name="bestbuy_invites_check")
        t.start()
        t.join(timeout=300)  # Max 5 minutes for full check cycle

    def _get_invite_state(self, product: dict) -> str:
        """
        Use Playwright to render the Best Buy product page and read the
        current button state. Returns one of:
          'requestable' - invite button is live, hasn't been clicked yet
          'requested'   - you've already requested an invite
          'selected'    - you've been selected, can now add to cart
          'unavailable' - sold out, coming soon, or no button found
          'unknown'     - couldn't determine state
        """
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            log.warning("[bestbuy_invites] Playwright not installed")
            return "unknown"

        url = product.get("url", "")
        if not url:
            return "unknown"

        state = "unknown"

        try:
            with sync_playwright() as p:
                context = launch_chromium_with_fallback(
                    p,
                    BROWSER_PROFILES["bestbuy_invites"],
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--blink-settings=imagesEnabled=false",
                    ],
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    log_prefix="bestbuy_invites",
                )

                page = context.new_page()

                # Block images/fonts/media - faster, lower CPU
                page.route("**/*", lambda r: r.abort()
                    if r.request.resource_type in ("image", "media", "font", "stylesheet")
                    else r.continue_()
                )

                page.goto(url, wait_until="domcontentloaded", timeout=20000)

                try:
                    # Wait for the buy/invite button area to render
                    page.wait_for_selector(
                        ".add-to-cart-button, [data-button-state], "
                        ".btn-primary, [class*='fulfillment']",
                        timeout=8000,
                    )
                except PWTimeout:
                    pass  # Continue and check what we can

                content = page.content()

                # Check rendered button states
                state = self._parse_button_state(content, page)

                log.debug(f"[bestbuy_invites] {product['name']}: state={state}")
                # Tear down route handlers cleanly before close to prevent
                # asyncio CancelledError noise from in-flight requests
                # being cancelled mid-flight (v6.0.0 step 4.8.5).
                # page.unroute waits for pending handlers to drain.
                try:
                    page.unroute("**/*")
                except Exception:
                    pass
                page.close()
                context.close()

        except Exception as e:
            log.debug(f"[bestbuy_invites] Playwright error for {product['name']}: {e}")
            state = "unknown"

        return state

    def _parse_button_state(self, content: str, page) -> str:
        """Parse rendered page content to determine invite state."""

        # Priority order: selected > requestable > requested > unavailable > unknown

        # Check data-button-state attribute (most reliable)
        btn_states = re.findall(r'data-button-state=["\']([^"\']+)["\']', content)
        btn_texts = re.findall(r'class="[^"]*btn[^"]*"[^>]*>([^<]{3,40})<', content)

        # Also check rendered button elements directly
        try:
            btn_el = page.query_selector(".add-to-cart-button, [data-button-state]")
            if btn_el:
                rendered_state = btn_el.get_attribute("data-button-state") or ""
                rendered_text = btn_el.inner_text().strip()
                btn_states.insert(0, rendered_state)
                btn_texts.insert(0, rendered_text)
        except Exception:
            pass

        all_signals = [s.upper() for s in btn_states + btn_texts if s]

        # Check in priority order
        for signal in all_signals:
            if any(s.upper() in signal for s in INVITE_BUTTON_STATES["selected"]):
                return "selected"

        for signal in all_signals:
            if any(s.upper() in signal for s in INVITE_BUTTON_STATES["requestable"]):
                return "requestable"

        for signal in all_signals:
            if any(s.upper() in signal for s in INVITE_BUTTON_STATES["requested"]):
                return "requested"

        for signal in all_signals:
            if any(s.upper() in signal for s in INVITE_BUTTON_STATES["unavailable"]):
                return "unavailable"

        return "unknown"

    # ── State change handler ─────────────────────────────────────────────────

    def _handle_state_change(self, product: dict, new_state: str) -> None:
        """
        Compare new state to previous state and fire alerts on meaningful changes.
        Avoids re-alerting for the same state.
        """
        url    = product.get("url", "")
        name   = product.get("name", "")
        key    = f"bb_{product.get('sku', url)}"
        prev   = self.history.get(key, {})
        prev_state = prev.get("state", "unknown")

        # Always update last seen
        self.history[key] = {
            **prev,
            "state":        new_state,
            "last_checked": datetime.now().isoformat(),
            "name":         name,
            "url":          url,
        }

        # No change - nothing to do
        if new_state == prev_state:
            save_history(HISTORY_FILE, self.history)
            return

        log.info(f"[bestbuy_invites] State change: {name}: {prev_state} -> {new_state}")

        # ── SELECTED: you have an invite - act immediately ──
        if new_state == "selected":
            self.history[key]["selected_at"] = datetime.now().isoformat()
            save_history(HISTORY_FILE, self.history)

            send_ntfy(
                topic=self.ntfy_topic,
                title="Best Buy INVITE SELECTED",
                body=(
                    f"YOU HAVE BEEN SELECTED!\n"
                    f"{name}\n"
                    f"24 HOURS TO COMPLETE PURCHASE\n"
                    f"Browser opening to checkout now"
                ),
                url=BB_CART_URL,
                priority="urgent",
                tags="rotating_light,tada,shopping_cart",
            )
            log.info(f"[bestbuy_invites] SELECTED alert sent for {name}")

            # Open browser directly to cart/checkout - YOU click Place Order
            open_browser(
                BB_CART_URL,
                banner_title=f"INVITE SELECTED: {name[:40]}",
                banner_msg="You have 24 hours - click Checkout and Place Order NOW",
            )

        # ── REQUESTABLE: invite button just went live ──
        elif new_state == "requestable":
            self.history[key]["requestable_at"] = datetime.now().isoformat()
            save_history(HISTORY_FILE, self.history)

            send_ntfy(
                topic=self.ntfy_topic,
                title="Best Buy Invite Available",
                body=(
                    f"Invite button is LIVE\n"
                    f"{name}\n"
                    f"Clicking invite button automatically..."
                ),
                url=url,
                priority="high",
                tags="bell,shopping_cart",
            )
            log.info(f"[bestbuy_invites] Invite available for {name} - auto-clicking")

            # Auto-click the invite button so you're registered immediately
            self._auto_request_invite(product)

        # ── REQUESTED: already registered, just track it ──
        elif new_state == "requested":
            save_history(HISTORY_FILE, self.history)
            log.info(f"[bestbuy_invites] Invite already requested for {name} - monitoring for selection")

        else:
            save_history(HISTORY_FILE, self.history)

    # ── Auto-click invite button ─────────────────────────────────────────────

    def _auto_request_invite(self, product: dict) -> None:
        """
        Open a VISIBLE browser, click the Request Invite button,
        and confirm the request was submitted.
        Runs in background thread so it doesn't block the check loop.

        SECURITY: Only clicks the invite/request button.
        Never touches Add to Cart, checkout, or payment fields.
        """
        import threading

        def _run():
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

                url  = product.get("url", "")
                name = product.get("name", "")

                with sync_playwright() as p:
                    context = launch_chromium_with_fallback(
                        p,
                        BROWSER_PROFILE,
                        headless=False,  # Visible - you can watch and verify
                        viewport=None,
                        args=[
                            "--start-maximized",
                            "--disable-blink-features=AutomationControlled",
                            "--window-size=1400,900",
                        ],
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        log_prefix="bestbuy_invites",
                    )
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2500)

                    # Find the invite/request button - be very specific
                    # Only click invite-type buttons, never purchase buttons
                    invite_btn = None
                    safe_selectors = [
                        "button[data-button-state='REQUEST_INVITE']",
                        "button[data-button-state='INVITE_REQUESTED']:not([disabled])",
                        "button.request-invite-button",
                        "button[aria-label*='Request Invite']",
                        "button[aria-label*='request invite']",
                    ]
                    # Fallback text search - only if text is explicitly invite-related
                    for sel in safe_selectors:
                        try:
                            btn = page.query_selector(sel)
                            if btn:
                                invite_btn = btn
                                log.info(f"[bestbuy_invites] Found invite button via: {sel}")
                                break
                        except Exception:
                            continue

                    # If no specific selector matched, search by visible text
                    if not invite_btn:
                        try:
                            invite_btn = page.get_by_role(
                                "button",
                                name=re.compile(r"request invite", re.I)
                            ).first
                        except Exception:
                            pass

                    if invite_btn:
                        # Verify this is NOT an add-to-cart or purchase button
                        btn_text = invite_btn.inner_text().strip().lower()
                        btn_state = invite_btn.get_attribute("data-button-state") or ""
                        is_safe = (
                            "invite" in btn_text
                            or "REQUEST_INVITE" in btn_state
                        )
                        is_purchase = any(
                            w in btn_text for w in
                            ["add to cart", "buy", "checkout", "place order", "purchase"]
                        )

                        if is_safe and not is_purchase:
                            invite_btn.click()
                            page.wait_for_timeout(2000)
                            log.info(f"[bestbuy_invites] Invite button clicked for {name}")

                            # Confirm submission
                            page.evaluate("""
                                () => {
                                    const d = document.createElement('div');
                                    d.id = '_bb_invite_confirm';
                                    d.style.cssText = `
                                        position:fixed;top:0;left:0;right:0;z-index:2147483647;
                                        background:#1d3557;color:#ffd700;padding:14px 20px;
                                        font-family:monospace;font-size:14px;font-weight:bold;
                                        border-bottom:3px solid #ffd700;text-align:center;
                                        box-shadow:0 4px 16px rgba(0,0,0,.6);
                                    `;
                                    d.innerHTML = `
                                        PokeBS: Invite Requested at Best Buy!
                                        <span style="color:#90ee90;font-weight:normal;margin-left:16px">
                                            You will be notified via app and ntfy if selected
                                        </span>
                                        <span style="color:#aaa;font-size:11px;margin-left:16px;cursor:pointer"
                                              onclick="this.parentNode.remove()">(dismiss)</span>
                                    `;
                                    document.body.prepend(d);
                                }
                            """)

                            # Update history to reflect invite was requested
                            key = f"bb_{product.get('sku', product.get('url', ''))}"
                            self.history[key] = {
                                **self.history.get(key, {}),
                                "state":        "requested",
                                "requested_at": datetime.now().isoformat(),
                            }
                            save_history(HISTORY_FILE, self.history)

                            send_ntfy(
                                topic=self.ntfy_topic,
                                title="Best Buy Invite Requested",
                                body=(
                                    f"Invite successfully requested\n"
                                    f"{name}\n"
                                    f"Monitor will alert you when selected"
                                ),
                                url=url,
                                priority="default",
                                tags="white_check_mark,bell",
                            )
                        else:
                            log.warning(
                                f"[bestbuy_invites] Skipped click - button text "
                                f"'{btn_text}' failed safety check"
                            )
                    else:
                        log.warning(
                            f"[bestbuy_invites] Could not find invite button for {name} "
                            f"- may need manual click at: {url}"
                        )
                        send_ntfy(
                            topic=self.ntfy_topic,
                            title="Best Buy Invite - Manual Action Needed",
                            body=(
                                f"Could not auto-click invite button\n"
                                f"{name}\n"
                                f"Please click Request Invite manually"
                            ),
                            url=url,
                            priority="high",
                            tags="warning,shopping_cart",
                        )

                    # Keep browser open briefly so you can verify
                    try:
                        page.wait_for_timeout(8000)
                        page.close()
                        context.close()
                    except Exception:
                        pass

            except ImportError:
                log.warning("[bestbuy_invites] Playwright not installed")
            except Exception as e:
                log.error(f"[bestbuy_invites] Auto-invite error: {e}")

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"bb_invite_{product.get('sku', 'unknown')}",
        )
        thread.start()

    # ── Status summary ───────────────────────────────────────────────────────

    def get_status_summary(self) -> list[dict]:
        """Return current invite status for all tracked products."""
        summary = []
        for product in self.bb_products:
            key   = f"bb_{product.get('sku', product.get('url', ''))}"
            entry = self.history.get(key, {})
            summary.append({
                "name":          product["name"],
                "sku":           product.get("sku", ""),
                "url":           product.get("url", ""),
                "state":         entry.get("state", "unknown"),
                "last_checked":  entry.get("last_checked", "never"),
                "requested_at":  entry.get("requested_at"),
                "selected_at":   entry.get("selected_at"),
            })
        return sorted(summary, key=lambda x: x["state"])


# ── Standalone diagnostic ────────────────────────────────────────────────────

def run_diagnostics(config: dict, products: list) -> None:
    """
    Run invite status check for all Best Buy products and print a summary.
    Usage: python bestbuy_invites.py
    """
    print("\n" + "=" * 60)
    print("  Best Buy Invite Monitor - Diagnostic")
    print("=" * 60)

    monitor = BestBuyInviteMonitor(config, products)

    if not monitor.bb_products:
        print("\n  No Best Buy products with SKUs found in PRODUCTS list.")
        print("  Ensure products have 'retailer': 'bestbuy' and 'sku' set.\n")
        return

    print(f"\n  Checking {len(monitor.bb_products)} products...\n")

    for product in monitor.bb_products:
        state = monitor._get_invite_state(product)
        state_labels = {
            "requestable": "INVITE AVAILABLE - click now",
            "requested":   "Invite already requested",
            "selected":    "SELECTED - buy within 24hrs",
            "unavailable": "Unavailable / sold out",
            "unknown":     "Unknown (Playwright issue?)",
        }
        label = state_labels.get(state, state)
        print(f"  {product['name'][:50]:<50}  [{state.upper():<12}]  {label}")
        time.sleep(2)

    print("\n  Current history:")
    for entry in monitor.get_status_summary():
        print(
            f"  {entry['name'][:40]:<40}  "
            f"state={entry['state']:<12}  "
            f"last={entry['last_checked'][:16] if entry['last_checked'] != 'never' else 'never'}"
        )

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
        log.error("Run from the tcg_tracker/ directory: python plugins/bestbuy_invites.py")
