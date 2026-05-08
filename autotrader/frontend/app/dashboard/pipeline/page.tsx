"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  type PipelineStatus,
  type RiskCaps,
  type RiskOverview,
  type StatsOverview,
  type TradeAttempt,
  pipeline,
  risk,
  stats,
} from "@/lib/api";
import { type FeedState, useTradeFeed } from "@/lib/use-trade-feed";

export default function PipelinePage() {
  const feedState = useTradeFeed();

  const status = useQuery<PipelineStatus>({
    queryKey: ["pipeline", "status"],
    queryFn: pipeline.status,
    refetchInterval: 5_000,
  });

  const trades = useQuery<TradeAttempt[]>({
    queryKey: ["pipeline", "trades"],
    queryFn: () => pipeline.trades(100),
    // Slower poll because the WebSocket now drives most updates;
    // this is a self-healing fall-back when the socket drops.
    refetchInterval: 15_000,
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

      {status.data && <RiskOverviewCard />}

      {status.data && <StatsOverviewCard />}

      {status.data && (
        <TradesTable
          trades={trades.data ?? []}
          loading={trades.isLoading}
          feedState={feedState}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stats — per-channel breakdown + latency percentiles (Phase 6)
// ---------------------------------------------------------------------------

function StatsOverviewCard() {
  const overview = useQuery<StatsOverview>({
    queryKey: ["stats", "overview"],
    queryFn: stats.overview,
    refetchInterval: 15_000,
  });

  if (overview.isLoading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-muted-foreground">Loading stats…</p>
        </CardContent>
      </Card>
    );
  }
  if (!overview.data) return null;

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <LatencyCard data={overview.data.latency} />
      <ChannelStatsCard data={overview.data.channels} />
    </div>
  );
}

function LatencyCard({ data }: { data: StatsOverview["latency"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Latency (today, UTC)</CardTitle>
        <CardDescription>
          <span className="font-mono">signal → place</span> covers parser
          dispatch through broker accept;{" "}
          <span className="font-mono">place → settle</span> measures the broker
          round-trip from accept to win/loss.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2">
          <LatencyTile label="signal → place" tile={data.signal_to_place} />
          <LatencyTile label="place → settle" tile={data.place_to_settle} />
        </div>
      </CardContent>
    </Card>
  );
}

function LatencyTile({
  label,
  tile,
}: {
  label: string;
  tile: StatsOverview["latency"]["signal_to_place"];
}) {
  return (
    <div className="rounded-md border p-3 text-xs">
      <div className="text-muted-foreground">{label}</div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <Stat label="p50" value={fmtMs(tile.p50_ms)} />
        <Stat label="p99" value={fmtMs(tile.p99_ms)} />
        <Stat label="n" value={String(tile.count)} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="font-mono text-sm">{value}</div>
    </div>
  );
}

function fmtMs(value: number | null): string {
  if (value === null) return "—";
  if (value < 1) return `${value.toFixed(2)}ms`;
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(2)}s`;
}

function ChannelStatsCard({ data }: { data: StatsOverview["channels"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Per-channel performance (today, UTC)</CardTitle>
        <CardDescription>
          Win rate counts only settled trades. Committed stake covers
          pending + won + lost.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {data.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No trades from any channel yet today.
          </p>
        )}
        {data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-1.5 font-medium">Channel</th>
                  <th className="px-2 py-1.5 font-medium">Total</th>
                  <th className="px-2 py-1.5 font-medium">W / L</th>
                  <th className="px-2 py-1.5 font-medium">Win rate</th>
                  <th className="px-2 py-1.5 font-medium">P&amp;L</th>
                  <th className="px-2 py-1.5 font-medium">Committed</th>
                  <th className="px-2 py-1.5 font-medium">Other</th>
                </tr>
              </thead>
              <tbody>
                {data.map((c) => (
                  <tr key={c.chat_id} className="border-b last:border-0">
                    <td className="px-2 py-1.5 font-mono">{c.title}</td>
                    <td className="px-2 py-1.5 font-mono">{c.total}</td>
                    <td className="px-2 py-1.5 font-mono">
                      <span className="text-emerald-400">{c.won}</span>
                      {" / "}
                      <span className="text-destructive">{c.lost}</span>
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {c.win_rate === null
                        ? "—"
                        : `${(c.win_rate * 100).toFixed(0)}%`}
                    </td>
                    <td
                      className={
                        c.realised_pnl < 0
                          ? "px-2 py-1.5 font-mono text-destructive"
                          : "px-2 py-1.5 font-mono text-emerald-400"
                      }
                    >
                      {c.realised_pnl >= 0 ? "+" : ""}
                      {c.realised_pnl.toFixed(2)}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {c.committed_stake.toFixed(2)}
                    </td>
                    <td className="px-2 py-1.5 text-xs text-muted-foreground">
                      {[
                        c.rejected && `${c.rejected} rejected`,
                        c.broker_error && `${c.broker_error} broker_error`,
                        c.expired && `${c.expired} expired`,
                        c.pending && `${c.pending} pending`,
                      ]
                        .filter(Boolean)
                        .join(" · ") || "—"}
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

// ---------------------------------------------------------------------------
// Risk caps + budget + streaks
// ---------------------------------------------------------------------------

function RiskOverviewCard() {
  const overview = useQuery<RiskOverview>({
    queryKey: ["risk", "overview"],
    queryFn: risk.overview,
    refetchInterval: 5_000,
  });

  if (overview.isLoading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-sm text-muted-foreground">Loading risk overview…</p>
        </CardContent>
      </Card>
    );
  }
  if (!overview.data) return null;

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <BudgetCard data={overview.data} />
      <RiskCapsForm data={overview.data} />
      <StreaksCard data={overview.data} />
    </div>
  );
}

function BudgetCard({ data }: { data: RiskOverview }) {
  const realised = data.budget.realised_pnl;
  const committed = data.budget.committed_stake;
  const open = data.budget.open_attempts;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Today&rsquo;s budget (UTC)</CardTitle>
        <CardDescription>Resets at 00:00 UTC.</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="space-y-2 text-sm">
          <Row
            label="Realised P&L"
            value={
              <span
                className={
                  realised < 0 ? "font-mono text-destructive" : "font-mono text-emerald-400"
                }
              >
                {realised >= 0 ? "+" : ""}
                {realised.toFixed(2)}
              </span>
            }
          />
          <Row
            label="Committed stake"
            value={<span className="font-mono">{committed.toFixed(2)}</span>}
          />
          <Row
            label="Open attempts"
            value={<span className="font-mono">{open}</span>}
          />
        </dl>
      </CardContent>
    </Card>
  );
}

function RiskCapsForm({ data }: { data: RiskOverview }) {
  const qc = useQueryClient();
  const [caps, setCaps] = useState<RiskCaps>(data.caps);
  const [error, setError] = useState<string | null>(null);

  // Re-sync when overview refetches.
  useEffect(() => {
    setCaps(data.caps);
  }, [data.caps]);

  const save = useMutation({
    mutationFn: () => risk.updateCaps(caps),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["risk"] });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : String(err)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily caps</CardTitle>
        <CardDescription>
          Set to <code>0</code> to disable a cap. Caps reset at 00:00 UTC.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="space-y-4"
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="daily_max_loss">Max daily loss</Label>
              <Input
                id="daily_max_loss"
                type="number"
                step="0.01"
                min="0"
                value={caps.daily_max_loss}
                onChange={(e) =>
                  setCaps({
                    ...caps,
                    daily_max_loss: Math.max(0, Number(e.target.value) || 0),
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="daily_max_stake">Max daily stake</Label>
              <Input
                id="daily_max_stake"
                type="number"
                step="0.01"
                min="0"
                value={caps.daily_max_stake}
                onChange={(e) =>
                  setCaps({
                    ...caps,
                    daily_max_stake: Math.max(0, Number(e.target.value) || 0),
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="max_concurrent_trades">Max concurrent</Label>
              <Input
                id="max_concurrent_trades"
                type="number"
                step="1"
                min="0"
                value={caps.max_concurrent_trades}
                onChange={(e) =>
                  setCaps({
                    ...caps,
                    max_concurrent_trades: Math.max(
                      0,
                      Number(e.target.value) || 0,
                    ),
                  })
                }
              />
            </div>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save caps"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function StreaksCard({ data }: { data: RiskOverview }) {
  const qc = useQueryClient();
  const reset = useMutation({
    mutationFn: (parserConfigId: number) => risk.resetStreak(parserConfigId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["risk"] }),
  });
  const martingaleRows = data.streaks.filter((s) => s.martingale_enabled);

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Martingale streaks</CardTitle>
        <CardDescription>
          One row per parser with martingale enabled. The next stake at
          step <em>n</em> is{" "}
          <code>base × multiplier^n</code>; a win resets to step 0
          (when reset-on-win is on).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {martingaleRows.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No parsers have martingale enabled. Toggle it on a parser&rsquo;s
            edit page to start tracking a recovery ladder.
          </p>
        )}
        {martingaleRows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-1.5 font-medium">Parser</th>
                  <th className="px-2 py-1.5 font-medium">Mult</th>
                  <th className="px-2 py-1.5 font-medium">Max</th>
                  <th className="px-2 py-1.5 font-medium">Step</th>
                  <th className="px-2 py-1.5 font-medium">Last</th>
                  <th className="px-2 py-1.5 font-medium">Last stake</th>
                  <th className="px-2 py-1.5 font-medium">Updated</th>
                  <th className="px-2 py-1.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {martingaleRows.map((s) => (
                  <tr key={s.parser_config_id} className="border-b last:border-0">
                    <td className="px-2 py-1.5 font-mono">
                      {s.parser_name}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      ×{s.multiplier}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {s.max_streak === 0 ? "∞" : s.max_streak}
                    </td>
                    <td className="px-2 py-1.5">
                      {s.current_streak === 0 ? (
                        <Badge variant="outline">base</Badge>
                      ) : (
                        <Badge variant="warning">step {s.current_streak}</Badge>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      {s.last_outcome === "won" && (
                        <Badge variant="success">won</Badge>
                      )}
                      {s.last_outcome === "lost" && (
                        <Badge variant="destructive">lost</Badge>
                      )}
                      {s.last_outcome === "" && (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 font-mono">
                      {s.last_stake > 0 ? s.last_stake.toFixed(2) : "—"}
                    </td>
                    <td className="px-2 py-1.5 text-xs text-muted-foreground">
                      {s.updated_at
                        ? new Date(s.updated_at).toLocaleTimeString()
                        : "—"}
                    </td>
                    <td className="px-2 py-1.5">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => reset.mutate(s.parser_config_id)}
                        disabled={reset.isPending || s.current_streak === 0}
                      >
                        Reset
                      </Button>
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

function FeedIndicator({ state }: { state: FeedState }) {
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
