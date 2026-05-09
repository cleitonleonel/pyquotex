"use client";

import { Suspense } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FilterBar } from "../_components/filter-bar";

/**
 * Analytics page — 3 tabs (Performance / Execution / Risk).
 *
 * Suspense wrapper is required by Next.js 15 because useSearchParams
 * (used inside FilterBar via useFilters) opts the page out of static
 * rendering unless wrapped.
 */
export default function AnalyticsPage() {
  return (
    <Suspense fallback={<AnalyticsLoading />}>
      <AnalyticsBody />
    </Suspense>
  );
}

function AnalyticsLoading() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Analytics</h2>
        <p className="text-sm text-muted-foreground">Loading…</p>
      </section>
    </div>
  );
}

function AnalyticsBody() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Analytics</h2>
        <p className="text-sm text-muted-foreground">
          Performance, execution health, and risk-cap utilisation across
          your watched channels. Use the filter bar to scope by date
          range, channel, parser, status, or direction.
        </p>
      </section>

      <FilterBar />

      <Tabs defaultValue="performance" className="space-y-4">
        <TabsList>
          <TabsTrigger value="performance">Performance</TabsTrigger>
          <TabsTrigger value="execution">Execution</TabsTrigger>
          <TabsTrigger value="risk">Risk</TabsTrigger>
        </TabsList>

        <TabsContent value="performance" className="space-y-4">
          {/* Phase 2 panels: leaderboard + matrix. Phase 3 adds parser comparison. */}
          <PerformanceTab />
        </TabsContent>

        <TabsContent value="execution" className="space-y-4">
          {/* Phase 2 panels: heatmap + funnel. Phase 3 adds latency drift. */}
          <ExecutionTab />
        </TabsContent>

        <TabsContent value="risk" className="space-y-4">
          {/* All risk panels are Phase 3. */}
          <RiskTabPlaceholder />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function PerformanceTab() {
  // PanelChannelLeaderboard + PanelAssetDirectionMatrix — both implemented in Bundle B.5.
  // Until those land, render a one-line note so the tab isn't empty.
  return (
    <p className="text-sm text-muted-foreground">
      Channel leaderboard + asset×direction matrix render here once
      Bundle B.5 lands.
    </p>
  );
}

function ExecutionTab() {
  // PanelHourHeatmap + PanelSignalFunnel — Bundle B.5.
  return (
    <p className="text-sm text-muted-foreground">
      Hour-of-day heatmap + signal funnel render here once Bundle B.5 lands.
    </p>
  );
}

function RiskTabPlaceholder() {
  return (
    <p className="text-sm text-muted-foreground">
      Risk-cap utilisation, martingale ladder ROI, and streak
      distribution land in Phase 3.
    </p>
  );
}
