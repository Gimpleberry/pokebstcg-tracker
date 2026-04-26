#!/usr/bin/env python3
"""
tests/test_plugin_lifecycle.py - Verify plugins.py phased boot (v6.0.0 step 2)

Runs 7 checks against an isolated plugin coordinator using mock plugins.
Does NOT load any real plugins (those exercise external services and aren't
appropriate for a unit test).

  1.  legacy_start_called_when_only_start_is_overridden
        A plugin that overrides only start() goes through the back-compat
        shim. Verified for both modes (Scheduler passed AND raw schedule lib).

  2.  new_lifecycle_calls_init_then_register
        A plugin overriding init() and register() has both called in order
        (init first, register second). start() must NOT be called.

  3.  init_phase_receives_config_and_products
        init(config, products) gets exactly those two args (not a scheduler).

  4.  register_phase_receives_scheduler
        register(scheduler) gets the actual Scheduler instance, not the
        underlying schedule library.

  5.  legacy_used_when_only_schedule_lib_passed
        Even if a plugin overrides register(), if tracker.py passes a raw
        schedule lib (pre-Step-3 state), the plugin falls back to start()
        via the back-compat shim. This is what makes Step 2 ship-safe.

  6.  one_plugin_failure_does_not_block_others
        If one plugin's init() or register() raises, the others still load.
        The failing plugin is logged but excluded from _loaded_plugins.

  7.  loaded_plugins_returned_in_enabled_order
        load_plugins() returns instances in the same order as ENABLED_PLUGINS,
        skipping any that failed.

Exit code 0 = all 7 pass. Non-zero = at least one failed.

Run from project root:
    python tests/test_plugin_lifecycle.py
"""

from __future__ import annotations

import os
import sys
import traceback
from unittest.mock import MagicMock

# Path resolution — works whether run from project root or tests/ folder
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here
if _root not in sys.path:
    sys.path.insert(0, _root)

import plugins as plugin_system    # noqa: E402
from plugins import Plugin         # noqa: E402
from scheduler import Scheduler    # noqa: E402

# Use the sandbox stub locally; real schedule lib on the user's machine.
try:
    import schedule
    _schedule_lib = schedule
    _SCHEDULE_SOURCE = "real"
except ImportError:
    import _schedule_stub as _schedule_lib    # type: ignore
    _SCHEDULE_SOURCE = "stub"


# ─────────────────────────────────────────────────────────────────────────────
# TEST INFRASTRUCTURE
# ─────────────────────────────────────────────────────────────────────────────
class _CallRecorder:
    """Records which lifecycle methods got called on a plugin, in order."""
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def record(self, method_name: str, *args, **kwargs):
        self.calls.append((method_name, args, kwargs))

    def methods_called(self) -> list[str]:
        return [c[0] for c in self.calls]

    def args_for(self, method_name: str) -> tuple:
        for name, args, _ in self.calls:
            if name == method_name:
                return args
        raise KeyError(f"method {method_name!r} was never called")


def _make_legacy_plugin(name: str, recorder: _CallRecorder, fail_in: str | None = None):
    """A plugin that overrides ONLY start() — pre-v6.0.0 style."""
    class LegacyPlugin(Plugin):
        pass
    LegacyPlugin.name = name
    LegacyPlugin.version = "test"

    def start(self, config, products, schedule):
        recorder.record("start", config, products, schedule)
        if fail_in == "start":
            raise RuntimeError(f"forced fail in {name}.start")
    LegacyPlugin.start = start
    return LegacyPlugin


def _make_new_plugin(name: str, recorder: _CallRecorder, fail_in: str | None = None):
    """A plugin that overrides init() + register() — v6.0.0 style."""
    class NewPlugin(Plugin):
        pass
    NewPlugin.name = name
    NewPlugin.version = "test"

    def init(self, config, products):
        recorder.record("init", config, products)
        if fail_in == "init":
            raise RuntimeError(f"forced fail in {name}.init")
    def register(self, scheduler):
        recorder.record("register", scheduler)
        if fail_in == "register":
            raise RuntimeError(f"forced fail in {name}.register")

    NewPlugin.init = init
    NewPlugin.register = register
    return NewPlugin


def _isolate_plugin_system(plugin_classes: dict, enabled_ids: list[str]):
    """
    Swap plugin_system._PLUGIN_CLASSES + ENABLED_PLUGINS for the duration
    of a test. Returns (restore_fn) — call it to put the originals back.
    """
    orig_registry = plugin_system._PLUGIN_CLASSES
    orig_enabled  = plugin_system.ENABLED_PLUGINS
    plugin_system._PLUGIN_CLASSES = plugin_classes
    plugin_system.ENABLED_PLUGINS = enabled_ids

    def restore():
        plugin_system._PLUGIN_CLASSES = orig_registry
        plugin_system.ENABLED_PLUGINS = orig_enabled
    return restore


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

def t_legacy_start_called_when_only_start_is_overridden():
    rec = _CallRecorder()
    LP = _make_legacy_plugin("legacy_p", rec)
    restore = _isolate_plugin_system({"legacy_pid": LP}, ["legacy_pid"])
    try:
        # Mode A: pass a Scheduler instance
        scheduler = Scheduler(_schedule_lib)
        loaded = plugin_system.load_plugins({"k":"v"}, [{"id":1}], scheduler)
        assert len(loaded) == 1, f"expected 1 plugin loaded, got {len(loaded)}"
        assert "start" in rec.methods_called(), (
            f"expected start() to be called via shim; got {rec.methods_called()}"
        )
        # Verify start received the underlying schedule lib (not the Scheduler)
        args = rec.args_for("start")
        assert args[2] is _schedule_lib, (
            f"start() should receive the raw schedule lib, got {type(args[2]).__name__}"
        )
    finally:
        restore()

    rec2 = _CallRecorder()
    LP2 = _make_legacy_plugin("legacy_p", rec2)
    restore2 = _isolate_plugin_system({"legacy_pid": LP2}, ["legacy_pid"])
    try:
        # Mode B: pass the raw schedule lib (pre-Step-3 tracker.py state)
        loaded = plugin_system.load_plugins({"k":"v"}, [], _schedule_lib)
        assert len(loaded) == 1
        assert rec2.methods_called() == ["start"], (
            f"expected ['start'], got {rec2.methods_called()}"
        )
    finally:
        restore2()


def t_new_lifecycle_calls_init_then_register():
    rec = _CallRecorder()
    NP = _make_new_plugin("new_p", rec)
    restore = _isolate_plugin_system({"new_pid": NP}, ["new_pid"])
    try:
        scheduler = Scheduler(_schedule_lib)
        loaded = plugin_system.load_plugins({}, [], scheduler)
        assert len(loaded) == 1
        # Order matters: init MUST come before register
        called = rec.methods_called()
        assert called == ["init", "register"], (
            f"expected ['init', 'register'] in order, got {called}"
        )
        # start() must NOT have been called for a new-style plugin
        assert "start" not in called, "start() should not run when register() is overridden"
    finally:
        restore()


def t_init_phase_receives_config_and_products():
    rec = _CallRecorder()
    NP = _make_new_plugin("new_p", rec)
    restore = _isolate_plugin_system({"new_pid": NP}, ["new_pid"])
    try:
        scheduler = Scheduler(_schedule_lib)
        config_obj = {"ntfy_topic": "test"}
        products_obj = [{"name": "thing", "url": "x"}]
        plugin_system.load_plugins(config_obj, products_obj, scheduler)

        init_args = rec.args_for("init")
        assert len(init_args) == 2, f"init should get 2 args, got {len(init_args)}"
        assert init_args[0] is config_obj,   "init should receive config dict"
        assert init_args[1] is products_obj, "init should receive products list"
    finally:
        restore()


def t_register_phase_receives_scheduler():
    rec = _CallRecorder()
    NP = _make_new_plugin("new_p", rec)
    restore = _isolate_plugin_system({"new_pid": NP}, ["new_pid"])
    try:
        scheduler = Scheduler(_schedule_lib)
        plugin_system.load_plugins({}, [], scheduler)

        reg_args = rec.args_for("register")
        assert len(reg_args) == 1
        assert reg_args[0] is scheduler, (
            f"register should receive the Scheduler instance, "
            f"got {type(reg_args[0]).__name__}"
        )
    finally:
        restore()


def t_legacy_used_when_only_schedule_lib_passed():
    """
    Even a plugin overriding register() falls back to start() if the caller
    passed a raw schedule library instead of a Scheduler. This is the
    Step-2-safe behavior — prevents bricking before Step 3 wires tracker.py.
    """
    rec = _CallRecorder()

    # Plugin overrides BOTH start AND register, so it works either way
    class HybridPlugin(Plugin):
        name = "hybrid"
        version = "test"
        def start(self, config, products, schedule):
            rec.record("start", config, products, schedule)
        def register(self, scheduler):
            rec.record("register", scheduler)

    restore = _isolate_plugin_system({"hybrid_pid": HybridPlugin}, ["hybrid_pid"])
    try:
        # Pass raw schedule lib — register() exists but no Scheduler available
        plugin_system.load_plugins({}, [], _schedule_lib)
        called = rec.methods_called()
        assert called == ["start"], (
            f"expected fallback to ['start'] when no Scheduler passed, got {called}"
        )
    finally:
        restore()


def t_one_plugin_failure_does_not_block_others():
    rec_a = _CallRecorder()
    rec_b = _CallRecorder()
    rec_c = _CallRecorder()

    PA = _make_legacy_plugin("plugin_a", rec_a)                       # OK
    PB = _make_legacy_plugin("plugin_b", rec_b, fail_in="start")      # raises
    PC = _make_legacy_plugin("plugin_c", rec_c)                       # OK

    restore = _isolate_plugin_system(
        {"a_pid": PA, "b_pid": PB, "c_pid": PC},
        ["a_pid", "b_pid", "c_pid"],
    )
    try:
        scheduler = Scheduler(_schedule_lib)
        loaded = plugin_system.load_plugins({}, [], scheduler)
        loaded_names = [p.name for p in loaded]
        # plugin_b should have failed; a and c should have loaded
        assert "plugin_a" in loaded_names, "plugin_a should have loaded"
        assert "plugin_c" in loaded_names, "plugin_c should have loaded despite b failure"
        assert "plugin_b" not in loaded_names, "plugin_b should be excluded after failure"
        assert len(loaded) == 2, f"expected 2 loaded, got {len(loaded)}"
    finally:
        restore()


def t_loaded_plugins_returned_in_enabled_order():
    rec_x = _CallRecorder(); rec_y = _CallRecorder(); rec_z = _CallRecorder()
    PX = _make_legacy_plugin("plug_x", rec_x)
    PY = _make_legacy_plugin("plug_y", rec_y)
    PZ = _make_legacy_plugin("plug_z", rec_z)

    # Deliberate order Z, X, Y to verify it's preserved
    restore = _isolate_plugin_system(
        {"x_pid": PX, "y_pid": PY, "z_pid": PZ},
        ["z_pid", "x_pid", "y_pid"],
    )
    try:
        scheduler = Scheduler(_schedule_lib)
        loaded = plugin_system.load_plugins({}, [], scheduler)
        names_in_order = [p.name for p in loaded]
        assert names_in_order == ["plug_z", "plug_x", "plug_y"], (
            f"expected order [z, x, y], got {names_in_order}"
        )
    finally:
        restore()


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(f" v6.0.0 plugins.py phased boot tests  (using {_SCHEDULE_SOURCE} schedule lib)")
    print("=" * 70)

    tests = [
        ("legacy_start_called_when_only_start_is_overridden",
            t_legacy_start_called_when_only_start_is_overridden),
        ("new_lifecycle_calls_init_then_register",
            t_new_lifecycle_calls_init_then_register),
        ("init_phase_receives_config_and_products",
            t_init_phase_receives_config_and_products),
        ("register_phase_receives_scheduler",
            t_register_phase_receives_scheduler),
        ("legacy_used_when_only_schedule_lib_passed",
            t_legacy_used_when_only_schedule_lib_passed),
        ("one_plugin_failure_does_not_block_others",
            t_one_plugin_failure_does_not_block_others),
        ("loaded_plugins_returned_in_enabled_order",
            t_loaded_plugins_returned_in_enabled_order),
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
