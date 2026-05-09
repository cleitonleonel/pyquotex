"use client";

import { Suspense } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FilterBar } from "../_components/filter-bar";
import { PanelAssetDirectionMatrix } from "../_components/panel-asset-direction-matrix";
import { PanelChannelLeaderboard } from "../_components/panel-channel-leaderboard";
import { PanelHourHeatmap } from "../_components/panel-hour-heatmap";
import { PanelLatencyDrift } from "../_components/panel-latency-drift";
import { PanelMartingaleRoi } from "../_components/panel-martingale-roi";
import { PanelParserComparison } from "../_components/panel-parser-comparison";
import { PanelRiskCapUtilisation } from "../_components/panel-risk-cap-utilisation";
import { PanelSignalFunnel } from "../_components/panel-signal-funnel";
import { PanelStreakDistribution } from "../_components/panel-streak-distribution";

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
          <TabsTrigger value="parsers">Parsers</TabsTrigger>
          <TabsTrigger value="execution">Execution</TabsTrigger>
          <TabsTrigger value="risk">Risk</TabsTrigger>
        </TabsList>

        <TabsContent value="performance" className="space-y-4">
          <PerformanceTab />
        </TabsContent>

        <TabsContent value="parsers" className="space-y-4">
          <ParsersTab />
        </TabsContent>

        <TabsContent value="execution" className="space-y-4">
          <ExecutionTab />
        </TabsContent>

        <TabsContent value="risk" className="space-y-4">
          <RiskTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function PerformanceTab() {
  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      <PanelChannelLeaderboard />
      <PanelAssetDirectionMatrix />
    </div>
  );
}

function ParsersTab() {
  return (
    <div className="space-y-4">
      <PanelParserComparison />
    </div>
  );
}

function ExecutionTab() {
  return (
    <div className="space-y-4">
      <PanelHourHeatmap />
      <PanelLatencyDrift />
      <PanelSignalFunnel />
    </div>
  );
}

function RiskTab() {
  return (
    <div className="space-y-4">
      <PanelRiskCapUtilisation />
      <PanelMartingaleRoi />
      <PanelStreakDistribution />
    </div>
  );
}
