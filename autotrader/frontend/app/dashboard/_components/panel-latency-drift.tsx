"use client";

/**
 * Latency drift — broker round-trip p50 + p99 over the selected window.
 *
 * Why a panel: when the WS feed degrades, expirations drift past the
 * intended entry and PnL silently degrades long before any alert fires.
 * Surfacing p50 / p99 over time turns "the bot feels slow" into a
 * measurable signal an operator can act on.
 *
 * Source: /stats/v2/timeseries with metric=latency_p50 and =latency_p99.
 * The endpoint returns one metric per request, so we run two queries in
 * parallel and zip them by bucket timestamp. Buckets that exist in only
 * one series (rare — the backend buckets identically per range) fall
 * back to null on the missing axis so Recharts skips that segment
 * cleanly instead of drawing a dive to zero.
 */

import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
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
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

interface LatencyPoint {
  t: string;
  p50: number | null;
  p99: number | null;
}

function formatTick(iso: string): string {
  // Compact "HH:MM" or "MM-DD HH" — Recharts XAxis ticks need to fit.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

export function PanelLatencyDrift() {
  const { filters } = useFilters();

  const p50Query = useQuery({
    queryKey: ["stats-v2", "timeseries", "latency_p50", filters],
    queryFn: () => statsV2.timeseries(filters, "latency_p50"),
    staleTime: 30_000,
  });

  const p99Query = useQuery({
    queryKey: ["stats-v2", "timeseries", "latency_p99", filters],
    queryFn: () => statsV2.timeseries(filters, "latency_p99"),
    staleTime: 30_000,
  });

  const isLoading = p50Query.isLoading || p99Query.isLoading;
  const isError = p50Query.isError || p99Query.isError;

  // Zip by timestamp. Both series come from the same range/bucket
  // resolver server-side, so in practice they line up 1:1, but we still
  // merge defensively in case one side has more buckets (e.g., one
  // metric was empty before a certain time).
  const merged: LatencyPoint[] = (() => {
    const byT = new Map<string, LatencyPoint>();
    for (const pt of p50Query.data?.points ?? []) {
      byT.set(pt.t, { t: pt.t, p50: pt.value, p99: null });
    }
    for (const pt of p99Query.data?.points ?? []) {
      const existing = byT.get(pt.t);
      if (existing) existing.p99 = pt.value;
      else byT.set(pt.t, { t: pt.t, p50: null, p99: pt.value });
    }
    return Array.from(byT.values()).sort((a, b) =>
      a.t < b.t ? -1 : a.t > b.t ? 1 : 0,
    );
  })();

  const hasAnyValue = merged.some((p) => p.p50 !== null || p.p99 !== null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Latency drift</CardTitle>
        <CardDescription>
          Broker round-trip p50 vs p99 over the selected window.
          Sustained p99 climb usually means the WS feed is degrading
          before the bot or the operator notices.
        </CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {isError && (
          <p className="text-sm text-destructive">
            Couldn&rsquo;t load latency series.
          </p>
        )}
        {!isLoading && !isError && !hasAnyValue && (
          <p className="text-sm text-muted-foreground">
            No latency samples in this range.
          </p>
        )}
        {!isLoading && !isError && hasAnyValue && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={merged}
              margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--color-border)"
              />
              <XAxis
                dataKey="t"
                tickFormatter={formatTick}
                stroke="var(--color-muted-foreground)"
                tick={{ fontSize: 10 }}
              />
              <YAxis
                stroke="var(--color-muted-foreground)"
                tick={{ fontSize: 10 }}
                tickFormatter={(v) => `${v}ms`}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--color-card)",
                  border: "1px solid var(--color-border)",
                  fontSize: "12px",
                }}
                labelFormatter={(label) => formatTick(String(label))}
                formatter={(value, name) => {
                  if (value === null || value === undefined)
                    return ["—", String(name)];
                  const n = typeof value === "number" ? value : Number(value);
                  return [
                    Number.isFinite(n) ? `${n.toFixed(0)} ms` : "—",
                    String(name),
                  ];
                }}
              />
              <Line
                type="monotone"
                dataKey="p50"
                name="p50"
                stroke="var(--color-chart-1)"
                strokeWidth={2}
                dot={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="p99"
                name="p99"
                stroke="var(--color-chart-3)"
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
