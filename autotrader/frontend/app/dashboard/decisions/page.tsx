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
