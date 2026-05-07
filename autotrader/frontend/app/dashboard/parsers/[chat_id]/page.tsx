"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";
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
  type ParserConfig,
  type TelegramDialog,
  parsers,
  telegram,
} from "@/lib/api";

export default function ChannelParsersList() {
  const params = useParams<{ chat_id: string }>();
  const chatId = Number(params.chat_id);
  const qc = useQueryClient();

  const watched = useQuery<TelegramDialog[]>({
    queryKey: ["telegram", "watched"],
    queryFn: telegram.watched,
  });
  const channel = useMemo(
    () => watched.data?.find((d) => d.chat_id === chatId),
    [watched.data, chatId],
  );

  const configs = useQuery<ParserConfig[]>({
    queryKey: ["parsers", "configs", "chat", chatId],
    queryFn: () => parsers.list(chatId),
  });

  const remove = useMutation({
    mutationFn: (id: number) => parsers.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["parsers"] }),
  });

  return (
    <div className="space-y-6">
      <section>
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">
              {channel?.title ?? `Chat ${chatId}`}
            </h2>
            <p className="text-sm text-muted-foreground">
              {channel?.username
                ? `@${channel.username}`
                : `chat ${chatId}`}
              {channel?.chat_type ? ` · ${channel.chat_type}` : ""}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/dashboard/parsers"
              className="text-sm text-muted-foreground hover:text-foreground"
            >
              ← All channels
            </Link>
            <Link
              href={`/dashboard/parsers/${chatId}/new`}
              className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90"
            >
              + Add parser
            </Link>
          </div>
        </div>
      </section>

      {configs.isLoading && (
        <p className="text-sm text-muted-foreground">Loading parsers…</p>
      )}
      {configs.data && configs.data.length === 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No parsers yet for this channel. Click <strong>Add parser</strong>{" "}
              to set one up.
            </p>
          </CardContent>
        </Card>
      )}

      <ul className="space-y-3">
        {(configs.data ?? []).map((cfg) => (
          <li key={cfg.id}>
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <CardTitle className="flex items-center gap-2">
                      <span className="truncate">
                        {cfg.name || `parser #${cfg.id}`}
                      </span>
                      <ParserStatusBadges cfg={cfg} />
                    </CardTitle>
                    <CardDescription>
                      priority {cfg.priority} · {cfg.parser_type} · trade-mode{" "}
                      {cfg.trade_mode}
                    </CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <Link
                      href={`/dashboard/parsers/${chatId}/${cfg.id}`}
                      className="inline-flex h-8 items-center justify-center rounded-md border border-input bg-background px-3 text-xs font-medium shadow-sm hover:bg-accent hover:text-accent-foreground"
                    >
                      Edit
                    </Link>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        if (
                          confirm(`Delete parser ${cfg.name || cfg.id}?`)
                        ) {
                          remove.mutate(cfg.id);
                        }
                      }}
                      disabled={remove.isPending}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
                  <Row
                    label="Duration"
                    value={`${cfg.default_duration_seconds}s`}
                  />
                  <Row label="Stake" value={String(cfg.default_stake)} />
                  <Row
                    label="Aggregate"
                    value={
                      cfg.aggregate_window_seconds > 0
                        ? `${cfg.aggregate_window_seconds}s`
                        : "off"
                    }
                  />
                  <Row
                    label="Martingale"
                    value={
                      cfg.martingale.enabled
                        ? `×${cfg.martingale.multiplier} max ${cfg.martingale.max_streak}`
                        : "off"
                    }
                  />
                </dl>
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ParserStatusBadges({ cfg }: { cfg: ParserConfig }) {
  return (
    <span className="inline-flex flex-wrap gap-1.5">
      <Badge variant={cfg.enabled ? "success" : "secondary"}>
        {cfg.enabled ? "enabled" : "disabled"}
      </Badge>
      {cfg.martingale.enabled && (
        <Badge variant="warning">martingale</Badge>
      )}
      {cfg.aggregate_window_seconds > 0 && (
        <Badge variant="outline">multi-msg</Badge>
      )}
    </span>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}
