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
