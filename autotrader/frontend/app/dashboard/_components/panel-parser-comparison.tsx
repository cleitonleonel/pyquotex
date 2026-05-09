"use client";

/**
 * Parser comparison — sortable table of per-parser performance, including
 * the closed-loss-streak roll-up that comes back on dim=parser rows.
 *
 * Operators run multiple parsers per channel; this panel exists to make
 * "which parser is actually pulling its weight" first-class so the
 * laggards can be disabled. Confidence band reuses the same Wilson
 * helper as the channel leaderboard so the two read consistently.
 *
 * Source: /stats/v2/breakdown?dim=parser
 */

import { useQuery } from "@tanstack/react-query";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { type BreakdownRow, statsV2 } from "@/lib/api-stats-v2";
import { useFilters } from "@/lib/use-filters";
import {
  classifyConfidence,
  formatPnl,
  formatWinRate,
} from "@/lib/wilson-format";

export function PanelParserComparison() {
  const { filters } = useFilters();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["stats-v2", "breakdown", "parser", filters],
    queryFn: () => statsV2.breakdown(filters, "parser"),
    staleTime: 30_000,
  });

  const [sorting, setSorting] = useState<SortingState>([
    { id: "win_rate", desc: true },
  ]);

  const columns: ColumnDef<BreakdownRow>[] = [
    {
      accessorKey: "label",
      header: "Parser",
      cell: ({ row }) => (
        <span className="font-mono text-xs">{row.original.label}</span>
      ),
    },
    {
      accessorKey: "total",
      header: "N",
      cell: ({ row }) => (
        <span className="font-mono text-xs">{row.original.total}</span>
      ),
    },
    {
      accessorKey: "win_rate",
      header: "Win rate",
      cell: ({ row }) => (
        <span className="font-mono text-xs">
          {formatWinRate(row.original.win_rate)}
        </span>
      ),
      sortingFn: (a, b) =>
        (a.original.win_rate ?? -1) - (b.original.win_rate ?? -1),
    },
    {
      id: "confidence",
      header: "Conf.",
      cell: ({ row }) => {
        const band = classifyConfidence(
          row.original.won + row.original.lost,
          row.original.win_rate_ci_low,
          row.original.win_rate_ci_high,
        );
        const variant =
          band === "solid"
            ? "success"
            : band === "thin"
              ? "warning"
              : "secondary";
        const label =
          band === "solid" ? "solid" : band === "thin" ? "thin n" : "unsure";
        return <Badge variant={variant}>{label}</Badge>;
      },
    },
    {
      accessorKey: "realised_pnl",
      header: "P&L",
      cell: ({ row }) => {
        const pnl = row.original.realised_pnl;
        return (
          <span
            className={`font-mono text-xs tabular-nums ${
              pnl > 0
                ? "text-success"
                : pnl < 0
                  ? "text-destructive"
                  : ""
            }`}
          >
            {formatPnl(pnl)}
          </span>
        );
      },
    },
    {
      id: "longest_loss",
      header: "Longest loss",
      // dim=parser always carries `streaks`; default to 0 if backend ever
      // omits it so the column stays sortable instead of throwing.
      accessorFn: (r) => r.streaks?.longest_loss ?? 0,
      cell: ({ row }) => (
        <span className="font-mono text-xs">
          {row.original.streaks?.longest_loss ?? 0}
        </span>
      ),
    },
  ];

  const table = useReactTable({
    data: data?.rows ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Parser comparison</CardTitle>
        <CardDescription>
          Per-parser performance with Wilson 95% confidence band and the
          longest closed losing streak observed in this window. Click any
          column to sort. Disable the laggards.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
        {isError && (
          <p className="text-sm text-destructive">
            Couldn&rsquo;t load parsers.
          </p>
        )}
        {!isLoading && !isError && (data?.rows.length ?? 0) === 0 && (
          <p className="text-sm text-muted-foreground">
            No trades from any parser in this range.
          </p>
        )}
        {(data?.rows.length ?? 0) > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                {table.getHeaderGroups().map((hg) => (
                  <tr
                    key={hg.id}
                    className="border-b text-left text-muted-foreground"
                  >
                    {hg.headers.map((header) => (
                      <th
                        key={header.id}
                        onClick={header.column.getToggleSortingHandler()}
                        className={`px-2 py-1.5 font-medium ${
                          header.column.getCanSort()
                            ? "cursor-pointer select-none"
                            : ""
                        }`}
                      >
                        <span className="flex items-center gap-1">
                          {flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                          {header.column.getIsSorted() === "asc" && (
                            <ChevronUp className="h-3 w-3" />
                          )}
                          {header.column.getIsSorted() === "desc" && (
                            <ChevronDown className="h-3 w-3" />
                          )}
                        </span>
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="border-b last:border-0">
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-2 py-1.5">
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </td>
                    ))}
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
