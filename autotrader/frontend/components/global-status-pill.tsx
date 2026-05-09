"use client";

/**
 * The pipeline-status pill rendered in the topbar on every dashboard
 * page. Reads /pipeline/status (5s poll) and renders one of:
 *
 *   ● Pipeline live · DEMO     (active, kill switch off)
 *   ● Idle · DEMO              (inactive, kill switch off)
 *   ⏸ Kill switch · DEMO       (kill switch engaged)
 *   ● Pipeline live · REAL     (active, REAL trading enabled — amber)
 *
 * Uses the same query key the existing dashboard pages use, so all
 * subscribers share one in-flight request via TanStack Query.
 */

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { type PipelineStatus, pipeline } from "@/lib/api";

export function GlobalStatusPill() {
  const { data, isLoading } = useQuery<PipelineStatus>({
    queryKey: ["pipeline", "status"],
    queryFn: pipeline.status,
    refetchInterval: 5_000,
  });

  if (isLoading || !data) {
    return (
      <Badge variant="outline" className="gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground" />
        loading…
      </Badge>
    );
  }

  const real = data.live_trading_enabled_env;
  const accountLabel = real ? "REAL" : "DEMO";

  if (data.kill_switch_engaged) {
    return (
      <Badge variant="destructive" className="gap-1.5">
        ⏸ Kill switch · {accountLabel}
      </Badge>
    );
  }
  if (data.active) {
    return (
      <Badge
        variant={real ? "warning" : "success"}
        className="gap-1.5"
      >
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
        Pipeline live · {accountLabel}
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="gap-1.5">
      <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground" />
      Idle · {accountLabel}
    </Badge>
  );
}
