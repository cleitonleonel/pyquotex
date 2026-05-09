"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { type RiskOverview, risk } from "@/lib/api";
import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

/**
 * KPI hero. Reads from:
 *   - /risk/overview  for today's budget + caps (P&L, risk-budget-left)
 *   - /stats/v2/breakdown?dim=channel  for the trade-count / win-rate
 *     summary, rolled up across channels and honouring the global
 *     filter bar (range, channels, parsers, assets, direction, statuses).
 *
 * The migration off /stats/overview (Phase 3 Task 2) means the trades
 * + win-rate tiles now respect the filter pills, while the P&L and
 * risk-budget tiles still reflect the *today UTC* risk-engine state —
 * those numbers are about the bot's live risk posture, not the
 * window the operator is browsing, so they intentionally stay
 * filter-independent.
 *
 * Deltas are intentionally absent — there's no historical comparison
 * endpoint yet. The card layout has a slot for them so a future phase
 * can drop the values in without touching markup.
 */
export function OverviewKpiHero() {
  const { filters } = useFilters();

  const r = useQuery<RiskOverview>({
    queryKey: ["risk", "overview"],
    queryFn: risk.overview,
    refetchInterval: 10_000,
  });
  const breakdown = useQuery({
    queryKey: ["overview-hero", "channel-breakdown", filters],
    queryFn: () => statsV2.breakdown(filters, "channel"),
    refetchInterval: 15_000,
  });

  const realised = r.data?.budget.realised_pnl ?? null;
  const cap = r.data?.caps.daily_max_loss ?? 0;
  // Risk budget remaining = cap - realised loss (only the negative side
  // counts; gains don't deplete the loss cap). Cap=0 means "no cap";
  // we render "—" then.
  const riskBudgetLeft =
    cap > 0 && realised !== null
      ? Math.max(0, cap - Math.abs(Math.min(0, realised)))
      : null;

  // Roll the per-channel breakdown rows up to dashboard-wide totals.
  // While the query is pending, totals stays null so the cards render
  // "—" instead of misleading zeros.
  const totals = useMemo(() => {
    if (!breakdown.data) return null;
    return breakdown.data.rows.reduce(
      (acc, row) => ({
        total: acc.total + row.total,
        won: acc.won + row.won,
        lost: acc.lost + row.lost,
        rejected: acc.rejected + row.rejected,
        pending: acc.pending + row.pending,
      }),
      { total: 0, won: 0, lost: 0, rejected: 0, pending: 0 },
    );
  }, [breakdown.data]);

  const settled = totals ? totals.won + totals.lost : 0;
  const winRate = totals && settled > 0 ? totals.won / settled : null;

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <KpiCard
        label="P&L today"
        value={
          realised === null
            ? "—"
            : `${realised >= 0 ? "+" : "−"}$${Math.abs(realised).toFixed(2)}`
        }
        valueTone={
          realised === null
            ? "neutral"
            : realised > 0
              ? "positive"
              : realised < 0
                ? "negative"
                : "neutral"
        }
        subtext="UTC day · resets at 00:00"
      />
      <KpiCard
        label="Win rate"
        value={winRate === null ? "—" : `${Math.round(winRate * 100)}%`}
        valueTone="neutral"
        subtext={
          totals === null
            ? "stats loading…"
            : settled === 0
              ? "no settled trades in window"
              : `${totals.won} won · ${totals.lost} lost`
        }
      />
      <KpiCard
        label="Trades"
        value={totals === null ? "—" : String(totals.total)}
        valueTone="neutral"
        subtext={
          totals === null
            ? "stats loading…"
            : totals.total === 0
              ? "no trades in window"
              : [
                  settled && `${settled} settled`,
                  totals.pending && `${totals.pending} open`,
                  totals.rejected && `${totals.rejected} rejected`,
                ]
                  .filter(Boolean)
                  .join(" · ")
        }
      />
      <KpiCard
        label="Risk budget left"
        value={
          riskBudgetLeft === null ? "—" : `$${riskBudgetLeft.toFixed(2)}`
        }
        valueTone="neutral"
        subtext={
          r.data === undefined
            ? "loading…"
            : cap > 0
              ? `of $${cap.toFixed(0)} daily loss cap`
              : "no cap set"
        }
      />
    </div>
  );
}

function KpiCard({
  label,
  value,
  valueTone,
  subtext,
}: {
  label: string;
  value: string;
  valueTone: "positive" | "negative" | "neutral";
  subtext: string;
}) {
  const toneClass =
    valueTone === "positive"
      ? "text-success"
      : valueTone === "negative"
        ? "text-destructive"
        : "text-foreground";
  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
      </CardHeader>
      <CardContent>
        <div
          className={`text-3xl font-semibold tracking-tight tabular-nums ${toneClass}`}
        >
          {value}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">{subtext}</div>
      </CardContent>
    </Card>
  );
}
