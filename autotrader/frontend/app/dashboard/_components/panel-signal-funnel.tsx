"use client";

/**
 * Signal funnel — five stages from message received through trade
 * settled. Horizontal bars sized to each stage's count relative to
 * the largest stage.
 *
 * Source: /stats/v2/funnel
 *
 * Note: messages_received and matched are zero in Phase 2 — the
 * pipeline-decisions ring isn't threaded through the endpoint yet.
 * The empty state surfaces this caveat.
 */

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

export function PanelSignalFunnel() {
  const { filters } = useFilters();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["stats-v2", "funnel", filters],
    queryFn: () => statsV2.funnel(filters),
    staleTime: 30_000,
  });

  const stages = data?.stages ?? [];
  const maxCount = stages.reduce((m, s) => Math.max(m, s.count), 0);
  const dropReasons = data?.drop_reasons.matched_to_passed_risk ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Signal funnel</CardTitle>
        <CardDescription>
          Where signals leak out. messages_received and matched come
          from the in-memory pipeline-decisions ring (zeroed in Phase
          2 — wired in Phase 3); the rest derive from trade attempts.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {isError && (
          <p className="text-sm text-destructive">Couldn&rsquo;t load funnel.</p>
        )}
        {!isLoading && !isError && (
          <>
            <div className="space-y-1.5">
              {stages.map((s) => {
                const widthPct =
                  maxCount > 0 ? (s.count / maxCount) * 100 : 0;
                return (
                  <div
                    key={s.key}
                    className="grid items-center gap-2"
                    style={{ gridTemplateColumns: "140px 1fr 60px" }}
                  >
                    <span className="text-xs text-muted-foreground">
                      {s.label}
                    </span>
                    <div className="h-3 rounded bg-muted/30">
                      <div
                        className="h-3 rounded"
                        style={{
                          width: `${widthPct}%`,
                          background:
                            "linear-gradient(90deg, var(--color-chart-1), var(--color-chart-2))",
                        }}
                      />
                    </div>
                    <span className="text-right text-xs font-mono tabular-nums">
                      {s.count}
                    </span>
                  </div>
                );
              })}
            </div>
            {dropReasons.length > 0 && (
              <div className="mt-4">
                <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                  Why signals were rejected
                </p>
                <div className="space-y-0.5">
                  {dropReasons.map((d) => (
                    <div
                      key={d.reason}
                      className="flex items-center justify-between text-xs"
                    >
                      <span className="font-mono text-muted-foreground">
                        {d.reason}
                      </span>
                      <span className="font-mono tabular-nums">{d.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
