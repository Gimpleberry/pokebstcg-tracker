#!/usr/bin/env python3
"""
plugins/invest_store.py - SQLite-backed Investment Portfolio Store (v5.9)

Replaces the localStorage-only persistence in dashboard/invest.html with a
durable, queryable, server-side store.

Schema design: Single table + JSON `attrs` column
  - Universal columns are top-level so KPI queries are pure SQL
  - Type-specific fields (grade, cert#, condition, bundle size) live in `attrs`
  - SQLite json_extract() available if JSON-field filtering ever needed

Storage:
  data/invest.db   - durable purchase log + market snapshot history

Plugin lifecycle:
  - Schema auto-created on plugin init (idempotent, safe to re-run)
  - schema_meta row tracks schema_version for future migrations
  - No scheduled tasks - passive CRUD store called by:
      * HTTP API in tracker.py (exposes /api/invest/*)
      * plugins/market_data_refresh.py (writes market value snapshots)

Safety:
  - All queries parameterized (no SQL injection)
  - Foreign keys ON, CHECK constraints on type and quantity
  - WAL mode for concurrent reads
  - Connection-per-call (SQLite handles this fine at our scale)
  - Module-level lock around writes to prevent race conditions
  - All exceptions logged, never silenced

CLI (manual diagnostics):
  python plugins/invest_store.py init    - create / verify schema
  python plugins/invest_store.py list    - dump all purchases as JSON
  python plugins/invest_store.py kpi     - print KPI summary
  python plugins/invest_store.py count   - row count
"""

import os
import sys
import json
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from contextlib import contextmanager

# Make shared.py importable when run as __main__ from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import DATA_DIR

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(DATA_DIR, "invest.db")
SCHEMA_VERSION = 1

VALID_TYPES = ("sealed", "raw_card", "graded")
VALID_VALUE_SOURCES = ("pokemontcg.io", "msrp_estimate", "manual", "tcgplayer")

# Schema is idempotent (CREATE IF NOT EXISTS everywhere)
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchases (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    type                     TEXT    NOT NULL CHECK (type IN ('sealed','raw_card','graded')),
    name                     TEXT    NOT NULL,
    set_code                 TEXT,
    purchase_date            TEXT    NOT NULL,
    purchase_price           REAL    NOT NULL,
    quantity                 INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    retailer                 TEXT,
    notes                    TEXT,
    current_market_value     REAL,
    market_value_source      TEXT,
    market_value_updated_at  TEXT,
    manual_value_override    REAL,
    attrs                    TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_purchases_type           ON purchases(type);
CREATE INDEX IF NOT EXISTS idx_purchases_set_code       ON purchases(set_code);
CREATE INDEX IF NOT EXISTS idx_purchases_purchase_date  ON purchases(purchase_date);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id     INTEGER NOT NULL,
    market_value    REAL    NOT NULL,
    source          TEXT    NOT NULL,
    snapshotted_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (purchase_id) REFERENCES purchases(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshots_purchase     ON market_snapshots(purchase_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_snapshot_at  ON market_snapshots(snapshotted_at);

-- Auto-bump updated_at on row changes
CREATE TRIGGER IF NOT EXISTS trg_purchases_updated_at
  AFTER UPDATE ON purchases
  FOR EACH ROW
  WHEN NEW.updated_at = OLD.updated_at
  BEGIN
    UPDATE purchases SET updated_at = datetime('now') WHERE id = NEW.id;
  END;
"""


# ── Connection management ────────────────────────────────────────────────────
_db_lock = threading.Lock()


@contextmanager
def _connect():
    """SQLite connection with FK + WAL + dict-like rows. Always closes cleanly."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def _init_schema() -> None:
    """Create tables/indexes/triggers if missing. Idempotent. Safe on every boot."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _db_lock:
        with _connect() as conn:
            conn.executescript(SCHEMA_DDL)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            log.info(f"[invest_store] schema ready at {DB_PATH} (v{SCHEMA_VERSION})")


# ── Row serialization ────────────────────────────────────────────────────────
def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert sqlite3.Row to plain dict, parsing the attrs JSON column."""
    d = dict(row)
    try:
        d["attrs"] = json.loads(d.get("attrs") or "{}")
    except json.JSONDecodeError:
        log.warning(f"[invest_store] malformed attrs JSON for id={d.get('id')}; defaulting to empty dict")
        d["attrs"] = {}
    return d


# ── Validation ───────────────────────────────────────────────────────────────
def _validate_payload(p: dict) -> None:
    """Raise ValueError on any invalid field. Run BEFORE writing."""
    if "type" not in p or p["type"] not in VALID_TYPES:
        raise ValueError(f"type must be one of {VALID_TYPES}")
    if not p.get("name"):
        raise ValueError("name is required")
    if not p.get("purchase_date"):
        raise ValueError("purchase_date is required (YYYY-MM-DD)")
    try:
        datetime.strptime(p["purchase_date"], "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError("purchase_date must be YYYY-MM-DD")
    if "purchase_price" not in p:
        raise ValueError("purchase_price is required")
    try:
        if float(p["purchase_price"]) < 0:
            raise ValueError("purchase_price must be >= 0")
    except (ValueError, TypeError):
        raise ValueError("purchase_price must be numeric")
    qty = p.get("quantity", 1)
    try:
        if int(qty) < 1:
            raise ValueError("quantity must be >= 1")
    except (ValueError, TypeError):
        raise ValueError("quantity must be a positive integer")


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ── CRUD ─────────────────────────────────────────────────────────────────────
def list_purchases() -> list[dict]:
    """All purchases, newest first by purchase_date then id."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM purchases ORDER BY purchase_date DESC, id DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_purchase(purchase_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
    return _row_to_dict(row) if row else None


def add_purchase(payload: dict) -> int:
    """Insert a new purchase. Returns new id. Raises ValueError on bad input."""
    _validate_payload(payload)
    attrs_json = json.dumps(payload.get("attrs") or {})
    with _db_lock:
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO purchases (
                    type, name, set_code, purchase_date, purchase_price, quantity,
                    retailer, notes, current_market_value, market_value_source,
                    market_value_updated_at, manual_value_override, attrs
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payload["type"],
                    payload["name"],
                    payload.get("set_code"),
                    payload["purchase_date"],
                    float(payload["purchase_price"]),
                    int(payload.get("quantity", 1)),
                    payload.get("retailer"),
                    payload.get("notes"),
                    _opt_float(payload.get("current_market_value")),
                    payload.get("market_value_source"),
                    payload.get("market_value_updated_at"),
                    _opt_float(payload.get("manual_value_override")),
                    attrs_json,
                ),
            )
            new_id = cur.lastrowid
            log.info(f"[invest_store] add id={new_id} type={payload['type']} name={payload['name'][:40]}")
            return new_id


def update_purchase(purchase_id: int, payload: dict) -> bool:
    """Update only the fields present in payload. Returns True if a row changed."""
    if not get_purchase(purchase_id):
        return False

    allowed = {
        "type", "name", "set_code", "purchase_date", "purchase_price", "quantity",
        "retailer", "notes", "current_market_value", "market_value_source",
        "market_value_updated_at", "manual_value_override",
    }
    sets, vals = [], []
    for k in allowed:
        if k in payload:
            sets.append(f"{k} = ?")
            v = payload[k]
            if k in ("purchase_price", "current_market_value", "manual_value_override"):
                v = _opt_float(v)
            elif k == "quantity":
                v = int(v)
            vals.append(v)

    if "attrs" in payload:
        sets.append("attrs = ?")
        vals.append(json.dumps(payload["attrs"] or {}))

    if not sets:
        return False

    vals.append(purchase_id)
    with _db_lock:
        with _connect() as conn:
            cur = conn.execute(f"UPDATE purchases SET {', '.join(sets)} WHERE id = ?", vals)
            updated = cur.rowcount > 0
            if updated:
                log.info(f"[invest_store] update id={purchase_id} fields={list(payload.keys())}")
            return updated


def delete_purchase(purchase_id: int) -> bool:
    with _db_lock:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
            deleted = cur.rowcount > 0
            if deleted:
                log.info(f"[invest_store] delete id={purchase_id}")
            return deleted


def bulk_import(purchases: list[dict], replace_all: bool = False) -> dict:
    """
    Bulk import. Used for the one-time localStorage migration on first invest.html load.
    
    Returns {imported, skipped, errors}.
    
    replace_all=True wipes existing rows first - reserved for explicit reseed.
    Default is additive insert; client must handle dedup before calling.
    All inserts run inside a single transaction - atomic on success or rollback on failure.
    """
    result = {"imported": 0, "skipped": 0, "errors": []}
    with _db_lock:
        with _connect() as conn:
            try:
                conn.execute("BEGIN")
                if replace_all:
                    conn.execute("DELETE FROM purchases")
                    log.warning("[invest_store] bulk_import replace_all=True - wiped existing rows")

                for idx, p in enumerate(purchases):
                    try:
                        _validate_payload(p)
                        attrs_json = json.dumps(p.get("attrs") or {})
                        conn.execute(
                            """
                            INSERT INTO purchases (
                                type, name, set_code, purchase_date, purchase_price, quantity,
                                retailer, notes, current_market_value, market_value_source,
                                market_value_updated_at, manual_value_override, attrs
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                p["type"], p["name"], p.get("set_code"),
                                p["purchase_date"], float(p["purchase_price"]),
                                int(p.get("quantity", 1)),
                                p.get("retailer"), p.get("notes"),
                                _opt_float(p.get("current_market_value")),
                                p.get("market_value_source"),
                                p.get("market_value_updated_at"),
                                _opt_float(p.get("manual_value_override")),
                                attrs_json,
                            ),
                        )
                        result["imported"] += 1
                    except (ValueError, KeyError, sqlite3.Error) as e:
                        result["skipped"] += 1
                        result["errors"].append(f"row {idx}: {e}")
                        log.warning(f"[invest_store] bulk_import skip row {idx}: {e}")
                conn.execute("COMMIT")
            except Exception as e:
                conn.execute("ROLLBACK")
                log.error(f"[invest_store] bulk_import rolled back: {e}")
                raise

    log.info(f"[invest_store] bulk_import done: imported={result['imported']} skipped={result['skipped']}")
    return result


def is_empty() -> bool:
    """True if no purchases exist. Client uses this to detect first-run migration window."""
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
    return n == 0


# ── Market value snapshots ───────────────────────────────────────────────────
def record_market_snapshot(purchase_id: int, value: float, source: str) -> int:
    """
    Insert a snapshot row AND update purchases.current_market_value.
    Wrapped in a transaction - both writes commit together or neither.
    """
    if source not in VALID_VALUE_SOURCES:
        raise ValueError(f"invalid source: {source}, expected one of {VALID_VALUE_SOURCES}")
    with _db_lock:
        with _connect() as conn:
            try:
                conn.execute("BEGIN")
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                cur = conn.execute(
                    "INSERT INTO market_snapshots (purchase_id, market_value, source, snapshotted_at) "
                    "VALUES (?,?,?,?)",
                    (purchase_id, float(value), source, now),
                )
                snap_id = cur.lastrowid
                conn.execute(
                    """
                    UPDATE purchases
                       SET current_market_value = ?,
                           market_value_source = ?,
                           market_value_updated_at = ?
                     WHERE id = ?
                    """,
                    (float(value), source, now, purchase_id),
                )
                conn.execute("COMMIT")
                log.debug(f"[invest_store] snapshot id={snap_id} purchase={purchase_id} val={value} src={source}")
                return snap_id
            except Exception as e:
                conn.execute("ROLLBACK")
                log.error(f"[invest_store] record_market_snapshot rolled back: {e}")
                raise


def get_snapshots(purchase_id: int, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM market_snapshots WHERE purchase_id = ? "
            "ORDER BY snapshotted_at DESC LIMIT ?",
            (purchase_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def prune_old_snapshots(days_to_keep: int = 730) -> int:
    """
    Delete snapshots older than N days. Returns rows deleted.
    Default 730 days (2 years) - tax-season lookback + trend analysis.
    Called by market_data_refresh weekly to keep DB lean.
    Easy to bump later: change the default and re-run, no migration needed.
    """
    with _db_lock:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM market_snapshots WHERE snapshotted_at < datetime('now', ?)",
                (f"-{int(days_to_keep)} days",),
            )
            n = cur.rowcount
            log.info(f"[invest_store] pruned {n} snapshots older than {days_to_keep} days")
            return n


# ── KPI summaries ────────────────────────────────────────────────────────────
def kpi_summary() -> dict:
    """
    KPI bar values. Quantity-aware. Manual override beats auto-fetched value.

    Items WITHOUT a market value:
      - DO contribute to total_cost (you spent that money)
      - Are EXCLUDED from total_market / total_pl / total_pl_pct (no honest figure)
      - Reflected in unvalued_purchases count so UI can render "X of Y valued"

    When NO purchases have a market value:
      total_market, total_pl, total_pl_pct all return None.  UI shows "N/A".
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, purchase_price, quantity,
                   COALESCE(manual_value_override, current_market_value) AS effective_value
              FROM purchases
            """
        ).fetchall()

    total_cost = 0.0
    total_market_valued = 0.0   # market sum across valued items only
    total_cost_valued = 0.0     # cost basis of valued items only (denom for P/L %)
    item_count_total = 0
    item_count_valued = 0
    purchase_count_total = len(rows)
    purchase_count_valued = 0
    best, best_pl_pct = None, None

    for r in rows:
        qty = r["quantity"] or 1
        cost = (r["purchase_price"] or 0.0) * qty
        total_cost += cost
        item_count_total += qty

        if r["effective_value"] is not None:
            market = r["effective_value"] * qty
            total_market_valued += market
            total_cost_valued += cost
            item_count_valued += qty
            purchase_count_valued += 1

            if cost > 0:
                pl_pct = ((market - cost) / cost) * 100
                if best_pl_pct is None or pl_pct > best_pl_pct:
                    best_pl_pct = pl_pct
                    best = {"id": r["id"], "name": r["name"], "pl_pct": round(pl_pct, 2)}

    has_any_value = purchase_count_valued > 0
    pl_valued = total_market_valued - total_cost_valued
    pl_pct_valued = (pl_valued / total_cost_valued * 100) if total_cost_valued > 0 else 0.0

    return {
        "total_cost":             round(total_cost, 2),
        "total_market":           round(total_market_valued, 2) if has_any_value else None,
        "total_pl":               round(pl_valued, 2)           if has_any_value else None,
        "total_pl_pct":           round(pl_pct_valued, 2)       if has_any_value else None,
        "item_count":             item_count_total,
        "item_count_valued":      item_count_valued,
        "purchase_count":         purchase_count_total,
        "purchase_count_valued":  purchase_count_valued,
        "unvalued_purchases":     purchase_count_total - purchase_count_valued,
        "best_performer":         best,
    }


# ── Implementation class ─────────────────────────────────────────────────────
class InvestStore:
    """
    Implementation class - instantiated by the InvestStore_Plugin wrapper in plugins.py:
        from invest_store import InvestStore
        self._store = InvestStore(config, products)
        self._store.start(schedule)

    No scheduled tasks: passive store. CRUD methods exposed as module-level
    functions so api_server.py can call them without needing the instance.
    """
    def __init__(self, config: dict, products: list):
        self.config = config
        _init_schema()
        log.info("[invest_store] initialized")

    def start(self, schedule) -> None:
        log.info("[invest_store] ready (no scheduled tasks)")

    # CRUD passthrough (also available as module functions)
    list_purchases     = staticmethod(list_purchases)
    get_purchase       = staticmethod(get_purchase)
    add_purchase       = staticmethod(add_purchase)
    update_purchase    = staticmethod(update_purchase)
    delete_purchase    = staticmethod(delete_purchase)
    bulk_import        = staticmethod(bulk_import)
    is_empty           = staticmethod(is_empty)
    record_snapshot    = staticmethod(record_market_snapshot)
    get_snapshots      = staticmethod(get_snapshots)
    prune_snapshots    = staticmethod(prune_old_snapshots)
    kpi_summary        = staticmethod(kpi_summary)


# ── CLI for diagnostics ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "init":
        _init_schema()
        print(f"OK - schema at {DB_PATH}")
    elif cmd == "list":
        for p in list_purchases():
            print(json.dumps(p, indent=2, default=str))
    elif cmd == "kpi":
        print(json.dumps(kpi_summary(), indent=2))
    elif cmd == "count":
        with _connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
        print(n)
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)
