"use client";

/**
 * Sidebar-footer user menu. The "user" today is whoever holds the
 * bearer token in localStorage — there is no /me endpoint yet — so
 * we show a generic label and a single Sign out action that clears
 * the token (via the existing api.logout helper) and bounces to
 * /login.
 *
 * Phase 3 §11 deferral: closes the gap that the dashboard had no
 * visible way to drop the operator session — the only logout button
 * lived under /dashboard/telegram and signed out of *Telegram*, not
 * the operator session.
 */

import { LogOut, User } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { logout } from "@/lib/api";

export function UserMenu() {
  const router = useRouter();

  const onSignOut = () => {
    logout();
    router.push("/login");
    router.refresh();
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start gap-2"
          aria-label="Account menu"
        >
          <User className="h-4 w-4" />
          <span className="text-sm group-data-[collapsible=icon]:hidden">
            Operator
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem disabled className="text-xs text-muted-foreground">
          Signed in via bearer token
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onSignOut}>
          <LogOut className="mr-2 h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
