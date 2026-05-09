"use client";

/**
 * The global filter bar. Sticky on the analytics page; reads
 * options from /pipeline/status (channel ids), /parsers/configs
 * (parser ids), and the trade-attempts cache (asset symbols).
 *
 * Status filter has hardcoded options; the rest are dynamic.
 */

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  type ParserConfig,
  type TelegramDialog,
  parsers as parsersApi,
  telegram,
} from "@/lib/api";
import type { TradeStatus } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";
import { FilterPillAssets } from "./filter-pill-assets";
import { FilterPillDirection } from "./filter-pill-direction";
import {
  FilterPillMultiSelect,
  type MultiSelectOption,
} from "./filter-pill-multiselect";
import { FilterPillRange } from "./filter-pill-range";

const STATUS_OPTIONS: MultiSelectOption<TradeStatus>[] = [
  { value: "won", label: "Won" },
  { value: "lost", label: "Lost" },
  { value: "rejected", label: "Rejected (risk)" },
  { value: "broker_error", label: "Broker error" },
  { value: "expired", label: "Expired" },
  { value: "pending", label: "Pending" },
];

export function FilterBar() {
  const { filters, setFilter, clear, hasAny } = useFilters();

  const watched = useQuery<TelegramDialog[]>({
    queryKey: ["telegram", "watched"],
    queryFn: telegram.watched,
    staleTime: 60_000,
  });
  const allParsers = useQuery<ParserConfig[]>({
    queryKey: ["parsers", "all"],
    queryFn: () => parsersApi.list(),
    staleTime: 60_000,
  });

  const channelOptions: MultiSelectOption<number>[] = (watched.data ?? []).map(
    (w) => ({ value: w.chat_id, label: w.title }),
  );
  const parserOptions: MultiSelectOption<number>[] = (allParsers.data ?? []).map(
    (p) => ({ value: p.id, label: p.name, hint: `chat ${p.chat_id}` }),
  );

  return (
    <Card className="flex flex-wrap items-center gap-2 p-2">
      <FilterPillRange />
      <FilterPillMultiSelect
        label="Channels"
        options={channelOptions}
        selected={filters.channels ?? []}
        onChange={(v) => setFilter("channels", v)}
      />
      <FilterPillMultiSelect
        label="Parsers"
        options={parserOptions}
        selected={filters.parsers ?? []}
        onChange={(v) => setFilter("parsers", v)}
      />
      <FilterPillAssets />
      <FilterPillMultiSelect
        label="Status"
        options={STATUS_OPTIONS}
        selected={filters.statuses ?? []}
        onChange={(v) => setFilter("statuses", v)}
      />
      <FilterPillDirection />
      <div className="ml-auto">
        {hasAny && (
          <Button
            variant="ghost"
            size="sm"
            onClick={clear}
            className="gap-1 text-muted-foreground"
          >
            <X className="h-3 w-3" />
            Clear
          </Button>
        )}
      </div>
    </Card>
  );
}
