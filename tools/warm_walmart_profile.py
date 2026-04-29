#!/usr/bin/env python3
"""
tools/warm_walmart_profile.py - Warm BROWSER_PROFILE with walmart.com session

ONE-OFF helper. Opens the same persistent Chromium profile that
bestbuy_invites, amazon_monitor, and costco_tracker plugins use, navigates
to walmart.com, and waits for you to interact before closing.

The intent: seed PerimeterX with behavioral signals (real cookies, real
session timing, real challenge-pass) so subsequent automated visits look
like a returning user, not a fresh bot. The _pxhd and pxvid cookies set
during a manual session are long-lived (months) and accepted as proof of
humanity for subsequent visits IF the rest of the request looks similar.

USAGE:
    py -3.14 tools\\warm_walmart_profile.py

After warmup, re-run the recon (no code change needed):
    py -3.14 tools\\recon_walmart_playwright.py

NOTES:
  - Opens a VISIBLE Chrome window. Browse like a human.
  - Uses the same BROWSER_PROFILE as production plugins. Won't disturb
    bestbuy/amazon/costco logins — only adds Walmart cookies alongside.
  - Press ENTER in this terminal when done browsing to close cleanly.
  - If you Ctrl+C, the browser closes but state may not flush; prefer ENTER.

WHAT TO DO IN THE BROWSER WINDOW:
  1. If PerimeterX challenges you (press & hold, captcha), solve it
  2. Browse 2-3 product pages (any products, doesn't have to be Pokemon)
  3. Accept the cookie banner if it appears
  4. Scroll, hover on items, click around for 30-60 seconds
  5. Come back to this terminal and press ENTER
"""

from __future__ import annotations

import os
import sys


# -- Self-contained config (matches recon_walmart_playwright.py) -------------

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tools" else _here

_appdata = os.environ.get("LOCALAPPDATA", "")
if _appdata:
    BROWSER_PROFILE = os.path.join(_appdata, "tcg_tracker", "browser_profile")
else:
    BROWSER_PROFILE = os.path.join(_root, ".browser_profile")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("FATAL: Playwright not installed.", file=sys.stderr)
        print("       py -3.14 -m pip install playwright", file=sys.stderr)
        print("       py -3.14 -m playwright install chromium", file=sys.stderr)
        return 1

    print(f"[warm] BROWSER_PROFILE = {BROWSER_PROFILE}")
    print(f"[warm] Opening visible Chromium pointed at the production profile...")
    print(f"[warm]")
    print(f"[warm] What to do in the browser window:")
    print(f"[warm]   1. If a PerimeterX challenge shows, solve it (press & hold)")
    print(f"[warm]   2. Browse 2-3 product pages -- any products, not just Pokemon")
    print(f"[warm]   3. Accept cookie banner if it appears")
    print(f"[warm]   4. Scroll, hover, click for 30-60 seconds")
    print(f"[warm]   5. Come back here and press ENTER")
    print(f"[warm]")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            BROWSER_PROFILE,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--start-maximized",
            ],
            user_agent=USER_AGENT,
            no_viewport=True,
        )
        page = context.new_page()
        try:
            page.goto("https://www.walmart.com", timeout=60000)
        except Exception as e:
            print(f"[warm] WARN: initial goto failed: {e}")
            print(f"[warm]       you can still navigate manually in the open window")

        try:
            input("[warm] Press ENTER when done browsing to close cleanly... ")
        except (KeyboardInterrupt, EOFError):
            print()  # newline after ^C

        print(f"[warm] Closing browser. Cookies preserved in profile.")
        try:
            context.close()
        except Exception:
            pass

    print(f"[warm] Done. Now re-run:")
    print(f"[warm]   py -3.14 tools\\recon_walmart_playwright.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
