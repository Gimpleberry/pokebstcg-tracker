#!/usr/bin/env python3
"""
Store Inventory Checker (#7)
Checks Target and Walmart store-level inventory for all tracked products
near your zip code, then sends a push notification listing which stores
have product in stock and what aisle to find it in.

Run manually:   python store_inventory.py
Scheduled:      runs automatically via tracker.py once per day at 8 AM

Config - edit the section below:
"""

import requests
import json
import os
import re
import math
import logging
from datetime import datetime

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# YOUR LOCATION - edit this
# ─────────────────────────────────────────────
YOUR_ZIP = "07060"          # Your zip code - also update in tracker.py CONFIG if desired
MAX_STORES = 5              # How many nearest stores to check
MAX_DISTANCE_MILES = 25     # Only check stores within this radius
NTFY_TOPIC = ""             # Filled from CONFIG automatically when run via tracker.py

# NOTE: The .browser_profile/ folder contains your retailer login cookies.
# Make sure it is NOT shared, uploaded to GitHub, or accessible to others.
# Add it to .gitignore if you ever version-control this folder.


# ── Path resolution - works from root or plugins/ folder ─────────────────────
import sys as _sys, os as _os
_here = _os.path.dirname(_os.path.abspath(__file__))
_root = _os.path.dirname(_here) if _os.path.basename(_here) == "plugins" else _here
if _root not in _sys.path:
    _sys.path.insert(0, _root)
if _here not in _sys.path:
    _sys.path.insert(0, _here)
# ─────────────────────────────────────────────────────────────────────────────
from shared import OUTPUT_DIR, HEADERS, HEADERS_JSON, get_msrp, parse_price, send_ntfy, load_history, save_history, open_browser


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def zip_to_coords(zip_code: str) -> tuple[float, float] | None:
    """Convert zip code to lat/lon using the free Census geocoder."""
    try:
        r = requests.get(
            f"https://api.zippopotam.us/us/{zip_code}",
            timeout=10
        )
        data = r.json()
        place = data["places"][0]
        return float(place["latitude"]), float(place["longitude"])
    except Exception as e:
        log.warning(f"Could not geocode zip {zip_code}: {e}")
        return None


def haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance in miles between two lat/lon points."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def send_store_alert(findings: list, ntfy_topic: str):
    """Send push notification with in-store availability summary."""
    if not ntfy_topic or ntfy_topic == "tcg-restock-MY-SECRET-TOPIC-123":
        log.warning("ntfy topic not configured - store alert not sent")
        return
    if not findings:
        return

    lines = [f"🏪 In-Store Stock Found - {datetime.now().strftime('%a %b %d')}"]
    for f in findings[:8]:  # Cap at 8 to keep notification readable
        aisle = f" (Aisle {f['aisle']})" if f.get("aisle") else ""
        qty = f" - {f['qty']} units" if f.get("qty") else ""
        lines.append(f"• {f['store_name']} ({f['distance']:.1f}mi){aisle}{qty}")
        lines.append(f"  {f['product']}")

    body = "\n".join(lines)
    success = send_ntfy(
        topic=ntfy_topic,
        title="In-Store Pokemon TCG Stock",
        body=body,
        url="https://www.walmart.com/search?q=pokemon+trading+card",
        priority="high",
        tags="department_store,rotating_light",
    )
    if success:
        log.info(f"Store alert sent: {len(findings)} in-store finds")


# ─────────────────────────────────────────────
# TARGET STORE INVENTORY
# ─────────────────────────────────────────────

def get_target_stores(lat: float, lon: float) -> list[dict]:
    """Find nearest Target stores using Target's store locator."""
    try:
        # Target's store locator endpoint
        url = (
            f"https://redsky.target.com/v3/stores/nearby/{lat},{lon}"
            f"?key=9f36aeafbe60771e321a7cc95a78140772ab3e96"
            f"&limit={MAX_STORES}&within={MAX_DISTANCE_MILES}&unit=mile"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        stores = []
        for store in data.get("locations", [])[:MAX_STORES]:
            stores.append({
                "id": store.get("location_id"),
                "name": f"Target - {store.get('city', '')}, {store.get('state', '')}",
                "address": store.get("address", {}).get("formatted_address", ""),
                "lat": store.get("geo", {}).get("latitude", 0),
                "lon": store.get("geo", {}).get("longitude", 0),
            })
        log.info(f"Found {len(stores)} Target stores near {lat},{lon}")
        return stores
    except Exception as e:
        log.warning(f"Target store locator error: {e}")
        return []


def check_target_store_inventory(sku: str, store_id: str) -> dict:
    """
    Check in-store inventory for a specific Target SKU at a specific store.
    Returns availability status, quantity, and aisle location.
    """
    try:
        url = (
            f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
            f"?key=9f36aeafbe60771e321a7cc95a78140772ab3e96"
            f"&tcin={sku}&store_id={store_id}"
            f"&store_positions_store_id={store_id}"
            f"&pricing_store_id={store_id}&is_bot=false"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        product = data.get("data", {}).get("product", {})
        fulfillment = product.get("fulfillment", {})

        # Store pickup availability
        store_options = fulfillment.get("store_options", [])
        for store in store_options:
            if str(store.get("location_id")) == str(store_id):
                in_store = store.get("in_store_only", {})
                pickup = store.get("order_pickup", {})
                status = in_store.get("availability_status") or pickup.get("availability_status", "")
                qty = store.get("location_available_to_promise_quantity", 0)

                # Aisle location
                positions = product.get("store_positions", [])
                aisle = None
                for pos in positions:
                    if str(pos.get("store_id")) == str(store_id):
                        aisle = f"{pos.get('aisle', '')}{pos.get('block', '')}"
                        break

                return {
                    "in_stock": status == "IN_STOCK",
                    "qty": qty,
                    "aisle": aisle,
                    "status": status,
                }

        return {"in_stock": False, "qty": 0, "aisle": None, "status": "UNKNOWN"}
    except Exception as e:
        log.debug(f"Target store inventory check error (store {store_id}, sku {sku}): {e}")
        return {"in_stock": False, "qty": 0, "aisle": None, "status": "ERROR"}


def scan_target_stores(products: list, lat: float, lon: float) -> list:
    """Scan all nearby Target stores for all tracked products."""
    stores = get_target_stores(lat, lon)
    if not stores:
        return []

    findings = []
    target_products = [p for p in products if p.get("retailer", "").lower() == "target" and p.get("sku")]

    if not target_products:
        log.info("No Target products with SKUs to check in-store")
        return []

    for store in stores:
        dist = haversine(lat, lon, store["lat"], store["lon"])
        if dist > MAX_DISTANCE_MILES:
            continue

        for product in target_products:
            result = check_target_store_inventory(product["sku"], store["id"])
            if result["in_stock"]:
                findings.append({
                    "retailer": "Target",
                    "store_name": store["name"],
                    "address": store["address"],
                    "distance": dist,
                    "product": product["name"],
                    "qty": result["qty"],
                    "aisle": result["aisle"],
                    "url": product["url"],
                })
                log.info(
                    f"✅ IN STORE: {product['name']} at {store['name']} "
                    f"({dist:.1f}mi) - Qty: {result['qty']}, Aisle: {result['aisle']}"
                )

        import time
        time.sleep(1)  # Be polite between store checks

    return findings


# ─────────────────────────────────────────────
# WALMART STORE INVENTORY
# ─────────────────────────────────────────────

def get_walmart_stores(zip_code: str) -> list[dict]:
    """Find nearest Walmart stores using Walmart's store locator."""
    try:
        url = f"https://www.walmart.com/store/finder/view?zip={zip_code}&distance=25"
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json, text/javascript"}, timeout=10)

        # Try JSON response
        try:
            data = r.json()
            stores_raw = data.get("payload", {}).get("storesData", {}).get("stores", [])
        except Exception:
            # Fallback: search for store data in HTML
            stores_raw = []
            matches = re.findall(r'"storeId"\s*:\s*"?(\d+)"?\s*,\s*"(?:city|name)"\s*:\s*"([^"]+)"', r.text)
            for store_id, city in matches[:MAX_STORES]:
                stores_raw.append({"storeId": store_id, "displayName": f"Walmart - {city}"})

        stores = []
        for s in stores_raw[:MAX_STORES]:
            stores.append({
                "id": str(s.get("storeId") or s.get("id", "")),
                "name": f"Walmart - {s.get('city', s.get('displayName', 'Store'))}",
                "address": s.get("address", {}).get("address", "") if isinstance(s.get("address"), dict) else "",
                "lat": float(s.get("geoPoint", {}).get("latitude", 0) or 0),
                "lon": float(s.get("geoPoint", {}).get("longitude", 0) or 0),
            })
        log.info(f"Found {len(stores)} Walmart stores near zip {zip_code}")
        return stores
    except Exception as e:
        log.warning(f"Walmart store locator error: {e}")
        return []


def check_walmart_store_inventory(item_id: str, store_id: str) -> dict:
    """Check in-store inventory for a Walmart item at a specific store."""
    try:
        url = (
            f"https://www.walmart.com/store/{store_id}/product/{item_id}/sellers"
        )
        r = requests.get(url, headers=HEADERS, timeout=10)

        # Look for in-store availability in the response
        text = r.text
        in_store_patterns = [
            r'"inStoreAvailability"\s*:\s*"?([^",}]+)"?',
            r'"availabilityStatus"\s*:\s*"([A-Z_]+)"',
            r'in.store.pick.up.*?([Aa]vailable|[Uu]navailable)',
        ]

        status = "UNKNOWN"
        for pat in in_store_patterns:
            match = re.search(pat, text)
            if match:
                status = match.group(1).upper()
                break

        in_stock = status in ("IN_STOCK", "AVAILABLE", "TRUE")
        return {"in_stock": in_stock, "qty": None, "aisle": None, "status": status}
    except Exception as e:
        log.debug(f"Walmart store inventory error (store {store_id}, item {item_id}): {e}")
        return {"in_stock": False, "qty": None, "aisle": None, "status": "ERROR"}


def scan_walmart_stores(products: list, zip_code: str, lat: float, lon: float) -> list:
    """Scan all nearby Walmart stores for all tracked products."""
    stores = get_walmart_stores(zip_code)
    if not stores:
        return []

    findings = []
    walmart_products = [p for p in products if p.get("retailer", "").lower() == "walmart" and p.get("item_id")]

    if not walmart_products:
        log.info("No Walmart products with item IDs to check in-store")
        return []

    for store in stores:
        if store["lat"] and store["lon"]:
            dist = haversine(lat, lon, store["lat"], store["lon"])
        else:
            dist = 0  # Unknown distance - include anyway

        for product in walmart_products:
            result = check_walmart_store_inventory(product["item_id"], store["id"])
            if result["in_stock"]:
                findings.append({
                    "retailer": "Walmart",
                    "store_name": store["name"],
                    "address": store["address"],
                    "distance": dist,
                    "product": product["name"],
                    "qty": result["qty"],
                    "aisle": result["aisle"],
                    "url": product["url"],
                })
                log.info(
                    f"✅ IN STORE: {product['name']} at {store['name']} ({dist:.1f}mi)"
                )

        import time
        time.sleep(1)

    return findings


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_store_check(products: list, config: dict, zip_code: str = None):
    """
    Run store inventory check for all products near the given zip code.
    Called daily from tracker.py.
    """
    zip_code = zip_code or YOUR_ZIP
    ntfy_topic = config.get("ntfy_topic", NTFY_TOPIC)

    log.info(f"Starting in-store inventory check for zip {zip_code}...")

    coords = zip_to_coords(zip_code)
    if not coords:
        log.warning(f"Could not get coordinates for zip {zip_code}")
        return

    lat, lon = coords
    log.info(f"Scanning stores within {MAX_DISTANCE_MILES} miles of {zip_code} ({lat:.4f}, {lon:.4f})")

    all_findings = []

    # Target
    target_findings = scan_target_stores(products, lat, lon)
    all_findings.extend(target_findings)

    # Walmart
    walmart_findings = scan_walmart_stores(products, zip_code, lat, lon)
    all_findings.extend(walmart_findings)

    # Sort by distance
    all_findings.sort(key=lambda x: x.get("distance", 999))

    # Save findings to JSON for dashboard
    output = {
        "last_updated": datetime.now().isoformat(),
        "zip_code": zip_code,
        "findings": all_findings,
    }
    with open(os.path.join(OUTPUT_DIR, "store_inventory.json"), "w") as f:
        json.dump(output, f, indent=2)

    if all_findings:
        log.info(f"Found {len(all_findings)} in-store products! Sending alert...")
        send_store_alert(all_findings, ntfy_topic)
    else:
        log.info("No in-store stock found near your location today")

    return all_findings


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    # Load products and config from tracker.py
    try:
        import sys
        sys.path.insert(0, OUTPUT_DIR)
        from tracker import PRODUCTS, CONFIG

        zip_input = input(f"Enter your zip code [{YOUR_ZIP}]: ").strip() or YOUR_ZIP
        findings = run_store_check(PRODUCTS, CONFIG, zip_input)

        if findings:
            print(f"\n🏪 Found {len(findings)} in-store products:\n")
            for f in findings:
                print(
                    f"  {'✅'} {f['store_name']} ({f['distance']:.1f} mi)\n"
                    f"     {f['product']}\n"
                    f"     {'Aisle: ' + f['aisle'] if f['aisle'] else 'Aisle unknown'}"
                    f"{'  Qty: ' + str(f['qty']) if f['qty'] else ''}\n"
                )
        else:
            print("\nNo in-store stock found near your zip code today.")
    except ImportError as e:
        log.error(f"Run from the tcg_tracker directory alongside tracker.py: {e}")
