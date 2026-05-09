# UI Modernization · Phase 1 — Foundation · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the top-nav + 1058-line monolithic Pipeline page with a sidebar-driven UI shell that supports light/dark/system themes, with the existing trades + decisions extracted into their own pages and a new Overview hero-KPI dashboard — **all on the same data**, no new analytics yet.

**Architecture:** Add `next-themes` for theme switching + a chart-token palette to `globals.css`. Pull in shadcn primitives via the CLI (`bunx shadcn@latest add`). Build `<AppSidebar>` / `<AppTopbar>` / `<ThemeToggle>` as project components in `frontend/components/`. Decompose `app/dashboard/pipeline/page.tsx` into shared, reusable modules under `app/dashboard/_components/` and rebuild the Pipeline page as controls-only. Add new routes for `/dashboard/trades` and `/dashboard/decisions`. Rebuild `/dashboard` (Overview) as KPI hero + equity-curve stub + recent activity + status mini-cards.

**Tech Stack:** Next.js 15, React 19, TypeScript 5.6, Tailwind v4, shadcn/ui, TanStack Query 5, Zustand 5, lucide-react, **+ next-themes (new)**.

**Out of scope for this phase:** Recharts (Phase 2), `/stats/v2/*` backend endpoints (Phase 2), filter bar (Phase 2), 10 analytics panels (Phase 2/3), Playwright tests (Phase 3).

**Reference design spec:** `docs/superpowers/specs/2026-05-09-ui-modernization-and-analytics-design.md`

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `frontend/components/theme-provider.tsx` | next-themes wrapper, mounted under root layout. |
| `frontend/components/theme-toggle.tsx` | Light / Dark / System cycle button. Lives in sidebar footer. |
| `frontend/components/app-sidebar.tsx` | Collapsible nav rail with grouped sections (Trade / Configure) + footer (version + theme toggle). |
| `frontend/components/app-topbar.tsx` | Breadcrumb + page title + global pipeline status pill. |
| `frontend/components/global-status-pill.tsx` | Reads `/pipeline/status` and renders the "● Pipeline live · DEMO" / kill-switch / REAL pill. |
| `frontend/components/ui/sidebar.tsx` | shadcn primitive (CLI-added). |
| `frontend/components/ui/sheet.tsx` | shadcn primitive (CLI-added). |
| `frontend/components/ui/tabs.tsx` | shadcn primitive (CLI-added). Future use; harmless to add now. |
| `frontend/components/ui/dropdown-menu.tsx` | shadcn primitive (CLI-added). |
| `frontend/components/ui/separator.tsx` | shadcn primitive (CLI-added). |
| `frontend/components/ui/scroll-area.tsx` | shadcn primitive (CLI-added). |
| `frontend/components/ui/skeleton.tsx` | shadcn primitive (CLI-added). |
| `frontend/components/ui/tooltip.tsx` | shadcn primitive (CLI-added). Used for collapsed-sidebar tooltips. |
| `frontend/components/ui/switch.tsx` | shadcn primitive (CLI-added). |
| `frontend/app/dashboard/_components/trades-table.tsx` | Extracted from `pipeline/page.tsx` — TradesTable + StatusBadge + FeedIndicator. |
| `frontend/app/dashboard/_components/decisions-feed.tsx` | Extracted from `pipeline/page.tsx` — ParserDecisionsCard + DecisionBadge. |
| `frontend/app/dashboard/_components/overview-kpi-hero.tsx` | 4-card KPI strip on Overview. |
| `frontend/app/dashboard/_components/overview-equity-stub.tsx` | Placeholder equity-curve panel; says "Coming in Phase 2". |
| `frontend/app/dashboard/_components/overview-recent-activity.tsx` | Last-8-trades table on Overview. |
| `frontend/app/dashboard/_components/overview-status-cards.tsx` | Three small status panels (Broker / Telegram / Pipeline). |
| `frontend/app/dashboard/trades/page.tsx` | New page. Renders the extracted `<TradesTable>`. |
| `frontend/app/dashboard/decisions/page.tsx` | New page. Renders the extracted `<DecisionsFeed>`. |

### Modified files

| Path | Change |
|---|---|
| `frontend/package.json` | Add `next-themes` dep. |
| `frontend/app/globals.css` | Add `--success`, `--warning`, `--info`, `--chart-1..5` tokens in both `:root` and `.dark` blocks. |
| `frontend/app/layout.tsx` | Remove hardcoded `className="dark"` on `<html>`; add `suppressHydrationWarning`; mount `<ThemeProvider>`. |
| `frontend/app/dashboard/layout.tsx` | Replace top-nav block with `<AppSidebar>` + `<AppTopbar>` shell. |
| `frontend/app/dashboard/page.tsx` | Rebuild as Overview: KPI hero + equity stub + recent activity + status cards. |
| `frontend/app/dashboard/pipeline/page.tsx` | Remove TradesTable + ParserDecisionsCard + StatsOverviewCard; keep status, risk caps, budget, streaks, master switch. Drop SectionNav. |

### Files left untouched in this phase

`frontend/app/dashboard/broker/page.tsx`, `telegram/page.tsx`, `parsers/**`, `login/page.tsx`, every backend file. They get the new sidebar shell automatically because it's in the dashboard layout.

---

## Pre-flight

### Task 0: Branch off and verify baseline builds

**Files:** none (git operations + sanity-check the baseline)

- [ ] **Step 1: Confirm working tree state**

Run: `cd /Users/imranahmedani/Desktop/pyquotex/autotrader && git status`

Expected: shows the in-flight changes from prior work (admin_bot_notify.py, executor.py, etc.). If those are not the unrelated existing changes, stop and ask.

- [ ] **Step 2: Branch off master cleanly**

The in-flight changes on `claude/fix-parser-count-display-N77cR` are unrelated; they should not ride along with this phase. Stash or commit them first if needed, then:

```bash
cd /Users/imranahmedani/Desktop/pyquotex/autotrader
git checkout master
git pull --ff-only origin master
git checkout -b claude/ui-modernization-phase-1-foundation
```

If the user prefers a worktree (recommended for parallel work), instead:

```bash
git worktree add ../pyquotex.worktree-ui-phase-1 -b claude/ui-modernization-phase-1-foundation master
cd ../pyquotex.worktree-ui-phase-1/autotrader
```

- [ ] **Step 3: Verify baseline build is green**

```bash
cd frontend
bun install
bun run type-check
bun run build
```

Expected: all three succeed. The build prints the route table for `/`, `/login`, `/dashboard`, `/dashboard/broker`, `/dashboard/telegram`, `/dashboard/parsers`, `/dashboard/parsers/[chat_id]`, `/dashboard/pipeline`.

If type-check fails on the baseline, stop and triage — do not proceed until baseline is green.

- [ ] **Step 4: Commit baseline marker (no code changes)**

Empty commit so subsequent commits have a clear starting point in the log:

```bash
git commit --allow-empty -m "chore(autotrader): start Phase 1 — UI modernization foundation

Baseline verified: bun type-check + build green on master."
```

---

## Theme system

### Task 1: Install `next-themes`

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/bun.lock` (auto)

- [ ] **Step 1: Install the package**

```bash
cd /Users/imranahmedani/Desktop/pyquotex/autotrader/frontend
bun add next-themes
```

Expected: `package.json` gains `"next-themes": "^0.4.x"` (or current major) under `dependencies`. `bun.lock` updates.

- [ ] **Step 2: Verify install**

```bash
bun run type-check
```

Expected: passes (no usages yet — just install verification).

- [ ] **Step 3: Commit**

```bash
git add package.json bun.lock
git commit -m "chore(autotrader/frontend): add next-themes for light/dark/system theming"
```

---

### Task 2: Add new design tokens to `globals.css`

**Files:**
- Modify: `frontend/app/globals.css`

The existing file defines `--background`, `--foreground`, `--card`, etc. for both `:root` and `.dark`. We add three semantic-state pairs (success/warning/info) and a 5-color chart palette so Phase 2 charts have ready-to-use CSS variables.

- [ ] **Step 1: Add tokens to the `:root` (light) block**

Open `frontend/app/globals.css`. Inside the `:root` block (currently lines 7-28), append the new tokens **before the closing `}`**:

```css
    /* New semantic state tokens (Phase 1). */
    --success: 142 71% 45%;
    --success-foreground: 0 0% 98%;
    --warning: 38 92% 50%;
    --warning-foreground: 0 0% 98%;
    --info: 217 91% 60%;
    --info-foreground: 0 0% 98%;

    /* Chart palette — Recharts series colors (Phase 2). */
    --chart-1: 142 71% 45%;
    --chart-2: 217 91% 60%;
    --chart-3: 38 92% 50%;
    --chart-4: 280 65% 60%;
    --chart-5: 340 75% 55%;
```

- [ ] **Step 2: Add tokens to the `.dark` block**

Inside the `.dark` block (currently lines 30-50), append before the closing `}`:

```css
    /* New semantic state tokens (Phase 1). */
    --success: 142 71% 50%;
    --success-foreground: 240 10% 5%;
    --warning: 38 92% 55%;
    --warning-foreground: 240 10% 5%;
    --info: 217 91% 65%;
    --info-foreground: 240 10% 5%;

    /* Chart palette (Phase 2). */
    --chart-1: 142 71% 50%;
    --chart-2: 217 91% 65%;
    --chart-3: 38 92% 55%;
    --chart-4: 280 65% 65%;
    --chart-5: 340 75% 60%;
```

- [ ] **Step 3: Map tokens into the `@theme inline` block**

Inside `@theme inline { ... }` (currently lines 53-74), append before the closing `}`:

```css
  --color-success: hsl(var(--success));
  --color-success-foreground: hsl(var(--success-foreground));
  --color-warning: hsl(var(--warning));
  --color-warning-foreground: hsl(var(--warning-foreground));
  --color-info: hsl(var(--info));
  --color-info-foreground: hsl(var(--info-foreground));
  --color-chart-1: hsl(var(--chart-1));
  --color-chart-2: hsl(var(--chart-2));
  --color-chart-3: hsl(var(--chart-3));
  --color-chart-4: hsl(var(--chart-4));
  --color-chart-5: hsl(var(--chart-5));
```

This makes Tailwind utilities like `bg-success`, `text-warning`, `border-chart-1` etc. work in both themes.

- [ ] **Step 4: Verify**

```bash
bun run type-check
bun run build
```

Expected: both pass. The build emits CSS containing `--color-success`, `--color-chart-1`, etc.

- [ ] **Step 5: Commit**

```bash
git add app/globals.css
git commit -m "feat(autotrader/frontend): add success/warning/info + chart palette design tokens

Adds 3 semantic-state token pairs and a 5-color chart palette to both
:root (light) and .dark blocks, mapped into the @theme inline scope so
Tailwind utilities like bg-success and text-chart-1 work in both
themes. Phase 2 charts read these tokens directly so the same chart
component renders correctly in either mode."
```

---

### Task 3: Add the `<ThemeProvider>` component

**Files:**
- Create: `frontend/components/theme-provider.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

/**
 * Thin wrapper around next-themes that:
 * - uses the `class` attribute strategy (matches our globals.css `.dark` block)
 * - defaults to "system" so a fresh load follows OS preference
 * - persists the user's pick to localStorage under the next-themes default key
 *
 * Mounted once, in the root layout, above all other providers.
 */

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

export function ThemeProvider(
  props: ComponentProps<typeof NextThemesProvider>,
) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      {...props}
    />
  );
}
```

`disableTransitionOnChange` prevents a one-frame flash when toggling, since the body has `transition-colors` on `bg-background`.

- [ ] **Step 2: Verify**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add components/theme-provider.tsx
git commit -m "feat(autotrader/frontend): add ThemeProvider wrapping next-themes

Class-attribute strategy (matches globals.css .dark block), system
default, transition disabled on switch to avoid the one-frame flash."
```

---

### Task 4: Wire `<ThemeProvider>` into the root layout

**Files:**
- Modify: `frontend/app/layout.tsx`

- [ ] **Step 1: Replace the file**

Current file hardcodes `className="dark"` on `<html>`. Replace the entire file with:

```tsx
import type { Metadata } from "next";
import { Providers } from "./providers";
import { ThemeProvider } from "@/components/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Autotrader",
  description: "Telegram-driven autotrader for pyquotex",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // ``suppressHydrationWarning`` is required by next-themes: the provider
  // injects the class on the client before React hydration completes, so
  // the server-rendered <html> momentarily differs from the client
  // tree. The warning is the documented escape valve.
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <Providers>{children}</Providers>
        </ThemeProvider>
      </body>
    </html>
  );
}
```

- [ ] **Step 2: Verify type-check + build**

```bash
bun run type-check
bun run build
```

Expected: both pass.

- [ ] **Step 3: Manual verification — system mode by default**

```bash
bun run dev
```

Open `http://localhost:3000`. Without any prior theme picked, the dashboard should follow the OS appearance setting. Switch the OS to light mode (System Settings → Appearance) — the dashboard should re-render in light without a page reload.

If the dashboard renders blindingly white text on white (broken light mode), that means tokens from Task 2 didn't land — go back and check.

- [ ] **Step 4: Commit**

```bash
git add app/layout.tsx
git commit -m "feat(autotrader/frontend): wire ThemeProvider, drop hardcoded dark class

Removes the hardcoded className=\"dark\" on <html>. ThemeProvider
manages the class via next-themes; system preference is the default,
manual selection persists in localStorage. suppressHydrationWarning is
the next-themes-recommended escape for the SSR/CSR mismatch on the
<html> element."
```

---

## shadcn primitives

### Task 5: Bulk-add shadcn primitives via CLI

**Files (all created by the CLI):**
- Create: `frontend/components/ui/sidebar.tsx`
- Create: `frontend/components/ui/sheet.tsx`
- Create: `frontend/components/ui/tabs.tsx`
- Create: `frontend/components/ui/dropdown-menu.tsx`
- Create: `frontend/components/ui/separator.tsx`
- Create: `frontend/components/ui/scroll-area.tsx`
- Create: `frontend/components/ui/skeleton.tsx`
- Create: `frontend/components/ui/tooltip.tsx`
- Create: `frontend/components/ui/switch.tsx`
- Modify: `frontend/package.json` (Radix peer deps auto-added)

`components.json` is already configured (`new-york` style, `neutral` baseColor, `rsc: true`, lucide icons, the `@/*` aliases). The CLI honours all of that.

- [ ] **Step 1: Run the CLI bulk add**

```bash
cd /Users/imranahmedani/Desktop/pyquotex/autotrader/frontend
bunx shadcn@latest add sidebar sheet tabs dropdown-menu separator scroll-area skeleton tooltip switch --yes
```

Expected: each component file appears under `components/ui/`. The CLI also adds the underlying Radix packages (`@radix-ui/react-dialog`, `@radix-ui/react-dropdown-menu`, `@radix-ui/react-tabs`, etc.) to `package.json`.

If the CLI complains about a config conflict, abort and check `components.json` matches what's documented in the design spec §4.

- [ ] **Step 2: Verify the new primitives type-check**

```bash
bun run type-check
```

Expected: passes. If a new primitive imports something missing (rare but possible if a transitive Radix dep didn't get added), `bun add @radix-ui/react-<name>` to fix.

- [ ] **Step 3: Verify the build still produces valid output**

```bash
bun run build
```

Expected: passes. Bundle size will grow ~50–80 KB; that's the Radix primitives loading. Acceptable.

- [ ] **Step 4: Commit**

```bash
git add components/ui/ package.json bun.lock
git commit -m "feat(autotrader/frontend): add shadcn primitives for the new app shell

Adds sidebar, sheet, tabs, dropdown-menu, separator, scroll-area,
skeleton, tooltip, switch via the shadcn CLI. components.json already
points at new-york/neutral/lucide/rsc:true so each primitive lands
pre-configured for our token palette. Radix peer dependencies pulled
in automatically."
```

---

## App shell

### Task 6: `<ThemeToggle>` component

**Files:**
- Create: `frontend/components/theme-toggle.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

/**
 * Light / Dark / System cycle button.
 *
 * Lives in the sidebar footer. We render a single button that cycles
 * through the three states rather than a dropdown — fewer clicks for
 * the most common toggle. The icon reflects the *resolved* theme so
 * the user can see at a glance what's currently applied (especially
 * useful when on "system").
 */

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

const ORDER = ["light", "dark", "system"] as const;

export function ThemeToggle() {
  const { theme, setTheme, resolvedTheme } = useTheme();
  // Hydration-safe: theme is undefined on the server. Render a neutral
  // placeholder until mounted so the SSR-emitted button matches the
  // first client paint.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return (
      <Button
        variant="ghost"
        size="sm"
        aria-label="Theme"
        className="w-full justify-start gap-2"
      >
        <Monitor className="h-4 w-4" /> Theme
      </Button>
    );
  }

  const current = (theme as (typeof ORDER)[number] | undefined) ?? "system";
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
  const Icon =
    current === "system"
      ? Monitor
      : (resolvedTheme ?? current) === "dark"
        ? Moon
        : Sun;
  const label =
    current === "system"
      ? `System (${resolvedTheme ?? "?"})`
      : current === "dark"
        ? "Dark"
        : "Light";

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => setTheme(next)}
      aria-label={`Switch theme — currently ${label}`}
      className="w-full justify-start gap-2"
    >
      <Icon className="h-4 w-4" /> {label}
    </Button>
  );
}
```

- [ ] **Step 2: Verify**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add components/theme-toggle.tsx
git commit -m "feat(autotrader/frontend): add ThemeToggle (cycles light/dark/system)

Single-button cycle through the three states with the resolved-theme
icon (Sun/Moon/Monitor) so users on \"system\" can see what's currently
applied. Hydration-safe — renders a placeholder until mounted so the
SSR/CSR diff doesn't trip a hydration warning."
```

---

### Task 7: `<AppSidebar>` component

**Files:**
- Create: `frontend/components/app-sidebar.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

/**
 * The new dashboard sidebar — collapsible icon rail, two grouped
 * sections (Trade / Configure), version + theme toggle in the footer.
 *
 * Built on shadcn's <Sidebar> primitive (added in Task 5), which gives
 * us responsive collapse behavior, a mobile sheet fallback, and the
 * SidebarProvider/SidebarTrigger pair for the topbar collapse button.
 */

import {
  Activity,
  AreaChart,
  Headphones,
  Landmark,
  LayoutDashboard,
  ListChecks,
  ScrollText,
  Target,
  Wind,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { ThemeToggle } from "@/components/theme-toggle";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const TRADE_NAV: NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard/analytics", label: "Analytics", icon: AreaChart },
  { href: "/dashboard/trades", label: "Trades", icon: ListChecks },
  { href: "/dashboard/decisions", label: "Decisions", icon: Wind },
  { href: "/dashboard/pipeline", label: "Pipeline", icon: Activity },
];

const CONFIG_NAV: NavItem[] = [
  { href: "/dashboard/parsers", label: "Parsers", icon: Target },
  { href: "/dashboard/telegram", label: "Telegram", icon: Headphones },
  { href: "/dashboard/broker", label: "Broker", icon: Landmark },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppSidebar() {
  const pathname = usePathname();
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <Link
          href="/dashboard"
          className="flex items-center gap-2 px-2 py-1.5 font-semibold tracking-tight"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-success text-success-foreground">
            <ScrollText className="h-4 w-4" />
          </div>
          <span className="group-data-[collapsible=icon]:hidden">
            Autotrader
          </span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Trade</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {TRADE_NAV.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton
                    asChild
                    isActive={isActive(pathname, item.href)}
                    tooltip={item.label}
                  >
                    <Link href={item.href}>
                      <item.icon className="h-4 w-4" />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>Configure</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {CONFIG_NAV.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton
                    asChild
                    isActive={isActive(pathname, item.href)}
                    tooltip={item.label}
                  >
                    <Link href={item.href}>
                      <item.icon className="h-4 w-4" />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <ThemeToggle />
      </SidebarFooter>
    </Sidebar>
  );
}
```

`/dashboard/analytics` doesn't exist yet (Phase 2); clicking it returns a 404 until then. That's acceptable — leaving the link in surfaces the IA early. We'll create a placeholder page in Step 4 of this task to avoid the 404.

- [ ] **Step 2: Add a placeholder Analytics page**

To avoid the 404, create `frontend/app/dashboard/analytics/page.tsx`:

```tsx
"use client";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function AnalyticsPlaceholderPage() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Analytics</h2>
        <p className="text-sm text-muted-foreground">
          Advanced analytics ship in Phase 2.
        </p>
      </section>
      <Card>
        <CardHeader>
          <CardTitle>Coming soon</CardTitle>
          <CardDescription>
            Phase 2 lands the global filter bar and 5 actionable panels
            (equity curve, hour-of-day heatmap, channel leaderboard,
            asset×direction matrix, signal funnel). Phase 3 adds parser
            comparison, latency drift, risk-cap utilization, martingale
            ladder ROI, and streak distribution.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            For now, see <strong>Trades</strong> for raw history,{" "}
            <strong>Decisions</strong> for the live dispatch feed, and{" "}
            <strong>Pipeline</strong> for status &amp; controls.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Verify type-check**

```bash
bun run type-check
```

Expected: passes. (No render verification yet — sidebar isn't mounted.)

- [ ] **Step 4: Commit**

```bash
git add components/app-sidebar.tsx app/dashboard/analytics/page.tsx
git commit -m "feat(autotrader/frontend): add AppSidebar with grouped nav + placeholder Analytics page

Two grouped sections (Trade / Configure), collapsible to icon rail,
tooltips on collapsed icons, theme toggle in footer. Lucide icons
chosen for visual coherence with the design spec. Placeholder
Analytics page avoids a 404 on the new sidebar link until Phase 2."
```

---

### Task 8: `<GlobalStatusPill>` component

**Files:**
- Create: `frontend/components/global-status-pill.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

/**
 * The pipeline-status pill rendered in the topbar on every dashboard
 * page. Reads /pipeline/status (5s poll) and renders one of:
 *
 *   ● Pipeline live · DEMO     (active, kill switch off)
 *   ● Idle · DEMO              (inactive, kill switch off)
 *   ⏸ Kill switch · DEMO       (kill switch engaged)
 *   ● Pipeline live · REAL     (active, REAL trading enabled — amber)
 *
 * Uses the same query key the existing dashboard pages use, so all
 * subscribers share one in-flight request via TanStack Query.
 */

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { type PipelineStatus, pipeline } from "@/lib/api";

export function GlobalStatusPill() {
  const { data, isLoading } = useQuery<PipelineStatus>({
    queryKey: ["pipeline", "status"],
    queryFn: pipeline.status,
    refetchInterval: 5_000,
  });

  if (isLoading || !data) {
    return (
      <Badge variant="outline" className="gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground" />
        loading…
      </Badge>
    );
  }

  const real = data.live_trading_enabled_env;
  const accountLabel = real ? "REAL" : "DEMO";

  if (data.kill_switch_engaged) {
    return (
      <Badge variant="destructive" className="gap-1.5">
        ⏸ Kill switch · {accountLabel}
      </Badge>
    );
  }
  if (data.active) {
    return (
      <Badge
        variant={real ? "warning" : "success"}
        className="gap-1.5"
      >
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
        Pipeline live · {accountLabel}
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="gap-1.5">
      <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground" />
      Idle · {accountLabel}
    </Badge>
  );
}
```

- [ ] **Step 2: Verify**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add components/global-status-pill.tsx
git commit -m "feat(autotrader/frontend): add GlobalStatusPill for the topbar

Shares the [\"pipeline\", \"status\"] query key with existing pages so
the 5s poll is deduplicated. Renders four states (live/idle/kill-
switch/loading) with DEMO vs REAL annotation."
```

---

### Task 9: `<AppTopbar>` component

**Files:**
- Create: `frontend/components/app-topbar.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

/**
 * Top bar above the main content area. Holds:
 *   - SidebarTrigger (collapse / expand the rail; shadcn primitive)
 *   - breadcrumb (group label + current page label)
 *   - GlobalStatusPill on the right
 *
 * Page title comes from the URL — we don't pass it as a prop because
 * leaving title management with the page itself (via <h2>) keeps the
 * topbar dumb.
 */

import { usePathname } from "next/navigation";
import { GlobalStatusPill } from "@/components/global-status-pill";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

interface PageMeta {
  group: "Trade" | "Configure";
  label: string;
}

const PAGE_META: Record<string, PageMeta> = {
  "/dashboard": { group: "Trade", label: "Overview" },
  "/dashboard/analytics": { group: "Trade", label: "Analytics" },
  "/dashboard/trades": { group: "Trade", label: "Trades" },
  "/dashboard/decisions": { group: "Trade", label: "Decisions" },
  "/dashboard/pipeline": { group: "Trade", label: "Pipeline" },
  "/dashboard/parsers": { group: "Configure", label: "Parsers" },
  "/dashboard/telegram": { group: "Configure", label: "Telegram" },
  "/dashboard/broker": { group: "Configure", label: "Broker" },
};

function resolveMeta(pathname: string): PageMeta {
  // Exact match first; fall back to prefix match for nested routes
  // like /dashboard/parsers/123.
  if (PAGE_META[pathname]) return PAGE_META[pathname];
  for (const [href, meta] of Object.entries(PAGE_META)) {
    if (href !== "/dashboard" && pathname.startsWith(`${href}/`)) {
      return meta;
    }
  }
  return { group: "Trade", label: "" };
}

export function AppTopbar() {
  const pathname = usePathname();
  const meta = resolveMeta(pathname);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="h-5" />
      <div className="flex flex-col">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {meta.group}
        </span>
        <span className="text-sm font-semibold leading-none tracking-tight">
          {meta.label}
        </span>
      </div>
      <div className="ml-auto">
        <GlobalStatusPill />
      </div>
    </header>
  );
}
```

- [ ] **Step 2: Verify**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add components/app-topbar.tsx
git commit -m "feat(autotrader/frontend): add AppTopbar with breadcrumb + sidebar trigger + status pill

Page metadata table in one place (PAGE_META) so adding new routes
later is a one-line change. Sticky positioning keeps the pill on
screen while scrolling long pages."
```

---

### Task 10: Replace dashboard layout with the new sidebar shell

**Files:**
- Modify: `frontend/app/dashboard/layout.tsx`

- [ ] **Step 1: Replace the file**

Current file (the 80-line top-nav layout) is replaced wholesale with the new shell.

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppSidebar } from "@/components/app-sidebar";
import { AppTopbar } from "@/components/app-topbar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { getToken } from "@/lib/api";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
    } else {
      setAuthed(true);
    }
  }, [router]);

  if (!authed) return null;

  return (
    // ``defaultOpen`` = true keeps the rail expanded on first visit;
    // shadcn persists the user's collapsed/expanded preference to a
    // cookie automatically.
    <SidebarProvider defaultOpen>
      <AppSidebar />
      <SidebarInset>
        <AppTopbar />
        <main className="mx-auto w-full max-w-7xl px-6 py-8">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  );
}
```

Notes:
- The previous "Sign out" button is gone from the layout. We'll add it as a sidebar-footer dropdown in Phase 3 polish — for Phase 1, users sign out by clearing localStorage or via the `logout()` helper in the browser console. **This is an intentional minor regression.** Logging it here as a follow-up: see "Open follow-ups" at the end of this plan.
- `max-w-6xl` → `max-w-7xl` because the sidebar takes 256 px of horizontal width when expanded; we want the content area to use the remaining width on a 1920-wide display.

- [ ] **Step 2: Verify type-check + build**

```bash
bun run type-check
bun run build
```

Expected: both pass.

- [ ] **Step 3: Manual verification — sidebar renders on every dashboard page**

```bash
bun run dev
```

Visit each:
- `http://localhost:3000/dashboard` (still old Overview content; that gets rebuilt in Task 19)
- `http://localhost:3000/dashboard/broker`
- `http://localhost:3000/dashboard/telegram`
- `http://localhost:3000/dashboard/parsers`
- `http://localhost:3000/dashboard/pipeline`
- `http://localhost:3000/dashboard/analytics` (placeholder from Task 7)

Each should show the sidebar on the left, topbar on top with the right breadcrumb (e.g. "Trade / Pipeline"), the status pill on the topbar right. Click the sidebar trigger (top-left) to collapse to the icon rail; tooltips should appear on icon hover. Refresh the page — the collapsed/expanded state should persist (cookie-backed).

`/dashboard/trades` and `/dashboard/decisions` will 404 — they're built in Tasks 11–14.

- [ ] **Step 4: Commit**

```bash
git add app/dashboard/layout.tsx
git commit -m "feat(autotrader/frontend): replace top-nav layout with sidebar shell

Wraps every /dashboard/* route in SidebarProvider + AppSidebar +
AppTopbar. shadcn persists the rail's collapsed state via cookie.
Content area widens from max-w-6xl to max-w-7xl to use the recovered
horizontal space. The previous header-mounted Sign out button is
intentionally absent in Phase 1; sidebar-footer user menu lands in
Phase 3 polish."
```

---

## Pipeline page decomposition

### Task 11: Extract trades table into a shared component

**Files:**
- Create: `frontend/app/dashboard/_components/trades-table.tsx`
- Modify: `frontend/app/dashboard/pipeline/page.tsx` (remove the extracted code in Task 14)

The current `pipeline/page.tsx` defines `TradesTable`, `StatusBadge`, and `FeedIndicator` inline (lines 927–1049). Move them into a dedicated file so both `/dashboard/pipeline` (interim) and `/dashboard/trades` can render them.

- [ ] **Step 1: Create the extracted module**

Copy the relevant code out of `pipeline/page.tsx` lines 927–1049 verbatim into `frontend/app/dashboard/_components/trades-table.tsx`. Add the necessary imports (the originals depend on `@/components/ui/badge`, `@/components/ui/card`, `@/lib/api`, and the `FeedState` type).

Full file content:

```tsx
"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { TradeAttempt } from "@/lib/api";
import type { FeedState } from "@/lib/use-trade-feed";

export function TradesTable({
  trades,
  loading,
  feedState,
}: {
  trades: TradeAttempt[];
  loading: boolean;
  feedState: FeedState;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              Recent trade attempts
              <FeedIndicator state={feedState} />
            </CardTitle>
            <CardDescription>
              Every signal that reached the executor — successful, blocked,
              or broker-rejected. Streams live over WebSocket; falls back
              to a 15s poll if the feed drops.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {loading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {!loading && trades.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No trade attempts yet. Activate the pipeline above and watch
            this fill up as signals arrive.
          </p>
        )}
        {trades.length > 0 && (
          <div className="max-h-[36rem] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-1.5 font-medium">When</th>
                  <th className="px-2 py-1.5 font-medium">Asset</th>
                  <th className="px-2 py-1.5 font-medium">Dir</th>
                  <th className="px-2 py-1.5 font-medium">Mode</th>
                  <th className="px-2 py-1.5 font-medium">Stake</th>
                  <th className="px-2 py-1.5 font-medium">Status</th>
                  <th className="px-2 py-1.5 font-medium">Profit</th>
                  <th className="px-2 py-1.5 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b last:border-0">
                    <td className="px-2 py-1.5 font-mono text-muted-foreground">
                      {new Date(t.received_at).toLocaleTimeString()}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {t.asset_raw && t.asset_raw !== t.asset
                        ? `${t.asset_raw} → ${t.asset}`
                        : t.asset}
                    </td>
                    <td className="px-2 py-1.5">
                      <Badge
                        variant={t.direction === "call" ? "success" : "destructive"}
                      >
                        {t.direction}
                      </Badge>
                    </td>
                    <td className="px-2 py-1.5">
                      <Badge variant="outline">{t.trade_mode}</Badge>
                    </td>
                    <td className="px-2 py-1.5 font-mono">{t.stake}</td>
                    <td className="px-2 py-1.5">
                      <StatusBadge status={t.status} />
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {t.profit !== null ? t.profit.toFixed(2) : "—"}
                    </td>
                    <td className="px-2 py-1.5 text-xs text-muted-foreground">
                      {t.error ?? t.broker_order_id ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function FeedIndicator({ state }: { state: FeedState }) {
  if (state === "live") {
    return (
      <span className="flex items-center gap-1 text-xs text-emerald-400">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
        live
      </span>
    );
  }
  if (state === "connecting") {
    return (
      <span className="text-xs text-muted-foreground">connecting…</span>
    );
  }
  return <span className="text-xs text-muted-foreground">offline</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const v = status.toLowerCase();
  if (v === "won") return <Badge variant="success">won</Badge>;
  if (v === "lost") return <Badge variant="destructive">lost</Badge>;
  if (v === "rejected") return <Badge variant="secondary">rejected</Badge>;
  if (v === "broker_error") return <Badge variant="destructive">error</Badge>;
  if (v === "expired") return <Badge variant="outline">expired</Badge>;
  return <Badge variant="warning">{v}</Badge>;
}
```

- [ ] **Step 2: Verify type-check passes (with the original components still in pipeline/page.tsx)**

```bash
bun run type-check
```

Expected: passes. We've added a new file but not yet removed the originals — the codebase has duplicated definitions for one task only. We won't commit this state.

- [ ] **Step 3: Commit (the new file only)**

```bash
git add app/dashboard/_components/trades-table.tsx
git commit -m "feat(autotrader/frontend): extract TradesTable, StatusBadge, FeedIndicator into shared module

Verbatim move out of pipeline/page.tsx so the new /dashboard/trades
page can render the same component without duplicating the code.
The originals in pipeline/page.tsx are removed in a later task — for
the next two commits the codebase has both copies."
```

---

### Task 12: Create `/dashboard/trades` page

**Files:**
- Create: `frontend/app/dashboard/trades/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { ApiError, type TradeAttempt, pipeline } from "@/lib/api";
import { useTradeFeed } from "@/lib/use-trade-feed";
import { TradesTable } from "../_components/trades-table";

export default function TradesPage() {
  const feedState = useTradeFeed();

  const trades = useQuery<TradeAttempt[]>({
    queryKey: ["pipeline", "trades"],
    queryFn: () => pipeline.trades(100),
    refetchInterval: 15_000,
  });

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Trades</h2>
        <p className="text-sm text-muted-foreground">
          Every signal that reached the executor — won, lost, blocked by
          the risk gate, broker-rejected, or expired. Live-streams over
          WebSocket; falls back to a 15s poll if the feed drops. Phase 2
          adds filters (date range, channel, parser, asset, direction,
          status).
        </p>
      </section>

      {trades.error && (
        <Card className="border-destructive/40">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Couldn&rsquo;t load trades:{" "}
              {trades.error instanceof ApiError
                ? trades.error.message
                : String(trades.error)}
            </p>
          </CardContent>
        </Card>
      )}

      <TradesTable
        trades={trades.data ?? []}
        loading={trades.isLoading}
        feedState={feedState}
      />
    </div>
  );
}
```

- [ ] **Step 2: Verify type-check + build**

```bash
bun run type-check
bun run build
```

Expected: both pass. The build's route table now includes `/dashboard/trades`.

- [ ] **Step 3: Manual verification**

```bash
bun run dev
```

Visit `http://localhost:3000/dashboard/trades`. The page should render with the sidebar showing "Trades" highlighted, topbar showing "Trade / Trades", and the trades table populated (or showing the empty state if there are no trades yet).

- [ ] **Step 4: Commit**

```bash
git add app/dashboard/trades/page.tsx
git commit -m "feat(autotrader/frontend): add /dashboard/trades page

Renders the extracted TradesTable on its own route. Same data source
(/pipeline/trades + WebSocket) as the previous monolith — pure IA
move, no behavior change. The subtitle flags Phase 2 filters as
the next addition."
```

---

### Task 13: Extract decisions feed into a shared component

**Files:**
- Create: `frontend/app/dashboard/_components/decisions-feed.tsx`

The current `pipeline/page.tsx` defines `ParserDecisionsCard` and `DecisionBadge` inline (lines 159–296).

- [ ] **Step 1: Create the extracted module**

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { type ParserDecision, pipeline } from "@/lib/api";
import { type FeedState } from "@/lib/use-trade-feed";
import { FeedIndicator } from "./trades-table";

export function DecisionsFeed({ feedState }: { feedState: FeedState }) {
  const decisions = useQuery<ParserDecision[]>({
    queryKey: ["pipeline", "decisions"],
    queryFn: () => pipeline.decisions(50),
    refetchInterval: feedState === "live" ? false : 15_000,
    staleTime: feedState === "live" ? Infinity : 0,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              Recent parsing decisions
              <FeedIndicator state={feedState} />
            </CardTitle>
            <CardDescription>
              Every dispatch — matched, rejected, or routed to a chat with
              no parsers. Surfaces the same data the executor logs as
              <code> pipeline.matched </code> / <code>pipeline.no_match</code>{" "}
              so you can debug parser regressions without scraping logs.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {decisions.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {!decisions.isLoading && (decisions.data ?? []).length === 0 && (
          <p className="text-sm text-muted-foreground">
            No parsing decisions yet. The next watched-chat message will
            appear here as it&rsquo;s dispatched.
          </p>
        )}
        {(decisions.data ?? []).length > 0 && (
          <div className="max-h-[28rem] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-1.5 font-medium">When</th>
                  <th className="px-2 py-1.5 font-medium">Chat</th>
                  <th className="px-2 py-1.5 font-medium">Parser</th>
                  <th className="px-2 py-1.5 font-medium">Outcome</th>
                  <th className="px-2 py-1.5 font-medium">Reason / preview</th>
                </tr>
              </thead>
              <tbody>
                {(decisions.data ?? []).map((d, idx) => (
                  <tr
                    key={`${d.ts}-${d.chat_id}-${d.parser_config_id ?? "none"}-${idx}`}
                    className="border-b last:border-0 align-top"
                  >
                    <td className="whitespace-nowrap px-2 py-1.5 text-muted-foreground">
                      {new Date(d.ts).toLocaleTimeString()}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 font-mono">
                      {d.chat_id}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 font-mono">
                      {d.parser_name ? (
                        <>
                          {d.parser_name}
                          {d.parser_type && (
                            <span className="ml-1 text-muted-foreground">
                              ({d.parser_type})
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5">
                      <DecisionBadge outcome={d.outcome} signals={d.signals} />
                    </td>
                    <td className="px-2 py-1.5 text-xs">
                      {d.reasons.length > 0 ? (
                        <span className="text-amber-300">
                          {d.reasons.join("; ")}
                        </span>
                      ) : d.text_preview ? (
                        <span className="text-muted-foreground">
                          {d.text_preview}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function DecisionBadge({
  outcome,
  signals,
}: {
  outcome: ParserDecision["outcome"];
  signals: number;
}) {
  if (outcome === "matched") {
    return <Badge variant="success">matched · {signals}</Badge>;
  }
  if (outcome === "no_match") {
    return <Badge variant="outline">no match</Badge>;
  }
  if (outcome === "build_failed") {
    return <Badge variant="destructive">build failed</Badge>;
  }
  if (outcome === "no_configs") {
    return <Badge variant="secondary">no parsers</Badge>;
  }
  if (outcome === "pipeline_inactive") {
    return <Badge variant="secondary">pipeline off</Badge>;
  }
  return <Badge variant="secondary">{outcome}</Badge>;
}
```

- [ ] **Step 2: Verify type-check**

```bash
bun run type-check
```

Expected: passes (the original component still lives in `pipeline/page.tsx` — duplicate is intentional for one task).

- [ ] **Step 3: Commit**

```bash
git add app/dashboard/_components/decisions-feed.tsx
git commit -m "feat(autotrader/frontend): extract DecisionsFeed into shared module

Same pattern as the trades-table extraction — enables /dashboard/decisions
to render this without duplicating the code. The pipeline-page copy
goes away in the slim-down task."
```

---

### Task 14: Create `/dashboard/decisions` page

**Files:**
- Create: `frontend/app/dashboard/decisions/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
"use client";

import { useTradeFeed } from "@/lib/use-trade-feed";
import { DecisionsFeed } from "../_components/decisions-feed";

export default function DecisionsPage() {
  const feedState = useTradeFeed();
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Decisions</h2>
        <p className="text-sm text-muted-foreground">
          Live parser-decision feed. Every dispatched message — matched,
          rejected, no-config, or pipeline-inactive — shows up here as
          it happens. The decision ring is in-memory (200-entry cap);
          for permanent history, see Trades. Phase 2 adds channel and
          outcome filters.
        </p>
      </section>
      <DecisionsFeed feedState={feedState} />
    </div>
  );
}
```

- [ ] **Step 2: Verify type-check + build**

```bash
bun run type-check
bun run build
```

Expected: both pass; route table includes `/dashboard/decisions`.

- [ ] **Step 3: Manual verification**

```bash
bun run dev
```

Visit `http://localhost:3000/dashboard/decisions`. Sidebar should show "Decisions" highlighted, breadcrumb "Trade / Decisions". If the pipeline is active and channels are firing, decisions should stream in live.

- [ ] **Step 4: Commit**

```bash
git add app/dashboard/decisions/page.tsx
git commit -m "feat(autotrader/frontend): add /dashboard/decisions page

Renders the extracted DecisionsFeed on its own route. Pure IA move."
```

---

### Task 15: Slim down `pipeline/page.tsx`

**Files:**
- Modify: `frontend/app/dashboard/pipeline/page.tsx`

The page goes from 1058 lines to ~250. Keeps: status card, master switch, kill switch, risk caps form, today's budget, streaks. Removes: stats overview (channel breakdown + latency tiles — these move to Phase 2 Analytics), decisions feed, trades table, section nav (no longer needed once the page fits on a single screen).

- [ ] **Step 1: Replace the file with the slim version**

Overwrite `frontend/app/dashboard/pipeline/page.tsx` entirely:

```tsx
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  type PipelineStatus,
  type RiskCaps,
  type RiskOverview,
  pipeline,
  risk,
} from "@/lib/api";

export default function PipelinePage() {
  const status = useQuery<PipelineStatus>({
    queryKey: ["pipeline", "status"],
    queryFn: pipeline.status,
    refetchInterval: 5_000,
  });

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Pipeline</h2>
        <p className="text-sm text-muted-foreground">
          Master switch &amp; safety controls. For trade history see{" "}
          <strong>Trades</strong>; for the live dispatch feed see{" "}
          <strong>Decisions</strong>; for performance breakdowns see{" "}
          <strong>Analytics</strong> (Phase 2).
        </p>
      </section>

      {status.isLoading && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Loading pipeline status…</p>
          </CardContent>
        </Card>
      )}

      {status.error && (
        <Card className="border-destructive/40">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Couldn&rsquo;t load pipeline status:{" "}
              {status.error instanceof ApiError
                ? status.error.message
                : String(status.error)}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              If you just signed in this can be a stale auth token —
              try a hard refresh.
            </p>
          </CardContent>
        </Card>
      )}

      {status.data && <StatusCard status={status.data} />}
      {status.data && <RiskOverviewCard />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status + master switch
// ---------------------------------------------------------------------------

function StatusCard({ status }: { status: PipelineStatus }) {
  const qc = useQueryClient();

  const activate = useMutation({
    mutationFn: (active: boolean) => pipeline.activate(active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipeline"] }),
  });

  const killSwitch = useMutation({
    mutationFn: (active: boolean) => pipeline.killSwitch(active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipeline"] }),
  });

  const ready =
    status.broker_connected &&
    status.telegram_logged_in &&
    status.enabled_parser_count > 0;

  return (
    <Card className={status.active ? "border-emerald-500/40" : undefined}>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              Master switch
              {status.active ? (
                <Badge variant="success">active</Badge>
              ) : (
                <Badge variant="secondary">stopped</Badge>
              )}
              {status.kill_switch_engaged && (
                <Badge variant="destructive">kill switch</Badge>
              )}
            </CardTitle>
            <CardDescription>
              Trades fire only when all gates align: master switch on,
              kill switch off, parser config enabled, and (for REAL
              accounts) the env flag enabled.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {status.active ? (
              <Button
                variant="outline"
                onClick={() => activate.mutate(false)}
                disabled={activate.isPending}
              >
                {activate.isPending ? "Stopping…" : "Deactivate"}
              </Button>
            ) : (
              <Button
                onClick={() => activate.mutate(true)}
                disabled={activate.isPending || !ready}
                title={
                  ready
                    ? undefined
                    : "Connect broker + login to Telegram + add at least one enabled parser"
                }
              >
                {activate.isPending ? "Starting…" : "Activate"}
              </Button>
            )}
            <Button
              variant={status.kill_switch_engaged ? "default" : "destructive"}
              onClick={() => killSwitch.mutate(!status.kill_switch_engaged)}
              disabled={killSwitch.isPending}
            >
              {status.kill_switch_engaged ? "Release kill switch" : "Kill switch"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {activate.isError && (
          <p className="mb-3 text-sm text-destructive">
            {activate.error instanceof ApiError
              ? activate.error.message
              : String(activate.error)}
          </p>
        )}
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-3 lg:grid-cols-4">
          <Row
            label="Broker"
            value={
              status.broker_connected ? (
                <Badge variant="success">connected</Badge>
              ) : (
                <Badge variant="secondary">disconnected</Badge>
              )
            }
          />
          <Row
            label="Telegram"
            value={
              status.telegram_logged_in ? (
                <Badge variant="success">logged in</Badge>
              ) : (
                <Badge variant="secondary">logged out</Badge>
              )
            }
          />
          <Row
            label="REAL trading (env)"
            value={
              status.live_trading_enabled_env ? (
                <Badge variant="warning">enabled</Badge>
              ) : (
                <Badge variant="outline">disabled</Badge>
              )
            }
          />
          <Row label="Watched chats" value={String(status.watched_chat_count)} />
          <Row
            label="Channels subscribed"
            value={
              <Badge
                variant={
                  status.subscribed_chat_count >= status.watched_chat_count
                    ? "success"
                    : "warning"
                }
              >
                {status.subscribed_chat_count} / {status.watched_chat_count}
              </Badge>
            }
          />
          <Row
            label="Enabled parsers"
            value={String(status.enabled_parser_count)}
          />
          <Row
            label="Last channel msg"
            value={<FreshnessBadge ts={status.last_message_received_at} />}
          />
        </dl>
      </CardContent>
    </Card>
  );
}

function FreshnessBadge({ ts }: { ts: string | null }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => (n + 1) % 1_000_000), 1_000);
    return () => clearInterval(id);
  }, []);

  if (ts === null) {
    return <span className="text-muted-foreground">—</span>;
  }
  const ageMs = Date.now() - Date.parse(ts);
  const ageSec = Math.max(0, Math.floor(ageMs / 1_000));
  const variant: "success" | "warning" | "destructive" =
    ageSec < 60 ? "success" : ageSec < 600 ? "warning" : "destructive";
  const label =
    ageSec < 60
      ? `${ageSec}s ago`
      : ageSec < 3_600
        ? `${Math.floor(ageSec / 60)}m ago`
        : `${Math.floor(ageSec / 3_600)}h ago`;
  return <Badge variant={variant}>{label}</Badge>;
}

// ---------------------------------------------------------------------------
// Risk caps + budget + streaks
// ---------------------------------------------------------------------------

function RiskOverviewCard() {
  const overview = useQuery<RiskOverview>({
    queryKey: ["risk", "overview"],
    queryFn: risk.overview,
    refetchInterval: 5_000,
  });

  if (overview.isLoading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-muted-foreground">Loading risk overview…</p>
        </CardContent>
      </Card>
    );
  }
  if (!overview.data) return null;

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <BudgetCard data={overview.data} />
      <RiskCapsForm data={overview.data} />
      <StreaksCard data={overview.data} />
    </div>
  );
}

function BudgetCard({ data }: { data: RiskOverview }) {
  const realised = data.budget.realised_pnl;
  const committed = data.budget.committed_stake;
  const open = data.budget.open_attempts;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Today&rsquo;s budget (UTC)</CardTitle>
        <CardDescription>Resets at 00:00 UTC.</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="space-y-2 text-sm">
          <Row
            label="Realised P&L"
            value={
              <span
                className={
                  realised < 0
                    ? "font-mono text-destructive"
                    : "font-mono text-emerald-400"
                }
              >
                {realised >= 0 ? "+" : ""}
                {realised.toFixed(2)}
              </span>
            }
          />
          <Row
            label="Committed stake"
            value={<span className="font-mono">{committed.toFixed(2)}</span>}
          />
          <Row
            label="Open attempts"
            value={<span className="font-mono">{open}</span>}
          />
        </dl>
      </CardContent>
    </Card>
  );
}

function RiskCapsForm({ data }: { data: RiskOverview }) {
  const qc = useQueryClient();
  const [caps, setCaps] = useState<RiskCaps>(data.caps);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setCaps(data.caps);
  }, [data.caps]);

  const save = useMutation({
    mutationFn: () => risk.updateCaps(caps),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["risk"] });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily caps</CardTitle>
        <CardDescription>
          Set to <code>0</code> to disable a cap. Caps reset at 00:00 UTC.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="space-y-4"
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="daily_max_loss">Max daily loss</Label>
              <Input
                id="daily_max_loss"
                type="number"
                step="0.01"
                min="0"
                value={caps.daily_max_loss}
                onChange={(e) =>
                  setCaps({
                    ...caps,
                    daily_max_loss: Math.max(0, Number(e.target.value) || 0),
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="daily_max_stake">Max daily stake</Label>
              <Input
                id="daily_max_stake"
                type="number"
                step="0.01"
                min="0"
                value={caps.daily_max_stake}
                onChange={(e) =>
                  setCaps({
                    ...caps,
                    daily_max_stake: Math.max(0, Number(e.target.value) || 0),
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="max_concurrent_trades">Max concurrent</Label>
              <Input
                id="max_concurrent_trades"
                type="number"
                step="1"
                min="0"
                value={caps.max_concurrent_trades}
                onChange={(e) =>
                  setCaps({
                    ...caps,
                    max_concurrent_trades: Math.max(
                      0,
                      Number(e.target.value) || 0,
                    ),
                  })
                }
              />
            </div>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save caps"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function StreaksCard({ data }: { data: RiskOverview }) {
  const qc = useQueryClient();
  const reset = useMutation({
    mutationFn: (parserConfigId: number) => risk.resetStreak(parserConfigId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["risk"] }),
  });
  const martingaleRows = data.streaks.filter((s) => s.martingale_enabled);

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Martingale streaks</CardTitle>
        <CardDescription>
          One row per parser with martingale enabled. The next stake at
          step <em>n</em> is <code>base × multiplier^n</code>;{" "}
          <code>recovery=N</code> caps the ladder at N steps before
          resetting to base — set it to match the channel directive
          (&ldquo;TAKE 1 STEP MTG&rdquo; → <code>recovery=1</code>). A
          win resets to step 0 when reset-on-win is on.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {martingaleRows.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No parsers have martingale enabled. Toggle it on a parser&rsquo;s
            edit page to start tracking a recovery ladder.
          </p>
        )}
        {martingaleRows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-1.5 font-medium">Parser</th>
                  <th className="px-2 py-1.5 font-medium">Mult</th>
                  <th
                    className="px-2 py-1.5 font-medium"
                    title="Maximum recovery steps before the ladder resets to base"
                  >
                    Recovery
                  </th>
                  <th className="px-2 py-1.5 font-medium">Step</th>
                  <th className="px-2 py-1.5 font-medium">Last</th>
                  <th className="px-2 py-1.5 font-medium">Last stake</th>
                  <th className="px-2 py-1.5 font-medium">Updated</th>
                  <th className="px-2 py-1.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {martingaleRows.map((s) => (
                  <tr key={s.parser_config_id} className="border-b last:border-0">
                    <td className="px-2 py-1.5 font-mono">{s.parser_name}</td>
                    <td className="px-2 py-1.5 font-mono">×{s.multiplier}</td>
                    <td className="px-2 py-1.5 font-mono">
                      {s.max_streak === 0 ? "∞" : s.max_streak}
                    </td>
                    <td className="px-2 py-1.5">
                      {s.current_streak === 0 ? (
                        <Badge variant="outline">base</Badge>
                      ) : (
                        <Badge variant="warning">step {s.current_streak}</Badge>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      {s.last_outcome === "won" && (
                        <Badge variant="success">won</Badge>
                      )}
                      {s.last_outcome === "lost" && (
                        <Badge variant="destructive">lost</Badge>
                      )}
                      {s.last_outcome === "" && (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {s.last_stake > 0 ? s.last_stake.toFixed(2) : "—"}
                    </td>
                    <td className="px-2 py-1.5 text-xs text-muted-foreground">
                      {s.updated_at
                        ? new Date(s.updated_at).toLocaleTimeString()
                        : "—"}
                    </td>
                    <td className="px-2 py-1.5">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => reset.mutate(s.parser_config_id)}
                        disabled={reset.isPending || s.current_streak === 0}
                      >
                        Reset
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
```

What's gone, deliberately:
- `useTradeFeed` hook call — moved into `/dashboard/trades` and `/dashboard/decisions`.
- `TradesTable`, `StatusBadge`, `FeedIndicator` — extracted in Task 11.
- `ParserDecisionsCard`, `DecisionBadge` — extracted in Task 13.
- `StatsOverviewCard`, `LatencyCard`, `LatencyTile`, `Stat`, `fmtMs`, `ChannelStatsCard` — these belong to Phase 2 Analytics; deleting them here is correct.
- `SectionNav`, `SECTION_LINKS`, scroll-mt anchor wrappers — page is short enough that nav-by-anchor isn't needed.

What still works exactly as before:
- Master switch, kill switch, broker/telegram/parser readiness gating.
- Daily caps form.
- Today's budget snapshot.
- Martingale streaks with reset button.
- Channel-subscription-mismatch warning badge.

- [ ] **Step 2: Verify type-check + build**

```bash
bun run type-check
bun run build
```

Expected: both pass. The build's route table no longer includes `/stats/overview` calls in the bundle (because `StatsOverviewCard` is gone). File line count goes from 1058 → roughly 460.

- [ ] **Step 3: Manual verification**

```bash
bun run dev
```

Visit `http://localhost:3000/dashboard/pipeline`. The page should render:
- Page title "Pipeline" + the new subtitle pointing to Trades / Decisions / Analytics
- Status card with master switch + kill switch buttons + the field grid (broker, telegram, REAL trading, watched chats, channels subscribed, enabled parsers, last channel msg)
- Three risk cards (Today's budget · Daily caps form · Martingale streaks)
- No Stats card (moved to future Analytics page)
- No Decisions card (now at /dashboard/decisions)
- No Trades card (now at /dashboard/trades)

Toggle the master switch (DEMO only — never on REAL during a smoke test). Confirm it activates without errors.

- [ ] **Step 4: Commit**

```bash
git add app/dashboard/pipeline/page.tsx
git commit -m "refactor(autotrader/frontend): slim pipeline page to controls only

Removes ~600 lines of trades + decisions + stats UI now that those
live on dedicated routes. Page goes from 1058 to ~460 lines and
focuses solely on operational control: status, master switch, kill
switch, daily caps, today's budget, martingale streaks. Behavior of
the kept controls is unchanged — pure extraction."
```

---

## New Overview page

### Task 16: `<OverviewKpiHero>` component

**Files:**
- Create: `frontend/app/dashboard/_components/overview-kpi-hero.tsx`

The hero strip shows 4 cards. Phase 1 sources only from existing endpoints — so deltas shown are computed from data already on the page (no time-series backend yet). When data isn't enough to compute a delta, show "—" not a fake number.

- [ ] **Step 1: Write the component**

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import {
  type RiskOverview,
  type StatsOverview,
  risk,
  stats,
} from "@/lib/api";

/**
 * Phase 1 KPI hero. Reads what already exists:
 *   - /risk/overview for today's budget + caps (P&L, risk-budget-left)
 *   - /stats/overview for today's per-channel summary (rolled up to
 *     dashboard-wide trade/win count)
 *
 * Deltas are intentionally absent in Phase 1 — there's no historical
 * comparison endpoint yet. The card layout already has a slot for
 * them so Phase 2 can drop the values in without touching markup.
 */
export function OverviewKpiHero() {
  const r = useQuery<RiskOverview>({
    queryKey: ["risk", "overview"],
    queryFn: risk.overview,
    refetchInterval: 10_000,
  });
  const s = useQuery<StatsOverview>({
    queryKey: ["stats", "overview"],
    queryFn: stats.overview,
    refetchInterval: 15_000,
  });

  const realised = r.data?.budget.realised_pnl ?? null;
  const cap = r.data?.caps.daily_max_loss ?? 0;
  // Risk budget remaining = cap - realised loss (only the negative side
  // counts; gains don't deplete the loss cap). Cap=0 means "no cap";
  // we render "—" then.
  const riskBudgetLeft =
    cap > 0 && realised !== null
      ? Math.max(0, cap - Math.abs(Math.min(0, realised)))
      : null;

  // Trade counts: sum across channels in the today snapshot.
  const totals = (s.data?.channels ?? []).reduce(
    (acc, c) => ({
      total: acc.total + c.total,
      won: acc.won + c.won,
      lost: acc.lost + c.lost,
      rejected: acc.rejected + c.rejected,
      pending: acc.pending + c.pending,
    }),
    { total: 0, won: 0, lost: 0, rejected: 0, pending: 0 },
  );
  const settled = totals.won + totals.lost;
  const winRate = settled > 0 ? totals.won / settled : null;

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <KpiCard
        label="P&L today"
        value={
          realised === null
            ? "—"
            : `${realised >= 0 ? "+" : "−"}$${Math.abs(realised).toFixed(2)}`
        }
        valueTone={
          realised === null
            ? "neutral"
            : realised > 0
              ? "positive"
              : realised < 0
                ? "negative"
                : "neutral"
        }
        subtext="UTC day · resets at 00:00"
      />
      <KpiCard
        label="Win rate"
        value={winRate === null ? "—" : `${Math.round(winRate * 100)}%`}
        valueTone="neutral"
        subtext={
          settled === 0
            ? "no settled trades yet today"
            : `${totals.won} won · ${totals.lost} lost`
        }
      />
      <KpiCard
        label="Trades"
        value={String(totals.total)}
        valueTone="neutral"
        subtext={
          totals.total === 0
            ? "no trades dispatched yet"
            : [
                settled && `${settled} settled`,
                totals.pending && `${totals.pending} open`,
                totals.rejected && `${totals.rejected} rejected`,
              ]
                .filter(Boolean)
                .join(" · ")
        }
      />
      <KpiCard
        label="Risk budget left"
        value={
          riskBudgetLeft === null ? "—" : `$${riskBudgetLeft.toFixed(2)}`
        }
        valueTone="neutral"
        subtext={cap > 0 ? `of $${cap.toFixed(0)} daily loss cap` : "no cap set"}
      />
    </div>
  );
}

function KpiCard({
  label,
  value,
  valueTone,
  subtext,
}: {
  label: string;
  value: string;
  valueTone: "positive" | "negative" | "neutral";
  subtext: string;
}) {
  const toneClass =
    valueTone === "positive"
      ? "text-success"
      : valueTone === "negative"
        ? "text-destructive"
        : "text-foreground";
  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
      </CardHeader>
      <CardContent>
        <div
          className={`text-3xl font-semibold tracking-tight tabular-nums ${toneClass}`}
        >
          {value}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">{subtext}</div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Verify type-check**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add app/dashboard/_components/overview-kpi-hero.tsx
git commit -m "feat(autotrader/frontend): add OverviewKpiHero — 4 KPI cards on the new dashboard

Sources from existing /risk/overview + /stats/overview endpoints —
no new backend. Empty-state values show \"—\" rather than fake zeros
so a fresh dashboard isn't misread as \"already broke even\". The
markup reserves a slot for Phase 2 deltas without rendering them yet."
```

---

### Task 17: `<OverviewEquityStub>` component

**Files:**
- Create: `frontend/app/dashboard/_components/overview-equity-stub.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

/**
 * Placeholder for the Phase 2 equity curve. Renders the right card
 * structure (header, range buttons) so the eventual swap is layout-
 * preserving. Body is a simple "ships in Phase 2" message with a
 * link to /dashboard/trades for the raw data.
 */

import Link from "next/link";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const RANGES = ["24h", "7d", "30d", "All"] as const;

export function OverviewEquityStub() {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Equity curve</CardTitle>
            <CardDescription>
              Cumulative P&amp;L over time.
            </CardDescription>
          </div>
          <div className="flex gap-1">
            {RANGES.map((r) => (
              <Button
                key={r}
                variant={r === "7d" ? "secondary" : "ghost"}
                size="sm"
                disabled
                title="Range toggle activates in Phase 2"
              >
                {r}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex h-32 items-center justify-center rounded-md border border-dashed">
          <div className="text-center">
            <p className="text-sm font-medium">Live charting ships in Phase 2.</p>
            <p className="mt-1 text-xs text-muted-foreground">
              For now, see{" "}
              <Link
                href="/dashboard/trades"
                className="underline underline-offset-2 hover:text-foreground"
              >
                Trades
              </Link>{" "}
              for raw history.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Verify**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add app/dashboard/_components/overview-equity-stub.tsx
git commit -m "feat(autotrader/frontend): add OverviewEquityStub placeholder for Phase 2 chart

Renders the final card chrome (title, range buttons disabled) so
Phase 2 only swaps the body. Honest empty state with a deep-link to
Trades for users who need history right now."
```

---

### Task 18: `<OverviewRecentActivity>` component

**Files:**
- Create: `frontend/app/dashboard/_components/overview-recent-activity.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { type TradeAttempt, pipeline } from "@/lib/api";
import { useTradeFeed } from "@/lib/use-trade-feed";
import { StatusBadge } from "./trades-table";

/**
 * Compact 8-row recent-trades table for the Overview page. Re-uses
 * the StatusBadge from trades-table.tsx so styling stays consistent
 * with the full Trades page. Live-updated by the existing WebSocket
 * hook — same query key as the full table, so they share data.
 */
export function OverviewRecentActivity() {
  // Mounting useTradeFeed here keeps the WS connection alive whenever
  // the user is on Overview. The hook is idempotent across components
  // (the singleton check is handled inside).
  useTradeFeed();

  const trades = useQuery<TradeAttempt[]>({
    queryKey: ["pipeline", "trades"],
    queryFn: () => pipeline.trades(50),
    refetchInterval: 30_000,
  });

  const recent = (trades.data ?? []).slice(0, 8);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>Last 8 trade attempts.</CardDescription>
          </div>
          <Link
            href="/dashboard/trades"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            View all →
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {trades.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {!trades.isLoading && recent.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No trades yet today. Activate the pipeline to see signals
            here as they arrive.
          </p>
        )}
        {recent.length > 0 && (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="px-2 py-1.5 font-medium">When</th>
                <th className="px-2 py-1.5 font-medium">Asset</th>
                <th className="px-2 py-1.5 font-medium">Dir</th>
                <th className="px-2 py-1.5 font-medium">Stake</th>
                <th className="px-2 py-1.5 font-medium">Status</th>
                <th className="px-2 py-1.5 font-medium text-right">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((t) => (
                <tr key={t.id} className="border-b last:border-0">
                  <td className="px-2 py-1.5 font-mono text-muted-foreground">
                    {new Date(t.received_at).toLocaleTimeString()}
                  </td>
                  <td className="px-2 py-1.5 font-mono">{t.asset}</td>
                  <td className="px-2 py-1.5">
                    <Badge
                      variant={t.direction === "call" ? "success" : "destructive"}
                    >
                      {t.direction}
                    </Badge>
                  </td>
                  <td className="px-2 py-1.5 font-mono">{t.stake}</td>
                  <td className="px-2 py-1.5">
                    <StatusBadge status={t.status} />
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right font-mono tabular-nums ${
                      t.profit === null
                        ? "text-muted-foreground"
                        : t.profit > 0
                          ? "text-success"
                          : t.profit < 0
                            ? "text-destructive"
                            : ""
                    }`}
                  >
                    {t.profit === null
                      ? "—"
                      : `${t.profit >= 0 ? "+" : "−"}${Math.abs(t.profit).toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Verify**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add app/dashboard/_components/overview-recent-activity.tsx
git commit -m "feat(autotrader/frontend): add OverviewRecentActivity (last 8 trades)

Shares the [\"pipeline\", \"trades\"] query key with /dashboard/trades so
the WebSocket-driven invalidation in use-trade-feed updates both
views in lockstep without two round-trips."
```

---

### Task 19: `<OverviewStatusCards>` component

**Files:**
- Create: `frontend/app/dashboard/_components/overview-status-cards.tsx`

- [ ] **Step 1: Write the component**

```tsx
"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import {
  type BrokerBalance,
  type BrokerStatus,
  type PipelineStatus,
  type TelegramStatus,
  broker,
  pipeline,
  telegram,
} from "@/lib/api";

/**
 * Three small status panels on the Overview page right column. Each
 * polls its own endpoint independently (different cadence makes
 * sense per source).
 */
export function OverviewStatusCards() {
  const b = useQuery<BrokerStatus>({
    queryKey: ["broker", "status"],
    queryFn: broker.status,
    refetchInterval: 10_000,
  });
  const balance = useQuery<BrokerBalance>({
    queryKey: ["broker", "balance"],
    queryFn: broker.balance,
    refetchInterval: 30_000,
    enabled: b.data?.connected ?? false,
  });
  const t = useQuery<TelegramStatus>({
    queryKey: ["telegram", "status"],
    queryFn: telegram.status,
    refetchInterval: 15_000,
  });
  const p = useQuery<PipelineStatus>({
    queryKey: ["pipeline", "status"],
    queryFn: pipeline.status,
    refetchInterval: 5_000,
  });

  return (
    <div className="grid gap-4">
      <MiniCard
        label="Broker"
        primary={
          b.data?.connected
            ? "● Connected"
            : b.data?.configured
              ? "Disconnected"
              : "Not configured"
        }
        secondary={
          b.data?.connected
            ? `${b.data.account_mode} · ${
                balance.data ? `$${balance.data.balance.toFixed(2)}` : "…"
              }`
            : "—"
        }
        tone={b.data?.connected ? "ok" : "muted"}
      />
      <MiniCard
        label="Telegram"
        primary={t.data?.logged_in ? "● Logged in" : "Not connected"}
        secondary={
          t.data?.logged_in
            ? `${p.data?.watched_chat_count ?? "?"} watched · ${
                p.data?.last_message_received_at
                  ? `last msg ${describeAge(p.data.last_message_received_at)}`
                  : "no messages yet"
              }`
            : "—"
        }
        tone={t.data?.logged_in ? "ok" : "muted"}
      />
      <MiniCard
        label="Pipeline"
        primary={
          p.data?.kill_switch_engaged
            ? "⏸ Kill switch"
            : p.data?.active
              ? "● Live"
              : "Idle"
        }
        secondary={
          p.data
            ? `${p.data.subscribed_chat_count}/${p.data.watched_chat_count} subscribed · ${p.data.enabled_parser_count} parsers`
            : "—"
        }
        tone={
          p.data?.kill_switch_engaged
            ? "warn"
            : p.data?.active
              ? "ok"
              : "muted"
        }
      />
    </div>
  );
}

function describeAge(iso: string): string {
  const sec = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function MiniCard({
  label,
  primary,
  secondary,
  tone,
}: {
  label: string;
  primary: string;
  secondary: string;
  tone: "ok" | "warn" | "muted";
}) {
  const toneClass =
    tone === "ok"
      ? "text-success"
      : tone === "warn"
        ? "text-warning"
        : "text-muted-foreground";
  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
      </CardHeader>
      <CardContent>
        <div className={`text-sm font-semibold ${toneClass}`}>{primary}</div>
        <div className="mt-1 text-xs text-muted-foreground">{secondary}</div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Verify**

```bash
bun run type-check
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add app/dashboard/_components/overview-status-cards.tsx
git commit -m "feat(autotrader/frontend): add OverviewStatusCards for broker/telegram/pipeline

Three small status panels with tone-coded primary lines. Balance is
gated behind broker.connected so we don't waste a request on a
disconnected account. Last-msg age is computed inline from the
pipeline status response."
```

---

### Task 20: Wire the new Overview page

**Files:**
- Modify: `frontend/app/dashboard/page.tsx`

- [ ] **Step 1: Replace the file**

```tsx
"use client";

import { OverviewEquityStub } from "./_components/overview-equity-stub";
import { OverviewKpiHero } from "./_components/overview-kpi-hero";
import { OverviewRecentActivity } from "./_components/overview-recent-activity";
import { OverviewStatusCards } from "./_components/overview-status-cards";

export default function OverviewPage() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Overview</h2>
        <p className="text-sm text-muted-foreground">
          What&rsquo;s happening right now. Hero metrics, equity curve
          (Phase 2), recent activity, and component status.
        </p>
      </section>

      <OverviewKpiHero />

      <OverviewEquityStub />

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <OverviewRecentActivity />
        <OverviewStatusCards />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify type-check + build**

```bash
bun run type-check
bun run build
```

Expected: both pass. The build's route table shows `/dashboard` as the new Overview page.

- [ ] **Step 3: Manual verification**

```bash
bun run dev
```

Visit `http://localhost:3000/dashboard`. The page should render:
- Sidebar with "Overview" highlighted
- Topbar with "Trade / Overview" + status pill
- KPI hero strip (4 cards across; on a fresh DB they'll all show "—" or "0")
- Equity curve placeholder card with disabled range buttons + "Phase 2" message
- Two-column row: Recent activity table on the left, three status mini-cards on the right

Toggle theme via the sidebar footer button — both modes should look polished, no broken text, no blinding white-on-white.

- [ ] **Step 4: Commit**

```bash
git add app/dashboard/page.tsx
git commit -m "feat(autotrader/frontend): replace dashboard with new Overview composition

KPI hero + equity stub + recent activity + status mini-cards. All
data sourced from existing endpoints — Phase 2 swaps the equity stub
for a live chart and adds delta values to the KPI cards once the
/stats/v2/* endpoints exist."
```

---

## Verification

### Task 21: End-to-end smoke check + final type-check + build

**Files:** none (verification only)

- [ ] **Step 1: Clean rebuild**

```bash
cd /Users/imranahmedani/Desktop/pyquotex/autotrader/frontend
rm -rf .next
bun install
bun run type-check
bun run build
```

Expected: all four succeed. Build emits the new route table:

```
/                              (login redirect)
/login
/dashboard                     (Overview)
/dashboard/analytics           (placeholder)
/dashboard/broker
/dashboard/decisions           (NEW)
/dashboard/parsers
/dashboard/parsers/[chat_id]
/dashboard/pipeline            (slimmed)
/dashboard/telegram
/dashboard/trades              (NEW)
```

If any route is missing or unexpected, stop and triage.

- [ ] **Step 2: Visual smoke checklist**

```bash
bun run dev
```

Run through this list, checking each item:

- [ ] `/login` renders with the passcode input. Sign in successfully.
- [ ] `/dashboard` (Overview) shows KPI hero, equity stub, recent activity, status mini-cards.
- [ ] Sidebar shows: Overview, Analytics, Trades, Decisions, Pipeline (Trade group); Parsers, Telegram, Broker (Configure group); theme toggle in footer.
- [ ] Click each sidebar item — it loads and the active highlight follows.
- [ ] Click the sidebar trigger (top-left of topbar) — sidebar collapses to icon rail. Hover an icon — tooltip with the label appears. Refresh — collapsed state persists.
- [ ] Click the theme toggle — page transitions to the next mode (light → dark → system). Repeat. The button label updates ("Light" / "Dark" / "System (light)" / "System (dark)").
- [ ] On `/dashboard/pipeline` — verify status card, master switch, kill switch button, today's budget, daily caps form, martingale streaks all render. The page should be noticeably shorter than before.
- [ ] On `/dashboard/trades` — verify the trades table renders.
- [ ] On `/dashboard/decisions` — verify the decisions feed renders.
- [ ] On `/dashboard/broker`, `/dashboard/telegram`, `/dashboard/parsers` — verify the existing pages still render correctly inside the new sidebar shell.
- [ ] Topbar status pill — confirm it reflects the live pipeline state and updates when you toggle the master switch on the Pipeline page.
- [ ] No console errors in the browser dev tools other than expected (cookie warnings, etc.).

If any item fails, fix it before moving on. Each fix gets its own commit using a descriptive message.

- [ ] **Step 3: Backend smoke**

The backend wasn't touched in this phase. Confirm tests still pass:

```bash
cd /Users/imranahmedani/Desktop/pyquotex/autotrader/backend
uv run pytest -q
```

Expected: all 195 tests pass in roughly 14 seconds. If anything fails, investigate — Phase 1 should not have touched any code these tests cover.

- [ ] **Step 4: Final commit (if any fixes were made above)**

If Step 2 surfaced fixes:

```bash
cd /Users/imranahmedani/Desktop/pyquotex/autotrader
git add <fixed files>
git commit -m "fix(autotrader/frontend): <specific fix from smoke check>"
```

Otherwise no commit needed.

---

## Wrap-up

### Task 22: Push branch and open the PR

**Files:** none (git + PR)

- [ ] **Step 1: Push the branch**

```bash
cd /Users/imranahmedani/Desktop/pyquotex/autotrader
git push -u origin claude/ui-modernization-phase-1-foundation
```

Expected: GitHub responds with a PR-creation URL.

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base master --title "feat(autotrader/frontend): UI modernization Phase 1 — foundation" --body "$(cat <<'EOF'
Phase 1 of the UI modernization tracked in
docs/superpowers/specs/2026-05-09-ui-modernization-and-analytics-design.md.

## What ships

- **Theme system.** next-themes with light / dark / system; toggle lives in the sidebar footer. New design tokens for success/warning/info plus a 5-color chart palette.
- **Sidebar navigation.** Replaces the top nav. Two grouped sections (Trade / Configure), collapsible to icon rail with tooltips, state persisted via cookie.
- **App shell.** New AppTopbar with breadcrumb + global pipeline status pill on every dashboard route.
- **Information architecture restructure.**
  - `/dashboard/trades` — extracted from the Pipeline page; full trades table.
  - `/dashboard/decisions` — extracted from the Pipeline page; live parser decisions.
  - `/dashboard/pipeline` — slimmed from 1058 → ~460 lines; controls only.
  - `/dashboard` (Overview) — rebuilt with KPI hero + equity stub + recent activity + status mini-cards.
  - `/dashboard/analytics` — placeholder until Phase 2.
- **shadcn primitives added:** sidebar, sheet, tabs, dropdown-menu, separator, scroll-area, skeleton, tooltip, switch.

## What does NOT ship (later phases)

- Recharts integration, /stats/v2/* backend endpoints, global filter bar, the 10 analytics panels — all Phase 2 / 3.
- Sign-out button in the sidebar footer (intentional minor regression — Phase 3 polish adds a user menu there).
- Playwright tests — Phase 3.

## Verification

- bun run type-check ✅
- bun run build ✅ (route table includes /dashboard/trades, /dashboard/decisions, /dashboard/analytics)
- backend pytest ✅ (no backend changes)
- Visual smoke checklist (in the plan) ✅

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR opens, CI kicks off.

- [ ] **Step 3: Update plan status**

This plan file (`docs/superpowers/plans/2026-05-09-ui-modernization-phase-1-foundation.md`) gets a status header note appended:

```markdown
> **Status (yyyy-mm-dd):** Implemented in PR #N. See PR for follow-up notes.
```

Commit:

```bash
git add docs/superpowers/plans/2026-05-09-ui-modernization-phase-1-foundation.md
git commit -m "docs(autotrader): mark Phase 1 plan as implemented"
git push
```

---

## Open follow-ups (deferred — track for Phase 2 or 3)

- **Sign-out button.** Removed from the layout in Task 10. Add back as a sidebar-footer dropdown menu (avatar + Sign out) in Phase 3 polish.
- **Phase 2 prereq:** install Recharts + react-day-picker + date-fns + @tanstack/react-table when Phase 2 starts.
- **`/stats/overview` removal.** Still used by `OverviewKpiHero` in Phase 1; Phase 3 cleanup removes it once the Phase 2 `/stats/v2/breakdown` endpoint covers the same numbers.
- **Sentry breadcrumbs for navigation.** Defer; not blocking.

---

## Self-review notes (run before handoff)

**Spec coverage check (against `2026-05-09-ui-modernization-and-analytics-design.md`):**

| Spec section | Phase 1 task |
|---|---|
| §4 Stack additions: `next-themes` | Task 1 |
| §4 shadcn primitives | Task 5 |
| §5 Theme tokens (`--success/warning/info/chart-1..5`) | Task 2 |
| §5 Theme switching (next-themes, system default) | Tasks 3, 4 |
| §5 Typography (`tabular-nums` on numerals) | Used in Tasks 16, 18 (KPI + activity) |
| §6 Sidebar (Trade / Configure groups) | Task 7 |
| §6 Topbar (breadcrumb + status pill) | Tasks 8, 9 |
| §9.1 Overview (KPI hero + equity stub + recent + status) | Tasks 16-20 |
| §9.5 Pipeline (controls only, ~250 lines) | Task 15 |
| Spec §11 Phase 1 explicit deliverables | All Phase 1 tasks combined |
| Spec §11 deferred to Phase 2/3 | Explicitly out of scope; placeholder Analytics page in Task 7 |

No gaps.

**Spec items NOT addressed in this plan (intentional):**
- `/stats/v2/*` endpoints — Phase 2.
- Recharts — Phase 2.
- Global filter bar + URL-backed filters — Phase 2.
- 10 analytics panels — Phase 2/3.
- Two new SQLite indices — Phase 2 (only needed once `/stats/v2/*` lands).
- Trades page redesign with TanStack Table sortable columns — Phase 2 (Phase 1 just extracts the existing table to its own route).
- Decisions page filter bar — Phase 2.

These match the design spec's phasing — see spec §11.

**Type / API consistency:**
- All references to `pipeline.status`, `pipeline.trades`, `pipeline.decisions`, `risk.overview`, `stats.overview`, `broker.status`, `broker.balance`, `telegram.status` use the existing API client signatures verified against `frontend/lib/api.ts`.
- `useTradeFeed` is imported from `@/lib/use-trade-feed` consistently.
- `FeedState` type is imported correctly across `trades-table.tsx`, `decisions-feed.tsx`, the trades page, the decisions page, and `OverviewRecentActivity`.
- Component prop types are explicit on every export.

No placeholder strings (`TBD`, `TODO`, "implement later") survived the self-review pass.
