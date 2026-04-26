#!/usr/bin/env python3
"""
shared.py - Shared Utilities for Keith's PokeBS Tracker

Single source of truth for:
  - MSRP price table
  - HTTP headers
  - ntfy alert sender (with URL click action)
  - Price parsing helpers
  - Browser opener
  - Output directory reference

All modules import from here. Never define these things twice.

Adding a new product type? Add it to MSRP_TABLE.
Changing your headers? Change them here once.
"""

import os
import re
import logging
import requests as _requests

log = logging.getLogger(__name__)

# ── Directory ────────────────────────────────────────────────────────────────
# ROOT_DIR is always the tcg_tracker/ folder (where tracker.py lives).
# We find it by walking up from shared.py's location until we find tracker.py,
# falling back to __file__'s directory if not found.
def _find_root() -> str:
    """Find the tcg_tracker/ root by locating tracker.py."""
    candidate = os.path.dirname(os.path.abspath(__file__))
    # Walk up max 2 levels (handles root/ and plugins/ locations)
    for _ in range(3):
        if os.path.exists(os.path.join(candidate, "tracker.py")):
            return candidate
        candidate = os.path.dirname(candidate)
    # Fallback - use __file__'s directory
    return os.path.dirname(os.path.abspath(__file__))

ROOT_DIR        = _find_root()
OUTPUT_DIR      = ROOT_DIR   # legacy alias
DATA_DIR        = os.path.join(ROOT_DIR, "data")


def _appdata_dir():
    """
    Return the per-user app-data directory for tcg_tracker.

    Windows:  %LOCALAPPDATA%\\tcg_tracker
    macOS:    ~/Library/Application Support/tcg_tracker
    Linux:    ~/.config/tcg_tracker  (or $XDG_CONFIG_HOME/tcg_tracker)

    This directory is OUTSIDE any cloud-sync path on Windows
    (OneDrive Known Folder Move excludes %LOCALAPPDATA%).
    """
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata:
        return os.path.join(appdata, "tcg_tracker")

    if os.path.isdir(os.path.expanduser("~/Library/Application Support")):
        return os.path.expanduser("~/Library/Application Support/tcg_tracker")

    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(xdg, "tcg_tracker")


APPDATA_DIR     = _appdata_dir()
CONFIG_PATH     = os.path.join(APPDATA_DIR, "config.json")
BROWSER_PROFILE = os.path.join(APPDATA_DIR, "browser_profile")

# Ensure data/ exists on first import
os.makedirs(DATA_DIR, exist_ok=True)
log.debug(f"[shared] ROOT_DIR={ROOT_DIR} | DATA_DIR={DATA_DIR} | APPDATA_DIR={APPDATA_DIR}")


# ── HTTP Headers ─────────────────────────────────────────────────────────────
# Used by all scrapers. One place to update if retailers change detection.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

HEADERS_JSON = {**HEADERS, "Accept": "application/json"}


# -- Local config (lives in AppData, not in the repo) ---------------------

# Cached config - loaded once per process
_local_config_cache = None

# Required keys - missing any of these raises a clear error
REQUIRED_CONFIG_KEYS = ("ntfy_topic", "home_zip", "home_city")

# Default values for optional keys
CONFIG_DEFAULTS = {
    "notify_push":              True,
    "check_interval_minutes":   3,
    "log_file":                 "tcg_tracker.log",
    "_schema_version":          1,
}


class ConfigError(RuntimeError):
    """Raised when local config is missing, malformed, or incomplete."""
    pass


def load_local_config(force_reload=False):
    """
    Load the local config from CONFIG_PATH.  Cached after first read.
    Raises ConfigError with a clear, actionable message on failure.
    Returns a fresh dict copy on each call.
    """
    global _local_config_cache

    if _local_config_cache is not None and not force_reload:
        return dict(_local_config_cache)

    import json

    if not os.path.exists(CONFIG_PATH):
        raise ConfigError(
            "Local config not found at: " + CONFIG_PATH + "\n\n"
            "Run the setup script to create it:\n"
            "    python tools/setup_config.py\n"
        )

    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(
            "Local config is malformed JSON: " + CONFIG_PATH + "\n"
            "  Error: " + str(e) + "\n"
            "  Fix the file or re-run: python tools/setup_config.py"
        )
    except Exception as e:
        raise ConfigError("Could not read " + CONFIG_PATH + ": " + str(e))

    missing = [k for k in REQUIRED_CONFIG_KEYS if not cfg.get(k)]
    if missing:
        raise ConfigError(
            "Local config missing required keys: " + str(missing) + "\n"
            "  File: " + CONFIG_PATH + "\n"
            "  Re-run: python tools/setup_config.py"
        )

    for key, default in CONFIG_DEFAULTS.items():
        cfg.setdefault(key, default)

    _local_config_cache = cfg
    log.debug("[shared] Loaded local config from " + CONFIG_PATH)
    return dict(cfg)


def get_ntfy_topic():
    """Convenience accessor - returns the ntfy topic from local config."""
    return load_local_config()["ntfy_topic"]


# ── MSRP Table ───────────────────────────────────────────────────────────────
# Single authoritative source for all MSRP values.
# Order matters - most specific entries must come first.
# Used by: msrp_alert.py, walmart_queue.py, store_inventory.py, dashboard.html
MSRP_TABLE = [
    ("pokemon center elite trainer box", 59.99),
    ("pc elite trainer box",             59.99),
    ("elite trainer box",                49.99),
    ("etb",                              49.99),
    ("display box 36pk",                159.99),
    ("display box",                     159.99),
    ("booster bundle 6pk",               26.99),
    ("booster bundle",                   26.99),
    ("booster box",                     159.99),
    ("3-pack blister",                   14.99),
    ("3 pack blister",                   14.99),
    ("sleeved booster pack",              5.49),
    ("sleeved booster",                   5.49),
    ("build & battle",                   14.99),
    ("collection box",                   29.99),
    ("mini tin",                          9.99),
    ("tin",                              19.99),
    ("premium poster collection",        49.99),
]

# Deal threshold - alert when price is at or below this % of MSRP
DEAL_THRESHOLD_PCT = 0.95


# ── MSRP Helpers ─────────────────────────────────────────────────────────────

def get_msrp(name: str, retailer: str = "") -> float | None:
    """
    Return the MSRP for a product based on its name.
    Retailer-aware: Pokemon Center ETBs are always $59.99.
    Returns None if no match found.
    """
    lower = name.lower()
    ret = retailer.lower().replace(" ", "")

    # Retailer override - PC ETBs cost more than standard ETBs
    if ret == "pokemoncenter" and ("elite trainer box" in lower or "etb" in lower):
        return 59.99

    for key, price in MSRP_TABLE:
        if key in lower:
            return price
    return None


def parse_price(price_str) -> float | None:
    """
    Parse a price string like '$49.99', '49.99', '$49', '49,99' into a float.
    Returns None if unparseable or empty.
    """
    if not price_str or str(price_str).strip() in ("N/A", "-", "", "null"):
        return None
    clean = re.sub(r"[,$\s]", "", str(price_str))
    try:
        val = float(clean)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def price_vs_msrp(name: str, price_str, retailer: str = "") -> dict:
    """
    Returns a dict describing how a listed price compares to MSRP:
    {
        'msrp': float | None,
        'listed': float | None,
        'status': 'below' | 'at' | 'above' | 'unknown',
        'savings': float,
        'pct_of_msrp': int,
        'is_deal': bool,
    }
    """
    msrp = get_msrp(name, retailer)
    listed = parse_price(price_str)

    if not msrp or not listed:
        return {
            "msrp": msrp, "listed": listed,
            "status": "unknown", "savings": 0.0,
            "pct_of_msrp": 0, "is_deal": False,
        }

    savings = round(msrp - listed, 2)
    pct = round((listed / msrp) * 100)

    if listed < msrp * DEAL_THRESHOLD_PCT:
        status = "below"
    elif listed <= msrp:
        status = "at"
    else:
        status = "above"

    return {
        "msrp": msrp,
        "listed": listed,
        "status": status,
        "savings": max(savings, 0.0),
        "pct_of_msrp": pct,
        "is_deal": status in ("below", "at"),
    }


# ── ntfy Sender ───────────────────────────────────────────────────────────────

def send_ntfy(
    topic: str,
    title: str,
    body: str,
    url: str = "",
    priority: str = "high",
    tags: str = "tada",
) -> bool:
    """
    Send a push notification via ntfy.sh.

    Always includes:
      - Click action: tapping the notification opens `url` directly on the phone
      - "Open" action button visible on the notification
      - UTF-8 encoding on body, ASCII-safe encoding on headers

    Returns True on success, False on failure.
    """
    if not topic or topic == "tcg-restock-MY-SECRET-TOPIC-123":
        log.warning("[ntfy] Topic not configured - notification not sent")
        return False

    def _ascii_safe(text: str, max_len: int = 255) -> str:
        """
        Make header value safe for HTTP transmission.
        Replaces common Unicode punctuation with ASCII equivalents,
        then strips any remaining non-ASCII characters.
        """
        replacements = {
            "\u2014": "-",   # em dash
            "\u2013": "-",   # en dash
            "\u2018": "'",   # left single quote
            "\u2019": "'",   # right single quote
            "\u201c": '"',   # left double quote
            "\u201d": '"',   # right double quote
            "\u2026": "...", # ellipsis
            "\u00e9": "e",   # e
            "\u00e8": "e",   # e
            "\u00f3": "o",   # ó
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        # Strip any remaining non-ASCII
        text = text.encode("ascii", errors="ignore").decode("ascii")
        return text[:max_len]

    headers = {
        "Title":        _ascii_safe(title),
        "Priority":     priority,
        "Tags":         tags,
        "Content-Type": "text/plain; charset=utf-8",
    }

    if url:
        headers["Click"]   = url
        headers["Actions"] = f"view, Open, {url}"

    full_body = body.strip()
    if url:
        full_body += f"\n\n{url}"

    try:
        r = _requests.post(
            f"https://ntfy.sh/{topic}",
            data=full_body.encode("utf-8"),
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            log.debug(f"[ntfy] Sent: {title[:60]}")
            return True
        else:
            log.warning(f"[ntfy] Unexpected status {r.status_code}: {r.text[:100]}")
            return False
    except _requests.exceptions.Timeout:
        log.warning("[ntfy] Request timed out")
        return False
    except Exception as e:
        log.warning(f"[ntfy] Send error: {e}")
        return False


# ── Browser Opener ────────────────────────────────────────────────────────────

def open_browser(url: str, banner_title: str = "", banner_msg: str = "") -> None:
    """
    Open a visible browser window to a URL using the persistent cart preloader
    profile (so you're already logged in).

    banner_title / banner_msg: optional text for the dismissable overlay banner.
    Runs in a background thread - does not block the caller.

    SECURITY: Never interacts with payment fields or purchase buttons.
    """
    import threading

    def _run():
        try:
            from playwright.sync_api import sync_playwright
            os.makedirs(BROWSER_PROFILE, exist_ok=True)

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

                # Block images/fonts during navigation - faster load
                def _block(route):
                    if route.request.resource_type in ("image", "media", "font"):
                        route.abort()
                    else:
                        route.continue_()
                page.route("**/*", _block)

                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1200)

                # Inject dismissable banner if text provided
                if banner_title or banner_msg:
                    safe_title = (banner_title or "PokeBS Alert").replace("`", "'")
                    safe_msg = (banner_msg or "").replace("`", "'")
                    page.evaluate(f"""
                        () => {{
                            const ex = document.getElementById('_pokebsbanner');
                            if (ex) ex.remove();
                            const d = document.createElement('div');
                            d.id = '_pokebsbanner';
                            d.style.cssText = `
                                position:fixed;top:0;left:0;right:0;z-index:2147483647;
                                background:#1a1a2e;color:#f0c040;padding:12px 20px;
                                font-family:monospace;font-size:14px;font-weight:bold;
                                border-bottom:3px solid #f0c040;display:flex;
                                align-items:center;justify-content:space-between;
                                box-shadow:0 4px 16px rgba(0,0,0,.6);
                            `;
                            d.innerHTML = `
                                <span>
                                    PokeBS: <span style="color:#3ddc84">{safe_title}</span>
                                    &nbsp; <span style="font-weight:normal;color:#9090a8">{safe_msg}</span>
                                </span>
                                <span style="color:#555570;font-size:12px;cursor:pointer"
                                      onclick="this.parentNode.remove()">dismiss</span>
                            `;
                            document.body.prepend(d);
                        }}
                    """)

                log.info(f"[browser] Opened: {url[:60]}")
                # Wait until user closes the tab/window
                try:
                    page.wait_for_event("close", timeout=0)
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass

        except ImportError:
            log.warning("[browser] Playwright not installed - run: pip install playwright && playwright install chromium")
        except Exception as e:
            log.warning(f"[browser] Open error: {e}")

    thread = threading.Thread(target=_run, daemon=True, name=f"browser_{url[:30]}")
    thread.start()


# ── File I/O Helpers ──────────────────────────────────────────────────────────

def load_json(filename: str, default=None):
    """Load a JSON file from DATA_DIR. Returns default if missing or corrupt."""
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return default
    except Exception as e:
        log.warning(f"[shared] Could not load {filename}: {e}")
        return default


def save_json(filename: str, data) -> bool:
    """Save data as JSON to DATA_DIR. Returns True on success. Logs path on failure."""
    path = os.path.join(DATA_DIR, filename)
    try:
        import json
        os.makedirs(DATA_DIR, exist_ok=True)  # Ensure dir exists every time
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"[shared] FAILED to save {filename} -> {path}: {e}")
        return False


def load_history(filename: str) -> dict:
    """Load a JSON history dict from DATA_DIR, returning {} if missing."""
    import json
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_history(filename: str, data: dict) -> None:
    """Save a history dict to JSON in DATA_DIR."""
    import json
    path = os.path.join(DATA_DIR, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"[shared] Could not save history {filename}: {e}")


# ── Self-test / Diagnostic ────────────────────────────────────────────────────

def run_diagnostics(ntfy_topic: str = "") -> None:
    """
    Run a full self-test of shared.py utilities.
    Usage:
        python shared.py                     # run tests, skip ntfy
        python shared.py your-ntfy-topic     # run tests + send test notification
    Can also be imported:
        from shared import run_diagnostics; run_diagnostics()
    """
    import sys
    failures = []

    print("\n" + "=" * 55)
    print("  shared.py Diagnostic")
    print("=" * 55)

    # ── 1. MSRP table coverage ──
    print("\n[1] MSRP Table Coverage")
    msrp_cases = [
        ("Pokemon Chaos Rising ETB",                           "",              49.99),
        ("Pokemon Chaos Rising PC Elite Trainer Box",          "pokemoncenter", 59.99),
        ("Pokemon Chaos Rising Booster Bundle",                "",              26.99),
        ("Pokemon Chaos Rising 3-Pack Blister",                "",              14.99),
        ("Pokemon Chaos Rising Sleeved Booster Pack",          "",               5.49),
        ("Ascended Heroes Mini Tin",                           "",               9.99),
        ("Unknown Product XYZ",                                "",              None),
    ]
    for name, retailer, expected in msrp_cases:
        got = get_msrp(name, retailer)
        ok = got == expected
        if not ok:
            failures.append(f"get_msrp('{name}') expected {expected}, got {got}")
        print(f"  {'PASS' if ok else 'FAIL'} | {name[:44]:<44} | ${got}")

    # ── 2. parse_price edge cases ──
    print("\n[2] parse_price Edge Cases")
    price_cases = [
        ("$49.99", 49.99), ("49.99", 49.99), ("$49", 49.0),
        ("N/A", None), ("", None), ("$1,299.99", 1299.99), (None, None),
    ]
    for raw, expected in price_cases:
        got = parse_price(raw)
        ok = got == expected
        if not ok:
            failures.append(f"parse_price({repr(raw)}) expected {expected}, got {got}")
        print(f"  {'PASS' if ok else 'FAIL'} | parse_price({repr(raw):<15}) = {got}")

    # ── 3. price_vs_msrp logic ──
    print("\n[3] price_vs_msrp Logic")
    pvm_cases = [
        ("Pokemon Chaos Rising ETB", "$49.99", "",             "at",      True),
        ("Pokemon Chaos Rising ETB", "$39.99", "",             "below",   True),
        ("Pokemon Chaos Rising ETB", "$59.99", "",             "above",   False),
        ("Pokemon Chaos Rising ETB", "N/A",    "",             "unknown", False),
        ("Pokemon Chaos Rising PC Elite Trainer Box", "$59.99", "pokemoncenter", "at", True),
    ]
    for name, price, retailer, exp_status, exp_deal in pvm_cases:
        r = price_vs_msrp(name, price, retailer)
        ok = r["status"] == exp_status and r["is_deal"] == exp_deal
        if not ok:
            failures.append(f"price_vs_msrp '{name}' @ {price}: expected status={exp_status}/deal={exp_deal}, got {r['status']}/{r['is_deal']}")
        print(f"  {'PASS' if ok else 'FAIL'} | {name[:35]:<35} @ {price:<8} -> status={r['status']} is_deal={r['is_deal']}")

    # ── 4. File I/O round-trip ──
    print("\n[4] File I/O Round-Trip")
    try:
        import os as _os
        test_data = {"diagnostic": True, "value": 42, "nested": {"ok": True}}
        save_json("_diag_test.json", test_data)
        loaded = load_history("_diag_test.json")
        assert loaded == test_data, f"Data mismatch: {loaded}"
        _os.remove(_os.path.join(DATA_DIR, "_diag_test.json"))
        print("  PASS | save_json + load_history round-trip successful")
    except Exception as e:
        failures.append(f"File I/O: {e}")
        print(f"  FAIL | File I/O: {e}")

    # ── 5. ntfy (optional) ──
    print("\n[5] ntfy Connectivity")
    if ntfy_topic:
        ok = send_ntfy(
            topic=ntfy_topic,
            title="shared.py Diagnostic PASS",
            body="All shared.py utilities passed self-test.",
            url="",
            priority="low",
            tags="white_check_mark",
        )
        if not ok:
            failures.append("ntfy send failed")
        print(f"  {'PASS' if ok else 'FAIL'} | ntfy notification to '{ntfy_topic[:4]}****'")
    else:
        print("  SKIP | No topic given - run: python shared.py YOUR_NTFY_TOPIC")

    # ── Summary ──
    print("\n" + "=" * 55)
    if failures:
        print(f"  RESULT: {len(failures)} FAILURE(S):")
        for f in failures:
            print(f"    x {f}")
        sys.exit(1)
    else:
        print("  RESULT: ALL TESTS PASSED")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    topic = sys.argv[1] if len(sys.argv) > 1 else ""
    run_diagnostics(ntfy_topic=topic)
