"use client";

/**
 * Hour-of-day × weekday heatmap. 7 rows × 24 cols of win-rate cells,
 * coloured by win rate intensity. Cells with n<3 render muted.
 *
 * Source: /stats/v2/breakdown?dim=hour_of_week
 */

import { useQuery } from "@tanstack/react-query";
import { Fragment } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
const HOURS = Array.from({ length: 24 }, (_, i) => i);

interface CellData {
  winRate: number | null;
  n: number;
  pnl: number;
}

export function PanelHourHeatmap() {
  const { filters } = useFilters();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["stats-v2", "breakdown", "hour_of_week", filters],
    queryFn: () => statsV2.breakdown(filters, "hour_of_week"),
    staleTime: 30_000,
  });

  // Build a 168-cell grid keyed by weekday*24+hour
  const grid: Record<number, CellData> = {};
  for (const row of data?.rows ?? []) {
    if (typeof row.key !== "number") continue;
    grid[row.key] = {
      winRate: row.win_rate,
      n: row.total,
      pnl: row.realised_pnl,
    };
  }

  // Find the worst window (n>=3 only) for the insight strip below
  const worst = Object.entries(grid)
    .filter(([, c]) => c.n >= 3 && c.winRate !== null)
    .sort(([, a], [, b]) => (a.winRate ?? 1) - (b.winRate ?? 1))[0];
  const worstLabel = worst
    ? (() => {
        const key = Number(worst[0]);
        const wd = WEEKDAYS[Math.floor(key / 24)];
        const hr = key % 24;
        return `${wd} ${String(hr).padStart(2, "0")}:00 · ${Math.round((worst[1].winRate ?? 0) * 100)}% wr (n=${worst[1].n})`;
      })()
    : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Hour-of-day heatmap</CardTitle>
        <CardDescription>
          Win rate by weekday + hour (UTC). Lighter cells = lower win
          rate. Cells with fewer than 3 trades are muted.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {isError && (
          <p className="text-sm text-destructive">Couldn&rsquo;t load heatmap.</p>
        )}
        {!isLoading && !isError && (
          <>
            <div
              className="grid gap-[1px]"
              style={{ gridTemplateColumns: "32px repeat(24, 1fr)" }}
            >
              <div />
              {HOURS.map((h) => (
                <div
                  key={h}
                  className="text-center text-[8px] text-muted-foreground"
                >
                  {h % 3 === 0 ? h : ""}
                </div>
              ))}
              {WEEKDAYS.map((wd, wdIdx) => (
                <Fragment key={wd}>
                  <div className="text-[10px] text-muted-foreground self-center">
                    {wd}
                  </div>
                  {HOURS.map((h) => {
                    const cell = grid[wdIdx * 24 + h];
                    return (
                      <HeatmapCell
                        key={`${wd}-${h}`}
                        cell={cell}
                        weekday={wd}
                        hour={h}
                      />
                    );
                  })}
                </Fragment>
              ))}
            </div>
            {worstLabel && (
              <p className="mt-3 text-xs text-muted-foreground">
                <span className="font-medium text-warning">
                  Worst window:
                </span>{" "}
                {worstLabel}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function HeatmapCell({
  cell,
  weekday,
  hour,
}: {
  cell: CellData | undefined;
  weekday: string;
  hour: number;
}) {
  if (!cell || cell.n === 0) {
    return (
      <div
        className="aspect-square rounded-[2px] bg-muted/30"
        title={`${weekday} ${hour}:00 — no data`}
      />
    );
  }
  if (cell.n < 3) {
    return (
      <div
        className="aspect-square rounded-[2px] bg-muted/60"
        title={`${weekday} ${hour}:00 — n=${cell.n} (too few)`}
      />
    );
  }
  const wr = cell.winRate ?? 0.5;
  const distance = Math.abs(wr - 0.5) * 2;
  const intensity = Math.max(0.25, distance);
  const color = wr >= 0.5 ? "var(--color-chart-1)" : "var(--color-destructive)";
  return (
    <div
      className="aspect-square rounded-[2px]"
      style={{ background: color, opacity: intensity }}
      title={`${weekday} ${hour}:00 — ${Math.round(wr * 100)}% wr · n=${cell.n} · pnl=${cell.pnl.toFixed(2)}`}
    />
  );
}
