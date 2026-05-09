"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { TradeAttempt } from "@/lib/api";
import type { FeedState } from "@/lib/use-trade-feed";

export function TradesTable({
  trades,
  loading,
  feedState,
}: {
  trades: TradeAttempt[];
  loading: boolean;
  feedState: FeedState;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              Recent trade attempts
              <FeedIndicator state={feedState} />
            </CardTitle>
            <CardDescription>
              Every signal that reached the executor — successful, blocked,
              or broker-rejected. Streams live over WebSocket; falls back
              to a 15s poll if the feed drops.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {loading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {!loading && trades.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No trade attempts yet. Activate the pipeline above and watch
            this fill up as signals arrive.
          </p>
        )}
        {trades.length > 0 && (
          <div className="max-h-[36rem] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-1.5 font-medium">When</th>
                  <th className="px-2 py-1.5 font-medium">Asset</th>
                  <th className="px-2 py-1.5 font-medium">Dir</th>
                  <th className="px-2 py-1.5 font-medium">Mode</th>
                  <th className="px-2 py-1.5 font-medium">Stake</th>
                  <th className="px-2 py-1.5 font-medium">Status</th>
                  <th className="px-2 py-1.5 font-medium">Profit</th>
                  <th className="px-2 py-1.5 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} className="border-b last:border-0">
                    <td className="px-2 py-1.5 font-mono text-muted-foreground">
                      {new Date(t.received_at).toLocaleTimeString()}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {t.asset_raw && t.asset_raw !== t.asset
                        ? `${t.asset_raw} → ${t.asset}`
                        : t.asset}
                    </td>
                    <td className="px-2 py-1.5">
                      <Badge
                        variant={t.direction === "call" ? "success" : "destructive"}
                      >
                        {t.direction}
                      </Badge>
                    </td>
                    <td className="px-2 py-1.5">
                      <Badge variant="outline">{t.trade_mode}</Badge>
                    </td>
                    <td className="px-2 py-1.5 font-mono">{t.stake}</td>
                    <td className="px-2 py-1.5">
                      <StatusBadge status={t.status} />
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {t.profit !== null ? t.profit.toFixed(2) : "—"}
                    </td>
                    <td className="px-2 py-1.5 text-xs text-muted-foreground">
                      {t.error ?? t.broker_order_id ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function FeedIndicator({ state }: { state: FeedState }) {
  if (state === "live") {
    return (
      <span className="flex items-center gap-1 text-xs text-emerald-400">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
        live
      </span>
    );
  }
  if (state === "connecting") {
    return (
      <span className="text-xs text-muted-foreground">connecting…</span>
    );
  }
  return <span className="text-xs text-muted-foreground">offline</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const v = status.toLowerCase();
  if (v === "won") return <Badge variant="success">won</Badge>;
  if (v === "lost") return <Badge variant="destructive">lost</Badge>;
  if (v === "rejected") return <Badge variant="secondary">rejected</Badge>;
  if (v === "broker_error") return <Badge variant="destructive">error</Badge>;
  if (v === "expired") return <Badge variant="outline">expired</Badge>;
  return <Badge variant="warning">{v}</Badge>;
}
