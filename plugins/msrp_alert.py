#!/usr/bin/env python3
"""
msrp_alert.py - MSRP Price Alerting Plugin
Reads status_snapshot.json after every check cycle.
Fires ntfy alert when any tracked product is in stock at/below MSRP.
Standalone: python msrp_alert.py
"""

import json
import os
import logging
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
    OUTPUT_DIR, DATA_DIR, get_msrp, parse_price, price_vs_msrp,
    send_ntfy, DEAL_THRESHOLD_PCT, load_history, save_history
)

log = logging.getLogger(__name__)

SNAPSHOT_FILE  = os.path.join(DATA_DIR, "status_snapshot.json")
HISTORY_FILE   = "price_history.json"


def send_msrp_alert(product: dict, listed: float, msrp: float, deal_type: str, config: dict):
    """Send ntfy alert via shared.send_ntfy with direct URL click action."""
    pct     = round((listed / msrp) * 100)
    savings = round(msrp - listed, 2)
    topic   = config.get("ntfy_topic", "")
    url     = product.get("url", "")

    if deal_type == "at_msrp":
        title    = f"MSRP PRICE: {product['retailer']}"
        body     = f"{product['name']}\nListed: ${listed:.2f} = MSRP (${msrp:.2f})"
        priority = "high"
        tags     = "white_check_mark,moneybag"
    else:
        title    = f"BELOW MSRP: {product['retailer']}"
        body     = f"{product['name']}\n${listed:.2f} vs MSRP ${msrp:.2f} - Save ${savings:.2f} ({100-pct}% off)"
        priority = "urgent"
        tags     = "fire,moneybag,tada"

    if send_ntfy(topic=topic, title=title, body=body, url=url, priority=priority, tags=tags):
        log.info(f"MSRP alert sent: {product['name']} @ ${listed:.2f}")


def check_msrp_prices(config: dict):
    """Read snapshot, compare prices to MSRP, fire alerts on deals."""
    if not os.path.exists(SNAPSHOT_FILE):
        log.debug("No snapshot yet - skipping MSRP check")
        return

    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            products = json.load(f)
    except Exception as e:
        log.warning(f"Could not read snapshot: {e}")
        return

    history      = load_history(HISTORY_FILE)
    alerts_fired = 0

    for product in products:
        name      = product.get("name", "")
        price_str = product.get("price", "")
        url       = product.get("url", "")
        retailer  = product.get("retailer", "")
        in_stock  = product.get("in_stock", False)

        if not in_stock:
            continue

        comp = price_vs_msrp(name, price_str, retailer)

        # Hard sanity checks - suppress alert if:
        #   1. No valid price was parsed
        #   2. Price is $0.00 or negative (API error)
        #   3. Price is more than 3.5x MSRP (marketplace scalper, not Walmart/retailer direct)
        if not comp["listed"] or comp["listed"] <= 0:
            log.debug(f"[msrp_alert] Skipping {name} - invalid price: {price_str!r}")
            continue

        if comp["msrp"] and comp["listed"] > comp["msrp"] * 3.5:
            log.debug(
                f"[msrp_alert] Skipping {name} - price ${comp['listed']:.2f} is "
                f"{comp['listed']/comp['msrp']:.1f}x MSRP (likely marketplace scalper)"
            )
            continue

        if not comp["is_deal"]:
            if comp["listed"]:
                history[url] = {
                    **history.get(url, {}),
                    "last_price":   comp["listed"],
                    "last_checked": datetime.now().isoformat(),
                }
            continue

        # Suppress re-alerts for same price
        prev       = history.get(url, {})
        prev_price = prev.get("last_price")
        alerted_at = prev.get("alerted_at")
        if prev_price is not None and abs(prev_price - comp["listed"]) < 0.50 and alerted_at:
            log.debug(f"Already alerted {name} @ ${comp['listed']:.2f} - skipping")
            history[url] = {**prev, "last_price": comp["listed"], "last_checked": datetime.now().isoformat()}
            continue

        deal_type = "below_msrp" if comp["status"] == "below" else "at_msrp"
        log.info(f"[msrp_alert] {deal_type.upper()}: {name} @ ${comp['listed']:.2f} (MSRP ${comp['msrp']:.2f})")

        send_msrp_alert(
            {"name": name, "retailer": retailer, "url": url},
            comp["listed"], comp["msrp"], deal_type, config
        )

        history[url] = {
            "last_price":   comp["listed"],
            "last_checked": datetime.now().isoformat(),
            "alerted_at":   datetime.now().isoformat(),
            "msrp":         comp["msrp"],
            "deal_type":    deal_type,
        }
        alerts_fired += 1

    save_history(HISTORY_FILE, history)
    if alerts_fired:
        log.info(f"[msrp_alert] {alerts_fired} price alert(s) fired")
    else:
        log.debug("[msrp_alert] No new price alerts this cycle")


def get_price_summary() -> list:
    """Return price vs MSRP summary for all tracked products. Used by dashboard."""
    if not os.path.exists(SNAPSHOT_FILE):
        return []
    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            products = json.load(f)
    except Exception:
        return []

    summary = []
    for p in products:
        comp = price_vs_msrp(p.get("name", ""), p.get("price", ""), p.get("retailer", ""))
        if comp["msrp"] and comp["listed"]:
            summary.append({
                "name":        p["name"],
                "retailer":    p["retailer"],
                "listed":      comp["listed"],
                "msrp":        comp["msrp"],
                "pct_of_msrp": comp["pct_of_msrp"],
                "status":      comp["status"],
                "in_stock":    p.get("in_stock", False),
            })
    return sorted(summary, key=lambda x: x["pct_of_msrp"])


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )
    try:
        import sys
        sys.path.insert(0, OUTPUT_DIR)
        from tracker import CONFIG
        check_msrp_prices(CONFIG)
        summary = get_price_summary()
        if summary:
            print(f"\n{'Product':<45} {'Retailer':<15} {'Listed':>8} {'MSRP':>8} {'%':>5}  Status")
            print("-" * 95)
            for s in summary:
                icon = "FIRE" if s["status"] == "below" else "OK" if s["status"] == "at" else "HIGH"
                print(
                    f"{s['name'][:44]:<45} {s['retailer']:<15} "
                    f"${s['listed']:>7.2f} ${s['msrp']:>7.2f} {s['pct_of_msrp']:>4}%  "
                    f"[{icon}] {'IN STOCK' if s['in_stock'] else 'OOS'}"
                )
    except ImportError:
        log.error("Run from the tcg_tracker directory alongside tracker.py")
