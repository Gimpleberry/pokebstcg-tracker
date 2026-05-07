"""
test_v6_1_18_lifecycle_migration.py

Structural + introspection tests for v6.1.18 — Batch 1 phased-lifecycle
migration of NewsScraper_Plugin, StoreInventory_Plugin, AltRetailer_Plugin.

Tests use the `def t_*` convention. Each test raises AssertionError on
failure. Self-runner emits `OK: N/N tests passed` on success.

Module loaded via importlib.util.spec_from_file_location to avoid
`import plugins` pathology on Windows + Py3.14 (per the v6.1.15 lesson).
"""

import importlib.util
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PLUGINS_PATH = os.path.join(_ROOT, "plugins.py")


def _read():
    with open(PLUGINS_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_plugins_module():
    """Import plugins.py via spec_from_file_location.
    plugins.py only imports stdlib at module level (logging, importlib,
    sys, os) — plugin-specific imports happen lazily inside method bodies.
    So this load succeeds without any inner-module stubs."""
    spec = importlib.util.spec_from_file_location(
        "plugins_under_test", PLUGINS_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- Structural tests (text searches) --------------------------------------

def t_three_legacy_start_methods_removed():
    """The 3 plugins migrated by v6.1.18 (NewsScraper, StoreInventory,
    AltRetailer) lose their start() methods.

    v6.1.19 rewrite: assert the specific properties of v6.1.18's victims,
    not a global count. The original test compared count to 8 (post-v6.1.18
    expected); v6.1.19 makes it 6, v6.1.20+ would make it 5/4/etc. — a
    drift trap. This rewrite is stable across all future batches because
    it pins to v6.1.18's specific changes.
    """
    src = _read()
    for cls_name in ("NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin"):
        cls_idx = src.find(f"class {cls_name}(Plugin):")
        assert cls_idx > 0, f"{cls_name} class not found"
        next_class = src.find("\nclass ", cls_idx + 1)
        cls_body = src[cls_idx:next_class] if next_class > 0 else src[cls_idx:]
        assert "def start(self, config, products, schedule)" not in cls_body, (
            f"{cls_name} still defines legacy start() — v6.1.18 migration regressed"
        )


def t_news_scraper_cadence_present():
    src = _read()
    assert 'cadence="daily 06:00"' in src, (
        '"daily 06:00" cadence missing from NewsScraper migration'
    )


def t_store_inventory_cadence_present():
    src = _read()
    assert 'cadence="daily 08:00"' in src, (
        '"daily 08:00" cadence missing from StoreInventory migration'
    )


def t_alt_retailer_weekly_cadences_present():
    """v6.1.18.1: scheduler regex requires 3-letter day abbreviations."""
    src = _read()
    assert 'cadence="weekly tue 09:00"' in src, (
        '"weekly tue 09:00" cadence missing (v6.1.18.1: short-day form required)'
    )
    assert 'cadence="weekly fri 09:00"' in src, (
        '"weekly fri 09:00" cadence missing (v6.1.18.1: short-day form required)'
    )


def t_cadence_strings_parse_correctly():
    """v6.1.18.1 regression test — ensure every cadence="..." string in
    plugins.py matches one of scheduler.py's four regex patterns.

    This test would have caught the v6.1.18 bug where 'weekly tuesday'
    didn't match _RE_WEEKLY which requires 3-letter day abbreviations.
    The structural tests in v6.1.18 only asserted text-presence; they
    didn't validate against the scheduler's actual contract.
    """
    src = _read()
    # Mirror scheduler.py's four cadence regexes EXACTLY (lines 70-76).
    RE_DAILY     = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
    RE_WEEKLY    = re.compile(
        r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$",
        re.IGNORECASE,
    )
    RE_EVERY_MIN = re.compile(r"^every\s+(\d+)\s+minutes?$", re.IGNORECASE)
    RE_EVERY_HR  = re.compile(r"^every\s+(\d+)\s+hours?$", re.IGNORECASE)

    bad = []
    for m in re.finditer(r'cadence="([^"]+)"', src):
        cadence = m.group(1)
        if not (RE_DAILY.match(cadence) or RE_WEEKLY.match(cadence)
                or RE_EVERY_MIN.match(cadence) or RE_EVERY_HR.match(cadence)):
            bad.append(cadence)
    assert not bad, (
        f"cadence string(s) don't match any scheduler.py regex: {bad}"
    )


def t_news_scraper_kickoff_preserved_via_kickoff_param():
    """Q answer (a): NewsScraper preserves boot-time scrape via
    kickoff=True, kickoff_delay=60."""
    src = _read()
    # NewsScraper is the ONLY new migration with kickoff in this batch
    # (StoreInventory and AltRetailer don't get kickoff per the design).
    # Find "news_scraper.run_news_scrape" register_job and verify kickoff
    # within ~400 chars window.
    idx = src.find('name="news_scraper.run_news_scrape"')
    assert idx > 0, "news_scraper.run_news_scrape register_job not found"
    window = src[idx:idx + 400]
    assert "kickoff=True" in window, "kickoff=True missing from NewsScraper"
    assert "kickoff_delay=60" in window, (
        "kickoff_delay=60 missing from NewsScraper (Q answer was 'a')"
    )


def t_register_job_names_present():
    """All 4 new register_job name= strings present in source."""
    src = _read()
    expected = [
        '"news_scraper.run_news_scrape"',
        '"store_inventory.run_store_check"',
        '"alt_retailer.run_tuesday"',
        '"alt_retailer.run_friday"',
    ]
    missing = [n for n in expected if n not in src]
    assert not missing, f"missing register_job names: {missing}"


def t_versions_bumped():
    """All 3 migrated plugins bumped to version 1.1."""
    src = _read()
    # Each appears once in plugins.py — find the wrapper class, then
    # check version line within the next 200 chars.
    for cls_name in ("NewsScraper_Plugin", "StoreInventory_Plugin",
                     "AltRetailer_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        assert idx > 0, f"{cls_name} class not found"
        window = src[idx:idx + 200]
        assert 'version = "1.1"' in window, (
            f"{cls_name} version not bumped to 1.1 in window: {window[:200]!r}"
        )


def t_existing_migrated_classes_unchanged():
    """Sanity: BBI/Amazon/Costco/WalmartPlaywright still on phased lifecycle."""
    src = _read()
    for cls_name in ("BestBuyInvites_Plugin", "AmazonMonitor_Plugin",
                     "CostcoTracker_Plugin", "WalmartPlaywright_Plugin"):
        idx = src.find(f"class {cls_name}(Plugin):")
        assert idx > 0, f"{cls_name} class not found"
        # next ~1500 chars should contain `def init` and `def register`
        window = src[idx:idx + 1500]
        assert "def init(self, config, products)" in window, (
            f"{cls_name} init() missing — pre-existing phased plugin disturbed"
        )
        assert "def register(self, scheduler)" in window, (
            f"{cls_name} register() missing — pre-existing phased plugin disturbed"
        )


# -- Introspection tests (load module + check overrides) -------------------

def _check_phased_lifecycle(cls_name):
    """Helper: assert a plugin class is on the phased lifecycle.
    Phased = overrides init() AND register(), does NOT override start()."""
    mod = _load_plugins_module()
    cls = getattr(mod, cls_name)
    base = mod.Plugin
    assert cls.init is not base.init, f"{cls_name}.init() not overridden"
    assert cls.register is not base.register, f"{cls_name}.register() not overridden"
    assert cls.start is base.start, (
        f"{cls_name} still overrides start() — should have been removed"
    )


def t_news_scraper_phased_lifecycle():
    _check_phased_lifecycle("NewsScraper_Plugin")


def t_store_inventory_phased_lifecycle():
    _check_phased_lifecycle("StoreInventory_Plugin")


def t_alt_retailer_phased_lifecycle():
    _check_phased_lifecycle("AltRetailer_Plugin")


# -- Self-runner ----------------------------------------------------------

if __name__ == "__main__":
    tests = sorted(
        [n for n in dir() if n.startswith("t_") and callable(globals()[n])]
    )
    passed = 0
    failed = []
    for name in tests:
        try:
            globals()[name]()
            passed += 1
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))

    if failed:
        print(f"FAIL: {len(failed)}/{len(tests)} test(s) failed")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print(f"OK: {passed}/{len(tests)} tests passed")
        sys.exit(0)
