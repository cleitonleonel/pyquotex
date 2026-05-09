"use client";

/**
 * Asset × direction matrix. Rows = assets, cols = call/put.
 * Cell content: WR% · n. Cells with n<3 render muted.
 *
 * Source: /stats/v2/breakdown?dim=asset (uses direction_split)
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

export function PanelAssetDirectionMatrix() {
  const { filters } = useFilters();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["stats-v2", "breakdown", "asset", filters],
    queryFn: () => statsV2.breakdown(filters, "asset"),
    staleTime: 30_000,
  });

  // Sort assets by total trades desc.
  const rows = (data?.rows ?? []).slice().sort((a, b) => b.total - a.total);

  // Find the largest call-vs-put asymmetry for the insight strip.
  const asymmetry = rows
    .map((r) => {
      const call = r.direction_split?.call;
      const put = r.direction_split?.put;
      if (!call || !put) return null;
      const callN = call.won + call.lost;
      const putN = put.won + put.lost;
      if (callN < 3 || putN < 3) return null;
      const callWr = callN > 0 ? call.won / callN : 0;
      const putWr = putN > 0 ? put.won / putN : 0;
      return {
        asset: String(r.key),
        diff: callWr - putWr,
        callWr,
        putWr,
      };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    .sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff))[0];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Asset × direction matrix</CardTitle>
        <CardDescription>
          Win rate per asset, split by call/put. Lit cells = n≥3.
          The largest asymmetry surfaces a directional edge worth
          knowing about.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {isError && (
          <p className="text-sm text-destructive">Couldn&rsquo;t load matrix.</p>
        )}
        {!isLoading && !isError && rows.length === 0 && (
          <p className="text-sm text-muted-foreground">No trades in this range.</p>
        )}
        {rows.length > 0 && (
          <>
            <div
              className="grid gap-1"
              style={{ gridTemplateColumns: "120px 1fr 1fr" }}
            >
              <div />
              <div className="text-center text-[10px] uppercase text-muted-foreground">
                Call
              </div>
              <div className="text-center text-[10px] uppercase text-muted-foreground">
                Put
              </div>
              {rows.map((r) => (
                <Fragment key={String(r.key)}>
                  <div className="text-xs font-mono self-center">{r.label}</div>
                  <MatrixCell
                    won={r.direction_split?.call.won ?? 0}
                    lost={r.direction_split?.call.lost ?? 0}
                  />
                  <MatrixCell
                    won={r.direction_split?.put.won ?? 0}
                    lost={r.direction_split?.put.lost ?? 0}
                  />
                </Fragment>
              ))}
            </div>
            {asymmetry && (
              <p className="mt-3 text-xs text-muted-foreground">
                <span className="font-medium text-warning">Edge:</span>{" "}
                {asymmetry.asset} — calls {Math.round(asymmetry.callWr * 100)}%
                vs puts {Math.round(asymmetry.putWr * 100)}%
                ({asymmetry.diff > 0 ? "+" : ""}
                {Math.round(asymmetry.diff * 100)}pp gap)
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function MatrixCell({ won, lost }: { won: number; lost: number }) {
  const n = won + lost;
  if (n < 3) {
    return (
      <div className="rounded bg-muted/30 px-2 py-1.5 text-center text-xs text-muted-foreground">
        — · n{n}
      </div>
    );
  }
  const wr = won / n;
  const intensity = Math.max(0.3, Math.abs(wr - 0.5) * 2);
  const color = wr >= 0.5 ? "var(--color-chart-1)" : "var(--color-destructive)";
  return (
    <div
      className="rounded px-2 py-1.5 text-center text-xs"
      style={{ background: color, opacity: intensity, color: "white" }}
    >
      {Math.round(wr * 100)}% · n{n}
    </div>
  );
}
