"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  type PipelineStatus,
  type TradeAttempt,
  pipeline,
} from "@/lib/api";

export default function PipelinePage() {
  const status = useQuery<PipelineStatus>({
    queryKey: ["pipeline", "status"],
    queryFn: pipeline.status,
    refetchInterval: 5_000,
  });

  const trades = useQuery<TradeAttempt[]>({
    queryKey: ["pipeline", "trades"],
    queryFn: () => pipeline.trades(100),
    refetchInterval: 5_000,
  });

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Pipeline</h2>
        <p className="text-sm text-muted-foreground">
          The end-to-end executor. When active, every message in a watched
          chat is run through its priority-ordered parsers, the first
          matching signal goes through the risk gate, and approved
          signals fire as live or scheduled trades.
        </p>
      </section>

      {status.isLoading && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Loading pipeline status…</p>
          </CardContent>
        </Card>
      )}

      {status.error && (
        <Card className="border-destructive/40">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Couldn&rsquo;t load pipeline status:{" "}
              {status.error instanceof ApiError
                ? status.error.message
                : String(status.error)}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              If you just signed in this can be a stale auth token —
              try a hard refresh.
            </p>
          </CardContent>
        </Card>
      )}

      {status.data && <StatusCard status={status.data} />}

      {status.data && (
        <TradesTable trades={trades.data ?? []} loading={trades.isLoading} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status + master switch
// ---------------------------------------------------------------------------

function StatusCard({ status }: { status: PipelineStatus }) {
  const qc = useQueryClient();

  const activate = useMutation({
    mutationFn: (active: boolean) => pipeline.activate(active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipeline"] }),
  });

  const killSwitch = useMutation({
    mutationFn: (active: boolean) => pipeline.killSwitch(active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipeline"] }),
  });

  const ready =
    status.broker_connected &&
    status.telegram_logged_in &&
    status.enabled_parser_count > 0;

  return (
    <Card className={status.active ? "border-emerald-500/40" : undefined}>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              Master switch
              {status.active ? (
                <Badge variant="success">active</Badge>
              ) : (
                <Badge variant="secondary">stopped</Badge>
              )}
              {status.kill_switch_engaged && (
                <Badge variant="destructive">kill switch</Badge>
              )}
            </CardTitle>
            <CardDescription>
              Trades fire only when all gates align: master switch on,
              kill switch off, parser config enabled, and (for REAL
              accounts) the env flag enabled.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {status.active ? (
              <Button
                variant="outline"
                onClick={() => activate.mutate(false)}
                disabled={activate.isPending}
              >
                {activate.isPending ? "Stopping…" : "Deactivate"}
              </Button>
            ) : (
              <Button
                onClick={() => activate.mutate(true)}
                disabled={activate.isPending || !ready}
                title={
                  ready
                    ? undefined
                    : "Connect broker + login to Telegram + add at least one enabled parser"
                }
              >
                {activate.isPending ? "Starting…" : "Activate"}
              </Button>
            )}
            <Button
              variant={status.kill_switch_engaged ? "default" : "destructive"}
              onClick={() => killSwitch.mutate(!status.kill_switch_engaged)}
              disabled={killSwitch.isPending}
            >
              {status.kill_switch_engaged ? "Release kill switch" : "Kill switch"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {activate.isError && (
          <p className="mb-3 text-sm text-destructive">
            {activate.error instanceof ApiError
              ? activate.error.message
              : String(activate.error)}
          </p>
        )}
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-3 lg:grid-cols-4">
          <Row
            label="Broker"
            value={
              status.broker_connected ? (
                <Badge variant="success">connected</Badge>
              ) : (
                <Badge variant="secondary">disconnected</Badge>
              )
            }
          />
          <Row
            label="Telegram"
            value={
              status.telegram_logged_in ? (
                <Badge variant="success">logged in</Badge>
              ) : (
                <Badge variant="secondary">logged out</Badge>
              )
            }
          />
          <Row
            label="REAL trading (env)"
            value={
              status.live_trading_enabled_env ? (
                <Badge variant="warning">enabled</Badge>
              ) : (
                <Badge variant="outline">disabled</Badge>
              )
            }
          />
          <Row label="Watched chats" value={String(status.watched_chat_count)} />
          <Row
            label="Enabled parsers"
            value={String(status.enabled_parser_count)}
          />
          <Row
            label="Cached parsers"
            value={String(status.cached_parser_count)}
          />
        </dl>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Trades feed
// ---------------------------------------------------------------------------

function TradesTable({
  trades,
  loading,
}: {
  trades: TradeAttempt[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent trade attempts</CardTitle>
        <CardDescription>
          Every signal that reached the executor — successful, blocked,
          or broker-rejected. Refreshes every 5s.
        </CardDescription>
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
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
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

function StatusBadge({ status }: { status: string }) {
  const v = status.toLowerCase();
  if (v === "won") return <Badge variant="success">won</Badge>;
  if (v === "lost") return <Badge variant="destructive">lost</Badge>;
  if (v === "rejected") return <Badge variant="secondary">rejected</Badge>;
  if (v === "broker_error") return <Badge variant="destructive">error</Badge>;
  if (v === "expired") return <Badge variant="outline">expired</Badge>;
  return <Badge variant="warning">{v}</Badge>;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
