"use client";

/**
 * Asset filter pill — populated from /stats/v2/assets so the option
 * set tracks whatever has actually traded in the current range.
 *
 * Refetches when the range changes; the rest of useFilters output is
 * intentionally NOT in the queryKey because changing chats / parsers
 * / direction should not shrink the asset universe (the operator may
 * want to pivot back).
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";

import {
  FilterPillMultiSelect,
  type MultiSelectOption,
} from "./filter-pill-multiselect";

export function FilterPillAssets() {
  const { filters, setFilter } = useFilters();

  const { data, isLoading } = useQuery({
    queryKey: ["stats-v2-assets", filters.range, filters.from, filters.to],
    queryFn: () =>
      statsV2.assets({
        range: filters.range,
        from: filters.from,
        to: filters.to,
      }),
    staleTime: 60_000,
  });

  const options: MultiSelectOption<string>[] = useMemo(
    () => (data?.assets ?? []).map((a) => ({ value: a, label: a })),
    [data],
  );

  return (
    <FilterPillMultiSelect<string>
      label="Assets"
      options={options}
      selected={filters.assets ?? []}
      onChange={(next) => setFilter("assets", next)}
      emptyHint={isLoading ? "Loading assets…" : "No assets traded in window."}
    />
  );
}
