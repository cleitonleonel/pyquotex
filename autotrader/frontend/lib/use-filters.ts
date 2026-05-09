"use client";

/**
 * useFilters — single source of truth for analytics filter state.
 *
 * Reads from URL search params via useSearchParams(); writes back via
 * router.replace() so the URL is the source of truth. Components
 * never own filter state — they read this hook and re-render when
 * the URL changes.
 *
 * Multi-value params are comma-separated (matches the backend's CSV
 * parser). Empty / default values are omitted from the URL so a
 * fresh page is `?range=7d` not `?range=7d&channels=&parsers=&...`.
 */

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useCallback, useMemo } from "react";
import type {
  Direction,
  FilterParams,
  RangeLabel,
  TradeStatus,
} from "./api-stats-v2";

const VALID_STATUSES: readonly TradeStatus[] = [
  "won",
  "lost",
  "rejected",
  "broker_error",
  "expired",
  "pending",
] as const;

function parseCsvInt(raw: string | null): number[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((p) => Number(p.trim()))
    .filter((n) => Number.isFinite(n));
}

function parseCsvStr(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

function parseRange(raw: string | null): RangeLabel {
  if (raw === "24h" || raw === "7d" || raw === "30d" || raw === "all" || raw === "custom") {
    return raw;
  }
  return "7d"; // default
}

function parseDirection(raw: string | null): Direction | undefined {
  return raw === "call" || raw === "put" ? raw : undefined;
}

function parseStatuses(raw: string | null): TradeStatus[] {
  return parseCsvStr(raw).filter(
    (s): s is TradeStatus => (VALID_STATUSES as readonly string[]).includes(s),
  );
}

export interface UseFiltersResult {
  filters: FilterParams;
  setFilter: <K extends keyof FilterParams>(key: K, value: FilterParams[K]) => void;
  setFilters: (next: Partial<FilterParams>) => void;
  clear: () => void;
  hasAny: boolean;
}

export function useFilters(): UseFiltersResult {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const filters: FilterParams = useMemo(
    () => ({
      range: parseRange(searchParams.get("range")),
      from: searchParams.get("from") ?? undefined,
      to: searchParams.get("to") ?? undefined,
      channels: parseCsvInt(searchParams.get("channels")),
      parsers: parseCsvInt(searchParams.get("parsers")),
      assets: parseCsvStr(searchParams.get("assets")),
      direction: parseDirection(searchParams.get("direction")),
      statuses: parseStatuses(searchParams.get("statuses")),
    }),
    [searchParams],
  );

  const writeNext = useCallback(
    (next: FilterParams) => {
      const params = new URLSearchParams();
      // Only emit non-default values so the URL stays clean.
      if (next.range !== "7d") params.set("range", next.range);
      if (next.from) params.set("from", next.from);
      if (next.to) params.set("to", next.to);
      if (next.channels && next.channels.length > 0) {
        params.set("channels", next.channels.join(","));
      }
      if (next.parsers && next.parsers.length > 0) {
        params.set("parsers", next.parsers.join(","));
      }
      if (next.assets && next.assets.length > 0) {
        params.set("assets", next.assets.join(","));
      }
      if (next.direction) params.set("direction", next.direction);
      if (next.statuses && next.statuses.length > 0) {
        params.set("statuses", next.statuses.join(","));
      }
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
    },
    [pathname, router],
  );

  const setFilter = useCallback(
    <K extends keyof FilterParams>(key: K, value: FilterParams[K]) => {
      writeNext({ ...filters, [key]: value });
    },
    [filters, writeNext],
  );

  const setFilters = useCallback(
    (next: Partial<FilterParams>) => {
      writeNext({ ...filters, ...next });
    },
    [filters, writeNext],
  );

  const clear = useCallback(() => {
    writeNext({ range: "7d" });
  }, [writeNext]);

  const hasAny = useMemo(
    () =>
      filters.range !== "7d" ||
      Boolean(filters.from) ||
      Boolean(filters.to) ||
      (filters.channels?.length ?? 0) > 0 ||
      (filters.parsers?.length ?? 0) > 0 ||
      (filters.assets?.length ?? 0) > 0 ||
      Boolean(filters.direction) ||
      (filters.statuses?.length ?? 0) > 0,
    [filters],
  );

  return { filters, setFilter, setFilters, clear, hasAny };
}
