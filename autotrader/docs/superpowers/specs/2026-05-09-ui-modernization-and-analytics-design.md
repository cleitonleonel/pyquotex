# UI modernization & advanced analytics — design

**Date:** 2026-05-09
**Status:** Approved through brainstorming; ready for implementation planning.
**Owner:** single-user self-hosted deployment (`autotrader/frontend` + `autotrader/backend`).

---

## 1. Goal

Replace the current dashboard's flat top-nav + 1058-line monolithic Pipeline page
with a modern sidebar-driven UI that surfaces decision-grade analytics — not just
trade logs — and supports light/dark themes.

The success test: an operator opening the dashboard for 30 seconds should be
able to answer "should I be trading right now, and if so, on which channel?"
without scraping logs or running SQL.

## 2. Decisions captured during brainstorming

| Decision | Choice | Reason |
|---|---|---|
| Visual direction | **Modern SaaS** (Vercel/Stripe-style) | Hero KPIs with deltas read in 2 seconds; light + dark both polished. |
| Analytics scope | **All 10 candidate panels** | Phased so highest-leverage 5 ship first. |
| Theme | Light + dark + system | `next-themes`; persisted; no FOUC. |
| Navigation | Collapsible sidebar | Frees horizontal width for KPI hero rows; topbar shows breadcrumb + global pipeline status. |
| Filter state | URL query params | Shareable links, refresh-safe, no extra state library. |
| Charting library | **Recharts** | shadcn-recommended, theme-aware via CSS vars, ~75 KB gzip. |
| Backend strategy | New `/stats/v2/*` endpoints alongside existing | Zero-risk migration; legacy endpoints stay until frontend stops calling them. |
| Phasing | 3 PRs (Foundation → Analytics core → Analytics depth) | Each independently shippable; can stop after any. |

## 3. Scope

**In scope**
- Theme system (light/dark/system) with persisted preference.
- Sidebar navigation shell + topbar with breadcrumb and global status pill.
- Information-architecture restructure: split monolithic Pipeline page into
  Overview / Analytics / Trades / Decisions / Pipeline-controls.
- New `/stats/v2/*` backend endpoints for time-range queries, breakdowns, and
  funnel counts. Filter bag accepted by all three.
- Two new SQLite composite indices on `trade_attempts`.
- Global filter bar (date range, channels, parsers, asset, direction, status)
  with URL-backed state.
- 10 analytics panels across 3 tabs (Performance / Execution / Risk).
- Hero KPI strip + equity curve + recent activity on Overview.
- Empty / loading / error states for every panel.

**Out of scope**
- Multi-user dashboards (app stays single-user passcode auth).
- Mobile-first responsive layout. Desktop ≥ 1280 px is the design target;
  ≥ 1024 px works with a collapsed sidebar; smaller falls back to a
  hamburger sheet but we don't optimise the layout for it.
- Anomaly detection, ML, or recommendations engine. All "insights" are
  computed aggregates the user can trust by inspection.
- Email / push alerts (the admin Telegram bot, separate effort, owns alerts).
- Historical migration of pre-deployment trade data.

## 4. Stack additions

### Frontend (all bun-installable)

| Package | Why |
|---|---|
| `next-themes` | Dark / light / system with no flash on initial load. |
| `recharts` | Charting; React-native, declarative, theme-able via CSS vars. |
| `react-day-picker` + `date-fns` | Date-range picker for the global filter bar. |
| `@tanstack/react-table` | Sortable/filterable data tables (channel leaderboard, trades log). |
| `cmdk` | Powers the shadcn `<Command>` palette inside multi-select pickers. |

### shadcn primitives to add

`sidebar`, `sheet`, `tabs`, `select`, `dropdown-menu`, `popover`, `calendar`,
`switch`, `tooltip`, `separator`, `scroll-area`, `table`, `chart`, `skeleton`,
`command`, `toggle-group`.

Each is a single-file copy from the shadcn registry, all already wired to the
existing `--color-*` CSS-var tokens in `globals.css`.

### Backend

No new Python packages. New routes added to `autotrader/routers/stats.py` (or
a new `stats_v2.py` if the file grows past ~300 lines). SQL stays in pure
SQLAlchemy — no ORM change, no schema migration beyond two indices.

## 5. Visual system

### Theme tokens

`globals.css` already defines `--background`, `--foreground`, `--card`,
`--muted`, `--accent`, etc. for both `:root` (light) and `.dark`. Adjustments:

- Add `--success` (emerald-600 / emerald-400 in dark) and `--success-foreground`.
- Add `--warning`, `--info` token pairs (already used ad-hoc as `text-amber-300`
  in places — this normalises).
- Add a chart palette: `--chart-1` through `--chart-5` so Recharts can pick
  colors that read on both themes. Mirrors the shadcn chart cookbook.

### Theme switching

`next-themes` with `attribute="class"`, `defaultTheme="system"`,
`enableSystem`. Toggle lives in the sidebar footer.

The current `<html lang="en" className="dark">` hard-coding is removed; the
provider injects the class on hydration. Follow the next-themes
"suppressHydrationWarning" pattern on `<html>`.

### Typography

Keep system font stack but tighten heading letter-spacing to `-0.02em` for
20px+ sizes (matches the SaaS-style direction). Numerals across the UI use
`font-variant-numeric: tabular-nums` so columns of P&L don't shift width as
digits change.

## 6. Information architecture

### Sidebar

```
TRADE
  📊 Overview                    /dashboard
  📈 Analytics                   /dashboard/analytics
  🔄 Trades                      /dashboard/trades
  🌪 Decisions                   /dashboard/decisions
  ⚡ Pipeline                    /dashboard/pipeline

CONFIGURE
  🎯 Parsers                     /dashboard/parsers
  📡 Telegram                    /dashboard/telegram
  🏦 Broker                      /dashboard/broker

[footer]
  v0.7.1                         🌙 Dark
```

Collapsible to a 56-px icon rail. State persisted to localStorage.

### Topbar

- Left: breadcrumb + page title.
- Right: global status pill (`● Pipeline live · DEMO` / `● Idle · DEMO` /
  `⏸ Kill switch engaged · DEMO` / `🔴 Pipeline live · REAL`).

### Page responsibilities

| Page | Contents | Notes |
|---|---|---|
| `/dashboard` (Overview) | KPI hero strip · equity curve (toggleable 24h/7d/30d/all) · recent activity table · broker/telegram/pipeline status mini-cards | No filter bar. The "right now" page. |
| `/dashboard/analytics` | Filter bar · 3 tabs (Performance / Execution / Risk) · 10 panels | Tab persisted in URL hash. |
| `/dashboard/trades` | Filter bar · full sortable trades table | Replaces the trades section of the old Pipeline page. |
| `/dashboard/decisions` | Filter bar (channel + outcome only) · live decisions stream | Replaces the decisions section of the old Pipeline page. |
| `/dashboard/pipeline` | Master switch · kill switch · risk caps form · pipeline status panel | Controls only. No tables. Page shrinks from 1058 lines to ~250. |
| `/dashboard/broker` | Existing page, sidebar wrapper applied | No content changes Phase 1. |
| `/dashboard/telegram` | Existing page, sidebar wrapper applied | No content changes Phase 1. |
| `/dashboard/parsers` | Existing pages, sidebar wrapper applied | No content changes Phase 1. |

## 7. Filter system

### Schema

```ts
type AnalyticsFilters = {
  range: "24h" | "7d" | "30d" | "all" | "custom";
  from?: string;          // ISO; only when range=custom
  to?: string;            // ISO; only when range=custom
  channels?: number[];    // chat_ids; absent = all
  parsers?: number[];     // parser_config_ids; absent = all
  assets?: string[];      // resolved asset symbols; absent = all
  direction?: "call" | "put";  // absent = both
  statuses?: TradeStatus[];   // absent = all
};
```

### URL encoding

```
/dashboard/analytics?range=7d&channels=12345,67890&assets=EURUSD&direction=call#performance
```

- Multi-value params join with comma.
- Empty / default values are omitted (so a fresh URL is `?range=7d`, not
  `?range=7d&channels=&parsers=&...`).
- `from` / `to` only appear when `range=custom`.

### Component

`<FilterBar>` renders a row of pill triggers. Each pill opens a popover:

| Filter | Popover content |
|---|---|
| Range | Preset list (Today / Yesterday / Last 7d / Last 30d / All / Custom). Custom opens a `<Calendar>` for the from/to dates. |
| Channels | `<Command>` with checkbox list of watched channels + count badge per channel. |
| Parsers | `<Command>` with checkbox list grouped by channel. |
| Asset | `<Command>` with debounced fuzzy search over distinct asset symbols. |
| Direction | `<ToggleGroup>` Call / Put / Both. |
| Status | Multi-select checkbox list of the 6 statuses. |

State: a single `useFilters()` hook reads from `useSearchParams()` and writes
back via `router.replace()`. Components don't own filter state — the URL is
the source of truth.

### Filter scoping

- Overview page ignores all filters (always "right now" view).
- Analytics, Trades, Decisions all read the same `useFilters()` hook so a
  user can hop between tabs and keep the same scope.
- The URL persists the filter into the Pipeline / Parsers pages too — there
  it's silently ignored, so a back-button restores filter state on return.

## 8. Backend API additions

All new endpoints live under `/stats/v2/` and accept the same filter bag as
query params. They share a `_resolve_filters(query) → ResolvedFilter` helper
that:

1. Maps `range` → `(since, until)` UTC datetimes.
2. Validates filter values against the live `WatchedChannel` /
   `ParserConfig` rows (silently drops unknown ids — defensive against
   stale URLs).
3. Returns a `Where` clause builder consumable by SQLModel `select()`.

### Endpoints

#### `GET /stats/v2/timeseries`

Query: filter bag + `metric=equity|win_rate|latency_p50|latency_p99|outcomes` + `bucket=auto|hour|day|week`.

`metric=outcomes` is special: instead of `value` per point it returns
`counts: {won, lost, rejected, broker_error, expired, pending}` so the
streak-distribution stacked bar can read it in one round-trip.

Response:
```json
{
  "metric": "equity",
  "bucket": "hour",
  "points": [
    {"t": "2026-05-09T00:00:00Z", "value": 12.40, "n": 24},
    ...
  ],
  "filters_applied": {...echo of resolved filters...}
}
```

`bucket=auto` rule: ≤24h → 15-min, ≤7d → hour, ≤30d → day, all-time → day.

#### `GET /stats/v2/breakdown`

Query: filter bag + `dim=channel|parser|asset|direction|hour_of_week`.

Response (channel example):
```json
{
  "dim": "channel",
  "rows": [
    {
      "key": 12345,
      "label": "Scalp OTC",
      "total": 48,
      "won": 34, "lost": 14, "rejected": 0, "broker_error": 0, "expired": 0, "pending": 0,
      "win_rate": 0.708, "win_rate_ci_low": 0.561, "win_rate_ci_high": 0.821,
      "realised_pnl": 32.20,
      "committed_stake": 192.0,
      "sparkline": [0.5, 0.6, 0.7, 0.65, 0.71]
    }
  ]
}
```

`win_rate_ci_*`: Wilson score interval at 95%. `sparkline`: last 7 daily
win rates (or null if range too short).

For `dim=hour_of_week` rows are 168 cells (`weekday × hour`) — feeds the
heatmap.

For `dim=asset` an extra `direction_split` field gives `{call: {...}, put: {...}}`
sub-stats — feeds the asset×direction matrix in one round-trip.

For `dim=parser` an extra `streaks` sub-field gives an array of
`{length, ended_in: "won"|"lost", recovered: bool}` for each completed
streak in the range — feeds the martingale ladder analysis.

Per-`dim` extra fields are documented in the endpoint's response model
so callers know what to expect.

#### `GET /stats/v2/funnel`

Query: filter bag.

Response:
```json
{
  "stages": [
    {"key": "messages_received", "label": "Messages received", "count": 412},
    {"key": "matched", "label": "Parser matched", "count": 87},
    {"key": "passed_risk", "label": "Passed risk gate", "count": 63},
    {"key": "placed", "label": "Placed at broker", "count": 61},
    {"key": "settled", "label": "Settled (won + lost)", "count": 58}
  ],
  "drop_reasons": {
    "matched_to_passed_risk": [
      {"reason": "daily_loss_cap_exhausted", "count": 14},
      {"reason": "concurrency_cap", "count": 8},
      {"reason": "kill_switch_engaged", "count": 2}
    ],
    ...
  }
}
```

`messages_received` and `matched` come from the in-memory pipeline-decision
ring (or zero outside its retention window — documented in the panel's
empty state). The remaining stages come from `trade_attempts` rows with
`received_at` in range.

### DB indices

```sql
CREATE INDEX IF NOT EXISTS ix_trade_attempts_received_chat
  ON trade_attempts(received_at, chat_id);

CREATE INDEX IF NOT EXISTS ix_trade_attempts_received_parser
  ON trade_attempts(received_at, parser_config_id);
```

Added via the existing ALTER-style migration block in `db.py`. Verified by a
new test that `EXPLAIN QUERY PLAN` shows index use on the timeseries query.

### Endpoints staying untouched

- `/stats/overview` — kept, unused after Phase 2 ships, removed in Phase 3 cleanup.
- `/pipeline/trades`, `/pipeline/decisions` — the dedicated pages still read these.
- `/risk/overview` — Pipeline-controls page still uses it.
- `/feed/ws` — WebSocket trade feed is unchanged; analytics queries
  invalidate via existing TanStack Query keys on each `trade.upserted`.

## 9. Page specs

### 9.1 Overview (`/dashboard`)

**Layout:**

```
[ KPI hero (4 cards) ........................................ ]
[ Equity curve (full width) .................................. ]
[ Recent activity (8 cols) ] [ Status mini-cards (4 cols) ... ]
```

**KPI cards:**

| Card | Value | Delta | Source |
|---|---|---|---|
| P&L today | sum(profit) for today UTC | % vs avg P&L over last 7 days | `/stats/v2/timeseries?metric=equity&range=24h` + comparison query |
| Win rate | won / settled today | pp vs last 7 days | same endpoint with metric=win_rate |
| Trades | total today | breakdown "X settled · Y open · Z rejected" | `/stats/v2/breakdown?dim=channel&range=24h` rolled up |
| Risk budget left | `daily_max_loss − abs(min(0, realised))` | "of $X cap" | `/risk/overview` |

When `realised_pnl == 0` and there are no trades yet today, P&L card shows
"—" not "+$0.00" so the empty state is unambiguous.

**Equity curve:**

Range toggle (24h / 7d / 30d / All) wired to one `/stats/v2/timeseries`
call. Recharts `<AreaChart>` with the chart-1 token, gradient fill, dashed
zero-line. Hover tooltip: timestamp + cumulative P&L + trades-in-bucket.

**Recent activity:**

Last 8 settled-or-open trades, latest first. Live-patched by the existing
WebSocket hook. Click row → navigates to `/dashboard/trades?focus=<id>`
(Trades page scrolls + highlights).

**Status mini-cards:**

Three small panels stacked: Broker (connected? account mode? balance?),
Telegram (logged in? watched count? last message age), Pipeline (active?
kill switch? subscribed_chat_count mismatch warning).

### 9.2 Analytics (`/dashboard/analytics#performance|execution|risk`)

**Filter bar** (sticky, top of content area).

**Tab layout — Performance:**

```
[ 🏆 Channel leaderboard (8 cols) ]  [ 🎲 Asset×direction (4 cols) ]
[ 🎯 Parser-level performance · per channel (12 cols) ]
```

**Tab layout — Execution:**

```
[ 🕐 Hour-of-day heatmap (12 cols) ]
[ 🌪 Signal funnel (6 cols) ]  [ ⚡ Latency drift (6 cols) ]
```

**Tab layout — Risk:**

```
[ 🛡 Risk-cap utilization (12 cols, 3 gauges + reject counts) ]
[ 🪜 Martingale ladder ROI (6 cols) ]  [ 📊 Streak distribution (6 cols) ]
```

**Per-panel specs:** see §10.

### 9.3 Trades (`/dashboard/trades`)

Full sortable trades table (TanStack Table). Columns:
`Time · Channel · Parser · Asset · Dir · Stake · Status · P&L · Latency`.

Top-of-page filter bar shared with Analytics. Pagination cursor-based on
`(received_at desc, id desc)`. Click row to expand inline with raw signal
text + parser-decision context.

### 9.4 Decisions (`/dashboard/decisions`)

Live decisions feed. Filter bar shows only Channel and Outcome filters
(date range hidden — decisions ring is in-memory and time-bounded by
retention, not by user range).

Stream powered by the existing `pipeline.decision` WebSocket frame; same
ring-buffer cap (200) as today.

### 9.5 Pipeline (`/dashboard/pipeline`)

What survives from the old monolith:
- Status card (broker connected, telegram logged in, pipeline active /
  kill switch state, subscribed-chat mismatch warning).
- Master toggle + kill switch.
- Risk caps form.
- Today's budget snapshot.
- Streaks table.

What moves out:
- Stats overview (channel breakdown + latency tiles) → Analytics.
- Decisions feed → /dashboard/decisions.
- Trades table → /dashboard/trades.

Result: ~250-line page focused on operational control.

## 10. Panel specs

### 10.1 Equity curve over time (Overview only)

- Chart: Recharts `<AreaChart>` + gradient.
- Source: `GET /stats/v2/timeseries?metric=equity&range=...`.
- Range toggle: 24h / 7d / 30d / All.
- Empty state (`points: []`): "No settled trades in this range yet."

### 10.2 Hour-of-day × weekday heatmap (Execution tab)

- Layout: 7×24 CSS grid (custom; ~60 lines, no Recharts).
- Cell color: `--chart-3` at opacity `(win_rate − 0.5) × 2` clamped to
  [0.1, 1]. Cells with `n < 3` rendered as muted "—".
- Hover tooltip: hour, weekday, n, win_rate, realised_pnl.
- Insight strip below: "Worst window: Fri 03:00 UTC · 12% wr (n=18)".
- Source: `GET /stats/v2/breakdown?dim=hour_of_week&range=...`.

### 10.3 Channel leaderboard (Performance tab)

- Table (TanStack Table, sortable on every column).
- Cols: `Channel · N · WR · WR conf. (Wilson 95%) · P&L · Committed · Sparkline`.
- Conf. badge: `solid` (n≥10 + ci.low > 0.5), `thin n` (n<10), `unsure` (n≥10 but ci straddles 0.5).
- Sparkline: 7-day daily win rate, Recharts `<LineChart>` mini-mode.
- Source: `GET /stats/v2/breakdown?dim=channel&range=...`.

### 10.4 Asset × direction matrix (Performance tab)

- Layout: CSS grid, asset rows × {Call, Put} cols.
- Cell content: `WR% · n` with cell color from chart palette by win rate.
- Cells with `n < 3` rendered muted.
- Insight strip: highlights largest call-vs-put asymmetry by asset.
- Source: `GET /stats/v2/breakdown?dim=asset&range=...` (uses
  `direction_split` sub-field).

### 10.5 Parser-level performance (Performance tab)

- Layout: per-channel grouped horizontal bars.
- For each parser within a channel: bar width = win rate, bar color = sign
  of P&L, label = parser name, right-aligned P&L value.
- Source: `GET /stats/v2/breakdown?dim=parser&range=...`.

### 10.6 Signal funnel (Execution tab)

- Layout: horizontal funnel; 5 stages from §8.
- Each stage shows count + drop label vs previous.
- Drop-reasons table below: clickable to expand the breakdown returned by
  the endpoint.
- Source: `GET /stats/v2/funnel?range=...`.

### 10.7 Latency drift (Execution tab)

- Chart: Recharts `<LineChart>` with two series (signal→place, place→settle).
- Each series shows p50 + a translucent p99 band.
- Y-axis: log scale (0.1ms → 60s spans many orders).
- Source: `GET /stats/v2/timeseries?metric=latency_p50` and `latency_p99`
  in two parallel queries (same range/bucket).

### 10.8 Risk-cap utilization (Risk tab)

- Layout: 3 gauge tiles (daily loss / daily stake / concurrency) + reject-count strip.
- Gauges: Recharts `<RadialBarChart>` with current ÷ cap; turn amber at
  80%, red at 100%.
- Reject strip: "Today: 14 rejected by daily-loss cap · 8 by concurrency · 2 by kill switch".
- Source: `/risk/overview` for caps + budget; `GET /stats/v2/breakdown?dim=channel&statuses=rejected` for reject counts grouped by reason (reason currently in `error` column — needs a small backend tweak to also expose a `rejection_reason` enum; see Risks).

### 10.9 Martingale ladder ROI (Risk tab)

- Layout: per-parser histogram of streak length with overlay of "would-have-been-recovered" rate.
- Y-axis: count of streaks of each length.
- Toggle: "without martingale" recomputes assuming flat base stake.
- Source: derived client-side from `GET /stats/v2/breakdown?dim=parser&range=...`
  augmented with a `streaks` sub-field listing streak lengths and outcomes.

### 10.10 Streak & outcome distribution (Risk tab)

- Chart: stacked bar by week.
- Stack order: won (green) · lost (red) · rejected (grey) · expired (amber) · broker_error (purple).
- Hover: counts and percentages per status.
- Source: `GET /stats/v2/timeseries?metric=outcomes&bucket=week`
  (returns parallel arrays for each status).

## 11. Phasing

### Phase 1 — Foundation (one PR)

**Goal:** modern UI shell on the same data.

- Add `next-themes`, theme toggle, theme tokens (`--success`, `--warning`,
  `--info`, `--chart-1..5`).
- Install/adopt the shadcn primitives listed in §4.
- Build `<AppSidebar>`, `<AppTopbar>`, route shell.
- Move `Pipeline` page contents into the new pages: extract trades into
  `/dashboard/trades`, decisions into `/dashboard/decisions`. Pipeline
  page becomes controls-only.
- Build new Overview page reusing existing `/stats/overview`,
  `/risk/overview`, `/pipeline/status` data — no new backend.
- KPI hero strip uses existing data; equity curve is a stub
  ("coming in Phase 2").
- All 195 backend tests still pass; type-check passes; visual review.

### Phase 2 — Analytics core (one PR)

**Goal:** the 5 highest-leverage panels.

- Add the two DB indices.
- Implement `_resolve_filters` helper + the three `/stats/v2/*` endpoints.
- Implement `<FilterBar>` + `useFilters()` hook.
- Build the `/dashboard/analytics` page shell + 3 tabs.
- Make the Overview equity curve live against `/stats/v2/timeseries`
  (it was a stub in Phase 1).
- Ship 4 Analytics-tab panels: hour-of-day heatmap, channel leaderboard,
  asset×direction matrix, signal funnel. (5 in total counting the equity curve.)
- Backend tests: golden-fixture tests on each endpoint covering the empty
  case, the single-trade case, and a 200-row case spanning channels and
  parsers.
- Frontend type-check + manual visual review.

### Phase 3 — Analytics depth (one PR)

**Goal:** finish the panel set + polish.

- Ship remaining panels: parser comparison, latency drift, risk-cap
  utilization, martingale ladder ROI, streak distribution.
- Add `rejection_reason` enum column to `trade_attempts` (default to
  parsing the existing `error` text for legacy rows on read; new rows
  populate at write time in the risk gate).
- Empty / loading / error states polish on every panel.
- Remove `/stats/overview` (legacy), update old callers.
- Add a basic Playwright smoke test covering: login → Overview renders →
  switch theme → navigate to Analytics → change filter → page updates.

## 12. Testing

**Backend:**
- New endpoint tests in `backend/tests/test_stats_v2.py`. One golden
  fixture per endpoint per shape (empty / single / many).
- Index test verifying `EXPLAIN QUERY PLAN` uses the new indices on
  representative queries.

**Frontend:**
- `bun run type-check` continues to pass each phase.
- Visual smoke checklist run before each PR: login → Overview KPIs → theme
  toggle (dark/light/system) → all pages reachable from sidebar → filter
  changes update analytics → mobile sheet collapse works at < 1024 px.
- Phase 3 adds the Playwright smoke test above.

**No regressions on:** existing `/stats/overview`, `/pipeline/*`,
`/risk/*`, `/feed/ws` — kept until explicitly replaced.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| 30d / all-time queries get slow on SQLite | Two composite indices + `EXPLAIN QUERY PLAN` test asserting index use. If a future deployment grows past ~100k rows we'll add nightly daily-rollup table; not now. |
| Recharts adds 75 KB gzip to client bundle | Acceptable for a self-hosted dashboard. Tree-shake unused charts; use Next.js dynamic imports for heavy panels (martingale histogram especially). |
| Wilson CI computation drift between server and client | Server is the only computer of CI. Client renders what comes back. |
| Decomposing the 1058-line Pipeline page introduces bugs | Phase 1 is mechanical extraction — no behavior changes. Add a snapshot-style test on the new pages confirming the same components render with same props. |
| `next-themes` flash of unstyled content | Standard pattern: `suppressHydrationWarning` on `<html>`, theme provider as the first child of `<body>`, `class` attribute strategy not data-attr. |
| Existing dashboards break during sidebar migration | Phase 1 keeps every existing route; only the chrome around them changes. Smoke checklist explicitly covers each. |
| Filter URL changes spam history | Use `router.replace()` not `router.push()`, debounce text inputs (asset search) by 250ms. |
| `rejection_reason` backfill for legacy `error` text rows | Read-time best-effort regex; new rows write the enum directly. Document the legacy gap in the Risk panel ("counts before <date> are estimated"). |

## 14. Open questions to revisit during planning

- Do we want a "compare" mode (last 7d vs previous 7d) on the analytics
  panels? Not in this design; flag for backlog.
- Should the Decisions page persist the in-memory ring to disk for
  longer history? Currently no — decided "logs are for that".
- Sentry breadcrumbs for filter / panel interactions? Defer; not
  blocking.

## 15. Delivery notes

- Each phase = its own branch + PR. Branch name pattern:
  `claude/ui-modernization-phase-1-foundation` etc.
- Each PR description quotes the relevant section of this spec.
- After Phase 3 lands, this spec is marked **Implemented** and a
  retrospective note appended (what changed, what we deferred).
