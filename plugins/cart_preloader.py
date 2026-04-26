#!/usr/bin/env python3
"""
Cart Pre-Loader - Auto-triggered on MSRP drop detection.

When the tracker detects a product is IN STOCK at or below MSRP,
this script fires automatically and:
  1. Opens a visible browser window
  2. Navigates to the product page
  3. Clicks "Add to Cart" if the button is present and enabled
  4. Navigates to the checkout page
  5. STOPS - you click Place Order yourself

SECURITY GUARANTEES:
  ✅ Never clicks Place Order, Submit, or any purchase button
  ✅ Never reads, stores, or logs any payment card data
  ✅ Never enters any credentials or financial information
  ✅ Uses your existing saved browser session (cookies/login)
  ✅ Runs entirely locally - only outbound call is ntfy alert
  ✅ You are always the final decision maker

SETUP (one time):
  pip install playwright
  playwright install chromium
  Then log into Target, Walmart, Best Buy, and Pokemon Center
  in Chrome normally - your login persists in .browser_profile/

The tracker calls trigger_cart_preload() automatically when
a product is detected in stock at MSRP. You can also test it:
  python cart_preloader.py --test
"""

import asyncio
import json
import logging
import os
import sys
import argparse
from datetime import datetime

# ── Path resolution ───────────────────────────────────────────────────────────
# Works whether run as:
#   python plugins/cart_preloader.py   (from tcg_tracker/ root)
#   python cart_preloader.py           (from plugins/ folder)
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "plugins" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)
if _here not in sys.path:
    sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────

from shared import OUTPUT_DIR, BROWSER_PROFILE, HEADERS, send_ntfy

log = logging.getLogger(__name__)

# ── Retailer checkout URL patterns ──────────────────────────────────
CHECKOUT_URLS = {
    "target":       "https://www.target.com/co-cart",
    "walmart":      "https://www.walmart.com/cart",
    "bestbuy":      "https://www.bestbuy.com/cart",
    "pokemoncenter":"https://www.pokemoncenter.com/cart",
}

# ── Add to Cart selectors per retailer ──────────────────────────────
ATC_SELECTORS = {
    "target": [
        "[data-test='add-to-cart-button']:not([disabled])",
        "button[aria-label*='Add to cart']:not([disabled])",
    ],
    "walmart": [
        "button[data-automation-id='add-to-cart-btn']:not([disabled])",
        "[class*='AddToCartButton']:not([disabled])",
        "button[class*='add-to-cart']:not([disabled])",
    ],
    "bestbuy": [
        ".add-to-cart-button:not([disabled])",
        "button.c-button-primary:not([disabled])[class*='add']",
        "button[data-button-state='ADD_TO_CART']",
    ],
    "pokemoncenter": [
        "button[class*='AddToCart']:not([disabled])",
        "button[data-testid*='add-to-cart']:not([disabled])",
        "button[class*='add-to-cart']:not([disabled])",
    ],
}

# ── Out of stock selectors - if found, don't bother clicking ────────
OOS_SELECTORS = {
    "target": [
        "[data-test='add-to-cart-button'][disabled]",
        "button[aria-label*='Unavailable']",
    ],
    "walmart": [
        "button[data-automation-id='add-to-cart-btn'][disabled]",
        "[class*='out-of-stock']",
    ],
    "bestbuy": [
        "button[data-button-state='SOLD_OUT']",
        "button[data-button-state='COMING_SOON']",
        ".btn-disabled",
    ],
    "pokemoncenter": [
        "button[class*='SoldOut']",
        "button[disabled][class*='AddToCart']",
    ],
}

# ── Checkout page selectors to confirm we landed correctly ──────────
CHECKOUT_INDICATORS = {
    "target":        ["[data-test='checkout-button']", "[data-test='order-summary']", ".checkout-summary"],
    "walmart":       ["[data-automation-id='checkout-btn']", ".checkout-summary", "[class*='CartTotal']"],
    "bestbuy":       [".cart-item", ".order-summary", "[class*='cartItem']"],
    "pokemoncenter": ["[class*='CartPage']", ".cart-summary", "[class*='checkout']"],
}


# ─────────────────────────────────────────────────────────────────────
# CORE BROWSER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────

async def open_and_stage_cart(product: dict, config: dict):
    """
    Open a visible browser, add the product to cart, navigate to checkout.
    Stops before any purchase button. You click Buy.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return {"success": False, "notes": ["Playwright not installed"]}

    retailer = product.get("retailer", "").lower().replace(" ", "")
    url = product.get("url", "")
    name = product.get("name", "Unknown")
    notes = []

    log.info(f"🛒 Opening browser for: {name}")
    os.makedirs(BROWSER_PROFILE, exist_ok=True)

    result = {"success": False, "retailer": retailer, "name": name, "notes": notes}

    async with async_playwright() as p:
        # Persistent context reuses your existing login/cookies
        context = await p.chromium.launch_persistent_context(
            BROWSER_PROFILE,
            headless=False,
            viewport=None,  # None = use the window size, fully resizable
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1400,900",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
        )

        page = await context.new_page()

        try:
            # ── Step 1: Navigate to product page ──
            log.info(f"  -> Navigating to product page")
            notes.append(f"Opening: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(3000)

            # ── Step 2: Check for out-of-stock signals ──
            for sel in OOS_SELECTORS.get(retailer, []):
                oos_el = await page.query_selector(sel)
                if oos_el:
                    notes.append(f"⚠️ Out-of-stock signal detected ({sel}) - product may have sold out")
                    log.warning(f"  OOS detected: {sel}")
                    break

            # ── Step 3: Find and click Add to Cart ──
            atc_clicked = False
            for sel in ATC_SELECTORS.get(retailer, []):
                try:
                    atc_el = await page.wait_for_selector(sel, timeout=5000)
                    if atc_el:
                        btn_text = await atc_el.inner_text()
                        log.info(f"  -> Found ATC button: '{btn_text.strip()}'")
                        notes.append(f"✅ Found Add to Cart: '{btn_text.strip()}'")

                        # Click it
                        await atc_el.click()
                        await page.wait_for_timeout(2500)
                        atc_clicked = True
                        notes.append("✅ Clicked Add to Cart")
                        log.info("  -> Clicked Add to Cart")
                        break
                except PWTimeout:
                    continue
                except Exception as e:
                    log.debug(f"  ATC selector {sel} failed: {e}")
                    continue

            if not atc_clicked:
                notes.append("⚠️ Could not find Add to Cart button - product may be out of stock or page structure changed")
                log.warning("  Could not click ATC - navigating to checkout anyway")

            # ── Step 4: Navigate to checkout page ──
            checkout_url = CHECKOUT_URLS.get(retailer, "")
            if checkout_url:
                log.info(f"  -> Navigating to checkout: {checkout_url}")
                await page.goto(checkout_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2500)
                notes.append(f"✅ Navigated to checkout page")

                # Confirm we're on the right page
                for sel in CHECKOUT_INDICATORS.get(retailer, []):
                    indicator = await page.query_selector(sel)
                    if indicator:
                        notes.append("✅ Checkout page confirmed - your item should be in cart")
                        log.info("  ✅ Checkout page confirmed")
                        result["success"] = True
                        break
                else:
                    notes.append("⚠️ On checkout page - verify your item is in the cart")
                    result["success"] = atc_clicked

            # ── Step 5: Show on-page alert to user (only after cart staged) ──
            if atc_clicked or result.get("success"):
                await page.evaluate("""
                    () => {
                        // Remove any existing banner first
                        const existing = document.getElementById('keithpokebsbanner');
                        if (existing) existing.remove();

                        const div = document.createElement('div');
                        div.id = 'keithpokebsbanner';
                        div.style.cssText = `
                            position: fixed; top: 0; left: 0; right: 0; z-index: 2147483647;
                            background: #1a1a2e; color: #f0c040; padding: 14px 24px;
                            font-family: monospace; font-size: 15px; font-weight: bold;
                            border-bottom: 3px solid #f0c040; text-align: center;
                            box-shadow: 0 4px 20px rgba(0,0,0,0.6);
                        `;
                        div.innerHTML = `
                            🃏 Keith's PokeBS - Cart is Ready!
                            &nbsp;&nbsp;
                            <span style="color:#3ddc84">
                                ✅ Item in cart - scroll down and click Checkout / Place Order to buy
                            </span>
                            &nbsp;&nbsp;
                            <span style="color:#ff8a80;font-size:13px">
                                (click anywhere on this bar to dismiss)
                            </span>
                        `;
                        div.onclick = () => div.remove();
                        document.body.prepend(div);
                    }
                """)

            log.info(f"  ✅ Browser staged for {name} - waiting for you to complete purchase")
            notes.append("🛒 READY - Click Place Order / Checkout to complete your purchase")

            # Keep browser open - don't close it, user needs to complete purchase
            # Wait indefinitely until user closes the browser
            log.info("  Browser is open and waiting. Complete your purchase, then close the browser.")
            await page.wait_for_event("close", timeout=0)  # Wait until page is closed

        except Exception as e:
            notes.append(f"❌ Error during cart staging: {e}")
            log.error(f"  Cart staging error: {e}")
            # Even on error, keep browser open so user can complete manually
            try:
                await page.wait_for_event("close", timeout=0)
            except Exception:
                pass

        try:
            await context.close()
        except Exception:
            pass

    return result


# ─────────────────────────────────────────────────────────────────────
# TRIGGER - called automatically by tracker.py on MSRP detection
# ─────────────────────────────────────────────────────────────────────

def trigger_cart_preload(product: dict, listed_price: float, msrp: float, config: dict):
    """
    Entry point called by tracker.py when a product is detected
    in stock at or below MSRP. Fires in a background thread so it
    doesn't block the tracker's check loop.
    """
    import threading

    retailer = product.get("retailer", "")
    name = product.get("name", "")
    savings = msrp - listed_price
    deal_type = "BELOW MSRP" if savings > 0.50 else "AT MSRP"

    log.info(f"🚨 Cart pre-loader triggered: {name} @ ${listed_price:.2f} ({deal_type})")

    def _run_in_thread():
        try:
            # Send ntfy heads-up that browser is opening
            ntfy_topic = config.get("ntfy_topic", "")
            if ntfy_topic and ntfy_topic != "tcg-restock-MY-SECRET-TOPIC-123":
                send_ntfy(
                    topic=ntfy_topic,
                    title=f"Cart Opening: {retailer.upper()}",
                    body=f"Cart opening NOW!\n{name}\n${listed_price:.2f} ({deal_type})\nBrowser staging cart - YOU click Buy!",
                    url=product.get("url", ""),
                    priority="urgent",
                    tags="shopping_cart,rotating_light",
                )
        except Exception as e:
            log.warning(f"ntfy trigger alert error: {e}")

        # Run the async browser function
        try:
            asyncio.run(open_and_stage_cart(product, config))
        except Exception as e:
            log.error(f"Cart preloader thread error: {e}")

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()
    log.info(f"  Cart preloader running in background thread")


# ─────────────────────────────────────────────────────────────────────
# INTEGRATION - patch into msrp_alert.py's check_msrp_prices
# ─────────────────────────────────────────────────────────────────────

def patch_msrp_alert(config: dict):
    """
    Monkey-patches msrp_alert.check_msrp_prices so that whenever
    an MSRP price is detected, the cart preloader fires automatically.
    Call this from tracker.py main() after importing both modules.
    """
    try:
        import msrp_alert
        original_send = msrp_alert.send_msrp_alert

        def patched_send(product, listed, msrp, deal_type, cfg):
            # Fire original ntfy alert
            original_send(product, listed, msrp, deal_type, cfg)
            # Also trigger cart preloader
            trigger_cart_preload(product, listed, msrp, cfg)

        msrp_alert.send_msrp_alert = patched_send
        log.info("Cart preloader: ✅ patched into MSRP alert pipeline")
    except ImportError:
        log.warning("msrp_alert.py not found - cart preloader won't auto-trigger")
    except Exception as e:
        log.warning(f"Cart preloader patch error: {e}")


# ─────────────────────────────────────────────────────────────────────
# COMMAND LINE - test mode
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    parser = argparse.ArgumentParser(description="Cart Pre-Loader")
    parser.add_argument("--test", action="store_true", help="Test with a specific URL")
    parser.add_argument("--setup", action="store_true", help="Setup mode - just open browser to log in")
    parser.add_argument("--retailer", default="target", help="Retailer (target/walmart/bestbuy/pokemoncenter)")
    parser.add_argument("--url", default="", help="Product URL to test with")
    args = parser.parse_args()

    print("=" * 60)
    print("CART PRE-LOADER")
    print("=" * 60)

    if args.setup:
        # Setup mode - just open the browser at the retailer homepage for login
        retailer_homes = {
            "target":        "https://www.target.com/login",
            "walmart":       "https://www.walmart.com/account/login",
            "bestbuy":       "https://www.bestbuy.com/identity/signin",
            "pokemoncenter": "https://www.pokemoncenter.com/account/login",
            "amazon":        "https://www.amazon.com/",
            "costco":        "https://www.costco.com/LogonForm",
        }
        url = retailer_homes.get(args.retailer, f"https://www.{args.retailer}.com")
        print(f"\nOpening {args.retailer} login page...")
        print("1. Log in with your account credentials")
        print("2. Confirm your payment method and address are saved")
        print("3. Close the browser when done - your session is saved automatically\n")

        async def setup_browser():
            from playwright.async_api import async_playwright
            os.makedirs(BROWSER_PROFILE, exist_ok=True)
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    BROWSER_PROFILE,
                    headless=False,
                    viewport=None,
                    args=["--start-maximized", "--window-size=1400,900",
                          "--disable-blink-features=AutomationControlled"],
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/147.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                print(f"Browser open at: {url}")
                print("Log in, then close the browser window when done.")
                # Wait until browser is closed
                try:
                    await page.wait_for_event("close", timeout=0)
                except Exception:
                    pass
                try:
                    await context.close()
                except Exception:
                    pass
            print(f"\n✅ {args.retailer} session saved to {BROWSER_PROFILE}")

        asyncio.run(setup_browser())

    elif args.test:
        url = args.url or input("Enter product URL to test: ").strip()
        if not url:
            print("No URL provided.")
            sys.exit(1)

        test_product = {
            "name": "Test Product",
            "retailer": args.retailer,
            "url": url,
        }

        try:
            sys.path.insert(0, OUTPUT_DIR)
            from tracker import CONFIG
            config = CONFIG
        except ImportError:
            config = {"ntfy_topic": ""}

        print(f"\nTesting cart preloader with: {url}")
        print(f"Retailer: {args.retailer}\n")
        asyncio.run(open_and_stage_cart(test_product, config))

    else:
        print()
        print("USAGE:")
        print()
        print("  Setup / log into a retailer account:")
        print("    python cart_preloader.py --setup --retailer target")
        print("    python cart_preloader.py --setup --retailer walmart")
        print("    python cart_preloader.py --setup --retailer bestbuy")
        print("    python cart_preloader.py --setup --retailer pokemoncenter")
        print()
        print("  Test with a specific product URL:")
        print("    python cart_preloader.py --test --retailer walmart --url https://www.walmart.com/ip/...")
        print()
        print("  Normal use - fires automatically from tracker.py:")
        print("    python tracker.py")
