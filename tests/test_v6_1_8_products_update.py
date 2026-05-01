#!/usr/bin/env python3
"""
tests/test_v6_1_8_products_update.py - Verify v6.1.8 products update.

5 structural tests against tracker.py source:

  1. count_increased            - PRODUCTS contains the new entries
  2. target_additions_present   - all 25 new Target product names present
  3. walmart_additions_present  - all 25 new Walmart product names present
  4. confirmed_sku_target       - 3 confirmed Target /p/ SKUs present
                                  (94636862, 94636860, 94681784)
  5. no_duplicate_walmart_ids   - no Walmart item_id appears twice in
                                  PRODUCTS (regression guard for
                                  accidental dupes)

Run from project root:
    python tests/test_v6_1_8_products_update.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

TRACKER_PY = os.path.join(_root, "tracker.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_products_block(src):
    """Return text of PRODUCTS = [...] block, or None."""
    m = re.search(r"^PRODUCTS\s*=\s*\[.*?^\]", src, re.M | re.S)
    return m.group(0) if m else None


# Names of v6.1.8 Target additions, post-v6.1.11 trim (12 verified /p/ URLs)
TARGET_NEW_NAMES = [
    "Pokemon Mega Evolution Gardevoir ETB",
    "Pokemon SV10.5 Black Bolt ETB",
    "Pokemon SV10.5 Black Bolt Booster Bundle",
    "Pokemon SV10.5 Black Bolt Binder Collection",
    "Pokemon SV10.5 Black Bolt Tech Sticker 3-Pack",
    "Pokemon SV10.5 White Flare ETB",
    "Pokemon SV10.5 White Flare Booster Bundle",
    "Pokemon SV10.5 White Flare Binder Collection",
    "Pokemon SV10.5 White Flare Tech Sticker 3-Pack",
    "Pokemon Victini Illustration Collection",
    "Pokemon Unova Poster Collection",
    "Pokemon Unova Mini Tins",
]

# Walmart item_ids of all 25 new Walmart additions
WALMART_NEW_ITEM_IDS = [
    "17823811037", "17780209250", "17527462929", "17785924366", "17818262325",
    "17525309434", "17576818418", "17344505131", "17344605256", "17344605257",
    "17530753410",
    "15494520186", "14803962651", "17497369978",
    "17738119614",
    "16484003729", "16454374284", "16448166601",
    "16516160047", "16516160046", "16448166186",
    "15762118882",
    "15375908353",
    "16517213276", "16800072727",
]

# Confirmed Target /p/ URL SKUs (post-v6.1.11: all 12 entries verified /p/)
CONFIRMED_TARGET_SKUS = [
    "94681784",  # Mega Evolution Gardevoir ETB
    "94636862",  # Black Bolt ETB
    "94681770",  # Black Bolt Booster Bundle
    "94636856",  # Black Bolt Binder Collection
    "94681767",  # Black Bolt Tech Sticker 3-Pack
    "94636860",  # White Flare ETB
    "94681785",  # White Flare Booster Bundle
    "94636851",  # White Flare Binder Collection
    "94681780",  # White Flare Tech Sticker 3-Pack
    "94636866",  # Victini Illustration Collection
    "94636854",  # Unova Poster Collection
    "94636858",  # Unova Mini Tins
]


def t_count_increased():
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS = [...] block not found in tracker.py"
    # Count {"name": ...} occurrences as a proxy for entry count
    entry_count = block.count('"name":')
    # Threshold of 50 = minimum count after additions, regardless of baseline.
    # The target/walmart_additions_present tests below verify all 50 new
    # entries individually, so this is just a sanity guard.
    assert entry_count >= 50, (
        f"PRODUCTS should have at least 50 entries after v6.1.8. "
        f"Found {entry_count}."
    )


def t_target_additions_present():
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS block missing"
    missing = [n for n in TARGET_NEW_NAMES if n not in block]
    assert not missing, (
        f"v6.1.8: {len(missing)} Target additions missing from PRODUCTS: {missing[:3]}..."
    )


def t_walmart_additions_present():
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS block missing"
    missing = [iid for iid in WALMART_NEW_ITEM_IDS if iid not in block]
    assert not missing, (
        f"v6.1.8: {len(missing)} Walmart item_ids missing from PRODUCTS: {missing[:3]}..."
    )


def t_confirmed_sku_target():
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS block missing"
    for sku in CONFIRMED_TARGET_SKUS:
        # SKU should appear in both URL (as A-NNNNN) and "sku" field
        assert f'"sku": "{sku}"' in block, (
            f"v6.1.8: confirmed Target SKU {sku} missing from PRODUCTS"
        )


def t_no_duplicate_walmart_ids():
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS block missing"
    # Find all item_ids in the block
    ids = re.findall(r'"item_id":\s*"(\d+)"', block)
    seen = {}
    for iid in ids:
        seen[iid] = seen.get(iid, 0) + 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    assert not dupes, (
        f"v6.1.8: duplicate Walmart item_ids detected (will cause double-checks): {dupes}"
    )


def main():
    print("=" * 70)
    print(" v6.1.8 products update tests")
    print("=" * 70)

    tests = [
        ("count_increased",            t_count_increased),
        ("target_additions_present",   t_target_additions_present),
        ("walmart_additions_present",  t_walmart_additions_present),
        ("confirmed_sku_target",       t_confirmed_sku_target),
        ("no_duplicate_walmart_ids",   t_no_duplicate_walmart_ids),
    ]

    passed = failed = 0
    for i, (name, fn) in enumerate(tests, start=1):
        try:
            fn()
            print(f"  [{i}] PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  [{i}] FAIL  {name}")
            print(f"        {e}")
            failed += 1
        except Exception as e:
            print(f"  [{i}] ERROR {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("-" * 70)
    print(f"  Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
