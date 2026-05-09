"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppSidebar } from "@/components/app-sidebar";
import { AppTopbar } from "@/components/app-topbar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { getToken } from "@/lib/api";

/**
 * shadcn's <SidebarProvider> writes the collapsed/expanded preference to
 * document.cookie on every toggle but does NOT read it back on mount —
 * the caller is expected to seed `defaultOpen` from the cookie. In a
 * Server Component you'd read it via next/headers; here the layout is
 * forced client-side by the auth gate, so we read the cookie directly
 * in a useState initializer (sync, before first paint).
 */
function readSidebarCookie(): boolean {
  if (typeof document === "undefined") return true;
  const match = document.cookie.match(/(?:^|;\s*)sidebar_state=([^;]+)/);
  return match ? match[1] === "true" : true;
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [sidebarOpen] = useState(readSidebarCookie);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
    } else {
      setAuthed(true);
    }
  }, [router]);

  if (!authed) return null;

  return (
    <SidebarProvider defaultOpen={sidebarOpen}>
      <AppSidebar />
      <SidebarInset>
        <AppTopbar />
        <main className="mx-auto w-full max-w-7xl px-6 py-8">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  );
}
