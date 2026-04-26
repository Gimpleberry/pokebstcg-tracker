#!/usr/bin/env python3
"""
plugins/market_data_refresh.py - Centralized Market Value Refresh (v5.9)

The single chokepoint for pokemontcg.io API calls and sealed-MSRP lookups.
Replaces invest.html's direct API calls (which re-fetched on every page load).

Behavior:
  * Schedule: every 12 hours
  * Initial fetch: 5 minutes after tracker boot (other plugins start first)
  * Weekly prune of old snapshots: Monday 03:00 (low-traffic window)
  * Manual refresh: HTTP API trigger, 1-hour server-side cooldown, bypasses cache

Per type:
  * raw_card: pokemontcg.io API (rate-limited, cached 12h)
  * sealed:   MSRP lookup from shared.py MSRP_TABLE (offline, free, fast)
              Used as floor value - "rough but better than nothing"
  * graded:   skipped (no free pricing API; manual entry only)

Storage:
  data/market_cache.db
    pokemontcg_cache  - raw API responses + extracted price + fetched_at TTL
    refresh_log       - audit trail of every refresh run for diagnostics

Snapshots are written to data/invest.db via invest_store.record_market_snapshot().

Safety:
  * All HTTP requests time-limited (15s)
  * Failures logged, never silenced
  * Per-item exception isolation: one bad card does not abort the whole run
  * Cache writes parameterized
  * Cooldown enforced server-side (cannot be bypassed by spamming the button)
"""

import os
import sys
import json
import time
import sqlite3
import logging
import threading
import requests
from datetime import datetime, timezone
from typing import Optional
from contextlib import contextmanager

# Make shared + sibling plugins importable
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_PLUGIN_DIR)
sys.path.insert(0, _ROOT_DIR)
sys.path.insert(0, _PLUGIN_DIR)

from shared import DATA_DIR, HEADERS, get_msrp
import invest_store

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
CACHE_DB_PATH = os.path.join(DATA_DIR, "market_cache.db")

# pokemontcg.io
POKEMONTCG_API_BASE = "https://api.pokemontcg.io/v2/cards"
API_TIMEOUT_SEC = 15
API_REQUEST_DELAY_SEC = 0.6  # ~100 req/min, well under the 1000/day free tier

# Cache TTL must match (or be slightly less than) the scheduled refresh interval
# so that scheduled runs always re-fetch but mid-cycle GETs serve cache.
CACHE_TTL_HOURS = 12

# Manual refresh cooldown
MANUAL_COOLDOWN_SEC = 3600  # 1 hour

# Initial-startup fetch delay (let tracker.py + other plugins boot cleanly first)
STARTUP_DELAY_SEC = 300  # 5 minutes

# Snapshot retention (calls invest_store.prune_old_snapshots which defaults 730 days)
SNAPSHOT_RETENTION_DAYS = 730

# Cache schema
CACHE_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS pokemontcg_cache (
    pokemontcg_id  TEXT PRIMARY KEY,
    market_value   REAL,
    variant_used   TEXT,
    raw_response   TEXT,
    fetched_at     TEXT NOT NULL DEFAULT (datetime('now')),
    success        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_cache_fetched_at ON pokemontcg_cache(fetched_at);

CREATE TABLE IF NOT EXISTS refresh_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    triggered_by     TEXT NOT NULL,
    items_total      INTEGER DEFAULT 0,
    items_refreshed  INTEGER DEFAULT 0,
    items_cached     INTEGER DEFAULT 0,
    items_failed     INTEGER DEFAULT 0,
    items_skipped    INTEGER DEFAULT 0,
    error_summary    TEXT
);
"""

_db_lock = threading.Lock()

# ── Manual-refresh cooldown state (module-level, accessible by api_server) ──
_last_manual_refresh = 0.0
_cooldown_lock = threading.Lock()


def trigger_manual_refresh() -> dict:
    """
    Manual refresh entry point. Called by api_server when the dashboard's
    refresh button is clicked. Returns immediately; refresh runs async.

    Cooldown is server-side - cannot be bypassed by spamming the button.
    """
    global _last_manual_refresh
    now = time.time()
    with _cooldown_lock:
        elapsed = now - _last_manual_refresh
        if elapsed < MANUAL_COOLDOWN_SEC:
            remaining = int(MANUAL_COOLDOWN_SEC - elapsed)
            m, s = divmod(remaining, 60)
            return {
                "status": "cooldown",
                "message": f"Manual refresh available in {m}m {s}s",
                "cooldown_remaining_seconds": remaining,
            }
        _last_manual_refresh = now

    threading.Thread(
        target=lambda: _run_safely("manual", bypass_cache=True),
        daemon=True,
        name="market_refresh_manual",
    ).start()

    return {"status": "started", "message": "Manual refresh started"}


def manual_cooldown_status() -> dict:
    """For UI to render the button state (enabled / disabled with countdown)."""
    elapsed = time.time() - _last_manual_refresh
    if elapsed >= MANUAL_COOLDOWN_SEC:
        return {"available": True, "remaining_seconds": 0}
    return {"available": False, "remaining_seconds": int(MANUAL_COOLDOWN_SEC - elapsed)}


def _run_safely(triggered_by: str, bypass_cache: bool = False) -> None:
    """Wrap _refresh_all so a thrown exception in scheduled jobs doesn't kill the scheduler."""
    try:
        _refresh_all(triggered_by=triggered_by, bypass_cache=bypass_cache)
    except Exception as e:
        log.error(f"[market_data_refresh] _run_safely caught: {e}")


# ── Cache DB ─────────────────────────────────────────────────────────────────
@contextmanager
def _cache_conn():
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def _init_cache_schema() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _db_lock:
        with _cache_conn() as conn:
            conn.executescript(CACHE_SCHEMA_DDL)
            log.info(f"[market_data_refresh] cache schema ready at {CACHE_DB_PATH}")


def _cache_get(pokemontcg_id: str) -> Optional[dict]:
    """Return cached entry as dict (or None if missing). Does not check TTL."""
    with _cache_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pokemontcg_cache WHERE pokemontcg_id = ?",
            (pokemontcg_id,),
        ).fetchone()
    return dict(row) if row else None


def _cache_is_fresh(entry: dict) -> bool:
    """True if entry is younger than CACHE_TTL_HOURS."""
    if not entry or not entry.get("fetched_at"):
        return False
    try:
        ts = datetime.fromisoformat(entry["fetched_at"].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return age_hours < CACHE_TTL_HOURS
    except (ValueError, TypeError) as e:
        log.warning(f"[market_data_refresh] bad cache timestamp: {e}")
        return False


def _cache_put(pokemontcg_id: str, market_value: Optional[float], variant: str,
               raw_response: Optional[str], success: bool) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db_lock:
        with _cache_conn() as conn:
            conn.execute(
                """
                INSERT INTO pokemontcg_cache (pokemontcg_id, market_value, variant_used, raw_response, fetched_at, success)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(pokemontcg_id) DO UPDATE SET
                    market_value = excluded.market_value,
                    variant_used = excluded.variant_used,
                    raw_response = excluded.raw_response,
                    fetched_at   = excluded.fetched_at,
                    success      = excluded.success
                """,
                (pokemontcg_id, market_value, variant, raw_response, now, 1 if success else 0),
            )


# ── pokemontcg.io fetch ──────────────────────────────────────────────────────
def _extract_market_price(card_data: dict) -> tuple[Optional[float], str]:
    """
    Pull the best available market price from a pokemontcg.io card response.
    Preference: tcgplayer.holofoil > normal > reverseHolofoil > 1st ed > unlimited
    Falls back to cardmarket.averageSellPrice / trendPrice.
    Returns (price, variant_used) or (None, "no_price").
    """
    tcg = (card_data.get("tcgplayer") or {}).get("prices") or {}
    preferred = [
        "holofoil", "normal", "reverseHolofoil",
        "1stEditionHolofoil", "1stEditionNormal", "unlimitedHolofoil",
    ]
    for variant in preferred:
        v = tcg.get(variant) or {}
        # market is preferred; mid is the next-best signal
        price = v.get("market") or v.get("mid")
        if price and price > 0:
            return float(price), f"tcgplayer:{variant}"

    # Any remaining tcgplayer variant we didn't know about
    for variant, v in tcg.items():
        if variant in preferred:
            continue
        if isinstance(v, dict):
            price = v.get("market") or v.get("mid")
            if price and price > 0:
                return float(price), f"tcgplayer:{variant}"

    cm = (card_data.get("cardmarket") or {}).get("prices") or {}
    avg = cm.get("averageSellPrice") or cm.get("trendPrice")
    if avg and avg > 0:
        return float(avg), "cardmarket:average"

    return None, "no_price"


def _fetch_pokemontcg(pokemontcg_id: str) -> tuple[Optional[float], str, Optional[str]]:
    """
    Hit pokemontcg.io for a single card. Returns (price, variant, raw_json).
    On error returns (None, "error:<reason>", None).
    Never raises - caller relies on this for error isolation.
    """
    url = f"{POKEMONTCG_API_BASE}/{pokemontcg_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=API_TIMEOUT_SEC)
        if r.status_code == 404:
            log.debug(f"[market_data_refresh] pokemontcg.io 404 for {pokemontcg_id}")
            return None, "error:404", None
        if r.status_code == 429:
            log.warning(f"[market_data_refresh] pokemontcg.io rate-limited (429); backing off")
            return None, "error:429", None
        if r.status_code != 200:
            log.warning(f"[market_data_refresh] pokemontcg.io {r.status_code} for {pokemontcg_id}")
            return None, f"error:{r.status_code}", None

        body = r.json()
        card = body.get("data") or {}
        price, variant = _extract_market_price(card)
        return price, variant, json.dumps(body)[:8000]  # cap raw blob size

    except requests.exceptions.Timeout:
        log.warning(f"[market_data_refresh] pokemontcg.io timeout for {pokemontcg_id}")
        return None, "error:timeout", None
    except requests.exceptions.RequestException as e:
        log.warning(f"[market_data_refresh] pokemontcg.io network error for {pokemontcg_id}: {e}")
        return None, "error:network", None
    except (ValueError, KeyError) as e:
        log.warning(f"[market_data_refresh] pokemontcg.io parse error for {pokemontcg_id}: {e}")
        return None, "error:parse", None


def get_market_value(pokemontcg_id: str, force_refresh: bool = False) -> dict:
    """
    Return market value for a single card, using cache if fresh.

    Used by:
      * scheduled refresh (force_refresh=True per cycle)
      * HTTP API GET /api/market/value (force_refresh=False - cache-aware)
      * manual refresh (force_refresh=True)

    Returns:
      {
        "pokemontcg_id": str,
        "market_value": float | None,
        "variant": str,            (e.g. "tcgplayer:holofoil")
        "source":  "cache" | "api" | "no_price",
        "fetched_at": iso8601,
        "age_hours": float
      }
    """
    cached = _cache_get(pokemontcg_id)
    if cached and not force_refresh and _cache_is_fresh(cached):
        return {
            "pokemontcg_id": pokemontcg_id,
            "market_value":  cached["market_value"],
            "variant":       cached["variant_used"],
            "source":        "cache",
            "fetched_at":    cached["fetched_at"],
            "age_hours":     _age_hours(cached["fetched_at"]),
        }

    price, variant, raw = _fetch_pokemontcg(pokemontcg_id)
    success = price is not None
    _cache_put(pokemontcg_id, price, variant, raw, success)

    return {
        "pokemontcg_id": pokemontcg_id,
        "market_value":  price,
        "variant":       variant,
        "source":        "api" if success else "no_price",
        "fetched_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "age_hours":     0.0,
    }


def _age_hours(iso_ts: str) -> float:
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - ts).total_seconds() / 3600, 2)
    except Exception:
        return -1.0


# ── Sealed MSRP lookup (offline) ─────────────────────────────────────────────
def _get_sealed_value(name: str, retailer: str = "") -> Optional[float]:
    """
    Sealed-product floor value via shared.py MSRP_TABLE.
    Cheap, free, deterministic - no API call. "Rough but better than nothing."
    """
    try:
        return get_msrp(name, retailer or "")
    except Exception as e:
        log.warning(f"[market_data_refresh] MSRP lookup failed for {name!r}: {e}")
        return None


# ── Refresh orchestration ────────────────────────────────────────────────────
def _refresh_all(triggered_by: str = "scheduled", bypass_cache: bool = False) -> dict:
    """
    Walk every purchase in invest.db, refresh its market value, snapshot it.

    Per-item exception isolation: one bad row does not abort the run.

    Counts in returned dict:
      total      - all purchases looked at
      refreshed  - snapshot was written (value changed or first-time)
      cached     - value confirmed unchanged, no snapshot needed
      failed     - lookup errored (API 4xx/5xx/timeout, or parse failure)
      skipped    - type unsupported or data missing (graded; sealed w/o MSRP match;
                   raw_card w/o pokemontcg_id)

    Returns the refresh_log row written.
    """
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info(f"[market_data_refresh] refresh START triggered_by={triggered_by} bypass_cache={bypass_cache}")

    # Open a refresh_log row up front so we can correlate errors mid-run
    with _db_lock:
        with _cache_conn() as conn:
            cur = conn.execute(
                "INSERT INTO refresh_log (started_at, triggered_by) VALUES (?, ?)",
                (started_at, triggered_by),
            )
            log_id = cur.lastrowid

    counts = {"total": 0, "refreshed": 0, "cached": 0, "failed": 0, "skipped": 0}
    errors = []

    try:
        purchases = invest_store.list_purchases()
    except Exception as e:
        log.error(f"[market_data_refresh] could not list purchases: {e}")
        _close_refresh_log(log_id, counts, str(e))
        return {"log_id": log_id, **counts, "error": str(e)}

    counts["total"] = len(purchases)

    for p in purchases:
        try:
            ptype = p.get("type")
            attrs = p.get("attrs") or {}

            # ── graded: skip (no free API) ──────────────────────────────
            if ptype == "graded":
                counts["skipped"] += 1
                continue

            # ── sealed: MSRP floor value (initial seed only) ───────────
            if ptype == "sealed":
                # MSRP is a fallback "floor" estimate, NOT real market data.
                # For hot Pokemon TCG products that sell 2-3x MSRP, overwriting
                # a user-set value with MSRP would be misleading. So we only
                # SEED with MSRP when no value exists yet. Once a value is set
                # (manual override OR previously-seeded current value), respect
                # it - the user can clear it via Edit if they want to re-seed.
                cur_val = p.get("current_market_value")
                manual_val = p.get("manual_value_override")
                if cur_val is not None or manual_val is not None:
                    counts["cached"] += 1
                    continue
                msrp = _get_sealed_value(p.get("name", ""), p.get("retailer") or "")
                if msrp is None:
                    log.debug(f"[market_data_refresh] no MSRP match for sealed: {p.get('name')!r}")
                    counts["skipped"] += 1
                    continue
                invest_store.record_market_snapshot(p["id"], msrp, "msrp_estimate")
                counts["refreshed"] += 1
                continue

            # ── raw_card: pokemontcg.io ─────────────────────────────────
            if ptype == "raw_card":
                pokemontcg_id = attrs.get("pokemontcg_id")
                if not pokemontcg_id:
                    log.debug(f"[market_data_refresh] raw_card missing pokemontcg_id: id={p['id']}")
                    counts["skipped"] += 1
                    continue
                result = get_market_value(pokemontcg_id, force_refresh=bypass_cache)
                if result["market_value"] is None:
                    counts["failed"] += 1
                    errors.append(f"id={p['id']} {pokemontcg_id}: {result['variant']}")
                    continue

                # Delta-detection: only snapshot when the value materially changed.
                # Threshold is $0.01 - tighter than sealed's MSRP comparison since
                # API prices have decimal precision.
                cur_val = p.get("current_market_value")
                new_val = result["market_value"]
                value_changed = cur_val is None or abs(cur_val - new_val) >= 0.01

                if value_changed:
                    invest_store.record_market_snapshot(
                        p["id"], new_val, "pokemontcg.io"
                    )
                    counts["refreshed"] += 1
                else:
                    counts["cached"] += 1

                # Polite delay only on actual API calls, not cache hits
                if result["source"] == "api":
                    time.sleep(API_REQUEST_DELAY_SEC)
                continue

            # Unknown type
            log.warning(f"[market_data_refresh] unknown type {ptype} for id={p['id']}")
            counts["skipped"] += 1

        except Exception as e:
            counts["failed"] += 1
            errors.append(f"id={p.get('id')}: {e}")
            log.warning(f"[market_data_refresh] error on id={p.get('id')}: {e}")

    error_summary = "; ".join(errors[:10]) if errors else None
    _close_refresh_log(log_id, counts, error_summary)
    log.info(
        f"[market_data_refresh] refresh DONE total={counts['total']} "
        f"refreshed={counts['refreshed']} cached={counts['cached']} "
        f"failed={counts['failed']} skipped={counts['skipped']}"
    )
    return {"log_id": log_id, **counts}


def _close_refresh_log(log_id: int, counts: dict, error_summary: Optional[str]) -> None:
    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db_lock:
        with _cache_conn() as conn:
            conn.execute(
                """
                UPDATE refresh_log SET
                    finished_at     = ?,
                    items_total     = ?,
                    items_refreshed = ?,
                    items_cached    = ?,
                    items_failed    = ?,
                    items_skipped   = ?,
                    error_summary   = ?
                WHERE id = ?
                """,
                (
                    finished_at,
                    counts.get("total", 0),
                    counts.get("refreshed", 0),
                    counts.get("cached", 0),
                    counts.get("failed", 0),
                    counts.get("skipped", 0),
                    error_summary,
                    log_id,
                ),
            )


def get_recent_refresh_log(limit: int = 20) -> list[dict]:
    """For diagnostics / admin endpoint."""
    with _cache_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM refresh_log ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Implementation class ─────────────────────────────────────────────────────
class MarketDataRefresh:
    """
    Implementation class - instantiated by MarketDataRefresh_Plugin in plugins.py:
        from market_data_refresh import MarketDataRefresh
        self._refresher = MarketDataRefresh(config, products)
        self._refresher.start(schedule)

    Manual refresh and cooldown live at module level (callable from api_server).
    """

    def __init__(self, config: dict, products: list):
        self.config = config
        _init_cache_schema()
        log.info("[market_data_refresh] initialized")

    def start(self, schedule) -> None:
        # Initial refresh after STARTUP_DELAY_SEC - other plugins boot first
        threading.Timer(
            STARTUP_DELAY_SEC,
            lambda: _run_safely("startup"),
        ).start()

        schedule.every(CACHE_TTL_HOURS).hours.do(lambda: _run_safely("scheduled"))
        schedule.every().monday.at("03:00").do(self._run_prune)

        log.info(
            f"[market_data_refresh] scheduled: every {CACHE_TTL_HOURS}h refresh, "
            f"weekly prune Mon 03:00, initial fetch in {STARTUP_DELAY_SEC}s"
        )

    def _run_prune(self) -> None:
        try:
            n = invest_store.prune_old_snapshots(SNAPSHOT_RETENTION_DAYS)
            log.info(f"[market_data_refresh] weekly prune complete: {n} snapshots removed")
        except Exception as e:
            log.error(f"[market_data_refresh] prune failed: {e}")

    # Module functions exposed as staticmethods for convenience
    trigger_manual_refresh = staticmethod(trigger_manual_refresh)
    manual_cooldown_status = staticmethod(manual_cooldown_status)
    get_market_value       = staticmethod(get_market_value)
    get_recent_refresh_log = staticmethod(get_recent_refresh_log)


# ── CLI for diagnostics ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nUsage:")
        print("  python plugins/market_data_refresh.py init      - create cache schema")
        print("  python plugins/market_data_refresh.py refresh   - run a full refresh now")
        print("  python plugins/market_data_refresh.py force     - run a full refresh, bypass cache")
        print("  python plugins/market_data_refresh.py log       - show last 20 refresh runs")
        print("  python plugins/market_data_refresh.py value <pokemontcg_id>  - look up one card")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "init":
        _init_cache_schema()
        print(f"OK - cache schema at {CACHE_DB_PATH}")
    elif cmd == "refresh":
        _init_cache_schema()
        result = _refresh_all(triggered_by="cli")
        print(json.dumps(result, indent=2))
    elif cmd == "force":
        _init_cache_schema()
        result = _refresh_all(triggered_by="cli-force", bypass_cache=True)
        print(json.dumps(result, indent=2))
    elif cmd == "log":
        _init_cache_schema()
        for r in get_recent_refresh_log(20):
            print(json.dumps(r, indent=2, default=str))
    elif cmd == "value":
        if len(sys.argv) < 3:
            print("usage: value <pokemontcg_id>")
            sys.exit(1)
        _init_cache_schema()
        result = get_market_value(sys.argv[2], force_refresh=False)
        print(json.dumps(result, indent=2))
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)
