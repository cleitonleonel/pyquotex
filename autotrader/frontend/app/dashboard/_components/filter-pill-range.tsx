"use client";

/**
 * Date-range filter pill. Click to open a popover with preset list
 * (Today / Yesterday / Last 7 days / Last 30 days / All / Custom).
 * Selecting "Custom" reveals a react-day-picker calendar for the
 * from/to dates.
 */

import { CalendarIcon } from "lucide-react";
import { useState } from "react";
import { DayPicker } from "react-day-picker";
import type { DateRange } from "react-day-picker";
import "react-day-picker/dist/style.css";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { useFilters } from "@/lib/use-filters";
import type { RangeLabel } from "@/lib/api-stats-v2";

const PRESETS: { label: string; value: RangeLabel }[] = [
  { label: "Today (24h)", value: "24h" },
  { label: "Last 7 days", value: "7d" },
  { label: "Last 30 days", value: "30d" },
  { label: "All time", value: "all" },
];

export function FilterPillRange() {
  const { filters, setFilters } = useFilters();
  const [open, setOpen] = useState(false);
  const [customRange, setCustomRange] = useState<DateRange | undefined>(
    filters.from && filters.to
      ? { from: new Date(filters.from), to: new Date(filters.to) }
      : undefined,
  );

  const display = (() => {
    if (filters.range === "custom" && filters.from && filters.to) {
      const from = new Date(filters.from).toLocaleDateString();
      const to = new Date(filters.to).toLocaleDateString();
      return `${from} → ${to}`;
    }
    return PRESETS.find((p) => p.value === filters.range)?.label ?? "Last 7 days";
  })();

  const applyPreset = (value: RangeLabel) => {
    setFilters({ range: value, from: undefined, to: undefined });
    setOpen(false);
  };

  const applyCustom = () => {
    if (customRange?.from && customRange.to) {
      setFilters({
        range: "custom",
        from: customRange.from.toISOString(),
        to: customRange.to.toISOString(),
      });
      setOpen(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <CalendarIcon className="h-3.5 w-3.5" />
          {display}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <div className="flex flex-col gap-1 p-2">
          {PRESETS.map((p) => (
            <Button
              key={p.value}
              variant={filters.range === p.value ? "secondary" : "ghost"}
              size="sm"
              className="justify-start"
              onClick={() => applyPreset(p.value)}
            >
              {p.label}
            </Button>
          ))}
          <Separator className="my-1" />
          <div className="px-2 py-1 text-xs text-muted-foreground">Custom range</div>
          <DayPicker
            mode="range"
            selected={customRange}
            onSelect={(range) => setCustomRange(range)}
            numberOfMonths={1}
            className="text-xs"
          />
          <Button
            size="sm"
            disabled={!customRange?.from || !customRange?.to}
            onClick={applyCustom}
          >
            Apply custom
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
