"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { type FeedFrame, type TradeAttempt, feedUrl, getToken } from "./api";

/**
 * Live trade-feed hook. Opens a WebSocket to ``/feed/ws`` and merges
 * each ``trade.upserted`` frame into the cached
 * ``["pipeline", "trades"]`` list, prepending fresh rows and patching
 * existing ones in place. The 5s background poll is still running as
 * a fall-back so a dropped connection self-heals on the next refetch.
 *
 * Returns the live connection state for the UI to render an
 * indicator. Reconnect uses an exponential backoff (1s → 2s → 4s →
 * 8s, capped at 8s) so a flaky network doesn't spam the server.
 */
export type FeedState = "connecting" | "live" | "offline";

export function useTradeFeed(): FeedState {
  const qc = useQueryClient();
  const [state, setState] = useState<FeedState>("connecting");
  // Hold the live socket in a ref so the cleanup closure always
  // closes the *current* connection rather than a stale one if
  // ``getToken()`` shifts during a render cycle.
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setState("offline");
      return;
    }

    let attempt = 0;
    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      const ws = new WebSocket(feedUrl(token));
      wsRef.current = ws;
      setState("connecting");

      ws.onopen = () => {
        attempt = 0;
        setState("live");
      };

      ws.onmessage = (event) => {
        let frame: FeedFrame;
        try {
          frame = JSON.parse(event.data) as FeedFrame;
        } catch {
          return;
        }
        if (frame.type !== "trade.upserted") return;
        const incoming = frame.payload;
        qc.setQueryData<TradeAttempt[]>(["pipeline", "trades"], (current) =>
          mergeTrade(current, incoming),
        );
        // Stats overview drives off the same ``trade_attempts`` rows
        // — invalidate so the channel breakdown / latency tiles
        // update without waiting for the next poll tick.
        qc.invalidateQueries({ queryKey: ["stats"] });
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (cancelled) return;
        setState("offline");
        const delay = Math.min(8_000, 1_000 * 2 ** attempt);
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // ``onclose`` fires next, so the reconnect path is shared.
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [qc]);

  return state;
}

function mergeTrade(
  current: TradeAttempt[] | undefined,
  incoming: TradeAttempt,
): TradeAttempt[] {
  if (!current) return [incoming];
  const idx = current.findIndex((t) => t.id === incoming.id);
  if (idx === -1) return [incoming, ...current];
  const next = current.slice();
  next[idx] = incoming;
  return next;
}
