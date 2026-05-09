"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import {
  type RiskOverview,
  type StatsOverview,
  risk,
  stats,
} from "@/lib/api";

/**
 * Phase 1 KPI hero. Reads what already exists:
 *   - /risk/overview for today's budget + caps (P&L, risk-budget-left)
 *   - /stats/overview for today's per-channel summary (rolled up to
 *     dashboard-wide trade/win count)
 *
 * Deltas are intentionally absent in Phase 1 — there's no historical
 * comparison endpoint yet. The card layout already has a slot for
 * them so Phase 2 can drop the values in without touching markup.
 */
export function OverviewKpiHero() {
  const r = useQuery<RiskOverview>({
    queryKey: ["risk", "overview"],
    queryFn: risk.overview,
    refetchInterval: 10_000,
  });
  const s = useQuery<StatsOverview>({
    queryKey: ["stats", "overview"],
    queryFn: stats.overview,
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

  // Trade counts: sum across channels in the today snapshot.
  // When the stats query hasn't resolved yet, totals is null so the
  // KPI cards can render "—" instead of misleading zeros.
  const totals = s.data
    ? s.data.channels.reduce(
        (acc, c) => ({
          total: acc.total + c.total,
          won: acc.won + c.won,
          lost: acc.lost + c.lost,
          rejected: acc.rejected + c.rejected,
          pending: acc.pending + c.pending,
        }),
        { total: 0, won: 0, lost: 0, rejected: 0, pending: 0 },
      )
    : null;
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
              ? "no settled trades yet today"
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
              ? "no trades dispatched yet"
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
