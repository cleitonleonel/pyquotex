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
