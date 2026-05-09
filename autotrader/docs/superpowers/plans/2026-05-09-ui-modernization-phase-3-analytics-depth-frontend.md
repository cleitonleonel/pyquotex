# UI Modernization Phase 3 — Analytics Depth (Part B: Frontend)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Depends on Part A backend tasks (1–3) being committed on the same branch first.**

**Goal:** Land the five Phase-3 deferred analytics panels (parser comparison, latency drift, risk-cap utilisation, martingale ROI, streak distribution), an asset filter pill backed by `/stats/v2/assets`, a sign-out user menu in the sidebar footer, a Playwright smoke harness, and removal of the now-unused legacy `/stats/overview` client.

**Architecture:** Each new panel is a self-contained client component under `frontend/app/dashboard/_components/` driven by a single `useQuery` against `/stats/v2/breakdown` (or in two cases, derived from the existing equity/leaderboard data already in cache). All panels read filter state via the existing `useFilters()` hook so the global filter bar drives everything. The sign-out menu reuses the auth helper that the legacy `/dashboard/page.tsx` flow uses — no new auth code, just relocation into the sidebar footer using `<DropdownMenu>` from shadcn.

**Tech Stack:** Next.js 15 App Router, React 19, TanStack Query v5, Recharts v3, shadcn/ui, Tailwind v4, Playwright (new — `@playwright/test`).

---

## File Structure

**Modify:**
- `frontend/lib/api-stats-v2.ts` — add `assets()` client + `streaks` field on the breakdown row type
- `frontend/lib/use-filters.ts` — already supports `assets` (verified); pass through to v2 calls
- `frontend/app/dashboard/_components/filter-bar.tsx` — register `<FilterPillAssets />`
- `frontend/app/dashboard/_components/overview-kpi-hero.tsx` — replace legacy `/stats/overview` call with v2 totals
- `frontend/app/dashboard/_components/panel-signal-funnel.tsx` — surface `messages_received_window` label
- `frontend/components/app-sidebar.tsx` — render `<UserMenu />` in `<SidebarFooter>`
- `frontend/lib/api.ts` — delete the `overview()` export (legacy)
- `frontend/package.json` + `frontend/playwright.config.ts` (new) — Playwright wiring

**Create:**
- `frontend/app/dashboard/_components/filter-pill-assets.tsx`
- `frontend/app/dashboard/_components/panel-parser-comparison.tsx`
- `frontend/app/dashboard/_components/panel-latency-drift.tsx`
- `frontend/app/dashboard/_components/panel-risk-cap-utilisation.tsx`
- `frontend/app/dashboard/_components/panel-martingale-roi.tsx`
- `frontend/app/dashboard/_components/panel-streak-distribution.tsx`
- `frontend/components/user-menu.tsx`
- `frontend/e2e/dashboard.spec.ts`

---

## Task 0: Pre-flight (verify Part A is in place)

- [ ] **Step 1: Confirm Part A commits are on the branch**

```bash
cd /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader
git log --oneline -5
```

Expected: top three commits are the Part A tasks (assets endpoint, parser streaks, funnel ring wiring) plus the docs commit. If they're missing, stop and execute Part A first.

- [ ] **Step 2: Bring the frontend env up**

```bash
cd frontend
pnpm install
```

Expected: `Done in Ns` with no errors. If `pnpm` is not on PATH, use `npm install` (the lockfile is `pnpm-lock.yaml` but `npm` will produce a working `node_modules`; do not commit a regenerated `package-lock.json`).

- [ ] **Step 3: Baseline build + typecheck**

```bash
cd frontend
pnpm typecheck
pnpm lint
```

Expected: zero errors. This is the green starting point — every new task should keep it green.

---

## Task 1: Asset filter pill

**Why this exists:** The asset filter pill was the single user-facing Phase 3 deferral that requires a backend endpoint (Part A Task 1 just shipped it). Wiring it makes "show me only EURUSD across the last 7 days" possible — the highest-leverage filter for an operator triaging a single instrument's drift.

**Files:**
- Create: `frontend/app/dashboard/_components/filter-pill-assets.tsx`
- Modify: `frontend/lib/api-stats-v2.ts` — add typed `assets()` call
- Modify: `frontend/app/dashboard/_components/filter-bar.tsx` — register the pill

- [ ] **Step 1: Add the typed client**

In `frontend/lib/api-stats-v2.ts`, append to the existing `statsV2` object literal:

```ts
  assets: (params: { range?: string; from?: string; to?: string } = {}) =>
    api<{ assets: string[] }>(
      `/stats/v2/assets?${qs(params)}`,
    ),
```

Reuse the existing `qs()` query-string helper already in the file. Don't introduce a new one.

- [ ] **Step 2: Build the pill**

Create `frontend/app/dashboard/_components/filter-pill-assets.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

import { FilterPillMultiSelect } from "./filter-pill-multiselect";

/**
 * Asset filter pill — populated from /stats/v2/assets so the option set
 * tracks whatever has actually traded in the current range. Refetches
 * when the range changes; the rest of useFilters output is intentionally
 * not in the queryKey because changing chats/parsers should NOT shrink
 * the asset universe (operator may want to pivot back).
 */
export function FilterPillAssets() {
  const { filters, setFilters } = useFilters();

  const { data, isLoading } = useQuery({
    queryKey: ["stats-v2-assets", filters.range, filters.from, filters.to],
    queryFn: () =>
      statsV2.assets({
        range: filters.range,
        from: filters.from,
        to: filters.to,
      }),
  });

  const options = useMemo(
    () => (data?.assets ?? []).map((a) => ({ value: a, label: a })),
    [data],
  );

  return (
    <FilterPillMultiSelect
      label="Asset"
      options={options}
      values={filters.assets ?? []}
      onChange={(next) => setFilters({ assets: next })}
      placeholder={isLoading ? "Loading…" : "All assets"}
    />
  );
}
```

- [ ] **Step 3: Register in the filter bar**

In `frontend/app/dashboard/_components/filter-bar.tsx`, import and place the new pill **after** the existing parser pill and **before** the direction pill (so the order reads: Range → Chats → Parsers → Assets → Direction):

```tsx
import { FilterPillAssets } from "./filter-pill-assets";

// ... inside the JSX, after the Parsers FilterPillMultiSelect:
<FilterPillAssets />
```

- [ ] **Step 4: Manual smoke**

```bash
cd frontend
pnpm dev
```

Open `http://localhost:3000/dashboard/analytics`. The Asset pill should populate with whatever symbols exist in the test DB. Selecting one should append `?assets=EURUSD` to the URL. Reloading should restore the selection.

- [ ] **Step 5: Lint + commit**

```bash
cd frontend
pnpm lint
git add lib/api-stats-v2.ts app/dashboard/_components/filter-pill-assets.tsx app/dashboard/_components/filter-bar.tsx
git commit -m "feat(autotrader/frontend): asset filter pill backed by /stats/v2/assets"
```

---

## Task 2: Migrate OverviewKpiHero off `/stats/overview`

**Why this exists:** `OverviewKpiHero` is the only remaining caller of the legacy endpoint. Migrating it lets us delete `/stats/overview` from the client (Task 10) and removes a parallel data path that doesn't honour the global filter bar.

**Replacement:** A single `/stats/v2/breakdown?dim=channel` call gives us per-channel rows whose totals roll up to the same numbers `/stats/overview` returns, but filtered by the current pills.

**Files:**
- Modify: `frontend/app/dashboard/_components/overview-kpi-hero.tsx`

- [ ] **Step 1: Read the current hero**

```bash
cd /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader/frontend
cat app/dashboard/_components/overview-kpi-hero.tsx
```

Note the four KPI tiles it renders (trades / win rate / pnl / pending or similar) and the loading/error patterns it uses — preserve those.

- [ ] **Step 2: Swap the data source**

Replace the existing `useQuery` block with:

```tsx
import { useFilters } from "@/lib/use-filters";
import { statsV2 } from "@/lib/api-stats-v2";

const { filters } = useFilters();
const { data, isLoading, isError } = useQuery({
  queryKey: ["overview-hero", filters],
  queryFn: () =>
    statsV2.breakdown({
      dim: "channel",
      range: filters.range,
      from: filters.from,
      to: filters.to,
      chats: filters.chats?.join(","),
      parsers: filters.parsers?.join(","),
      assets: filters.assets?.join(","),
      direction: filters.direction,
    }),
});

// Roll rows up into headline numbers.
const totals = (data?.rows ?? []).reduce(
  (acc, r) => ({
    trades: acc.trades + r.trades,
    won: acc.won + r.won,
    lost: acc.lost + r.lost,
    pnl: acc.pnl + r.pnl,
  }),
  { trades: 0, won: 0, lost: 0, pnl: 0 },
);
const winRate =
  totals.won + totals.lost > 0
    ? totals.won / (totals.won + totals.lost)
    : null;
```

Drive the four tiles from `totals.trades`, `winRate`, `totals.pnl`, and (replace the legacy "pending" tile with) `totals.won + totals.lost` labelled "settled". If the hero used a different fourth metric, keep that intent — the goal is parity, not redesign.

- [ ] **Step 3: Manual smoke**

`pnpm dev` and open `/dashboard`. The hero numbers should match what `/dashboard/analytics` shows in its first row (both go through `/stats/v2/breakdown` now).

- [ ] **Step 4: Lint + commit**

```bash
cd frontend
pnpm lint
git add app/dashboard/_components/overview-kpi-hero.tsx
git commit -m "refactor(autotrader/frontend): hero KPIs read from /stats/v2/breakdown"
```

---

## Task 3: Sign-out user menu in sidebar footer

**Why this exists:** Spec §11 deferral. The dashboard currently has no visible way to sign out — the only `logout` mutation is buried in `/dashboard/telegram/page.tsx` and clears the Telegram session, not the operator session.

**Files:**
- Create: `frontend/components/user-menu.tsx`
- Modify: `frontend/components/app-sidebar.tsx` — render `<UserMenu />` in `<SidebarFooter>` next to `<ThemeToggle />`

- [ ] **Step 1: Build the user menu component**

Create `frontend/components/user-menu.tsx`:

```tsx
"use client";

import { LogOut, User } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { clearAuthToken } from "@/lib/api";

/**
 * Sidebar-footer user menu. The "user" today is whoever holds the
 * bearer token in localStorage — there is no /me endpoint yet — so we
 * show a generic label and a single Sign out action that clears the
 * token and bounces to /login.
 */
export function UserMenu() {
  const router = useRouter();

  const onSignOut = () => {
    clearAuthToken();
    router.push("/login");
    router.refresh();
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start gap-2"
          aria-label="Account menu"
        >
          <User className="h-4 w-4" />
          <span className="text-sm">Operator</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem disabled className="text-xs text-muted-foreground">
          Signed in via bearer token
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onSignOut}>
          <LogOut className="mr-2 h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

If `clearAuthToken` does not exist in `lib/api.ts`, add it as a one-liner: `export const clearAuthToken = () => localStorage.removeItem("auth_token");` (or whatever key the existing `getAuthToken` reads from — match it exactly).

If `dropdown-menu` is not installed yet:

```bash
cd frontend
pnpm dlx shadcn@latest add dropdown-menu --yes --overwrite
```

(`--overwrite` is needed because `--yes` alone doesn't suppress the prompt — same gotcha that bit us in Phase 1.)

- [ ] **Step 2: Slot into the sidebar footer**

In `frontend/components/app-sidebar.tsx`, replace the `<SidebarFooter>` body:

```tsx
import { UserMenu } from "@/components/user-menu";

// ...

<SidebarFooter>
  <UserMenu />
  <ThemeToggle />
</SidebarFooter>
```

Order matters: `UserMenu` first so the operator's "scope" and the theme toggle share the footer with the most common action on top.

- [ ] **Step 3: Manual smoke**

`pnpm dev`. The sidebar footer should show "Operator" with a chevron + the theme toggle. Click "Operator" → "Sign out" → expect to land on `/login` with localStorage cleared.

- [ ] **Step 4: Lint + commit**

```bash
cd frontend
pnpm lint
git add components/user-menu.tsx components/app-sidebar.tsx lib/api.ts
git add -A components/ui/  # picks up dropdown-menu.tsx if shadcn added it
git commit -m "feat(autotrader/frontend): sign-out user menu in sidebar footer"
```

---

## Task 4: Panel — Parser comparison

**Why this exists:** Operators run multiple parsers per channel and need to see which parser is producing the best EV/winrate so they can disable laggards. Spec §11 deferral.

**Data source:** `/stats/v2/breakdown?dim=parser` (already exists from Phase 2). Renders as a sortable table with parser name, trades, win rate (with Wilson CI band classification from the existing helper), pnl, and best-streak indicator.

**Files:**
- Create: `frontend/app/dashboard/_components/panel-parser-comparison.tsx`
- Modify: `frontend/app/dashboard/analytics/page.tsx` — slot into the "Parsers" tab

- [ ] **Step 1: Build the panel**

Create `frontend/app/dashboard/_components/panel-parser-comparison.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { statsV2, type BreakdownRow } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";
import { wilsonBandClass } from "@/lib/wilson-format";

const columns: ColumnDef<BreakdownRow>[] = [
  { accessorKey: "label", header: "Parser" },
  { accessorKey: "trades", header: "Trades" },
  {
    id: "winRate",
    header: "Win rate",
    accessorFn: (r) => (r.won + r.lost > 0 ? r.won / (r.won + r.lost) : 0),
    cell: ({ row, getValue }) => {
      const rate = getValue<number>();
      return (
        <span className={wilsonBandClass(row.original.wilson_lower)}>
          {(rate * 100).toFixed(1)}%
        </span>
      );
    },
    sortingFn: "basic",
  },
  {
    accessorKey: "pnl",
    header: "PnL",
    cell: ({ getValue }) => `$${getValue<number>().toFixed(2)}`,
  },
  {
    id: "longestLoss",
    header: "Longest loss streak",
    accessorFn: (r) =>
      (r.streaks as { longest_loss?: number } | undefined)?.longest_loss ?? 0,
  },
];

export function PanelParserComparison() {
  const { filters } = useFilters();
  const [sorting, setSorting] = useState<SortingState>([
    { id: "winRate", desc: true },
  ]);

  const { data, isLoading } = useQuery({
    queryKey: ["parser-comparison", filters],
    queryFn: () =>
      statsV2.breakdown({
        dim: "parser",
        range: filters.range,
        from: filters.from,
        to: filters.to,
        chats: filters.chats?.join(","),
        parsers: filters.parsers?.join(","),
        assets: filters.assets?.join(","),
        direction: filters.direction,
      }),
  });

  const table = useReactTable({
    data: data?.rows ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Parser comparison</CardTitle>
        <CardDescription>
          Which parser is pulling its weight in the current window. Sort by
          any column.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((h) => (
                    <TableHead
                      key={h.id}
                      onClick={h.column.getToggleSortingHandler()}
                      className="cursor-pointer select-none"
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell ?? cell.column.columnDef.header, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Update the BreakdownRow type**

In `frontend/lib/api-stats-v2.ts`, extend the `BreakdownRow` type definition with the new optional `streaks` field (matches Part A Task 2 backend output):

```ts
export type ParserStreaks = {
  longest_loss: number;
  histogram: Record<string, number>;
  recovered_count: number;
  recovery_rate: number;
};

export type BreakdownRow = {
  // ... existing fields ...
  streaks?: ParserStreaks;
};
```

- [ ] **Step 3: Slot into the analytics page**

In `frontend/app/dashboard/analytics/page.tsx`, add a new `<TabsContent value="parsers">` (or extend the existing one) containing `<PanelParserComparison />`.

- [ ] **Step 4: Lint + commit**

```bash
cd frontend
pnpm lint
git add lib/api-stats-v2.ts app/dashboard/_components/panel-parser-comparison.tsx app/dashboard/analytics/page.tsx
git commit -m "feat(autotrader/frontend): parser comparison panel"
```

---

## Task 5: Panel — Latency drift

**Why this exists:** When the broker WS slows down, expirations drift past the optimal entry window and PnL silently degrades. Surfacing latency p50/p95 over time turns "the bot feels slow" into a measurable signal. Spec §11 deferral.

**Data source:** `/stats/v2/timeseries` already returns `latency_ms_p50` and `latency_ms_p95` per bucket (verified in Phase 2). We render a dual-line chart.

**Files:**
- Create: `frontend/app/dashboard/_components/panel-latency-drift.tsx`
- Modify: `frontend/app/dashboard/analytics/page.tsx`

- [ ] **Step 1: Build the panel**

Create `frontend/app/dashboard/_components/panel-latency-drift.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

export function PanelLatencyDrift() {
  const { filters } = useFilters();

  const { data, isLoading } = useQuery({
    queryKey: ["latency-drift", filters],
    queryFn: () =>
      statsV2.timeseries({
        range: filters.range,
        from: filters.from,
        to: filters.to,
        chats: filters.chats?.join(","),
        parsers: filters.parsers?.join(","),
        assets: filters.assets?.join(","),
        direction: filters.direction,
      }),
  });

  const series = (data?.buckets ?? []).map((b) => ({
    t: b.bucket_start,
    p50: b.latency_ms_p50 ?? 0,
    p95: b.latency_ms_p95 ?? 0,
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Latency drift</CardTitle>
        <CardDescription>
          Broker round-trip p50 / p95 over the window. Sustained p95 climb
          usually means the WS feed is degrading before the bot notices.
        </CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="t" />
              <YAxis unit="ms" />
              <Tooltip
                formatter={(v) =>
                  typeof v === "number" ? `${v.toFixed(0)} ms` : String(v)
                }
              />
              <Line
                type="monotone"
                dataKey="p50"
                stroke="var(--chart-1)"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="p95"
                stroke="var(--chart-3)"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Slot in + commit**

```bash
cd frontend
pnpm lint
git add app/dashboard/_components/panel-latency-drift.tsx app/dashboard/analytics/page.tsx
git commit -m "feat(autotrader/frontend): latency drift panel (p50 + p95 over time)"
```

---

## Task 6: Panel — Risk-cap utilisation

**Why this exists:** When the daily cap fires, the bot stops trading but the operator only sees an absence of new attempts. A utilisation panel makes "we used 87% of today's cap" first-class so operators can decide to widen or tighten it. Spec §11 deferral.

**Data source:** `/stats/v2/breakdown?dim=channel` returns per-channel `pnl` and `risk_rejected_count`. Combined with the static `daily_loss_cap` from `/parsers` config (already in the parsers query cache from Phase 1), we can compute used vs cap per channel.

**Files:**
- Create: `frontend/app/dashboard/_components/panel-risk-cap-utilisation.tsx`
- Modify: `frontend/app/dashboard/analytics/page.tsx`

- [ ] **Step 1: Build the panel**

Create `frontend/app/dashboard/_components/panel-risk-cap-utilisation.tsx`:

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

type ParserConfig = {
  id: number;
  chat_id: number;
  daily_loss_cap: number;
  enabled: boolean;
};

export function PanelRiskCapUtilisation() {
  const { filters } = useFilters();

  const { data: breakdown, isLoading: bLoading } = useQuery({
    queryKey: ["risk-cap-breakdown", filters],
    queryFn: () =>
      statsV2.breakdown({
        dim: "channel",
        range: filters.range,
        from: filters.from,
        to: filters.to,
        chats: filters.chats?.join(","),
        parsers: filters.parsers?.join(","),
        assets: filters.assets?.join(","),
        direction: filters.direction,
      }),
  });

  const { data: parsers } = useQuery({
    queryKey: ["parsers-list-for-caps"],
    queryFn: () => api<ParserConfig[]>("/parsers"),
  });

  // Roll caps up per chat (sum of enabled parser caps).
  const capsByChat = new Map<number, number>();
  for (const p of parsers ?? []) {
    if (!p.enabled) continue;
    capsByChat.set(p.chat_id, (capsByChat.get(p.chat_id) ?? 0) + p.daily_loss_cap);
  }

  const rows = (breakdown?.rows ?? []).map((r) => {
    const cap = capsByChat.get(Number(r.key)) ?? 0;
    const used = Math.max(0, -r.pnl); // Cap counts losses; negative pnl = drawdown.
    const pct = cap > 0 ? Math.min(1, used / cap) : 0;
    return { name: r.label, used, cap, pct };
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Risk-cap utilisation</CardTitle>
        <CardDescription>
          Drawdown against each channel's daily loss cap. Bars approach 100%
          when the cap is about to halt trading.
        </CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {bLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} domain={[0, 1]} />
              <Tooltip
                formatter={(v, _, ctx) => {
                  const r = ctx.payload as { used: number; cap: number; pct: number };
                  return [`$${r.used.toFixed(2)} / $${r.cap.toFixed(2)} (${(r.pct * 100).toFixed(0)}%)`, "Used"];
                }}
              />
              <Legend />
              <Bar dataKey="pct" name="Cap used">
                {rows.map((r, i) => (
                  <Cell
                    key={i}
                    fill={
                      r.pct >= 0.9
                        ? "var(--destructive)"
                        : r.pct >= 0.6
                          ? "var(--warning)"
                          : "var(--chart-1)"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Slot in + commit**

```bash
cd frontend
pnpm lint
git add app/dashboard/_components/panel-risk-cap-utilisation.tsx app/dashboard/analytics/page.tsx
git commit -m "feat(autotrader/frontend): risk-cap utilisation panel"
```

---

## Task 7: Panel — Martingale ladder ROI

**Why this exists:** Operators using martingale need to see whether the recovery legs actually recover or just compound losses. Spec §11 deferral. Backed entirely by the new `streaks` sub-stats from Part A Task 2.

**Files:**
- Create: `frontend/app/dashboard/_components/panel-martingale-roi.tsx`
- Modify: `frontend/app/dashboard/analytics/page.tsx`

- [ ] **Step 1: Build the panel**

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

export function PanelMartingaleRoi() {
  const { filters } = useFilters();

  const { data, isLoading } = useQuery({
    queryKey: ["martingale-roi", filters],
    queryFn: () =>
      statsV2.breakdown({
        dim: "parser",
        range: filters.range,
        from: filters.from,
        to: filters.to,
        chats: filters.chats?.join(","),
        parsers: filters.parsers?.join(","),
        assets: filters.assets?.join(","),
        direction: filters.direction,
      }),
  });

  const rows = (data?.rows ?? [])
    .filter((r) => r.streaks && r.streaks.longest_loss > 0)
    .map((r) => ({
      label: r.label,
      recoveryRate: r.streaks!.recovery_rate,
      longest: r.streaks!.longest_loss,
      recovered: r.streaks!.recovered_count,
    }))
    .sort((a, b) => b.recoveryRate - a.recoveryRate);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Martingale ladder ROI</CardTitle>
        <CardDescription>
          For each parser, fraction of losing streaks that ended in a win
          (vs void / abort). Low recovery + long ladders is the danger
          combination.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No closed losing streaks in this window.
          </p>
        ) : (
          rows.map((r) => (
            <div key={r.label} className="space-y-1">
              <div className="flex items-center justify-between text-sm">
                <span>{r.label}</span>
                <span className="text-muted-foreground">
                  {r.recovered} / {r.recovered + Math.max(1, Math.round(r.recovered / Math.max(0.001, r.recoveryRate))) - r.recovered}{" "}
                  recovered · longest {r.longest}
                </span>
              </div>
              <Progress value={r.recoveryRate * 100} />
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Slot in + commit**

```bash
cd frontend
pnpm lint
git add app/dashboard/_components/panel-martingale-roi.tsx app/dashboard/analytics/page.tsx
git commit -m "feat(autotrader/frontend): martingale ladder ROI panel"
```

If the `Progress` shadcn primitive isn't installed: `pnpm dlx shadcn@latest add progress --yes --overwrite` and include `components/ui/progress.tsx` in the commit.

---

## Task 8: Panel — Streak distribution

**Why this exists:** Histogram view of streak lengths per parser — complements the martingale panel by showing the *shape* of pain, not just recovery rate. Spec §11 deferral.

**Files:**
- Create: `frontend/app/dashboard/_components/panel-streak-distribution.tsx`
- Modify: `frontend/app/dashboard/analytics/page.tsx`

- [ ] **Step 1: Build the panel**

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

export function PanelStreakDistribution() {
  const { filters } = useFilters();
  const [parserKey, setParserKey] = useState<string | undefined>(undefined);

  const { data, isLoading } = useQuery({
    queryKey: ["streak-dist", filters],
    queryFn: () =>
      statsV2.breakdown({
        dim: "parser",
        range: filters.range,
        from: filters.from,
        to: filters.to,
        chats: filters.chats?.join(","),
        parsers: filters.parsers?.join(","),
        assets: filters.assets?.join(","),
        direction: filters.direction,
      }),
  });

  const parserRows = (data?.rows ?? []).filter((r) => r.streaks);
  const selected =
    parserRows.find((r) => String(r.key) === parserKey) ?? parserRows[0];

  const histogram = Object.entries(selected?.streaks?.histogram ?? {})
    .map(([len, count]) => ({ len: Number(len), count }))
    .sort((a, b) => a.len - b.len);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Streak distribution</CardTitle>
        <CardDescription>
          Closed losing streak lengths for the selected parser. Tail length
          tells you what your worst-day kelly assumption needs to absorb.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Select
          value={parserKey ?? (selected ? String(selected.key) : undefined)}
          onValueChange={setParserKey}
        >
          <SelectTrigger className="w-64">
            <SelectValue placeholder="Choose a parser" />
          </SelectTrigger>
          <SelectContent>
            {parserRows.map((r) => (
              <SelectItem key={String(r.key)} value={String(r.key)}>
                {r.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="h-64">
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={histogram}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="len" label={{ value: "Streak length", position: "insideBottom", offset: -4 }} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" fill="var(--chart-2)" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Slot in + commit**

```bash
cd frontend
pnpm lint
git add app/dashboard/_components/panel-streak-distribution.tsx app/dashboard/analytics/page.tsx
git commit -m "feat(autotrader/frontend): streak distribution panel"
```

---

## Task 9: Surface `messages_received_window` in funnel panel

**Why this exists:** Part A Task 3 renamed the meaning of the funnel's top stages from "all zeros" to "last N from in-process ring." The frontend has to label that honestly so operators don't misread it as range-scoped.

**Files:**
- Modify: `frontend/app/dashboard/_components/panel-signal-funnel.tsx`

- [ ] **Step 1: Update the description and stage label**

Read the panel and locate where the `messages_received` value renders. Add a small `<Badge>` next to the stage label that reads `last 200 from ring` whenever `data.messages_received_window === "ring"`:

```tsx
import { Badge } from "@/components/ui/badge";

// ... where the messages_received stage renders:
<div className="flex items-center gap-2">
  <span>Messages received</span>
  {data?.messages_received_window === "ring" && (
    <Badge variant="outline" className="text-xs">
      last 200 from ring
    </Badge>
  )}
</div>
```

Update the `CardDescription` to include "messages_received and matched come from the in-process decision ring (last N), not the trade DB" replacing the existing parenthetical that said they were 0.

- [ ] **Step 2: Lint + commit**

```bash
cd frontend
pnpm lint
git add app/dashboard/_components/panel-signal-funnel.tsx
git commit -m "fix(autotrader/frontend): label funnel top stages as ring-scoped"
```

---

## Task 10: Remove legacy `/stats/overview` client

**Why this exists:** Task 2 migrated the only remaining caller. Deleting the export prevents future code from regressing onto the legacy endpoint. The backend route stays for now (operators may have curl-based dashboards) — only the typed client is removed.

**Files:**
- Modify: `frontend/lib/api.ts` — delete the `overview()` export and its `StatsOverview` type if unreferenced

- [ ] **Step 1: Verify no remaining callers**

```bash
cd /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader/frontend
grep -rn "stats.overview\|/stats/overview\|StatsOverview" --include='*.ts' --include='*.tsx' .
```

Expected: zero hits outside `lib/api.ts` itself. If any survive, fix them before deleting (most likely a stale import in the hero from Task 2).

- [ ] **Step 2: Delete the export and type**

In `lib/api.ts`, remove the `overview: () => api<StatsOverview>("/stats/overview"),` line and the `export type StatsOverview = { ... }` block.

- [ ] **Step 3: Typecheck**

```bash
cd frontend
pnpm typecheck
```

Expected: zero errors. If anything fails, fix the caller (do not restore the legacy export).

- [ ] **Step 4: Commit**

```bash
git add lib/api.ts
git commit -m "chore(autotrader/frontend): drop legacy /stats/overview typed client"
```

---

## Task 11: Playwright smoke harness

**Why this exists:** Spec §11 deferral. Phase 1+2 added a lot of UI surface with no end-to-end coverage; this lays the foundation so future panels are guarded by at least one smoke test.

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/dashboard.spec.ts`
- Modify: `frontend/package.json` — add `@playwright/test` devDep + `test:e2e` script

- [ ] **Step 1: Install Playwright**

```bash
cd frontend
pnpm add -D @playwright/test
pnpm dlx playwright install chromium
```

Expected: chromium browser bundle downloads (~120MB), `@playwright/test` lands in `devDependencies`.

- [ ] **Step 2: Add the config**

Create `frontend/playwright.config.ts`:

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "pnpm dev",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
```

- [ ] **Step 3: Add the smoke spec**

Create `frontend/e2e/dashboard.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

/**
 * Smoke coverage for the Phase 3 dashboard surface. Each test exercises
 * one critical path; we are NOT trying to recreate component-level
 * coverage here — TanStack Query + Recharts internals stay out of scope.
 */

test.describe("Dashboard", () => {
  test("loads overview without console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));

    await page.goto("/dashboard");
    await expect(page.getByText(/operator/i)).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("analytics page renders the equity curve", async ({ page }) => {
    await page.goto("/dashboard/analytics");
    await expect(page.getByText(/equity curve/i)).toBeVisible();
  });

  test("asset filter pill opens", async ({ page }) => {
    await page.goto("/dashboard/analytics");
    await page.getByRole("button", { name: /asset/i }).click();
    // The popover content should appear (even if empty in the test env).
    await expect(page.getByRole("dialog").or(page.getByRole("listbox"))).toBeVisible();
  });

  test("user menu signs out", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: /account menu/i }).click();
    await page.getByRole("menuitem", { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/login/);
  });
});
```

- [ ] **Step 4: Wire the script**

In `frontend/package.json`, add to `"scripts"`:

```json
"test:e2e": "playwright test",
"test:e2e:ui": "playwright test --ui"
```

- [ ] **Step 5: Run the smoke (best-effort — local Playwright behaviour varies)**

```bash
cd frontend
pnpm test:e2e
```

Expected: 4 tests pass against a freshly-spun dev server. If a test environmentally cannot reach localhost (e.g. sandboxed CI), commit anyway — the harness presence is the value here, not 100% green on first run.

- [ ] **Step 6: Commit**

```bash
git add playwright.config.ts e2e/ package.json pnpm-lock.yaml
git commit -m "test(autotrader/frontend): playwright smoke harness for dashboard"
```

---

## Task 12: Final smoke + push + open stacked PR

- [ ] **Step 1: Full backend + frontend sweep**

```bash
cd /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader/backend
AUTOTRADER_DB_URL=sqlite+aiosqlite:///./tests/.test.db uv run pytest -q

cd ../frontend
pnpm typecheck
pnpm lint
pnpm build
```

Expected: all green. `pnpm build` is the strongest signal — Next.js's production build catches issues `dev` hides.

- [ ] **Step 2: Push the branch**

```bash
cd /Users/imranahmedani/Desktop/pyquotex.worktree-ui-phase-3/autotrader
git push -u origin claude/ui-modernization-phase-3-analytics-depth
```

- [ ] **Step 3: Open the stacked PR**

```bash
gh pr create \
  --base claude/ui-modernization-phase-2-analytics-core \
  --head claude/ui-modernization-phase-3-analytics-depth \
  --title "Phase 3: analytics depth (parser comparison + latency + risk-cap + martingale + streaks + asset filter + sign-out + Playwright)" \
  --body "Stacked on Phase 2 (PR #16). Adds the five Phase 3 deferred panels, asset filter pill, sign-out menu in the sidebar footer, Playwright smoke harness, and removes the legacy /stats/overview client. Backend additions: /stats/v2/assets endpoint, parser streak sub-stats on /stats/v2/breakdown?dim=parser, and Pipeline.recent_decisions wired into /stats/v2/funnel top stages."
```

Expected: a new PR (will be #17 if numbering continues).

---

## Self-Review Checklist (run before handoff)

1. **Spec coverage:** Phase 3 deferrals from `2026-05-09-ui-modernization-and-analytics-design.md` §11 — all five panels (parser comparison, latency drift, risk-cap utilisation, martingale ladder ROI, streak distribution) ✅, sign-out user menu ✅, Playwright tests ✅, removal of legacy `/stats/overview` ✅, funnel ring wiring ✅, asset filter pill ✅.
2. **Placeholders:** None. Every step has explicit code, file paths, expected output.
3. **Type consistency:** `BreakdownRow.streaks` is `ParserStreaks | undefined` everywhere. `messages_received_window === "ring"` discriminator matches Part A Task 3 exactly.
4. **Filter propagation:** Every new panel's `useQuery` reads `filters.range/from/to/chats/parsers/assets/direction` and forwards them to `statsV2.*`. Caches are keyed on the full `filters` object so a pill change refetches.
5. **Stacking:** PR base is `claude/ui-modernization-phase-2-analytics-core`. When Phase 2 squash-merges, Phase 3 will need its base updated (mirror of the Phase 1 → Phase 2 pattern).

---

## Handoff

After Part A (backend) and Part B (frontend) are both committed:
- `superpowers:subagent-driven-development` to execute task-by-task
- Final reviewer pass over the full diff
- Push + stacked PR (Task 12)
