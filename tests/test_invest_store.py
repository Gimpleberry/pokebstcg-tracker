#!/usr/bin/env python3
"""
tests/test_invest_store.py - Verify invest_store SQLite layer + market refresh (v5.9.1)

Runs 15 checks against a temporary database (does NOT touch your real data/invest.db
or data/market_cache.db):
  1.  Schema creates idempotently (re-init is safe)
  2.  CRUD round-trip for sealed (add/get/update/delete)
  3.  CRUD round-trip preserves attrs JSON column
  4.  Validation rejects bad type
  5.  Validation rejects bad date
  6.  KPI math correct with mixed valued/unvalued items
  7.  KPI returns None when no items have market values (UI shows N/A)
  8.  Snapshots accumulate per purchase + update current_market_value
  9.  Snapshot pruning removes old entries
  10. Bulk import is partial-failure tolerant (good rows succeed, bad ones reported)
  11. Manual override beats auto-fetched value in KPI calculations

  v5.9.1 regression tests for sealed MSRP refresh:
  12. Sealed entry with no value gets seeded by MSRP on first refresh
  13. Sealed entry with current_market_value is PROTECTED (not overwritten by MSRP)
  14. Sealed entry with manual_value_override fully blocks MSRP seed
  15. Sealed with no MSRP match is skipped, no value written

Exit code 0 = all checks pass. Non-zero = at least one check failed.

Run from project root:
    python tests/test_invest_store.py

This script is read-only WRT your real data -- it uses a fresh temp DB
that gets cleaned up on exit.
"""

import os
import sys
import shutil
import sqlite3
import tempfile

# Make plugins importable from any cwd
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "plugins"))

import invest_store  # noqa: E402
import market_data_refresh as mdr  # noqa: E402

# ── Set up sandbox DBs ───────────────────────────────────────────────────────
TEMP_DIR = tempfile.mkdtemp(prefix="invest_store_test_")
TEMP_DB = os.path.join(TEMP_DIR, "test_invest.db")
TEMP_CACHE_DB = os.path.join(TEMP_DIR, "test_market_cache.db")

# Redirect both modules' DB paths to our temp files BEFORE schema init
_REAL_DB_PATH = invest_store.DB_PATH
_REAL_CACHE_DB_PATH = mdr.CACHE_DB_PATH
invest_store.DB_PATH = TEMP_DB
mdr.CACHE_DB_PATH = TEMP_CACHE_DB
invest_store._init_schema()
mdr._init_cache_schema()


def _wipe_db():
    """Reset between tests so each one starts clean."""
    with sqlite3.connect(TEMP_DB) as conn:
        conn.execute("DELETE FROM purchases")
        conn.execute("DELETE FROM market_snapshots")
        conn.execute("DELETE FROM sqlite_sequence")  # reset autoincrement


# ── Test runner ──────────────────────────────────────────────────────────────
results = []


def run(name, fn):
    """Run one test. Catches assertions + unexpected exceptions."""
    try:
        fn()
        results.append((True, name, ""))
        print(f"  PASS  {name}")
    except AssertionError as e:
        results.append((False, name, str(e) or "AssertionError"))
        print(f"  FAIL  {name}  -- {e}")
    except Exception as e:
        results.append((False, name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}  -- {type(e).__name__}: {e}")


# ── Tests ────────────────────────────────────────────────────────────────────
def t_schema_idempotent():
    invest_store._init_schema()
    invest_store._init_schema()
    assert invest_store.is_empty(), "is_empty should be True after schema init with no rows"


def t_crud_sealed():
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "sealed",
        "name": "Prismatic Evolutions ETB",
        "set_code": "PE",
        "purchase_date": "2026-04-20",
        "purchase_price": 49.99,
        "quantity": 2,
        "retailer": "Costco",
    })
    assert pid > 0, f"expected positive id, got {pid}"

    p = invest_store.get_purchase(pid)
    assert p is not None
    assert p["name"] == "Prismatic Evolutions ETB"
    assert p["quantity"] == 2
    assert p["purchase_price"] == 49.99

    # Update
    updated = invest_store.update_purchase(pid, {"quantity": 3, "notes": "bought 1 more"})
    assert updated is True
    p = invest_store.get_purchase(pid)
    assert p["quantity"] == 3
    assert "bought 1 more" in (p["notes"] or "")

    # Delete
    assert invest_store.delete_purchase(pid) is True
    assert invest_store.get_purchase(pid) is None
    assert invest_store.delete_purchase(pid) is False  # already gone


def t_attrs_round_trip():
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "graded",
        "name": "Charizard ex 199/091 PSA 10",
        "set_code": "PE",
        "purchase_date": "2026-04-20",
        "purchase_price": 850.00,
        "attrs": {
            "grader": "PSA",
            "grade": 10,
            "cert_number": "12345678",
            "card_number": "199/091",
        },
    })
    p = invest_store.get_purchase(pid)
    assert p["attrs"]["grader"] == "PSA"
    assert p["attrs"]["grade"] == 10
    assert p["attrs"]["cert_number"] == "12345678"
    assert p["attrs"]["card_number"] == "199/091"


def t_validation_bad_type():
    _wipe_db()
    try:
        invest_store.add_purchase({
            "type": "garbage_type",
            "name": "x",
            "purchase_date": "2026-01-01",
            "purchase_price": 1.0,
        })
    except ValueError as e:
        assert "type" in str(e).lower()
        return
    raise AssertionError("expected ValueError for bad type")


def t_validation_bad_date():
    _wipe_db()
    try:
        invest_store.add_purchase({
            "type": "sealed",
            "name": "x",
            "purchase_date": "not-a-date",
            "purchase_price": 1.0,
        })
    except ValueError as e:
        assert "date" in str(e).lower() or "YYYY-MM-DD" in str(e)
        return
    raise AssertionError("expected ValueError for bad date")


def t_kpi_mixed():
    _wipe_db()
    # 2 valued + 1 unvalued; quantity-aware
    invest_store.add_purchase({
        "type": "sealed", "name": "ETB x2",
        "purchase_date": "2026-04-20",
        "purchase_price": 49.99, "quantity": 2,
        "current_market_value": 89.99,
    })
    invest_store.add_purchase({
        "type": "graded", "name": "Charizard PSA 10",
        "purchase_date": "2026-03-15",
        "purchase_price": 850.0, "quantity": 1,
        "current_market_value": 1300.0,
    })
    invest_store.add_purchase({
        "type": "raw_card", "name": "Umbreon (no value yet)",
        "purchase_date": "2026-02-10",
        "purchase_price": 425.0, "quantity": 1,
    })

    kpi = invest_store.kpi_summary()
    # Cost: 49.99*2 + 850 + 425 = 1374.98
    # Market (valued only): 89.99*2 + 1300 = 1479.98
    # Cost of valued items: 99.98 + 850 = 949.98
    # P/L: 1479.98 - 949.98 = 530.00
    # P/L%: 530.00 / 949.98 * 100 = 55.79%
    assert kpi["total_cost"] == 1374.98, f"total_cost {kpi['total_cost']}"
    assert kpi["total_market"] == 1479.98, f"total_market {kpi['total_market']}"
    assert kpi["total_pl"] == 530.0, f"total_pl {kpi['total_pl']}"
    assert kpi["total_pl_pct"] == 55.79, f"total_pl_pct {kpi['total_pl_pct']}"
    assert kpi["unvalued_purchases"] == 1
    assert kpi["purchase_count"] == 3
    assert kpi["purchase_count_valued"] == 2
    assert kpi["item_count"] == 4   # 2 + 1 + 1
    assert kpi["item_count_valued"] == 3  # 2 + 1


def t_kpi_all_unvalued():
    _wipe_db()
    invest_store.add_purchase({
        "type": "sealed", "name": "Brand new",
        "purchase_date": "2026-04-26",
        "purchase_price": 100.0,
    })
    invest_store.add_purchase({
        "type": "raw_card", "name": "Brand new card",
        "purchase_date": "2026-04-26",
        "purchase_price": 50.0,
    })
    kpi = invest_store.kpi_summary()
    assert kpi["total_cost"] == 150.0
    # When nothing is valued, market/pl/pl_pct must be None so UI renders "N/A"
    assert kpi["total_market"] is None, f"expected None, got {kpi['total_market']}"
    assert kpi["total_pl"] is None
    assert kpi["total_pl_pct"] is None
    assert kpi["best_performer"] is None
    assert kpi["unvalued_purchases"] == 2


def t_snapshots_accumulate():
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "raw_card", "name": "X",
        "purchase_date": "2026-04-20",
        "purchase_price": 100.0,
        "attrs": {"pokemontcg_id": "x-1"},
    })
    invest_store.record_market_snapshot(pid, 110.0, "pokemontcg.io")
    invest_store.record_market_snapshot(pid, 115.0, "pokemontcg.io")
    invest_store.record_market_snapshot(pid, 120.0, "pokemontcg.io")

    snaps = invest_store.get_snapshots(pid)
    assert len(snaps) == 3, f"expected 3 snapshots, got {len(snaps)}"

    # current_market_value should reflect the latest snapshot
    p = invest_store.get_purchase(pid)
    assert p["current_market_value"] == 120.0
    assert p["market_value_source"] == "pokemontcg.io"


def t_snapshot_prune():
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "raw_card", "name": "X",
        "purchase_date": "2026-04-20",
        "purchase_price": 100.0,
    })
    invest_store.record_market_snapshot(pid, 110.0, "pokemontcg.io")
    invest_store.record_market_snapshot(pid, 115.0, "pokemontcg.io")

    # Backdate one snapshot to be older than the retention window
    with sqlite3.connect(TEMP_DB) as conn:
        conn.execute("UPDATE market_snapshots SET snapshotted_at = datetime('now', '-1000 days') WHERE id = 1")

    pruned = invest_store.prune_old_snapshots(days_to_keep=730)
    assert pruned == 1, f"expected 1 pruned, got {pruned}"
    snaps = invest_store.get_snapshots(pid)
    assert len(snaps) == 1, f"expected 1 snapshot remaining, got {len(snaps)}"


def t_bulk_partial_failure():
    _wipe_db()
    result = invest_store.bulk_import([
        {"type": "sealed", "name": "Good 1", "purchase_date": "2026-01-01", "purchase_price": 25.0},
        {"type": "INVALID_TYPE", "name": "Bad 1", "purchase_date": "2026-01-01", "purchase_price": 25.0},
        {"type": "sealed", "name": "Good 2", "purchase_date": "2026-01-02", "purchase_price": 30.0},
        {"type": "sealed", "name": "Bad 2 (missing date)", "purchase_price": 40.0},
    ])
    assert result["imported"] == 2, f"imported: {result['imported']}"
    assert result["skipped"] == 2, f"skipped: {result['skipped']}"
    assert len(result["errors"]) == 2

    # Good rows should actually be in the DB
    rows = invest_store.list_purchases()
    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"Good 1", "Good 2"}


def t_manual_override_beats_current():
    _wipe_db()
    invest_store.add_purchase({
        "type": "raw_card", "name": "Card with both values",
        "purchase_date": "2026-04-20",
        "purchase_price": 100.0,
        "current_market_value": 200.0,        # auto-fetched
        "manual_value_override": 250.0,       # user manual entry — should win
    })
    kpi = invest_store.kpi_summary()
    assert kpi["total_market"] == 250.0, (
        f"manual override should beat auto value; expected $250, got ${kpi['total_market']}"
    )


# ── v5.9.1 regression tests: sealed MSRP refresh behavior ────────────────────

def t_sealed_seeds_empty():
    """Sealed entry with no value gets seeded with MSRP on first refresh."""
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "sealed",
        "name": "Test Elite Trainer Box",   # matches "elite trainer box" in MSRP_TABLE
        "purchase_date": "2026-04-26",
        "purchase_price": 49.99,
        "quantity": 1,
        "retailer": "Target",
    })
    p = invest_store.get_purchase(pid)
    assert p["current_market_value"] is None, "precondition: should start empty"

    mdr._refresh_all(triggered_by="t_sealed_seeds_empty")

    p = invest_store.get_purchase(pid)
    assert p["current_market_value"] == 49.99, (
        f"expected MSRP $49.99 to be seeded; got {p['current_market_value']}"
    )
    assert p["market_value_source"] == "msrp_estimate"


def t_sealed_protects_user_value():
    """v5.9.1 BUG REGRESSION: user-set sealed values must survive an MSRP refresh.

    The original bug: refresh would overwrite a user-entered $99.98 with MSRP $49.99.
    For Pokemon TCG products selling above MSRP, this produced misleading losses.
    """
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "sealed",
        "name": "Test Elite Trainer Box",       # MSRP_TABLE has $49.99 for this
        "purchase_date": "2026-04-26",
        "purchase_price": 59.99,
        "quantity": 2,
        "current_market_value": 99.98,           # user-set, double the MSRP
        "market_value_source": "manual",
    })

    mdr._refresh_all(triggered_by="t_sealed_protects_user_value")

    p = invest_store.get_purchase(pid)
    assert p["current_market_value"] == 99.98, (
        f"BUG REGRESSION (v5.9.1): user value got overwritten to "
        f"${p['current_market_value']}. Sealed MSRP refresh must respect existing values."
    )
    assert p["market_value_source"] == "manual", (
        f"BUG REGRESSION: source got changed to '{p['market_value_source']}'"
    )

    # Verify the KPI still shows positive P/L (the original bug symptom was a fake loss)
    kpi = invest_store.kpi_summary()
    # cost = 59.99 * 2 = 119.98; market = 99.98 * 2 = 199.96; pl% = +66.66
    assert kpi["total_pl"] > 0, f"expected positive P/L, got {kpi['total_pl']}"


def t_sealed_protects_manual_override():
    """If manual_value_override is set, MSRP refresh must not seed current_market_value either."""
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "sealed",
        "name": "Test Elite Trainer Box",
        "purchase_date": "2026-04-26",
        "purchase_price": 50.0,
        "manual_value_override": 250.0,         # user thinks it's worth way more than MSRP
    })

    mdr._refresh_all(triggered_by="t_sealed_protects_manual_override")

    p = invest_store.get_purchase(pid)
    assert p["manual_value_override"] == 250.0, "manual override must be untouched"
    assert p["current_market_value"] is None, (
        f"manual override should fully prevent MSRP seed; "
        f"current_market_value is now {p['current_market_value']}"
    )


def t_sealed_no_msrp_match():
    """Sealed item whose name doesn't match any MSRP_TABLE entry is silently skipped."""
    _wipe_db()
    pid = invest_store.add_purchase({
        "type": "sealed",
        "name": "Some Random Made Up Product XYZ",   # no MSRP_TABLE entry contains this
        "purchase_date": "2026-04-26",
        "purchase_price": 100.0,
    })

    result = mdr._refresh_all(triggered_by="t_sealed_no_msrp_match")

    p = invest_store.get_purchase(pid)
    assert p["current_market_value"] is None, (
        f"no MSRP match should leave value unset; got {p['current_market_value']}"
    )
    assert result["skipped"] >= 1, "refresh should report this row as skipped"


# ── Run all tests ────────────────────────────────────────────────────────────
print("=" * 60)
print("invest_store unit tests (v5.9.1)")
print("=" * 60)
print(f"Sandbox DB: {TEMP_DB}")
print()

run("Schema is idempotent", t_schema_idempotent)
run("CRUD round-trip (sealed)", t_crud_sealed)
run("attrs JSON column round-trips", t_attrs_round_trip)
run("Validation rejects bad type", t_validation_bad_type)
run("Validation rejects bad date", t_validation_bad_date)
run("KPI math correct with mixed valued/unvalued", t_kpi_mixed)
run("KPI returns None when no items valued", t_kpi_all_unvalued)
run("Snapshots accumulate + update current value", t_snapshots_accumulate)
run("Snapshot pruning removes old entries", t_snapshot_prune)
run("Bulk import is partial-failure tolerant", t_bulk_partial_failure)
run("Manual override beats auto value in KPI", t_manual_override_beats_current)
print()
print("--- v5.9.1 regression tests (market_data_refresh sealed branch) ---")
run("Sealed empty value gets seeded by MSRP on refresh", t_sealed_seeds_empty)
run("Sealed user value PROTECTED from MSRP overwrite", t_sealed_protects_user_value)
run("Sealed manual_value_override blocks MSRP seed", t_sealed_protects_manual_override)
run("Sealed without MSRP match is skipped", t_sealed_no_msrp_match)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
print("=" * 60)
total = len(results)
passed = sum(1 for r in results if r[0])
failed = total - passed

if failed == 0:
    print(f"ALL {passed}/{total} TESTS PASSED")
    print("=" * 60)
    # Cleanup
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    sys.exit(0)
else:
    print(f"FAILED: {failed}/{total} tests failed")
    print("Failures:")
    for ok, name, err in results:
        if not ok:
            print(f"  - {name}: {err}")
    print("=" * 60)
    print(f"(Sandbox DB left at {TEMP_DB} for inspection)")
    sys.exit(1)
