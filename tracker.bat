@echo off
REM ─────────────────────────────────────────────────────────────────────
REM tracker.bat - launches tracker.py with the correct Python version
REM
REM Always uses Python 3.14 via the `py` launcher, regardless of which
REM Python your PATH resolves to. Prevents the "ModuleNotFoundError"
REM class of bug when multiple Python installs are present.
REM
REM Pass-through: any args you give to tracker.bat are passed to
REM tracker.py. Examples:
REM   tracker.bat              (start the tracker normally)
REM   tracker.bat debug        (run debug mode - first 3 Target products)
REM
REM See README.md "🐍 Python Setup" section for context.
REM ─────────────────────────────────────────────────────────────────────

REM ─────────────────────────────────────────────────────────────────────
REM v6.1.4 step 2a: warm browser profiles before tracker.py to prevent
REM the boot-time chromium storm. The preflight is silent when all
REM profiles are already warm (typical case after first run).
REM ─────────────────────────────────────────────────────────────────────
py -3.14 "%~dp0tools\warm_browser_profiles.py" --check-or-warm
if errorlevel 1 (
    echo [ERROR] Profile warming failed - tracker not started.
    echo Run "py -3.14 tools\warm_browser_profiles.py --verbose" to diagnose.
    pause
    exit /b 1
)

REM ─────────────────────────────────────────────────────────────────────
REM v6.1.5: kill orphan chromium processes from previous tracker runs
REM (bestbuy_batch zombies that survived their parent's daemon thread).
REM Non-fatal - tracker proceeds even if killer encounters issues.
REM ─────────────────────────────────────────────────────────────────────
py -3.14 "%~dp0tools\kill_chromium_zombies.py" --quiet

py -3.14 "%~dp0tracker.py" %*
