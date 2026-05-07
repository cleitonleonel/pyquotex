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
    queryKey: ["parsers", "configs", "all"],
    queryFn: () => parsers.list(),
  });

  // Group configs by chat for the count badge.
  const counts = new Map<number, number>();
  const enabled = new Map<number, number>();
  for (const c of configs.data ?? []) {
    counts.set(c.chat_id, (counts.get(c.chat_id) ?? 0) + 1);
    if (c.enabled) {
      enabled.set(c.chat_id, (enabled.get(c.chat_id) ?? 0) + 1);
    }
  }

  const channels = watched.data ?? [];

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Parsers</h2>
        <p className="text-sm text-muted-foreground">
          Each channel can host multiple named parsers, ranked by priority.
          Click a channel to manage its parser list.
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
          const count = counts.get(channel.chat_id) ?? 0;
          const on = enabled.get(channel.chat_id) ?? 0;
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
                      <CountBadge count={count} enabled={on} />
                    </div>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-muted-foreground">
                      {count === 0
                        ? "No parsers yet. Click to add one."
                        : `${on} of ${count} enabled.`}
                    </p>
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

function CountBadge({ count, enabled }: { count: number; enabled: number }) {
  if (count === 0) return <Badge variant="outline">none</Badge>;
  if (enabled === 0) return <Badge variant="secondary">{count} disabled</Badge>;
  return <Badge variant="success">{enabled}/{count}</Badge>;
}
