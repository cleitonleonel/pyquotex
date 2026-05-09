"use client";

/**
 * Equity curve — cumulative P&L over time.
 *
 * Source: /stats/v2/timeseries?metric=equity. Renders an area chart
 * with a gradient fill using --chart-1 from the theme tokens.
 *
 * `compact` prop omits the panel chrome (header, range buttons) and
 * uses a smaller height — for embedding inside the Overview page.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { type RangeLabel, statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

interface PanelEquityCurveProps {
  compact?: boolean;
}

const RANGES: { label: string; value: RangeLabel }[] = [
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "30d", value: "30d" },
  { label: "All", value: "all" },
];

export function PanelEquityCurve({ compact = false }: PanelEquityCurveProps) {
  // On the Overview page we want our own range toggle (independent
  // of the global filter bar, which only applies to /analytics).
  // On the analytics page we read from the global filters.
  const { filters: globalFilters } = useFilters();
  const [overviewRange, setOverviewRange] = useState<RangeLabel>("7d");
  const range = compact ? overviewRange : globalFilters.range;
  const filters = compact
    ? { range: overviewRange, from: undefined, to: undefined }
    : globalFilters;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["stats-v2", "timeseries", "equity", filters],
    queryFn: () => statsV2.timeseries(filters, "equity"),
    staleTime: 30_000,
  });

  const chartData = (data?.points ?? []).map((p) => ({
    t: new Date(p.t).getTime(),
    value: p.value ?? 0,
  }));
  const last = chartData[chartData.length - 1]?.value ?? 0;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Equity curve</CardTitle>
            <CardDescription>
              Cumulative P&amp;L · {range}{" "}
              {chartData.length > 0 && (
                <span
                  className={
                    last >= 0 ? "text-success" : "text-destructive"
                  }
                >
                  · {last >= 0 ? "+" : "−"}${Math.abs(last).toFixed(2)}
                </span>
              )}
            </CardDescription>
          </div>
          {compact && (
            <div className="flex gap-1">
              {RANGES.map((r) => (
                <Button
                  key={r.value}
                  variant={overviewRange === r.value ? "secondary" : "ghost"}
                  size="sm"
                  onClick={() => setOverviewRange(r.value)}
                >
                  {r.label}
                </Button>
              ))}
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {isError && (
          <p className="text-sm text-destructive">Couldn&rsquo;t load equity curve.</p>
        )}
        {!isLoading && !isError && chartData.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No settled trades in this range yet.
          </p>
        )}
        {chartData.length > 0 && (
          <div style={{ height: compact ? 160 : 240 }}>
            <ResponsiveContainer>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                    <stop
                      offset="0%"
                      stopColor="var(--color-chart-1)"
                      stopOpacity={0.4}
                    />
                    <stop
                      offset="100%"
                      stopColor="var(--color-chart-1)"
                      stopOpacity={0}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--color-border)"
                  vertical={false}
                />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(v: number) => new Date(v).toLocaleDateString()}
                  stroke="var(--color-muted-foreground)"
                  fontSize={10}
                />
                <YAxis
                  stroke="var(--color-muted-foreground)"
                  fontSize={10}
                  width={50}
                  tickFormatter={(v: number) => `$${v.toFixed(0)}`}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-card)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                  labelFormatter={(v) =>
                    typeof v === "number" ? new Date(v).toLocaleString() : String(v)
                  }
                  formatter={(v) => {
                    const n = typeof v === "number" ? v : 0;
                    return [
                      `${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(2)}`,
                      "Cumulative P&L",
                    ];
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="var(--color-chart-1)"
                  strokeWidth={2}
                  fill="url(#equityFill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
