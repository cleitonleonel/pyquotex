"use client";

/**
 * Streak distribution — histogram of closed losing-streak lengths for a
 * single parser. Complements the martingale ROI panel by showing the
 * *shape* of the pain (tail length, modal streak length) rather than
 * just the recovery rate.
 *
 * The Select picker lists every parser that has streaks data in the
 * current filter window. We default to the parser with the longest
 * streak (most relevant to the operator's worst-case sizing question)
 * rather than alphabetical, so the panel opens on something interesting.
 *
 * Source: /stats/v2/breakdown?dim=parser (streaks.histogram)
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type BreakdownRow, statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

interface HistogramBar {
  len: number;
  count: number;
}

export function PanelStreakDistribution() {
  const { filters } = useFilters();
  const [parserKey, setParserKey] = useState<string | undefined>(undefined);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["stats-v2", "breakdown", "parser", "streak-dist", filters],
    queryFn: () => statsV2.breakdown(filters, "parser"),
    staleTime: 30_000,
  });

  const parserRows = useMemo<BreakdownRow[]>(
    () => (data?.rows ?? []).filter((r) => r.streaks),
    [data],
  );

  // Default to the parser with the longest closed streak — that's the
  // one whose tail shape is most worth looking at first. If the user
  // explicitly chose another, honour their choice.
  const defaultRow = useMemo(() => {
    if (parserRows.length === 0) return undefined;
    return parserRows.reduce((best, r) =>
      (r.streaks?.longest_loss ?? 0) > (best.streaks?.longest_loss ?? 0)
        ? r
        : best,
    );
  }, [parserRows]);

  const selected =
    parserRows.find((r) => String(r.key) === parserKey) ?? defaultRow;

  const histogram: HistogramBar[] = useMemo(() => {
    if (!selected?.streaks?.histogram) return [];
    return Object.entries(selected.streaks.histogram)
      .map(([len, count]) => ({ len: Number(len), count }))
      .filter((b) => Number.isFinite(b.len))
      .sort((a, b) => a.len - b.len);
  }, [selected]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Streak distribution</CardTitle>
        <CardDescription>
          Closed losing-streak lengths for the selected parser. Tail
          length tells you what your worst-day sizing assumption needs
          to absorb; the modal length is the streak you should expect
          to keep happening.
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
        {!isLoading && !isError && parserRows.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No parser activity in this window.
          </p>
        )}
        {parserRows.length > 0 && (
          <>
            <Select
              value={selected ? String(selected.key) : undefined}
              onValueChange={setParserKey}
            >
              <SelectTrigger className="w-64">
                <SelectValue placeholder="Choose a parser" />
              </SelectTrigger>
              <SelectContent>
                {parserRows.map((r) => (
                  <SelectItem key={String(r.key)} value={String(r.key)}>
                    {r.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="h-64">
              {histogram.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No closed losing streaks for{" "}
                  <span className="font-mono">{selected?.label}</span> in
                  this window.
                </p>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={histogram}
                    margin={{ top: 8, right: 16, bottom: 16, left: 0 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="var(--color-border)"
                    />
                    <XAxis
                      dataKey="len"
                      stroke="var(--color-muted-foreground)"
                      tick={{ fontSize: 10 }}
                      label={{
                        value: "Streak length (consecutive losses)",
                        position: "insideBottom",
                        offset: -8,
                        style: {
                          fontSize: 10,
                          fill: "var(--color-muted-foreground)",
                        },
                      }}
                    />
                    <YAxis
                      allowDecimals={false}
                      stroke="var(--color-muted-foreground)"
                      tick={{ fontSize: 10 }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "var(--color-card)",
                        border: "1px solid var(--color-border)",
                        fontSize: "12px",
                      }}
                      labelFormatter={(label) => `Length ${label}`}
                      formatter={(value) => [String(value), "Streaks"]}
                    />
                    <Bar dataKey="count" fill="var(--color-chart-2)" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
