#!/usr/bin/env python3
"""
amazon_monitor.py - Amazon MSRP Monitor (#6)
Plugin for Keith's PokeBS tracker system.

Monitors Amazon.com for Pokemon TCG products listed at or below MSRP
by Amazon itself (not third-party sellers).

WHY AMAZON-ONLY SELLER:
  Pokemon TCG products on Amazon from third-party sellers carry real
  authenticity risks - resealed packs and weighted boxes have been
  documented. Amazon.com as the direct seller guarantees:
    - Product sourced from authorized distributors
    - Amazon's A-to-Z guarantee if anything is wrong
    - No intermediary who could tamper with packaging
  FBA sellers and third-party sellers are flagged separately at lower
  priority so you can make your own informed decision.

HOW IT WORKS:
  Uses Playwright to render Amazon product pages (Amazon blocks plain
  requests with bot detection). Reads the seller name and price from
  the rendered page. Checks every 15 minutes - less frequently than
  other retailers since Amazon MSRP windows are random and not tied
  to predictable drop schedules.

ON DETECTION:
  - Sends urgent ntfy alert with direct URL click action
  - Opens browser to product page
  - Attempts to click Add to Cart (YOU complete checkout)

SECURITY:
  - Never clicks Buy Now, Place Order, or any purchase button
  - Never reads or stores payment information
  - Uses your existing Amazon login session from .browser_profile/
  - YOU complete the purchase

SETUP:
  Log into Amazon in the cart preloader browser:
    python cart_preloader.py --setup --retailer amazon
  Add --retailer amazon to the setup flow (uses amazon.com URL)
"""

import os
import re
import time
import logging
from datetime import datetime

import sys
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
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
from shared import (
    DATA_DIR, BROWSER_PROFILE, HEADERS,
    get_msrp, parse_price, send_ntfy,
    open_browser, load_history, save_history,
)

log = logging.getLogger(__name__)

HISTORY_FILE = "amazon_monitor_history.json"
AMAZON_CART  = "https://www.amazon.com/gp/cart/view.html"

# ── Amazon product list ────────────────────────────────────────────────────
# ASINs sourced from confirmed Amazon product pages Apr 2026
# Add new products here as sets release - format: (name, asin, msrp_override)
# msrp_override=None means shared.get_msrp() will determine it from the name
AMAZON_PRODUCTS = [
    # ── Chaos Rising (May 22, 2026) ──
    ("Pokemon Chaos Rising ETB",            "B0GR6N18F6", None),
    ("Pokemon Chaos Rising Booster Bundle", "B0GR6Q72ND", None),
    ("Pokemon Chaos Rising Display Box",    "B0GSJMQ3QQ", None),

    # ── Ascended Heroes ──
    ("Pokemon Ascended Heroes ETB",         "B0G3CY83L5", None),

    # ── Destined Rivals ──
    ("Pokemon Destined Rivals ETB",         "B0CTG2ZJKL", None),

    # ── Journey Together ──
    ("Pokemon Journey Together ETB",        "B0CRMHP2VZ", None),

    # ── Prismatic Evolutions ──
    ("Pokemon Prismatic Evolutions ETB",    "B0CP4DX3RS", None),
]

def asin_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin}"


# ── Price / seller extraction ──────────────────────────────────────────────

def _parse_amazon_page(content: str, page) -> dict:
    """
    Extract price, seller, and availability from a rendered Amazon page.
    Returns dict with keys: price, seller, in_stock, seller_type
    """
    result = {
        "price":       None,
        "seller":      "unknown",
        "in_stock":    False,
        "seller_type": "unknown",  # 'amazon', 'fba', 'third_party'
    }

    # ── Price ──
    # Try rendered element first
    try:
        price_el = (
            page.query_selector(".priceToPay .a-price-whole") or
            page.query_selector("#priceblock_ourprice") or
            page.query_selector("#price_inside_buybox") or
            page.query_selector(".a-price .a-offscreen")
        )
        if price_el:
            raw = price_el.inner_text().strip()
            result["price"] = parse_price(raw)
    except Exception:
        pass

    # Fallback - regex on page source
    if not result["price"]:
        price_patterns = [
            r'"price"\s*:\s*"?\$?([\d.]+)"?',
            r'priceAmount["\s]*:\s*"?([\d.]+)"?',
            r'class="a-price-whole"[^>]*>([^<]+)',
        ]
        for pat in price_patterns:
            m = re.search(pat, content)
            if m:
                result["price"] = parse_price(m.group(1))
                if result["price"]:
                    break

    # ── Seller ──
    try:
        seller_el = (
            page.query_selector("#sellerProfileTriggerId") or
            page.query_selector("#merchant-info a") or
            page.query_selector("[data-feature-name='merchant-info'] a")
        )
        if seller_el:
            result["seller"] = seller_el.inner_text().strip()
    except Exception:
        pass

    if result["seller"] == "unknown":
        seller_patterns = [
            r'Ships from and sold by <[^>]+>([^<]+)<',
            r'Sold by\s*<[^>]+>([^<]+)<',
            r'"merchant"\s*:\s*"([^"]+)"',
        ]
        for pat in seller_patterns:
            m = re.search(pat, content)
            if m:
                result["seller"] = m.group(1).strip()
                break

    # ── Seller type classification ──
    seller_lower = result["seller"].lower()
    if "amazon.com" in seller_lower or seller_lower == "amazon":
        result["seller_type"] = "amazon"
    elif "fulfilled by amazon" in content.lower() or "fulfillment by amazon" in content.lower():
        result["seller_type"] = "fba"
    else:
        result["seller_type"] = "third_party"

    # ── Availability ──
    try:
        avail_el = page.query_selector("#availability span")
        avail_text = avail_el.inner_text().strip().lower() if avail_el else ""
        result["in_stock"] = any(
            w in avail_text for w in
            ["in stock", "ships from", "only", "order soon"]
        )
        if any(w in avail_text for w in ["currently unavailable", "out of stock", "temporarily"]):
            result["in_stock"] = False
    except Exception:
        pass

    # Fallback - add to cart button present means in stock
    if not result["in_stock"]:
        try:
            atc = page.query_selector("#add-to-cart-button:not([disabled])")
            if atc:
                result["in_stock"] = True
        except Exception:
            pass

    return result


# ── Core monitor class ─────────────────────────────────────────────────────

class AmazonMSRPMonitor:
    """
    Monitors Amazon.com for Pokemon TCG products at MSRP sold by Amazon.
    Registered as a plugin via plugins.py AmazonMonitor_Plugin.
    """

    def __init__(self, config: dict, products: list):
        self.config     = config
        self.ntfy_topic = config.get("ntfy_topic", "")
        self.history    = load_history(HISTORY_FILE)

        # Merge tracked PRODUCTS list with our built-in ASIN list
        # If a product in PRODUCTS has an 'asin' key, add it too
        self.watch_list = list(AMAZON_PRODUCTS)
        for p in products:
            asin = p.get("asin", "")
            if asin and not any(a[1] == asin for a in self.watch_list):
                self.watch_list.append((p["name"], asin, None))

        log.info(f"[amazon_monitor] Watching {len(self.watch_list)} Amazon products")

    def start(self, schedule) -> None:
        """Register scheduled checks."""
        # Check every 15 minutes - Amazon restocks are random, not scheduled
        # Less frequent than other retailers to reduce CPU load
        schedule.every(15).minutes.do(self._check_all)
        self._check_all()
        log.info("[amazon_monitor] Scheduled - checking every 15 minutes")

    def _check_all(self) -> None:
        """Check all watched Amazon products. Runs in a thread to avoid
        sync_playwright conflict with the tracker's asyncio event loop."""
        import threading
        import concurrent.futures

        done = concurrent.futures.Future()

        def _run():
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                log.warning("[amazon_monitor] Playwright not installed - skipping")
                done.set_result(None)
                return

            log.debug(f"[amazon_monitor] Checking {len(self.watch_list)} products...")

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

                    # Block images, fonts, media to reduce load
                    page.route("**/*", lambda r: r.abort()
                        if r.request.resource_type in ("image", "media", "font", "stylesheet")
                        else r.continue_()
                    )

                    for name, asin, msrp_override in self.watch_list:
                        try:
                            self._check_product(page, name, asin, msrp_override)
                            time.sleep(4)  # Polite delay between products
                        except Exception as e:
                            log.debug(f"[amazon_monitor] Error checking {name}: {e}")

                    page.close()
                    context.close()

            except Exception as e:
                log.warning(f"[amazon_monitor] Session error: {e}")
            finally:
                done.set_result(None)

        t = threading.Thread(target=_run, daemon=True, name="amz_check_all")
        t.start()
        t.join(timeout=300)  # Max 5 minutes for full check cycle

    def _check_product(self, page, name: str, asin: str, msrp_override) -> None:
        """Check a single Amazon product page."""
        from playwright.sync_api import TimeoutError as PWTimeout

        url  = asin_url(asin)
        msrp = msrp_override or get_msrp(name)
        key  = f"amz_{asin}"
        prev = self.history.get(key, {})

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_selector(
                    "#availability, #priceblock_ourprice, .priceToPay, #add-to-cart-button",
                    timeout=8000,
                )
            except PWTimeout:
                pass

            content = page.content()
            data    = _parse_amazon_page(content, page)

            log.debug(
                f"[amazon_monitor] {name}: price={data['price']} "
                f"seller={data['seller']} seller_type={data['seller_type']} "
                f"in_stock={data['in_stock']}"
            )

            # ── Check conditions ──
            if not data["in_stock"] or not data["price"] or not msrp:
                self.history[key] = {
                    **prev,
                    "last_checked":  datetime.now().isoformat(),
                    "last_price":    data["price"],
                    "last_seller":   data["seller"],
                    "in_stock":      data["in_stock"],
                }
                save_history(HISTORY_FILE, self.history)
                return

            at_msrp     = data["price"] <= msrp
            prev_alerted = prev.get("alerted_at_price")
            already_sent = (
                prev_alerted is not None and
                abs(prev_alerted - data["price"]) < 0.50 and
                prev.get("seller_type") == data["seller_type"]
            )

            # ── Amazon-sold at MSRP - URGENT alert ──
            if data["seller_type"] == "amazon" and at_msrp and not already_sent:
                savings      = round(msrp - data["price"], 2)
                savings_note = f" - Save ${savings:.2f}!" if savings > 0 else " - Exact MSRP"
                log.info(f"[amazon_monitor] MSRP ALERT (Amazon): {name} @ ${data['price']:.2f}")

                send_ntfy(
                    topic=self.ntfy_topic,
                    title=f"Amazon MSRP: {name[:40]}",
                    body=(
                        f"Amazon.com selling at MSRP!\n"
                        f"{name}\n"
                        f"${data['price']:.2f} (MSRP ${msrp:.2f}){savings_note}\n"
                        f"Sold by: Amazon.com - AUTHENTIC"
                    ),
                    url=url,
                    priority="urgent",
                    tags="rotating_light,amazon,moneybag",
                )

                # Open browser and attempt add to cart
                self._open_and_add_to_cart(name, url, data["price"], msrp)

                self.history[key] = {
                    **prev,
                    "last_checked":    datetime.now().isoformat(),
                    "last_price":      data["price"],
                    "last_seller":     data["seller"],
                    "seller_type":     data["seller_type"],
                    "in_stock":        True,
                    "alerted_at_price": data["price"],
                    "alerted_at":      datetime.now().isoformat(),
                }

            # ── FBA at MSRP - lower-priority heads-up ──
            elif data["seller_type"] == "fba" and at_msrp and not already_sent:
                log.info(f"[amazon_monitor] FBA MSRP (lower priority): {name} @ ${data['price']:.2f}")
                send_ntfy(
                    topic=self.ntfy_topic,
                    title=f"Amazon FBA MSRP: {name[:35]}",
                    body=(
                        f"FBA seller at MSRP (verify authenticity)\n"
                        f"{name}\n"
                        f"${data['price']:.2f} - Seller: {data['seller'][:40]}\n"
                        f"Note: FBA = 3rd party in Amazon warehouse"
                    ),
                    url=url,
                    priority="default",
                    tags="warning,shopping_cart",
                )

                self.history[key] = {
                    **prev,
                    "last_checked":     datetime.now().isoformat(),
                    "last_price":       data["price"],
                    "seller_type":      data["seller_type"],
                    "alerted_at_price": data["price"],
                    "alerted_at":       datetime.now().isoformat(),
                }

            else:
                # Update history without alerting
                self.history[key] = {
                    **prev,
                    "last_checked": datetime.now().isoformat(),
                    "last_price":   data["price"],
                    "last_seller":  data["seller"],
                    "seller_type":  data["seller_type"],
                    "in_stock":     data["in_stock"],
                }

            save_history(HISTORY_FILE, self.history)

        except Exception as e:
            log.debug(f"[amazon_monitor] {name} check error: {e}")

    def _open_and_add_to_cart(
        self, name: str, url: str, price: float, msrp: float
    ) -> None:
        """
        Open a visible browser, navigate to product, click Add to Cart,
        go to cart page. YOU complete checkout.

        SECURITY: Never clicks Buy Now, Place Order, or any payment button.
        """
        import threading

        def _run():
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(
                        BROWSER_PROFILE,
                        headless=False,
                        viewport=None,
                        args=[
                            "--start-maximized",
                            "--disable-blink-features=AutomationControlled",
                            "--window-size=1400,900",
                        ],
                        user_agent=HEADERS["User-Agent"],
                    )
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(2500)

                    # Add to Cart - safe selectors only, never Buy Now
                    atc_clicked = False
                    safe_selectors = [
                        "#add-to-cart-button",
                        "input[name='submit.add-to-cart']",
                        "input[id='add-to-cart-button']",
                    ]
                    # Explicitly excluded: Buy Now, 1-Click, Subscribe & Save
                    excluded_text = [
                        "buy now", "1-click", "subscribe", "place your order"
                    ]

                    for sel in safe_selectors:
                        try:
                            btn = page.query_selector(sel)
                            if btn:
                                btn_text = btn.inner_text().strip().lower()
                                if any(x in btn_text for x in excluded_text):
                                    log.warning(
                                        f"[amazon_monitor] Skipped button "
                                        f"'{btn_text}' - excluded text match"
                                    )
                                    continue
                                btn.click()
                                page.wait_for_timeout(2500)
                                atc_clicked = True
                                log.info(f"[amazon_monitor] Add to Cart clicked for {name}")
                                break
                        except Exception:
                            continue

                    # Navigate to cart
                    page.goto(AMAZON_CART, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(1500)

                    # Inject banner
                    savings = round(msrp - price, 2)
                    savings_note = f"Save ${savings:.2f} vs MSRP!" if savings > 0 else "At MSRP"
                    page.evaluate(f"""
                        () => {{
                            const ex = document.getElementById('_amzbanner');
                            if (ex) ex.remove();
                            const d = document.createElement('div');
                            d.id = '_amzbanner';
                            d.style.cssText = `
                                position:fixed;top:0;left:0;right:0;z-index:2147483647;
                                background:#131921;color:#ff9900;padding:12px 20px;
                                font-family:monospace;font-size:14px;font-weight:bold;
                                border-bottom:3px solid #ff9900;display:flex;
                                align-items:center;justify-content:space-between;
                                box-shadow:0 4px 16px rgba(0,0,0,.6);
                            `;
                            d.innerHTML = `
                                <span>
                                    PokeBS Amazon Alert:
                                    <span style="color:#3ddc84">${name[:40]} @ $${price:.2f} - {savings_note}</span>
                                    <span style="color:#fff;font-weight:normal;margin-left:12px">
                                        {'Item added to cart - ' if {str(atc_clicked).lower()} else 'Add to cart manually - '}
                                        YOU click Checkout to buy
                                    </span>
                                </span>
                                <span style="color:#aaa;font-size:11px;cursor:pointer"
                                      onclick="this.parentNode.remove()">(dismiss)</span>
                            `;
                            document.body.prepend(d);
                        }}
                    """)

                    log.info(f"[amazon_monitor] Browser open - waiting for user to checkout")
                    try:
                        page.wait_for_event("close", timeout=0)
                    except Exception:
                        pass
                    try:
                        context.close()
                    except Exception:
                        pass

            except ImportError:
                log.warning("[amazon_monitor] Playwright not installed")
            except Exception as e:
                log.error(f"[amazon_monitor] Browser error: {e}")

        thread = threading.Thread(
            target=_run, daemon=True, name=f"amz_{name[:20]}"
        )
        thread.start()

    def get_status_summary(self) -> list[dict]:
        """Return last-known status for all watched products."""
        results = []
        for name, asin, msrp_override in self.watch_list:
            key  = f"amz_{asin}"
            entry = self.history.get(key, {})
            results.append({
                "name":         name,
                "asin":         asin,
                "url":          asin_url(asin),
                "last_price":   entry.get("last_price"),
                "last_seller":  entry.get("last_seller", "unknown"),
                "seller_type":  entry.get("seller_type", "unknown"),
                "in_stock":     entry.get("in_stock", False),
                "last_checked": entry.get("last_checked", "never"),
                "alerted_at":   entry.get("alerted_at"),
            })
        return results


# ── Standalone diagnostic ──────────────────────────────────────────────────

def run_diagnostics(config: dict, products: list) -> None:
    """
    Check all Amazon products and print current prices/sellers.
    Usage: python amazon_monitor.py
    """
    print("\n" + "=" * 65)
    print("  Amazon MSRP Monitor - Diagnostic")
    print("=" * 65)
    print(f"  Checking {len(AMAZON_PRODUCTS)} products...\n")

    monitor = AmazonMSRPMonitor(config, products)
    monitor._check_all()

    print(f"\n  {'Product':<42} {'Price':>7} {'MSRP':>7}  Seller Type    In Stock")
    print("  " + "-" * 78)

    for entry in monitor.get_status_summary():
        msrp    = get_msrp(entry["name"])
        price   = entry["last_price"]
        p_str   = f"${price:.2f}" if price else "N/A"
        m_str   = f"${msrp:.2f}" if msrp else "N/A"
        at_msrp = " DEAL" if (price and msrp and price <= msrp) else ""
        print(
            f"  {entry['name'][:41]:<42} {p_str:>7} {m_str:>7}  "
            f"{entry['seller_type']:<14} {'YES' if entry['in_stock'] else 'no'}"
            f"{at_msrp}"
        )

    print("\n" + "=" * 65 + "\n")


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
        log.error("Run from tcg_tracker/ directory: python plugins/amazon_monitor.py")
