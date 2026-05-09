"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import {
  type BrokerBalance,
  type BrokerStatus,
  type PipelineStatus,
  type TelegramStatus,
  broker,
  pipeline,
  telegram,
} from "@/lib/api";

/**
 * Three small status panels on the Overview page right column. Each
 * polls its own endpoint independently (different cadence makes
 * sense per source).
 */
export function OverviewStatusCards() {
  const b = useQuery<BrokerStatus>({
    queryKey: ["broker", "status"],
    queryFn: broker.status,
    refetchInterval: 10_000,
  });
  const balance = useQuery<BrokerBalance>({
    queryKey: ["broker", "balance"],
    queryFn: broker.balance,
    refetchInterval: 30_000,
    enabled: b.data?.connected ?? false,
  });
  const t = useQuery<TelegramStatus>({
    queryKey: ["telegram", "status"],
    queryFn: telegram.status,
    refetchInterval: 15_000,
  });
  const p = useQuery<PipelineStatus>({
    queryKey: ["pipeline", "status"],
    queryFn: pipeline.status,
    refetchInterval: 5_000,
  });

  return (
    <div className="grid gap-4">
      <MiniCard
        label="Broker"
        primary={
          b.data?.connected
            ? "● Connected"
            : b.data?.configured
              ? "Disconnected"
              : "Not configured"
        }
        secondary={
          b.data?.connected
            ? `${b.data.account_mode} · ${
                balance.data ? `$${balance.data.balance.toFixed(2)}` : "…"
              }`
            : "—"
        }
        tone={b.data?.connected ? "ok" : "muted"}
      />
      <MiniCard
        label="Telegram"
        primary={t.data?.logged_in ? "● Logged in" : "Not connected"}
        secondary={
          t.data?.logged_in
            ? `${p.data?.watched_chat_count ?? "?"} watched · ${
                p.data?.last_message_received_at
                  ? `last msg ${describeAge(p.data.last_message_received_at)}`
                  : "no messages yet"
              }`
            : "—"
        }
        tone={t.data?.logged_in ? "ok" : "muted"}
      />
      <MiniCard
        label="Pipeline"
        primary={
          p.data?.kill_switch_engaged
            ? "⏸ Kill switch"
            : p.data?.active
              ? "● Live"
              : "Idle"
        }
        secondary={
          p.data
            ? `${p.data.subscribed_chat_count}/${p.data.watched_chat_count} subscribed · ${p.data.enabled_parser_count} parsers`
            : "—"
        }
        tone={
          p.data?.kill_switch_engaged
            ? "warn"
            : p.data?.active
              ? "ok"
              : "muted"
        }
      />
    </div>
  );
}

function describeAge(iso: string): string {
  const sec = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

function MiniCard({
  label,
  primary,
  secondary,
  tone,
}: {
  label: string;
  primary: string;
  secondary: string;
  tone: "ok" | "warn" | "muted";
}) {
  const toneClass =
    tone === "ok"
      ? "text-success"
      : tone === "warn"
        ? "text-warning"
        : "text-muted-foreground";
  return (
    <Card>
      <CardHeader className="pb-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
      </CardHeader>
      <CardContent>
        <div className={`text-sm font-semibold ${toneClass}`}>{primary}</div>
        <div className="mt-1 text-xs text-muted-foreground">{secondary}</div>
      </CardContent>
    </Card>
  );
}
