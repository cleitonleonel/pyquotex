"use client";

import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group";
import { useFilters } from "@/lib/use-filters";

export function FilterPillDirection() {
  const { filters, setFilter } = useFilters();
  const value = filters.direction ?? "both";
  return (
    <ToggleGroup
      type="single"
      size="sm"
      value={value}
      onValueChange={(v) => {
        // ToggleGroup emits "" when the active item is clicked again
        // — treat that as "both" (clear filter).
        if (v === "call" || v === "put") {
          setFilter("direction", v);
        } else {
          setFilter("direction", undefined);
        }
      }}
      className="rounded-md border"
    >
      <ToggleGroupItem value="both" className="text-xs">
        Both
      </ToggleGroupItem>
      <ToggleGroupItem value="call" className="text-xs">
        Call
      </ToggleGroupItem>
      <ToggleGroupItem value="put" className="text-xs">
        Put
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
