"use client";

/**
 * Risk-cap utilisation — how close are we to halting trading?
 *
 * The daily loss cap in the autotrader is a *global* setting
 * (GlobalSettings.daily_max_loss), not per-parser or per-channel — when
 * cumulative realised PnL crosses that bound, the risk gate stops
 * placing new trades for the rest of the UTC day. The plan originally
 * sketched a per-channel cap, but that doesn't exist in the data model;
 * we instead show:
 *   1. A headline "X% of cap used" tile reading from /risk/overview
 *      (today's actual budget vs the global cap), and
 *   2. A per-channel drawdown bar chart for the current filter window
 *      so the operator can see which channels are *contributing* the
 *      drawdown that's eating the cap.
 *
 * The two views answer different questions: the tile is "how close to
 * halting are we right now?" and the chart is "who's costing us?".
 *
 * Sources: GET /risk/overview · /stats/v2/breakdown?dim=channel
 */

import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
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
import { Progress } from "@/components/ui/progress";
import { risk } from "@/lib/api";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

interface ChannelDrawdown {
  name: string;
  drawdown: number;
}

export function PanelRiskCapUtilisation() {
  const { filters } = useFilters();

  const { data: riskData, isLoading: riskLoading } = useQuery({
    queryKey: ["risk-overview"],
    queryFn: () => risk.overview(),
    staleTime: 15_000,
  });

  const { data: breakdown, isLoading: bLoading } = useQuery({
    queryKey: ["stats-v2", "breakdown", "channel", "risk-cap", filters],
    queryFn: () => statsV2.breakdown(filters, "channel"),
    staleTime: 30_000,
  });

  const cap = riskData?.caps.daily_max_loss ?? 0;
  // Cap is on losses → realised_pnl < 0 is the consumption side.
  const todayUsed = Math.max(0, -(riskData?.budget.realised_pnl ?? 0));
  const todayPct = cap > 0 ? Math.min(1, todayUsed / cap) : 0;
  const capConfigured = cap > 0;

  const drawdownRows: ChannelDrawdown[] = (breakdown?.rows ?? [])
    // Negative pnl = drawdown contribution. Positive (winning) channels
    // are not eating the cap, so they don't belong in this chart.
    .map((r) => ({ name: r.label, drawdown: Math.max(0, -r.realised_pnl) }))
    .filter((r) => r.drawdown > 0)
    .sort((a, b) => b.drawdown - a.drawdown);

  const barColor = (d: number) => {
    if (cap <= 0) return "var(--color-chart-1)";
    const ratio = d / cap;
    if (ratio >= 0.9) return "var(--color-destructive)";
    if (ratio >= 0.6) return "var(--color-warning)";
    return "var(--color-chart-1)";
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Risk-cap utilisation</CardTitle>
        <CardDescription>
          How close are we to today&rsquo;s daily loss cap? The global
          cap is set in <code>/risk/caps</code>; the bars below show
          which channels in the current filter window are contributing
          the drawdown that&rsquo;s eating it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          {riskLoading ? (
            <p className="text-sm text-muted-foreground">
              Loading cap status…
            </p>
          ) : !capConfigured ? (
            <p className="text-sm text-muted-foreground">
              No daily loss cap is configured. Set one in{" "}
              <code>PUT /risk/caps</code> to enable utilisation tracking.
            </p>
          ) : (
            <>
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-muted-foreground">
                  Today&rsquo;s drawdown vs cap
                </span>
                <span
                  className={`font-mono text-sm tabular-nums ${
                    todayPct >= 0.9
                      ? "text-destructive"
                      : todayPct >= 0.6
                        ? "text-warning"
                        : ""
                  }`}
                >
                  ${todayUsed.toFixed(2)} / ${cap.toFixed(2)} (
                  {(todayPct * 100).toFixed(0)}%)
                </span>
              </div>
              <Progress value={todayPct * 100} />
            </>
          )}
        </div>

        <div className="h-64">
          {bLoading && (
            <p className="text-sm text-muted-foreground">
              Loading per-channel drawdown…
            </p>
          )}
          {!bLoading && drawdownRows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No losing channels in this range.
            </p>
          )}
          {drawdownRows.length > 0 && (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={drawdownRows}
                margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--color-border)"
                />
                <XAxis
                  dataKey="name"
                  stroke="var(--color-muted-foreground)"
                  tick={{ fontSize: 10 }}
                />
                <YAxis
                  stroke="var(--color-muted-foreground)"
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => `$${v}`}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-card)",
                    border: "1px solid var(--color-border)",
                    fontSize: "12px",
                  }}
                  formatter={(value) => {
                    const n = typeof value === "number" ? value : 0;
                    if (cap > 0) {
                      const pct = ((n / cap) * 100).toFixed(0);
                      return [`$${n.toFixed(2)} (${pct}% of cap)`, "Drawdown"];
                    }
                    return [`$${n.toFixed(2)}`, "Drawdown"];
                  }}
                />
                <Bar dataKey="drawdown">
                  {drawdownRows.map((r, i) => (
                    <Cell key={i} fill={barColor(r.drawdown)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
