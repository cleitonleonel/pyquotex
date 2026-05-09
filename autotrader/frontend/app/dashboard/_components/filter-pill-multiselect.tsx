"use client";

/**
 * Generic multi-select filter pill, used for channels / parsers /
 * assets / statuses. Renders a popover with a Command (search +
 * checkbox list) interior. Selection is committed on each toggle —
 * no Apply button — because the backend handles partial filters
 * fine and live feedback is more useful for exploration.
 */

import { Check, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface MultiSelectOption<V extends string | number> {
  value: V;
  label: string;
  hint?: string; // e.g. trade count or chat type
}

interface FilterPillMultiSelectProps<V extends string | number> {
  label: string;
  options: MultiSelectOption<V>[];
  selected: V[];
  onChange: (next: V[]) => void;
  emptyHint?: string;
}

export function FilterPillMultiSelect<V extends string | number>({
  label,
  options,
  selected,
  onChange,
  emptyHint,
}: FilterPillMultiSelectProps<V>) {
  const toggle = (v: V) => {
    if (selected.includes(v)) {
      onChange(selected.filter((s) => s !== v));
    } else {
      onChange([...selected, v]);
    }
  };

  const triggerLabel =
    selected.length === 0
      ? label
      : selected.length === 1
        ? options.find((o) => o.value === selected[0])?.label ?? label
        : `${label} · ${selected.length}`;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          {triggerLabel}
          <ChevronsUpDown className="h-3 w-3 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        <Command>
          <CommandInput placeholder={`Filter ${label.toLowerCase()}…`} />
          <CommandList>
            <CommandEmpty>
              {emptyHint ?? `No ${label.toLowerCase()} found.`}
            </CommandEmpty>
            <CommandGroup>
              {options.map((opt) => {
                const isSelected = selected.includes(opt.value);
                return (
                  <CommandItem
                    key={String(opt.value)}
                    onSelect={() => toggle(opt.value)}
                    className="cursor-pointer"
                  >
                    <Check
                      className={cn(
                        "mr-2 h-4 w-4",
                        isSelected ? "opacity-100" : "opacity-0",
                      )}
                    />
                    <span className="flex-1">{opt.label}</span>
                    {opt.hint && (
                      <span className="text-xs text-muted-foreground">
                        {opt.hint}
                      </span>
                    )}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
