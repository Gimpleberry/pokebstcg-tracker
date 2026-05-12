# Future Backlog

Forward-looking ideas captured during development. Not yet scheduled.
Each entry has enough context to pick up in a future session without
re-explaining.

Format: each item describes the **what**, the **why**, and any **open
questions** that need answering before implementation.

---

## Phases (chronological order, each finishes before the next starts)

1. **v6.1.x chain — COMPLETE (May 4, 2026).** Phase 1 closed out via
   v6.1.13 (audit script parser fix), v6.1.14 (cart_preloader timeout
   centralization, ~4.5s saved per cart action), and the deferral of 1d
   (bestbuy_invites traceback noise filter — see "Deferred" section).
2. **Phase 2 — Technical debt + observability cleanup** (in progress)
   - **2a/help.html v6.1.x changelog block** — DONE May 4. v1+v2.
   - **2c/Costco resilience** — DONE May 4 as v6.1.15. Re-scoped
     from "programmatic re-login" to resilience hardening.
   - **2d/scheduler introspection panel** — DONE May 5.
     - **v6.1.16** shipped backend `/api/scheduler/health` endpoint
       (12 tests, returns ok/ready/generated_at/job_count/jobs[]).
     - **v6.1.17** shipped collapsible dashboard panel polling every
       60s with color-coded status pills (11 tests + node --check).
   - **2e/legacy lifecycle migration** — IN PROGRESS, 5/11 done.
     - **v6.1.18** Batch 1 (May 5) — NewsScraper, StoreInventory,
       AltRetailer migrated. Net +4 jobs to scheduler.
     - **v6.1.18.1** hotfix (May 5) — weekly cadence regex needed
       3-letter day abbreviations.
     - **v6.1.19** Batch 2 (May 5) — MSRPAlert, CartPreloader
       migrated (service-style, +0 jobs). v18 brittle test rewrite.
     - **Remaining batches (v6.1.20+):** WalmartQueue, RestockReminder,
       PriceHistory, InvestStore, MarketDataRefresh, ApiServer.
       Each "delegating" — needs both inner module + wrapper change.
       One plugin per ship.
   - **Costco login retry** (programmatic) — superseded by v6.1.15
     resilience approach. Not actionable without credential storage.
   - **BitLocker / device encryption** — machine-level setup task,
     not code.
   - **Password manager** — Bitwarden recommended. Out-of-scope.
   - **BB HTTP/2 errors on `/site/` URL pattern** — Akamai-side, may
     be unfixable from our end (see Known Limitations below).
3. **Phase 3 — UI Audit** — page-by-page visual + IA cleanup across the
   dashboard (see "UI Audit Phase" section below for scope and approach).
4. **Phase 4 — Feature increments** — net-new analytical features that
   aren't pure UI cleanup (currently just "Spending trend by set").

The phases matter because doing them out of order causes rework.
Cosmetic polish on a page that's about to be functionally restructured
is wasted effort. Functional features built on top of unfixed technical
debt inherit the debt. **Stay disciplined about the phase order.**

---

## Phase 2 / 2e — COMPLETE (v6.1.25, May 12, 2026)

All 15 plugins migrated to phased lifecycle. Final state: 22 scheduler
jobs, 7 kickoffs, 0 legacy. The unified Scheduler is the single source
of truth for every cadenced job. See PROJECT_KNOWLEDGE.txt PATCH SUMMARIES
for v6.1.20 → v6.1.25 details.

Next phase per phase ordering: high-priority technical debt cleanup.
---

## Deferred

These were active items that the session work proved aren't currently
actionable. Captured here so they don't get re-litigated.

### 1d — bestbuy_invites traceback noise filter (DEFERRED May 4)

**Original BACKLOG note:** Cosmetic ~700 prefixes/22h in log.

**Why deferred:** Audit on the post-v6.1.12 log shows 0 BBI
WARNING/ERROR lines and 0 tracebacks over a ~26h window.
The "~700/22h" rate may have been incidentally resolved by
v6.1.7 Option B (page.unroute error-gating).

**Re-open trigger:** if BBI noise reappears in the log, search
wider than `[bestbuy_invites]` prefix. Then design a surgical
filter against the actual pattern.

**Lesson:** don't ship a filter for noise that doesn't exist.
Tenet 5 (Quality > backtracking).

---

## Known Limitations (not actionable)

These are accepted operational realities. Listed here so they're not
re-litigated as bugs.

- **BB HTTP/2 errors on 2/6 products.** The two failing URLs use the
  `bestbuy.com/site/.../XXXXXXX.p` pattern (older format). Suspected
  Akamai bot-protection applying stricter rules to that URL pattern.
  Not a tracker bug. Error noise is already minimal.
- **Pokemon Center / Walmart price extraction returns N/A for OOS
  items.** Stock state detection works; price extraction doesn't
  surface a value when the item is unavailable. Acceptable behavior.

---

## Observations (candidate items, not yet scheduled)

### Obs A — PC concurrent batch is silent per-product

**Surfaced:** May 4 audit (v6.1.13 cycle).

**What:** v6.1.12's `ThreadPoolExecutor` dispatch in
pokemoncenter_batch emits batch summary lines but no per-product
result lines. Audit script can't attribute price observations
to individual PC products.

**Two paths if this becomes important:**

1. Add a per-product DEBUG line in the PC worker function.
2. Add a PC-specific batch-line parser to the audit script.

**Triage:** wait until audit-script use becomes routine, then
decide. Currently audit only runs ad-hoc.

---

## UI Audit Phase

**What:** A dedicated cleanup pass across all dashboard pages,
addressing minor visual + IA issues that have accumulated over many
incremental PRs.

**Why:** The dashboard has grown to 11 pages (10 originally + the
new scheduler-panel section on dashboard.html added by v6.1.17),
each shipped independently. Small inconsistencies drift between
pages. Fixing these one-at-a-time during feature work is inefficient.

**Approach when we get there:**
1. Spend one session doing a screenshot-based walkthrough of every
   dashboard page, logging visual and IA issues into a checklist.
2. Group issues by category (color, spacing, typography, empty
   states, etc.) so patterns can be fixed in CSS-variable changes.
3. Prioritize fixes by visibility × frequency-of-use.
4. Ship in small page-batches with screenshots for review.

**Pages in scope (current count: 10 pages + 1 panel):**
- `dashboard.html` (Tracker — now hosts the scheduler-panel from v6.1.17)
- `info.html`, `pricing.html`, `binder.html`, `calendar.html`
- `retail-drops.html`, `local.html`, `invest.html`,
- `pricing-history.html`, `help.html`

**Known items to address during the audit** (this list will grow):

- **KPI bar visual differentiation on `invest.html`** — the 5 KPI
  cards currently render as uniform gray panels. Eye doesn't know
  where to land first. Recommended starting point: 2px colored top
  border per card.

- **Scheduler panel polish** (post v6.1.17). The panel ships
  functional but utilitarian. Audit candidates: typography hierarchy
  in the table headers (uppercase letter-spacing matches site
  pattern but feels heavy at this density), empty-state messaging
  if scheduler is unwired, tooltip presentation on the "error"
  pill (currently uses native `title` attr — a custom tooltip
  matching the rest of the dashboard would be more polished).

- **Scheduler panel state persistence** (post v6.1.17). Panel
  defaults to collapsed and resets on every page reload. If users
  want it expanded by default, store the expand/collapse preference
  in localStorage. Low priority.

**Touches:** Mostly `dashboard/*.html` files and CSS variables. No
backend changes expected.

---

## Spending trend by set (multi-line chart)

**What:** The current "Spending Trend" chart on `invest.html` is a
monthly cumulative spend bar chart. Add an alternate view: a
multi-line chart where each line represents one set's cumulative
spend over time.

**Why:** Surfaces *which* sets are driving spend, not just *when*.

**Open questions:**
- Top N sets only or all sets? With 10+ sets, lines overlap.
- Linear y-axis (cumulative dollars) or rate (dollars per period)?
- Color palette — existing CSS vars don't have enough distinct
  values for 10+ sets. Need a generated palette function.

**Data:** All needed in `invest.db`. New `/api/invest/spending_by_set`
endpoint would aggregate server-side.

**Touches:**
- `plugins/api_server.py` — new endpoint
- `plugins/invest_store.py` — supporting query function
- `dashboard/invest.html` — chart toggle + new chart rendering
- `tests/test_invest_store.py` — query correctness tests

---

## Resolved (kept here briefly for audit trail; pruned in 2-3 weeks)

- ~~**v6.1.19 — Phased lifecycle Batch 2**~~ — Resolved May 5.
  MSRPAlert + CartPreloader migrated to phased lifecycle (service-
  style: init() + no-op register()). v6.1.18 brittle test rewritten
  to plugin-specific assertions. 22 combined tests.
- ~~**v6.1.18.1 — Weekly cadence hotfix**~~ — Resolved May 5.
  AltRetailer's "weekly tuesday/friday" rejected by scheduler regex
  which requires 3-letter day abbreviations. Fixed + new regression
  test mirroring scheduler.py's 4 cadence regexes. Lesson: cadence
  strings must validate against scheduler's actual contract, not
  just be text-present.
- ~~**v6.1.18 — Phased lifecycle Batch 1**~~ — Resolved May 5
  (with v6.1.18.1). NewsScraper, StoreInventory, AltRetailer
  migrated. NewsScraper preserves boot-time scrape via kickoff=60s.
- ~~**v6.1.17 — Dashboard scheduler health panel**~~ — Resolved May 5.
  Collapsible panel at top of dashboard.html, polls every 60s,
  color-coded status pills. 11 tests + node --check JS validation.
  Caught v6.1.18's bug 5 minutes after restart.
- ~~**v6.1.16 — Scheduler introspection endpoint**~~ — Resolved May 5.
  GET /api/scheduler/health. Re-tagged from BACKLOG's v6.0.2 to
  v6.1.16 for chain continuity. 12 tests. Three explicit failure
  modes (503 unwired, 500 jobs() throws, 200 happy).
- ~~**v6.1.15 — Costco resilience hardening**~~ — Resolved May 4.
- ~~**v6.1.x help.html changelog block**~~ — Resolved May 4 (v1+v2).
- ~~**v6.1.14 — cart_preloader timeout centralization**~~ — May 4.
- ~~**v6.1.13 — audit script parser fix**~~ — May 4.
- ~~**v6.1.12 — Pokemon Center concurrent batching**~~ — May 4.

---

## How to use this file

- **When you have a new idea,** add it as a new section above the
  "Phases" table or under the appropriate phase.
- **When you start a session,** scan this file to remind yourself of
  priority order.
- **When you finish a backlog item,** remove its entry (don't leave
  it marked done — that's what git history is for). Exception:
  brief audit-trail entries in "Resolved" for a few weeks.
- **Don't merge this with PROJECT_KNOWLEDGE.txt.** That file is the
  current-state reference; this is the future-state plan. Keep them
  conceptually separate.
