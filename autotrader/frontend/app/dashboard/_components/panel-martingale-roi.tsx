"use client";

/**
 * Martingale ladder ROI — for each parser, what fraction of losing
 * streaks ended in a recovery win (vs void / abort / cap-exhaust)?
 *
 * Operators using martingale need to see whether the recovery legs
 * actually recover or just compound losses. Low recovery + long ladders
 * is the danger combination — that's the parser whose strategy is
 * paying for variance with capital.
 *
 * Closed streak total = sum of histogram counts (each entry is a
 * length -> count of streaks that closed at that length). Recovered
 * count comes straight from the aggregate.
 *
 * Source: /stats/v2/breakdown?dim=parser (streaks aggregate)
 */

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

interface MartingaleRow {
  key: string;
  label: string;
  recoveryRate: number;
  longest: number;
  recovered: number;
  totalClosed: number;
}

function sumHistogram(h: Record<string, number>): number {
  let total = 0;
  for (const v of Object.values(h)) total += v;
  return total;
}

export function PanelMartingaleRoi() {
  const { filters } = useFilters();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["stats-v2", "breakdown", "parser", "martingale", filters],
    queryFn: () => statsV2.breakdown(filters, "parser"),
    staleTime: 30_000,
  });

  const rows: MartingaleRow[] = (data?.rows ?? [])
    .filter((r) => r.streaks && r.streaks.longest_loss > 0)
    .map((r) => ({
      key: String(r.key),
      label: r.label,
      recoveryRate: r.streaks!.recovery_rate,
      longest: r.streaks!.longest_loss,
      recovered: r.streaks!.recovered_count,
      totalClosed: sumHistogram(r.streaks!.histogram),
    }))
    .sort((a, b) => b.recoveryRate - a.recoveryRate);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Martingale ladder ROI</CardTitle>
        <CardDescription>
          For each parser, the fraction of losing streaks that ended in
          an actual win (recovery) versus closing on a void / abort.
          Low recovery paired with a long ladder is the combination
          worth disabling.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {isError && (
          <p className="text-sm text-destructive">
            Couldn&rsquo;t load streak data.
          </p>
        )}
        {!isLoading && !isError && rows.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No closed losing streaks for any parser in this window.
          </p>
        )}
        {rows.map((r) => (
          <div key={r.key} className="space-y-1">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-mono text-xs">{r.label}</span>
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {r.recovered} / {r.totalClosed} recovered ·{" "}
                {(r.recoveryRate * 100).toFixed(0)}% · longest {r.longest}
              </span>
            </div>
            <Progress value={r.recoveryRate * 100} />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
