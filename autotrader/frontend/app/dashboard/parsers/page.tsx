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
import {
  type ParserConfig,
  type TelegramDialog,
  parsers,
  telegram,
} from "@/lib/api";

export default function ParsersIndex() {
  const watched = useQuery<TelegramDialog[]>({
    queryKey: ["telegram", "watched"],
    queryFn: telegram.watched,
  });
  const configs = useQuery<ParserConfig[]>({
    queryKey: ["parsers", "configs"],
    queryFn: parsers.list,
  });

  const byChatId = new Map<number, ParserConfig>();
  for (const c of configs.data ?? []) byChatId.set(c.chat_id, c);

  const channels = watched.data ?? [];

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Parsers</h2>
        <p className="text-sm text-muted-foreground">
          Configure how each watched channel&rsquo;s messages translate into
          structured signals. Click a channel to open the live tester.
        </p>
      </section>

      {watched.isLoading && (
        <p className="text-sm text-muted-foreground">Loading channels…</p>
      )}
      {watched.data && channels.length === 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No channels watched yet. Pick some on the{" "}
              <Link href="/dashboard/telegram" className="underline">
                Telegram
              </Link>{" "}
              page first.
            </p>
          </CardContent>
        </Card>
      )}

      <ul className="grid gap-4 md:grid-cols-2">
        {channels.map((channel) => {
          const cfg = byChatId.get(channel.chat_id);
          return (
            <li key={channel.chat_id}>
              <Link href={`/dashboard/parsers/${channel.chat_id}`}>
                <Card className="transition-colors hover:border-primary">
                  <CardHeader>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <CardTitle className="truncate">
                          {channel.title}
                        </CardTitle>
                        <CardDescription className="truncate">
                          {channel.username
                            ? `@${channel.username}`
                            : `chat ${channel.chat_id}`}
                          {channel.chat_type ? ` · ${channel.chat_type}` : ""}
                        </CardDescription>
                      </div>
                      <ParserStatusBadge cfg={cfg} />
                    </div>
                  </CardHeader>
                  <CardContent>
                    {cfg ? (
                      <dl className="space-y-1 text-xs">
                        <Row label="Type" value={cfg.parser_type} />
                        <Row
                          label="Default duration"
                          value={`${cfg.default_duration_seconds}s`}
                        />
                        <Row label="Stake" value={String(cfg.default_stake)} />
                        <Row
                          label="Aggregate window"
                          value={
                            cfg.aggregate_window_seconds > 0
                              ? `${cfg.aggregate_window_seconds}s`
                              : "single message"
                          }
                        />
                      </dl>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        No parser configured. Click to set one up.
                      </p>
                    )}
                  </CardContent>
                </Card>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ParserStatusBadge({ cfg }: { cfg?: ParserConfig }) {
  if (!cfg) return <Badge variant="outline">unset</Badge>;
  if (!cfg.enabled) return <Badge variant="secondary">disabled</Badge>;
  return <Badge variant="success">configured</Badge>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}
