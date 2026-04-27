# Keith's PokeBS Tracker — Daily Operator's README

> **Who this is for:** Me, when I forget how to run my own project.
> **When to use this:** Before Googling, before asking Claude. Most answers are here.
> **Last updated:** v5.7.1 (April 2026)

---

## ⚡ Quick Start (the "what command was that again?" page)

You need **TWO things running at the same time** for everything to work:

### 1. The tracker (does the actual stock checking)
Open a terminal, then:
```
cd C:\path\to\tcg_tracker
python tracker.py
```
Leave this window open. If you close it, the tracker stops checking.

### 2. The dashboard server (lets your browser see the dashboard)
Open a **second** terminal (don't close the first one), then:
```
cd C:\path\to\tcg_tracker
python -m http.server 8080 --bind 127.0.0.1
```
Leave this window open too.

### 3. Open the dashboard in your browser
Go to: **http://localhost:8080/dashboard/dashboard.html**

That's it. Two terminals, one URL. Bookmark the URL.

---

## 🎯 What URL Do I Visit For Each Page?

All URLs start with `http://localhost:8080/dashboard/` and then the page name:

| What I want to see | URL ending |
|---|---|
| Main tracker view | `dashboard.html` |
| Set info & chase cards | `info.html` |
| Pricing per set | `pricing.html` |
| My collection binder | `binder.html` |
| 2026 release calendar | `calendar.html` |
| Retail drop patterns | `retail-drops.html` |
| Local store radar | `local.html` |
| **My investment portfolio** | `invest.html` |
| Price history charts | `pricing-history.html` |
| Help / changelog / FAQ | `help.html` |

**Tip:** all pages have a nav bar at the top — once you're on one, you can jump to any other.

---

## 🔧 First-Time Setup (only needed once per machine)

If this is a fresh install or a fresh PC, do this **before** running the tracker:

```
cd C:\path\to\tcg_tracker
python tools/setup_config.py
```

This walks you through:
- Setting your **ntfy topic** (the secret string for push notifications)
- Setting your **home ZIP code** (for nearby store searches)
- Setting your **city** (Plainfield NJ for me)
- Setting **anchor locations** (Cherry Hill, Princeton, etc.)

After that's done:
1. Install the **ntfy app** on your phone (App Store / Play Store)
2. In the app, tap "+" and subscribe to your topic name
3. Test it: `python tests/test_config.py` — you should get a push notification

If the test push works, you're good to go.

---

## 📁 What's Where? (the folder map)

```
tcg_tracker/                  ← the project root, where you `cd` into
├── tracker.py                ← the main thing — runs the 24/7 stock checker
├── shared.py                 ← shared code (don't edit unless you know why)
├── plugins.py                ← loads all plugins at startup
├── products_backup.txt       ← list of every product being tracked
│
├── plugins/                  ← every "feature" lives here as its own file
│   ├── price_history.py
│   ├── restock_reminder.py
│   ├── costco_tracker.py
│   ├── alternative_retailers.py
│   ├── bestbuy_invites.py
│   ├── invest_store.py       (v5.9)
│   ├── market_data_refresh.py (v5.9)
│   └── ...
│
├── dashboard/                ← every HTML page you see in the browser
│   ├── dashboard.html
│   ├── invest.html
│   ├── help.html
│   └── ...
│
├── data/                     ← stuff the tracker creates while it runs
│   ├── status_snapshot.json  (current stock state - dashboard reads this)
│   ├── price_history.db      (SQLite, hourly price log)
│   ├── invest.db             (v5.9 - your portfolio)
│   ├── market_cache.db       (v5.9 - cached pokemontcg.io data)
│   └── tcg_tracker.log       (debug log if something seems off)
│
├── tools/                    ← setup helpers
│   └── setup_config.py
│
└── tests/                    ← diagnostic scripts
    └── test_config.py
```

**Stuff that's NOT in the project folder** (lives elsewhere on your machine):

| What | Where |
|---|---|
| Your config (ntfy topic, ZIP, etc.) | `%LOCALAPPDATA%\tcg_tracker\config.json` |
| Browser profile for retailer logins | `%LOCALAPPDATA%\tcg_tracker\browser_profile\` |

These are intentionally **outside** OneDrive so they don't get synced to the cloud.

---

## 🔌 Plugin Commands (things I might run occasionally)

These are one-off commands. The plugins also auto-run inside `tracker.py`, but sometimes you want to run one manually.

### Price History — export to CSV
```
python plugins/price_history.py --csv
```
Writes `data/price_summary_30d.csv` and `data/price_history_90d.csv`. Open in Excel or Google Sheets.

### Restock Reminder — preview today's message
```
python plugins/restock_reminder.py
```
Just shows what would be sent. To actually send it now:
```
python plugins/restock_reminder.py --send
```

### Investment store (v5.9) — quick KPI check
```
python plugins/invest_store.py kpi
```
Prints total cost, market value, P/L without opening the dashboard.

### Market refresh (v5.9) — force a refresh now
```
python plugins/market_data_refresh.py force
```
Re-fetches every market value, ignoring the 12-hour cache.

---

## 🧪 Diagnostics — "is everything working?"

Run these in order if something feels off:

| Command | What it checks |
|---|---|
| `python tools/setup_config.py --show` | Is my config readable? Are the values right? |
| `python tools/setup_config.py --validate` | Does my config pass all the safety checks? |
| `python tests/test_config.py` | Full check: config + ntfy push (sends a test notification) |
| `python tests/test_config.py --no-ntfy` | Same as above but skip the push test (offline mode) |
| `python tracker.py debug` | Show what the retailers are actually returning for the first 3 Target products |

Most of the time, if the dashboard looks empty or weird, the answer is one of:
1. The tracker isn't running
2. The HTTP server isn't running
3. The tracker just started and hasn't finished its first check yet (wait 5 min)

---

## ⚠️ Common Gotchas (mistakes I will probably make again)

### "The dashboard is empty / shows demo data"
Means `data/status_snapshot.json` doesn't exist or wasn't found. Check:
- Is `tracker.py` actually running? (Look at the first terminal — it should be printing log lines.)
- Did you start the http.server from inside the `tcg_tracker/` folder, not from somewhere else?
- Has the tracker been running at least 5 minutes? The first full check takes a while.

### "I'm not getting push notifications"
Run `python tests/test_config.py` — it sends a test push. If you get the test push but not real alerts, the tracker isn't seeing in-stock items (that's normal — most checks return out-of-stock). If you don't get the test push:
- Check your ntfy app subscription matches the topic in `config.json`
- Make sure the ntfy app has notification permissions on your phone

### "I see Walmart errors in the log"
There are ~6 Walmart products with stale item IDs. Known issue, on the priority list. Doesn't break anything — just noisy.

### "Best Buy invite plugin keeps erroring"
Known issue (v5.8). Circuit breaker correctly trips after 3 failures so it doesn't loop forever. Fix is on the priority list. Best Buy stock detection still works fine — only the auto-invite-request feature is affected.

### "I edited a plugin file and now the tracker won't start"
Plugins import from `shared.py` — if you accidentally broke something there, every plugin breaks. Run:
```
python -m py_compile shared.py
```
If it errors, you can see which line. Use `git status` and `git diff` to see what you changed, or `git checkout shared.py` to undo.

---

## 🔒 Security Reminders (don't be sloppy)

✅ **Already done:**
- Project is OFF Desktop and OFF OneDrive (stays on D: drive or wherever you put it)
- `config.json` is in `%LOCALAPPDATA%`, never in the repo
- `.gitignore` blocks all data/*.json, browser cookies, log files
- ntfy topic is 16+ random characters, not your name or anything guessable
- HTTP server is bound to `127.0.0.1` only (won't expose to your home network)
- All retailer accounts have 2FA enabled

⏳ **Still TODO:**
- Enable BitLocker / Device Encryption on this machine
- Set up a real password manager (Bitwarden) instead of browser-saved passwords

🚫 **Never do these:**
- Don't commit `config.json` to git (it has your ntfy topic)
- Don't paste your ntfy topic in screenshots, Discord, or anywhere public
- Don't run `python -m http.server 8080` without `--bind 127.0.0.1` (that exposes the dashboard to your whole home Wi-Fi network)
- Don't change the cart_preloader to actually click "Buy" or "Place Order" buttons. The whole design rule is: **the script never spends money.** You always make the final click.

---

## 🆘 When Things Really Break

1. **Check the log first:** `data/tcg_tracker.log` — look at the last 50 lines.
2. **Try restarting both processes:** Ctrl+C in both terminals, then start them again.
3. **Try the diagnostics:** `python tests/test_config.py`
4. **Read the help page in the dashboard:** `http://localhost:8080/dashboard/help.html` — has a "FAQ & Diagnostics" tab with more troubleshooting.
5. **Ask Claude in the project chat** — paste the error message.
6. **Last resort:** `git status` to see what's changed, `git stash` to undo all uncommitted changes and try again from a clean slate.

---

## 🔧 Patch System (v6.0.0+)

Starting with v6.0.0, code changes are delivered as **self-contained patch scripts** instead of hand-copied files. Each patch knows what to back up, what to install, what to verify, and what to clean up.

### Quick reference

| Command | What it does |
|---|---|
| `python apply_<version>.py` | Apply the patch (preflight → backup → install → verify → cleanup) |
| `python apply_<version>.py --dry-run` | Show the plan without changing anything |
| `python apply_<version>.py --show <file>` | Print the embedded file content (or PATCH preview) for review |
| `python apply_<version>.py --rollback` | Reverse the patch using `.bak` files (only works pre-finalize) |
| `python apply_<version>.py --finalize` | After git commit confirmed: delete backups + archive the script |
| `python apply_<version>.py --force` | Bypass soft preflight warnings (use sparingly) |

### Day-to-day workflow

```
# 1. Apply
python apply_v6_0_0_stepN.py

# 2. Optional smoke test on real machine
python tracker.py    # then Ctrl+C after confirming it works

# 3. Commit (run the suggested git commands)
git add <files>
git commit -m "<suggested message>"

# 4. Clean up artifacts
python apply_v6_0_0_stepN.py --finalize
```

After step 4, your project root is clean — no `.bak` files, no leftover apply script. Old apply scripts get archived to `.patches_archive/` (gitignored) for audit-trail purposes.

### The 7-phase patch lifecycle

Every patch script follows the same flow:

| Phase | Stage | What happens | Side effects |
|---|---|---|---|
| 1 | **Preflight** | Validate project root, git state, manifest find/replace counts, idempotency check | None |
| 2 | **Backup** | Copy each REPLACE/PATCH target to `<file>.pre_<label>.bak` | `.bak` files created |
| 3 | **Install** | Apply NEW/REPLACE/PATCH actions per manifest | Source files modified |
| 4 | **Verify** | Run syntax checks + test suites, match output patterns | `.log` file if anything WARNs or FAILs |
| 5 | **Auto-cleanup** | (only if no FAIL) Delete `__pycache__`, transient logs | Transient files removed |
| 6 | **Suggest commit** | Print `git add` + `git commit` lines | None — user runs them |
| 7 | **Finalize** | (separate `--finalize` invocation, after commit) Verify commit in git, delete `.bak`, archive script | `.patches_archive/` populated, project root cleaned |

If anything fails before phase 4 completes, **`--rollback` works** — your project goes back to the pre-patch state. Phase 7 explicitly closes that escape hatch (the `.bak` files are gone), at which point `git revert <commit>` is the rollback path.

### FAQ — patch system

**Q: I ran a patch script and got "already applied — no-op." What happened?**
The patch detected via hash that all target files are already in their post-patch state. This is normal if you re-run a script accidentally. No changes were made.

**Q: I got "MIXED STATE — cannot proceed safely" in preflight. What do I do?**
The script found that *some* of the patch's target files match the expected post-patch state but others don't. The abort message lists which is which. Most common cause: a previous apply was interrupted; run `--rollback` then re-apply. Less common: you've manually edited one of the files; review the edit and decide whether to keep it (don't re-run the patch) or `--rollback` to revert.

**Q: Verify failed with [FAIL]. Now what?**
Read the `.patch_<label>_verify_fail.log` file in your project root — it has the full output of the failing test. Most often this is a real regression and `--rollback` is correct. If you're sure the failure is benign (rare), `--skip-verify` exists for emergency hot-fixes, but you're then on your own.

**Q: Verify reported [WARN] for one test. Should I worry?**
WARN means the test exited cleanly (exit code 0) but the patch script couldn't find its expected output pattern. Most often this is an output format change in the test itself, not a regression. Read the `.patch_<label>_verify_warn.log` to confirm. If the test really did pass, just commit and move on.

**Q: I committed but forgot to `--finalize`. Is that bad?**
No, just leaves `.bak` files lying around. You can run `--finalize` whenever, even days later — it just confirms the commit is in git history then cleans up. If you'd rather skip it, manually delete `*.bak` files when you feel like it (they're already gitignored, so they don't show up in git status).

**Q: I ran `--finalize`, then changed my mind. How do I undo?**
After finalize, the `.bak` files are gone. The patch script's `--rollback` will refuse and tell you to use `git revert <commit-hash>` instead. Git history is your safety net post-finalize.

**Q: Why are old apply scripts moved to `.patches_archive/` instead of deleted?**
Audit trail. They're typically 50-80KB each — negligible disk cost. If you ever want to inspect what a past patch did (`--show <file>` still works on archived scripts), or run a forensic `--rollback` from before a `--finalize`, the archive has them.

**Q: I downloaded a patch script but I'm not sure if I should run it. What do I do?**
`python apply_<version>.py --dry-run` shows the full plan — what files it'll touch, what tests it'll run, what verifications it expects to pass — without changing anything. Then `python apply_<version>.py --show <filename>` lets you read the exact content of any embedded file before applying.

### Patch artifacts you might see

| File pattern | What it is | Cleaned by |
|---|---|---|
| `apply_v*.py` | A patch script | `--finalize` (moves to `.patches_archive/`) |
| `*.pre_v*_*.bak` | Backup of a file modified by a patch | `--finalize` (deletes after commit confirmed) |
| `.patch_*_verify_warn.log` | Test ran clean but pattern miss; check before committing | Auto-cleaned in phase 5 if no warnings remain |
| `.patch_*_verify_fail.log` | Test failed; investigate before doing anything else | Stays until `--rollback` succeeds and resolves the issue |
| `.patches_archive/` | Folder of archived past patch scripts | Gitignored; clean manually if it grows uncomfortably large |

### Notes on Steps 1-3 (legacy)

The first three patches in v6.0.0 (Steps 1, 2, 3) were applied before the new patch infrastructure existed. They left some `.bak` files and apply scripts in your project root. These are harmless — the `.gitignore` rules keep them out of git status — but you can hand-delete them whenever you feel like:

```
del apply_v6_0_0_step1.py apply_v6_0_0_step1_hotfix1.py apply_v6_0_0_step2.py apply_v6_0_0_step3.py
del *.bak tests\*.bak
```

Step 4 forward uses the new infrastructure described above.

---

## 🐍 Python Setup (v6.0.0+)

This project requires **Python 3.14 or newer**. Earlier Python installs may be missing required packages or language features.

### Recommended: use `tracker.bat`

```
tracker.bat
```

This wrapper always uses Python 3.14 via the `py` launcher, regardless of your `PATH` ordering. If you have multiple Python versions installed, this is the safest way to run the tracker.

You can also pass arguments through:
```
tracker.bat debug
```

### If running `python` directly

On Windows with multiple Python installs, prefer the `py` launcher to pin a specific version:

```
py -3.14 tracker.py
```

Apply scripts (patches) need the same:

```
py -3.14 apply_v6_0_0_stepN.py
```

### Wrong-Python symptoms

If you see any of these, you're likely running with the wrong Python:

- `ModuleNotFoundError: No module named 'requests'` (or `schedule`, etc.) — bare-install Python doesn't have the project's packages
- `ERROR: tracker.py requires Python 3.14+` — the version-check guard at the top of `tracker.py` caught it (this is the friendly error; everything else is a symptom of bypassing it)
- Apply scripts report unrelated regression test failures — the verify step is using the wrong interpreter

To investigate which Python `cmd.exe` is using:

```
where python
python --version
py -3.14 --version
```

If `python` doesn't resolve to 3.14, switch to `tracker.bat` or `py -3.14` for everything related to this project.

---

## 📚 Glossary (terms I'll forget)

| Term | What it means |
|---|---|
| **MSRP** | Manufacturer's Suggested Retail Price. The "fair price" before scalpers. |
| **ntfy** | The push notification service (ntfy.sh). Free, no account needed, works on your phone. |
| **Akamai** | A bot-detection service. Why we can't fully automate Best Buy logins. |
| **Playwright** | A browser automation library. How the tracker scrapes Target and Best Buy. |
| **Plugin** | A file in `plugins/` that adds a feature. Loaded automatically when tracker.py starts. |
| **Circuit breaker** | A safety pattern: if something fails 3+ times in a row, stop trying for 30 minutes. Prevents the tracker from getting stuck on a dead service. |
| **localStorage** | Where the dashboard stores data inside your browser. Fragile — clearing site data wipes it. (v5.9 fixes this for the invest page.) |
| **WAL mode** | A SQLite setting that lets reads happen while writes are happening. Used for `price_history.db` and `invest.db`. |
| **Adaptive scheduling** | The tracker checks more often during known drop windows (Tue/Fri at Target, Wed at Walmart) and less often during dead hours. Saves CPU. |
| **Cart pre-loader** | When MSRP is detected, browser opens, navigates to product, clicks Add to Cart, lands on checkout. **You** click Buy. |

---

## 🔄 Restart Cheat Sheet

If you just want to fully restart everything from scratch:

1. Close all terminal windows you have open for this project (Ctrl+C, then close)
2. Open a fresh terminal:
   ```
   cd C:\path\to\tcg_tracker
   python tracker.py
   ```
3. Open another fresh terminal:
   ```
   cd C:\path\to\tcg_tracker
   python -m http.server 8080 --bind 127.0.0.1
   ```
4. Refresh your browser tab pointed at `http://localhost:8080/dashboard/dashboard.html`
5. Wait 5 minutes for the tracker to finish its first check
6. Done.

---

**One last thing:** when you ask Claude to add a feature or fix a bug, mention which file you think the change should go in. Claude will figure it out either way, but pointing at the file makes things faster and reduces back-and-forth.
