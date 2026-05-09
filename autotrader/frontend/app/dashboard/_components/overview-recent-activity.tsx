"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { type TradeAttempt, pipeline } from "@/lib/api";
import { useTradeFeed } from "@/lib/use-trade-feed";
import { StatusBadge } from "./trades-table";

/**
 * Compact 8-row recent-trades table for the Overview page. Re-uses
 * the StatusBadge from trades-table.tsx so styling stays consistent
 * with the full Trades page. Live-updated by the existing WebSocket
 * hook — same query key as the full table, so they share data.
 */
export function OverviewRecentActivity() {
  // Mounting useTradeFeed here keeps the WebSocket alive whenever
  // the user is on Overview, so trade.upserted frames refresh the
  // shared ["pipeline", "trades"] cache live. NOTE: the hook is NOT
  // a singleton — each mount opens its own connection. During an
  // Overview→Trades navigation transition there's a brief window
  // with two open connections; both fire setQueryData on the same
  // cache (last-write wins, same data) so it's safe but redundant.
  // Phase 2 should lift this into a context provider mounted once
  // by the dashboard layout.
  useTradeFeed();

  const trades = useQuery<TradeAttempt[]>({
    queryKey: ["pipeline", "trades"],
    queryFn: () => pipeline.trades(50),
    refetchInterval: 30_000,
  });

  const recent = (trades.data ?? []).slice(0, 8);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>Last 8 trade attempts.</CardDescription>
          </div>
          <Link
            href="/dashboard/trades"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            View all →
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {trades.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {!trades.isLoading && recent.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No trades yet today. Activate the pipeline to see signals
            here as they arrive.
          </p>
        )}
        {recent.length > 0 && (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="px-2 py-1.5 font-medium">When</th>
                <th className="px-2 py-1.5 font-medium">Asset</th>
                <th className="px-2 py-1.5 font-medium">Dir</th>
                <th className="px-2 py-1.5 font-medium">Stake</th>
                <th className="px-2 py-1.5 font-medium">Status</th>
                <th className="px-2 py-1.5 font-medium text-right">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((t) => (
                <tr key={t.id} className="border-b last:border-0">
                  <td className="px-2 py-1.5 font-mono text-muted-foreground">
                    {new Date(t.received_at).toLocaleTimeString()}
                  </td>
                  <td className="px-2 py-1.5 font-mono">{t.asset}</td>
                  <td className="px-2 py-1.5">
                    <Badge
                      variant={t.direction === "call" ? "success" : "destructive"}
                    >
                      {t.direction}
                    </Badge>
                  </td>
                  <td className="px-2 py-1.5 font-mono">{t.stake}</td>
                  <td className="px-2 py-1.5">
                    <StatusBadge status={t.status} />
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right font-mono tabular-nums ${
                      t.profit === null
                        ? "text-muted-foreground"
                        : t.profit > 0
                          ? "text-success"
                          : t.profit < 0
                            ? "text-destructive"
                            : ""
                    }`}
                  >
                    {t.profit === null
                      ? "—"
                      : `${t.profit >= 0 ? "+" : "−"}${Math.abs(t.profit).toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
