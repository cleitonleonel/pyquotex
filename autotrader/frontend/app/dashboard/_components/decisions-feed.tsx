"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { type ParserDecision, pipeline } from "@/lib/api";
import { type FeedState } from "@/lib/use-trade-feed";
import { FeedIndicator } from "./trades-table";

export function DecisionsFeed({ feedState }: { feedState: FeedState }) {
  const decisions = useQuery<ParserDecision[]>({
    queryKey: ["pipeline", "decisions"],
    queryFn: () => pipeline.decisions(50),
    refetchInterval: feedState === "live" ? false : 15_000,
    staleTime: feedState === "live" ? Infinity : 0,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              Recent parsing decisions
              <FeedIndicator state={feedState} />
            </CardTitle>
            <CardDescription>
              Every dispatch — matched, rejected, or routed to a chat with
              no parsers. Surfaces the same data the executor logs as
              <code> pipeline.matched </code> / <code>pipeline.no_match</code>{" "}
              so you can debug parser regressions without scraping logs.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {decisions.isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {!decisions.isLoading && (decisions.data ?? []).length === 0 && (
          <p className="text-sm text-muted-foreground">
            No parsing decisions yet. The next watched-chat message will
            appear here as it&rsquo;s dispatched.
          </p>
        )}
        {(decisions.data ?? []).length > 0 && (
          <div className="max-h-[28rem] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-2 py-1.5 font-medium">When</th>
                  <th className="px-2 py-1.5 font-medium">Chat</th>
                  <th className="px-2 py-1.5 font-medium">Parser</th>
                  <th className="px-2 py-1.5 font-medium">Outcome</th>
                  <th className="px-2 py-1.5 font-medium">Reason / preview</th>
                </tr>
              </thead>
              <tbody>
                {(decisions.data ?? []).map((d, idx) => (
                  <tr
                    key={`${d.ts}-${d.chat_id}-${d.parser_config_id ?? "none"}-${idx}`}
                    className="border-b last:border-0 align-top"
                  >
                    <td className="whitespace-nowrap px-2 py-1.5 text-muted-foreground">
                      {new Date(d.ts).toLocaleTimeString()}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 font-mono">
                      {d.chat_id}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5 font-mono">
                      {d.parser_name ? (
                        <>
                          {d.parser_name}
                          {d.parser_type && (
                            <span className="ml-1 text-muted-foreground">
                              ({d.parser_type})
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5">
                      <DecisionBadge outcome={d.outcome} signals={d.signals} />
                    </td>
                    <td className="px-2 py-1.5 text-xs">
                      {d.reasons.length > 0 ? (
                        <span className="text-amber-300">
                          {d.reasons.join("; ")}
                        </span>
                      ) : d.text_preview ? (
                        <span className="text-muted-foreground">
                          {d.text_preview}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
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

export function DecisionBadge({
  outcome,
  signals,
}: {
  outcome: ParserDecision["outcome"];
  signals: number;
}) {
  if (outcome === "matched") {
    return <Badge variant="success">matched · {signals}</Badge>;
  }
  if (outcome === "no_match") {
    return <Badge variant="outline">no match</Badge>;
  }
  if (outcome === "build_failed") {
    return <Badge variant="destructive">build failed</Badge>;
  }
  if (outcome === "no_configs") {
    return <Badge variant="secondary">no parsers</Badge>;
  }
  if (outcome === "pipeline_inactive") {
    return <Badge variant="secondary">pipeline off</Badge>;
  }
  return <Badge variant="secondary">{outcome}</Badge>;
}
