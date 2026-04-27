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

py -3.14 "%~dp0tracker.py" %*
