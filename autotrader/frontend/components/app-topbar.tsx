"use client";

/**
 * Top bar above the main content area. Holds:
 *   - SidebarTrigger (collapse / expand the rail; shadcn primitive)
 *   - breadcrumb (group label + current page label)
 *   - GlobalStatusPill on the right
 *
 * Page title comes from the URL — we don't pass it as a prop because
 * leaving title management with the page itself (via <h2>) keeps the
 * topbar dumb.
 */

import { usePathname } from "next/navigation";
import { GlobalStatusPill } from "@/components/global-status-pill";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

interface PageMeta {
  group: "Trade" | "Configure";
  label: string;
}

const PAGE_META: Record<string, PageMeta> = {
  "/dashboard": { group: "Trade", label: "Overview" },
  "/dashboard/analytics": { group: "Trade", label: "Analytics" },
  "/dashboard/trades": { group: "Trade", label: "Trades" },
  "/dashboard/decisions": { group: "Trade", label: "Decisions" },
  "/dashboard/pipeline": { group: "Trade", label: "Pipeline" },
  "/dashboard/parsers": { group: "Configure", label: "Parsers" },
  "/dashboard/telegram": { group: "Configure", label: "Telegram" },
  "/dashboard/broker": { group: "Configure", label: "Broker" },
};

function resolveMeta(pathname: string): PageMeta {
  // Exact match first; fall back to prefix match for nested routes
  // like /dashboard/parsers/123.
  if (PAGE_META[pathname]) return PAGE_META[pathname];
  for (const [href, meta] of Object.entries(PAGE_META)) {
    if (href !== "/dashboard" && pathname.startsWith(`${href}/`)) {
      return meta;
    }
  }
  return { group: "Trade", label: "" };
}

export function AppTopbar() {
  const pathname = usePathname();
  const meta = resolveMeta(pathname);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="h-5" />
      <div className="flex flex-col">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {meta.group}
        </span>
        <span className="text-sm font-semibold leading-none tracking-tight">
          {meta.label}
        </span>
      </div>
      <div className="ml-auto">
        <GlobalStatusPill />
      </div>
    </header>
  );
}
