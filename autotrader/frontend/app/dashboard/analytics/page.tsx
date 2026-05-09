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
