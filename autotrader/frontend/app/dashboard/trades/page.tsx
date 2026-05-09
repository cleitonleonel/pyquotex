"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { ApiError, type TradeAttempt, pipeline } from "@/lib/api";
import { useTradeFeed } from "@/lib/use-trade-feed";
import { TradesTable } from "../_components/trades-table";

export default function TradesPage() {
  const feedState = useTradeFeed();

  const trades = useQuery<TradeAttempt[]>({
    queryKey: ["pipeline", "trades"],
    queryFn: () => pipeline.trades(100),
    refetchInterval: 15_000,
  });

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Trades</h2>
        <p className="text-sm text-muted-foreground">
          Every signal that reached the executor — won, lost, blocked by
          the risk gate, broker-rejected, or expired. Live-streams over
          WebSocket; falls back to a 15s poll if the feed drops. Phase 2
          adds filters (date range, channel, parser, asset, direction,
          status).
        </p>
      </section>

      {trades.error && (
        <Card className="border-destructive/40">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Couldn&rsquo;t load trades:{" "}
              {trades.error instanceof ApiError
                ? trades.error.message
                : String(trades.error)}
            </p>
          </CardContent>
        </Card>
      )}

      <TradesTable
        trades={trades.data ?? []}
        loading={trades.isLoading}
        feedState={feedState}
      />
    </div>
  );
}
