#!/usr/bin/env python3
"""
tests/test_boot_stall_regression.py - Lock in v6.0.0 boot speed contract

This is a STRUCTURAL regression test for the architectural patterns that
produced the v6.0.0 boot speedup (1:43 -> 0:02, 44x faster). Future
changes that accidentally re-introduce synchronous I/O during plugin
loading will fail these tests immediately.

Background:
    Pre-v6.0.0, several plugins called their _check_all() method
    synchronously inside their start(schedule) lifecycle method. This
    caused plugin loading to block for up to 90 seconds per plugin while
    Playwright sessions completed initial product checks. The cumulative
    effect was a 1:43 boot time on Keith's machine.

    Steps 4, 5, and 6 of the v6.0.0 migration moved bestbuy_invites,
    amazon_monitor, and costco_tracker to a phased lifecycle:
      Phase 1: init(config, products)        - instantiate only, no I/O
      Phase 2: register(scheduler)            - declare schedule, no I/O
      Phase 3: scheduler.boot_ready()         - dispatch staggered kickoffs

    The initial check now fires asynchronously T+30/90/150 seconds AFTER
    the tracker's main loop is already running. Boot dropped to 2.3s.

What these tests enforce:

  1.  init_methods_avoid_blocking_calls
        For every plugin class with a def init(...) method, the body must
        NOT contain calls to .start( / ._check_all( / ._check_all_online(
        / ._check_all_products( etc. - these are the patterns that
        historically blocked plugin loading.

  2.  register_methods_are_simple_delegation
        For every plugin class with a def register(...) method, the body
        must be lightweight: instantiation guard, try/except, single
        delegation call to monitor/tracker register, and logging. No
        scheduler.every(), no direct check calls, no network/disk I/O.

  3.  monitor_classes_expose_register_method
        For each plugin currently using the new lifecycle (currently:
        bestbuy_invites, amazon_monitor, costco_tracker), the underlying
        monitor module MUST expose def register(self, scheduler). Without
        this, plugins.py's register() phase would fall through silently
        and the kickoff would never be queued.

  4.  scheduler_register_job_supports_kickoff
        scheduler.py's Scheduler.register_job() must accept kickoff=bool
        and kickoff_delay=int parameters. Without these, plugins can't
        defer their initial check to T+N seconds and we're back to
        synchronous blocking.

Exit code 0 = all 4 pass.

Run from project root:
    python tests/test_boot_stall_regression.py
"""

from __future__ import annotations

import os
import re
import sys
import inspect
import traceback

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here) if os.path.basename(_here) == "tests" else _here

PLUGINS_PATH       = os.path.join(_root, "plugins.py")
SCHEDULER_PATH     = os.path.join(_root, "scheduler.py")
BESTBUY_PATH       = os.path.join(_root, "plugins", "bestbuy_invites.py")
AMAZON_PATH        = os.path.join(_root, "plugins", "amazon_monitor.py")
COSTCO_PATH        = os.path.join(_root, "plugins", "costco_tracker.py")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_method_body(src: str, class_name: str, method_name: str) -> str | None:
    """
    Find `def method_name(` inside `class class_name(` and return the
    method body as a string. Returns None if not found.

    Naive but effective: walks line by line, tracks indent level, ends
    when we hit a line at or shallower than the method's def indent.
    """
    # Match `class Foo(Base):` OR `class Foo:` (no parens)
    cls_match = re.search(rf"^class\s+{re.escape(class_name)}\b", src, re.MULTILINE)
    if not cls_match:
        return None
    cls_start = cls_match.end()
    # Limit search to within this class (until next class declaration)
    next_cls = re.search(r"\nclass\s+\w+", src[cls_start:])
    cls_block = src[cls_start:cls_start + next_cls.start()] if next_cls else src[cls_start:]

    # Find the method definition
    method_pattern = re.compile(
        rf"^(\s+)def\s+{re.escape(method_name)}\s*\(",
        re.MULTILINE,
    )
    method_match = method_pattern.search(cls_block)
    if not method_match:
        return None

    method_indent = len(method_match.group(1))
    method_start = method_match.end()
    # The body is everything until we hit a line at <= method_indent that
    # starts with non-whitespace (next method or end of class)
    lines = cls_block[method_start:].split("\n")
    body_lines = []
    skipped_signature = False
    for line in lines:
        if not skipped_signature:
            # Skip until we close the signature paren block
            body_lines.append(line)
            if ")" in line and ":" in line:
                skipped_signature = True
            continue
        if line.strip() == "":
            body_lines.append(line)
            continue
        # Lines at <= method_indent that aren't blank end the method
        leading = len(line) - len(line.lstrip())
        if leading <= method_indent and line.strip():
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _find_classes_with_method(src: str, method_name: str) -> list[str]:
    """Return names of all classes in src that define `def method_name(`."""
    result = []
    for cls_match in re.finditer(r"^class\s+(\w+)\s*\(", src, re.MULTILINE):
        cls_name = cls_match.group(1)
        cls_start = cls_match.end()
        next_cls = re.search(r"\nclass\s+\w+", src[cls_start:])
        cls_end = cls_start + next_cls.start() if next_cls else len(src)
        cls_block = src[cls_start:cls_end]
        # Don't match the base Plugin class's no-op stubs
        if cls_name == "Plugin":
            continue
        if re.search(rf"^\s+def\s+{re.escape(method_name)}\s*\(", cls_block, re.MULTILINE):
            result.append(cls_name)
    return result


# -----------------------------------------------------------------------------
# TESTS
# -----------------------------------------------------------------------------

# Patterns that historically caused boot stalls. If any of these appear
# in a plugin's init() body, they probably re-introduce synchronous I/O.
FORBIDDEN_INIT_PATTERNS = [
    r"\._monitor\.start\s*\(",
    r"\._tracker\.start\s*\(",
    r"\._reminder\.start\s*\(",
    r"\._check_all\s*\(",
    r"\._check_all_online\s*\(",
    r"\._check_all_products\s*\(",
    r"\.run\(",
]


def t_init_methods_avoid_blocking_calls():
    """Phased plugin init() bodies must not contain synchronous I/O patterns."""
    src = _read(PLUGINS_PATH)
    classes_with_init = _find_classes_with_method(src, "init")

    assert classes_with_init, (
        "No plugin classes define def init(...) - the v6.0.0 phased lifecycle "
        "is missing entirely. Either steps 4/5/6 were rolled back or this "
        "test is reading the wrong plugins.py."
    )

    failures = []
    for cls_name in classes_with_init:
        body = _extract_method_body(src, cls_name, "init")
        if body is None:
            failures.append(f"{cls_name}: could not extract init() body")
            continue
        for pattern in FORBIDDEN_INIT_PATTERNS:
            if re.search(pattern, body):
                failures.append(
                    f"{cls_name}.init() contains forbidden pattern {pattern!r} - "
                    f"this will block plugin loading and re-introduce the boot "
                    f"stall fixed by v6.0.0 step 4/5/6."
                )

    assert not failures, "Boot-stall regression detected:\n  " + "\n  ".join(failures)


def t_register_methods_are_simple_delegation():
    """Phased plugin register() bodies should only delegate + log."""
    src = _read(PLUGINS_PATH)
    classes_with_register = _find_classes_with_method(src, "register")

    assert classes_with_register, (
        "No plugin classes define def register(...) - the v6.0.0 phased "
        "lifecycle is missing entirely."
    )

    # Allowed building blocks in register() bodies
    failures = []
    for cls_name in classes_with_register:
        body = _extract_method_body(src, cls_name, "register")
        if body is None:
            failures.append(f"{cls_name}: could not extract register() body")
            continue
        # Forbidden: directly calling .every() or do() (would mean we
        # bypassed the Scheduler and went back to legacy schedule lib)
        for pattern in [r"\.every\(", r"\.do\("]:
            if re.search(pattern, body):
                failures.append(
                    f"{cls_name}.register() uses legacy schedule API "
                    f"(matched {pattern!r}) - should call "
                    f"self._monitor.register(scheduler) instead."
                )
        # Forbidden: synchronous check calls
        for pattern in [
            r"\._check_all\s*\(",
            r"\._check_all_online\s*\(",
            r"\._check_all_products\s*\(",
        ]:
            if re.search(pattern, body):
                failures.append(
                    f"{cls_name}.register() calls a synchronous _check method "
                    f"({pattern!r}) - this re-introduces the boot stall. "
                    f"Use scheduler.register_job(..., kickoff=True, "
                    f"kickoff_delay=N) instead."
                )

    assert not failures, "register() body regressions:\n  " + "\n  ".join(failures)


def t_monitor_classes_expose_register_method():
    """Each migrated plugin's underlying monitor module must expose register(scheduler)."""
    expected = {
        "plugins/bestbuy_invites.py": BESTBUY_PATH,
        "plugins/amazon_monitor.py":   AMAZON_PATH,
        "plugins/costco_tracker.py":   COSTCO_PATH,
    }
    failures = []
    for label, path in expected.items():
        if not os.path.exists(path):
            failures.append(f"{label}: file not found")
            continue
        src = _read(path)
        if "def register(self, scheduler)" not in src:
            failures.append(
                f"{label}: missing `def register(self, scheduler)` method. "
                f"plugins.py's phased lifecycle expects monitor classes "
                f"to expose register() - without it the kickoff is never queued."
            )
    assert not failures, "monitor register() missing:\n  " + "\n  ".join(failures)


def t_scheduler_register_job_supports_kickoff():
    """Scheduler.register_job() must accept kickoff and kickoff_delay parameters.

    Without these parameters, plugins cannot defer initial checks - we'd
    be back to synchronous blocking.
    """
    src = _read(SCHEDULER_PATH)
    body = _extract_method_body(src, "Scheduler", "register_job")
    assert body is not None, (
        "Scheduler.register_job() not found in scheduler.py - the v6.0.0 "
        "phased lifecycle has no scheduler API to register against."
    )
    # Look at the signature line specifically (first non-blank line of body)
    sig_match = re.search(
        r"def\s+register_job\s*\(\s*self\s*,\s*([^)]+)\)",
        src,
        re.DOTALL,
    )
    assert sig_match, "Could not parse Scheduler.register_job() signature"
    params_text = sig_match.group(1)
    # Strip comments — `# kickoff removed for test` would otherwise pass
    params_text_no_comments = re.sub(r"#[^\n]*", "", params_text)
    # Require kickoff and kickoff_delay to appear as parameter names: a name
    # followed by `:` (type annotation) or `=` (default), with `\s*` between.
    # `\bkickoff\s*[:=]` matches `kickoff: bool` but NOT `kickoff_delay`
    # (because `_` follows `kickoff`, breaking the regex).
    assert re.search(r"\bkickoff\s*[:=]", params_text_no_comments), (
        "Scheduler.register_job() signature does not have a `kickoff` parameter. "
        "Without this parameter, plugins cannot defer initial checks "
        "and we regress to synchronous blocking during plugin loading."
    )
    assert re.search(r"\bkickoff_delay\s*[:=]", params_text_no_comments), (
        "Scheduler.register_job() signature does not have a `kickoff_delay` parameter. "
        "Without staggered delays, all kickoff jobs would fire at "
        "boot_ready() simultaneously, stacking heavy Playwright sessions."
    )


# -----------------------------------------------------------------------------
# RUNNER
# -----------------------------------------------------------------------------
def main():
    print("=" * 70)
    print(" v6.0.0 step 7 - boot stall regression tests")
    print("=" * 70)

    tests = [
        ("init_methods_avoid_blocking_calls",      t_init_methods_avoid_blocking_calls),
        ("register_methods_are_simple_delegation", t_register_methods_are_simple_delegation),
        ("monitor_classes_expose_register_method", t_monitor_classes_expose_register_method),
        ("scheduler_register_job_supports_kickoff",t_scheduler_register_job_supports_kickoff),
    ]

    passed = failed = 0
    for i, (name, fn) in enumerate(tests, start=1):
        try:
            fn()
            print(f"  [{i}] PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  [{i}] FAIL  {name}")
            for line in str(e).split("\n"):
                print(f"        {line}")
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
