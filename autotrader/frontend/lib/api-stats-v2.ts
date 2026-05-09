/**
 * Typed client for the Phase 2 /stats/v2/* endpoints.
 *
 * Same fetch wrapper as lib/api.ts; types mirror the pydantic
 * response models in backend/src/autotrader/routers/stats_v2.py.
 */

import { api } from "./api";

export type Metric =
  | "equity"
  | "win_rate"
  | "latency_p50"
  | "latency_p99"
  | "outcomes";

export type Bucket = "auto" | "hour" | "day" | "week";

export type Dim =
  | "channel"
  | "parser"
  | "asset"
  | "direction"
  | "hour_of_week";

export type RangeLabel = "24h" | "7d" | "30d" | "all" | "custom";

export type Direction = "call" | "put";

export type TradeStatus =
  | "won"
  | "lost"
  | "rejected"
  | "broker_error"
  | "expired"
  | "pending";

export interface FilterParams {
  range: RangeLabel;
  from?: string; // ISO datetime, only when range=custom
  to?: string; // ISO datetime, only when range=custom
  channels?: number[];
  parsers?: number[];
  assets?: string[];
  direction?: Direction;
  statuses?: TradeStatus[];
}

export interface TimeseriesPoint {
  t: string; // ISO datetime
  value: number | null;
  n: number;
  counts?: Record<string, number>; // only set when metric=outcomes
}

export interface TimeseriesResponse {
  metric: Metric;
  bucket: Bucket;
  points: TimeseriesPoint[];
  filters_applied: Record<string, unknown>;
}

export interface BreakdownRow {
  key: number | string;
  label: string;
  total: number;
  won: number;
  lost: number;
  rejected: number;
  broker_error: number;
  expired: number;
  pending: number;
  win_rate: number | null;
  win_rate_ci_low: number | null;
  win_rate_ci_high: number | null;
  realised_pnl: number;
  committed_stake: number;
  direction_split?: { call: BreakdownRow; put: BreakdownRow };
  streaks?: { length: number; ended_in: "won" | "lost"; recovered: boolean }[];
}

export interface BreakdownResponse {
  dim: Dim;
  rows: BreakdownRow[];
  filters_applied: Record<string, unknown>;
}

export interface FunnelStage {
  key:
    | "messages_received"
    | "matched"
    | "passed_risk"
    | "placed"
    | "settled";
  label: string;
  count: number;
}

export interface FunnelResponse {
  stages: FunnelStage[];
  drop_reasons: Record<string, { reason: string; count: number }[]>;
  filters_applied: Record<string, unknown>;
}

/**
 * Build a query string from a FilterParams + extras object. Empty /
 * default values are omitted so URLs stay clean.
 */
function buildQuery(
  filters: FilterParams,
  extras: Record<string, string | undefined> = {},
): string {
  const params = new URLSearchParams();
  params.set("range", filters.range);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.channels && filters.channels.length > 0) {
    params.set("channels", filters.channels.join(","));
  }
  if (filters.parsers && filters.parsers.length > 0) {
    params.set("parsers", filters.parsers.join(","));
  }
  if (filters.assets && filters.assets.length > 0) {
    params.set("assets", filters.assets.join(","));
  }
  if (filters.direction) params.set("direction", filters.direction);
  if (filters.statuses && filters.statuses.length > 0) {
    params.set("statuses", filters.statuses.join(","));
  }
  for (const [k, v] of Object.entries(extras)) {
    if (v !== undefined) params.set(k, v);
  }
  return params.toString();
}

/**
 * Build a minimal range-only query string for endpoints (like
 * /stats/v2/assets) that intentionally ignore the rest of the filter
 * state. We don't reuse buildQuery() because it always emits "range="
 * and we want the same here, but without dragging the unrelated
 * channels/parsers/direction params along.
 */
function buildRangeQuery(params: {
  range?: RangeLabel;
  from?: string;
  to?: string;
}): string {
  const qs = new URLSearchParams();
  qs.set("range", params.range ?? "7d");
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  return qs.toString();
}

export interface AssetsResponse {
  assets: string[];
}

export const statsV2 = {
  timeseries: (
    filters: FilterParams,
    metric: Metric,
    bucket: Bucket = "auto",
  ) =>
    api<TimeseriesResponse>(
      `/stats/v2/timeseries?${buildQuery(filters, { metric, bucket })}`,
    ),

  breakdown: (filters: FilterParams, dim: Dim) =>
    api<BreakdownResponse>(
      `/stats/v2/breakdown?${buildQuery(filters, { dim })}`,
    ),

  funnel: (filters: FilterParams) =>
    api<FunnelResponse>(`/stats/v2/funnel?${buildQuery(filters)}`),

  /**
   * Distinct assets traded inside the (range, from, to) window. The
   * endpoint deliberately ignores chats/parsers/direction so the pill
   * shows the full universe — operators can pivot between channels
   * without first widening other pills.
   */
  assets: (params: { range?: RangeLabel; from?: string; to?: string } = {}) =>
    api<AssetsResponse>(`/stats/v2/assets?${buildRangeQuery(params)}`),
};
