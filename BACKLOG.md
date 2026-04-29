# Future Backlog

Forward-looking ideas captured during development. Not yet scheduled.
Each entry has enough context to pick up in a future session without
re-explaining.

Format: each item describes the **what**, the **why**, and any **open
questions** that need answering before implementation.

---

## Phases (chronological order, each finishes before the next starts)

1. **v6.1.x chain completion** (active — next session)
   - **1a. v6.1.7 Option A — periodic zombie cleanup.** Run
     `kill_chromium_zombies` every N cycles instead of only at tracker.bat
     startup. Quick tactical win: converts bestbuy_batch from "dormant after
     cycle 1" to "productive every ~30 min." Doesn't fix root cause.
   - **1b. v6.1.7 Option B — fix page.unroute() hang in `check_bestbuy_batch`.**
     Root cause of zombie creation. Three candidate approaches: skip cleanup
     on error, wrap unroute in its own timeout, or refactor to subprocess.
     Tomorrow-fresh-eyes architectural work; ship after Option A is soaking.
   - **1c. v6.1.1 step 3 — Walmart cutover.** Remove `walmart` from
     `CHECKER_MAP` in `tracker.py`, replace `check_walmart()` body with
     deprecated shim, delete `_scrape_walmart_fallback()` (dead code post-
     cutover). Walmart traffic now lives in `walmart_playwright` plugin.
     Soak gate: 24h+ of clean walmart_playwright history before cutover.
2. **Remaining technical debt cleanup** — Costco login retry, BitLocker,
   password manager. Two prior items resolved: bestbuy_invites async/sync
   mismatch (fixed in v6.0.0 step 4_5 via daemon-thread wrapping), Walmart
   product list / API header refresh (superseded by walmart_playwright).
3. **UI Audit Phase** — page-by-page visual + IA cleanup across the dashboard
   (see "UI Audit Phase" section below for scope and approach).
4. **Feature increments** — net-new analytical features that aren't pure UI
   cleanup (currently just "Spending trend by set," see that section below).

The phases matter because doing them out of order causes rework. Cosmetic polish
on a page that's about to be functionally restructured by a feature increment is
wasted effort. Functional features built on top of unfixed technical debt inherit
the debt. **Stay disciplined about the phase order.**

---

## UI Audit Phase

**What:** A dedicated cleanup pass across all dashboard pages, addressing minor
visual + information-architecture issues that have accumulated over many
incremental PRs. Done as one coordinated effort rather than scattered drive-by
fixes.

**Why:** The dashboard has grown to 10+ pages, each shipped independently. Small
inconsistencies (color usage, spacing, typography hierarchy, button placement,
empty states) drift between pages. Fixing these one-at-a-time during feature
work is inefficient and produces inconsistent results — a "fix the cards on
invest.html" PR doesn't naturally also fix the same pattern on
pricing-history.html.

**Approach when we get there:**
1. Spend one session doing a screenshot-based walkthrough of every dashboard
   page, logging visual and IA issues into a checklist.
2. Group issues by category (color, spacing, typography, empty states, etc.) so
   patterns can be fixed in CSS-variable changes that propagate to all pages.
3. Prioritize fixes by visibility × frequency-of-use.
4. Ship in small page-batches with screenshots for review, not one massive PR.

**Pages in scope (current count: 10):**
- `dashboard.html` (Tracker)
- `info.html` (Set Info)
- `pricing.html` (Pricing)
- `binder.html` (Binder)
- `calendar.html` (Calendar)
- `retail-drops.html` (Retail Drops)
- `local.html` (Local)
- `invest.html` (Invest)
- `pricing-history.html` (Price History)
- `help.html` (Help)

**Known items to address during the audit** (this list will grow as more pages
get reviewed; add to it freely as items come up between now and the audit):

- **KPI bar visual differentiation on `invest.html`** — the 5 KPI cards (Cost
  Basis, Market Value, Total P/L, Best Performer, Avg per Item) currently render
  as uniform gray panels. The only color signal is the gain/loss tint on the P/L
  value itself. Eye doesn't know where to land first. Options range from subtle
  (2px colored top border per card) to strong (background tint by metric
  category). Recommended starting point: subtle. Do during the `invest.html`
  page batch.

**Touches:** Mostly `dashboard/*.html` files and CSS variables in each (or a
shared CSS file if we extract one during the audit). No backend changes
expected, unless an audit item turns up an actual data issue.

---

## Spending trend by set (multi-line chart)

**What:** The current "Spending Trend" chart on `invest.html` is a monthly
cumulative spend bar chart — one bar per month, total across all sets. Add an
alternate view: a multi-line chart where each line represents one set's
cumulative spend over time, with a toggle above the chart to switch between the
two views.

**Why:** Surfaces *which* sets are driving spend, not just *when*. A flat line
for one set means you stopped buying it. A line that suddenly spikes 3 months
after launch means you bought into a hot secondary market. The current bar
chart hides this dimension.

**Mental model:**
```
$ spent
  ↑
  │      ╭─────  Prismatic Evolutions
  │     ╱
  │    ╱  ╭──── Chaos Rising
  │   ╱  ╱
  │  ╱  ╱   ╭── Perfect Order
  │ ╱  ╱   ╱
  └─────────────→ time
   Jan Feb Mar Apr
```

**Why this is its own item, not part of the UI audit:** This is a net-new
feature requiring backend (a new aggregation endpoint) plus a non-trivial chart
implementation. The audit is for fixing existing things; this adds a new thing.
That said, if it's small enough by the time we get to it, it could fold into
the `invest.html` page-batch of the audit.

**Open questions:**
- Top N sets only (e.g., 5 by total spend) or all sets? With 10+ sets, lines
  overlap to the point of being unreadable.
- Linear y-axis (cumulative dollars) or rate (dollars per period)? Cumulative
  is more intuitive but rate-of-spend better surfaces "I stopped buying this
  set."
- Color palette — the existing CSS variables don't have enough distinct values
  for 10+ sets. Need a generated palette function (HSL distribution by set
  count).

**Data:** All needed data is already in `invest.db`. The query is essentially:
```sql
SELECT set_code, purchase_date, SUM(purchase_price * quantity) OVER (
    PARTITION BY set_code ORDER BY purchase_date
) AS cumulative_spend
FROM purchases
WHERE set_code IS NOT NULL
ORDER BY set_code, purchase_date;
```

**Touches:**
- `plugins/api_server.py` — new endpoint `/api/invest/spending_by_set` to
  deliver the aggregation server-side
- `plugins/invest_store.py` — supporting query function
- `dashboard/invest.html` — chart toggle + new chart rendering code
- `tests/test_invest_store.py` — query correctness tests

---

## Pre-existing technical debt (from PROJECT_KNOWLEDGE.txt)

These are bugs and ops tasks, not new feature ideas. Listed here so all
forward-looking work lives in one place. **Phase 2** above clears these.

- **Costco login retry** — deferred from v5.8 migration. Plugin currently
  works because session cookie is long-lived, but no retry path if it
  expires mid-session.
- **BitLocker / device encryption** — machine-level setup task. Drive readable
  if stolen until done.
- **Password manager** — Bitwarden recommended. Browser-saved passwords only
  until done.

Do NOT confuse these with the design items above. These are bugs and ops; the
items above are net-new feature work.

### Resolved (kept here briefly for audit trail)

- ~~**bestbuy_invites async/sync mismatch**~~ — Resolved in v6.0.0 step 4_5
  via daemon-thread wrapping pattern. The pattern is now used by all heavy
  Playwright plugins (bestbuy_invites, amazon_monitor, costco_tracker,
  walmart_playwright).
- ~~**Walmart product list / API header refresh**~~ — Superseded by
  v6.1.1 step 2 (`walmart_playwright` plugin via patchright + stealth).
  Legacy `check_walmart` still in `CHECKER_MAP` for fail-safe coverage
  until v6.1.1 step 3 cutover.

---

## How to use this file

- **When you have a new idea,** add it as a new section above the "Phases"
  table or under the appropriate phase. Capture it while context is fresh.
- **When you start a session,** scan this file to remind yourself of priority
  order.
- **When you finish a backlog item,** remove its entry (don't leave it marked
  done — that's what git history is for).
- **Don't merge this with PROJECT_KNOWLEDGE.txt.** That file is the
  current-state reference; this is the future-state plan. Keep them
  conceptually separate.
