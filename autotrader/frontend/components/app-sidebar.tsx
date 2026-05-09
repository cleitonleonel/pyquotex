"use client";

/**
 * The new dashboard sidebar — collapsible icon rail, two grouped
 * sections (Trade / Configure), version + theme toggle in the footer.
 *
 * Built on shadcn's <Sidebar> primitive (added in Task 5), which gives
 * us responsive collapse behavior, a mobile sheet fallback, and the
 * SidebarProvider/SidebarTrigger pair for the topbar collapse button.
 */

import {
  Activity,
  AreaChart,
  Headphones,
  Landmark,
  LayoutDashboard,
  ListChecks,
  ScrollText,
  Target,
  Wind,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { ThemeToggle } from "@/components/theme-toggle";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const TRADE_NAV: NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/dashboard/analytics", label: "Analytics", icon: AreaChart },
  { href: "/dashboard/trades", label: "Trades", icon: ListChecks },
  { href: "/dashboard/decisions", label: "Decisions", icon: Wind },
  { href: "/dashboard/pipeline", label: "Pipeline", icon: Activity },
];

const CONFIG_NAV: NavItem[] = [
  { href: "/dashboard/parsers", label: "Parsers", icon: Target },
  { href: "/dashboard/telegram", label: "Telegram", icon: Headphones },
  { href: "/dashboard/broker", label: "Broker", icon: Landmark },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppSidebar() {
  const pathname = usePathname();
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <Link
          href="/dashboard"
          className="flex items-center gap-2 px-2 py-1.5 font-semibold tracking-tight"
        >
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-success text-success-foreground">
            <ScrollText className="h-4 w-4" />
          </div>
          <span className="group-data-[collapsible=icon]:hidden">
            Autotrader
          </span>
        </Link>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Trade</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {TRADE_NAV.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton
                    asChild
                    isActive={isActive(pathname, item.href)}
                    tooltip={item.label}
                  >
                    <Link href={item.href}>
                      <item.icon className="h-4 w-4" />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>Configure</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {CONFIG_NAV.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton
                    asChild
                    isActive={isActive(pathname, item.href)}
                    tooltip={item.label}
                  >
                    <Link href={item.href}>
                      <item.icon className="h-4 w-4" />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <ThemeToggle />
      </SidebarFooter>
    </Sidebar>
  );
}
