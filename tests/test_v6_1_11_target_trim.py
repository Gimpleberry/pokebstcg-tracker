#!/usr/bin/env python3
"""
tests/test_v6_1_11_target_trim.py - Verify v6.1.11 Target trim.

5 structural tests against tracker.py source:

  1. all_12_v6_1_11_names_present       - 12 verified /p/ names in PRODUCTS
  2. all_12_confirmed_skus_present      - 12 confirmed /p/ SKUs in PRODUCTS
  3. dropped_names_absent_from_target   - dropped names NOT in v6.1.8 Target
                                          section (may exist as walmart entries)
  4. no_v6_1_8_target_uses_search_url   - URL pattern compliance check
  5. count_decreased_to_69              - PRODUCTS count is exactly 69

Run from project root:
    python tests/test_v6_1_11_target_trim.py
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
    m = re.search(r"^PRODUCTS\s*=\s*\[.*?^\]", src, re.M | re.S)
    return m.group(0) if m else None


def _extract_v6_1_8_target_section(src):
    """
    Return the substring from "v6.1.8 ADDITIONS - TARGET" header to
    "v6.1.8 ADDITIONS - WALMART" header (exclusive). Used to verify
    target-side state without colliding with same-named walmart entries.
    """
    start_idx = src.find("v6.1.8 ADDITIONS - TARGET")
    end_idx = src.find("v6.1.8 ADDITIONS - WALMART", start_idx)
    if start_idx == -1 or end_idx == -1:
        return None
    return src[start_idx:end_idx]


# 12 names that MUST be present post-v6.1.11
KEPT_NAMES = [
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

# 12 confirmed Target /p/ SKUs that MUST be present (in "sku" field)
KEPT_SKUS = [
    "94681784", "94636862", "94681770", "94636856", "94681767",
    "94636860", "94681785", "94636851", "94681780",
    "94636866", "94636854", "94636858",
]

# Names that MUST be absent FROM THE v6.1.8 TARGET SECTION.
# Many of these legitimately exist as v6.1.8 Walmart entries — that's fine.
# This test only ensures they were removed from the Target side.
DROPPED_OR_RENAMED_NAMES = [
    # 13 truly dropped (no /p/ URL on Target)
    "Pokemon Phantasmal Flames Mega Charizard UPC",
    "Pokemon Phantasmal Flames Mini Tins",
    "Pokemon Phantasmal Flames Booster Bundle",
    "Pokemon Phantasmal Flames 3-Pack Blister",
    "Pokemon Mega Evolution Lucario ETB",
    "Pokemon Mega Evolution Booster Box 36pk",
    "Pokemon Mega Evolution Sleeved Booster",
    "Pokemon Mega Evolution Mini Tin Display 10pk",
    "Pokemon Prismatic Evolutions SPC",
    "Pokemon Prismatic Evolutions Figure Collection",
    "Pokemon Paldean Fates Great Tusk Iron Threads Premium",
    "Pokemon SV10 Destined Rivals ETB",
    "Pokemon SV10 Destined Rivals Booster Box 36pk",
    "Pokemon 151 Booster Bundle 2-Pack",
    # 4 renamed-from (replaced by Tech Sticker 3-Pack / Mini Tins)
    "Pokemon Unova Mini Tin Random",
    "Pokemon SV10.5 Black Bolt Sticker Collection",
    "Pokemon SV10.5 White Flare Sticker Collection",
]


def t_all_12_v6_1_11_names_present():
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS block missing"
    missing = [n for n in KEPT_NAMES if n not in block]
    assert not missing, (
        f"v6.1.11: {len(missing)} verified Target names missing: {missing}"
    )


def t_all_12_confirmed_skus_present():
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS block missing"
    for sku in KEPT_SKUS:
        needle = '"sku": "' + sku + '"'
        assert needle in block, (
            f"v6.1.11: confirmed Target SKU {sku} missing from PRODUCTS"
        )


def t_dropped_names_absent_from_target():
    """
    Scoped to the v6.1.8 Target section. Names may legitimately exist as
    v6.1.8 Walmart entries (same product, different retailer) — that's
    expected. We only require the Target side has been trimmed.
    """
    src = _read(TRACKER_PY)
    section = _extract_v6_1_8_target_section(src)
    assert section is not None, (
        "v6.1.8 Target/Walmart section markers not found - patch may not "
        "have applied"
    )
    still_present = [n for n in DROPPED_OR_RENAMED_NAMES if n in section]
    assert not still_present, (
        f"v6.1.11: {len(still_present)} dropped/renamed names still in "
        f"v6.1.8 Target section: {still_present}"
    )


def t_no_v6_1_8_target_uses_search_url():
    """
    URL pattern compliance: no Target product added in v6.1.8 should use
    a /s/ search URL (those don't expose price JSON).
    """
    src = _read(TRACKER_PY)
    section = _extract_v6_1_8_target_section(src)
    assert section is not None, (
        "v6.1.8 Target section markers not found"
    )
    search_urls = re.findall(r'https://www\.target\.com/s/[^"]+', section)
    assert not search_urls, (
        f"v6.1.11: {len(search_urls)} Target /s/ URLs still present in v6.1.8 "
        f"section (should be 0): {search_urls[:3]}"
    )


def t_count_decreased_to_69():
    """
    Pre-v6.1.8:   32 products
    Post-v6.1.8:  82 products (+25 Target +25 Walmart)
    Post-v6.1.11: 69 products (-13 Target dropped)
    """
    src = _read(TRACKER_PY)
    block = _extract_products_block(src)
    assert block is not None, "PRODUCTS block missing"
    entry_count = block.count('"name":')
    assert entry_count == 69, (
        f"v6.1.11: PRODUCTS should have exactly 69 entries post-trim. "
        f"Found {entry_count}."
    )


def main():
    print("=" * 70)
    print(" v6.1.11 Target trim tests")
    print("=" * 70)

    tests = [
        ("all_12_v6_1_11_names_present",     t_all_12_v6_1_11_names_present),
        ("all_12_confirmed_skus_present",    t_all_12_confirmed_skus_present),
        ("dropped_names_absent_from_target", t_dropped_names_absent_from_target),
        ("no_v6_1_8_target_uses_search_url", t_no_v6_1_8_target_uses_search_url),
        ("count_decreased_to_69",            t_count_decreased_to_69),
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
