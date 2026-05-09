"use client";

/**
 * Light / Dark / System cycle button.
 *
 * Lives in the sidebar footer. We render a single button that cycles
 * through the three states rather than a dropdown — fewer clicks for
 * the most common toggle. The icon reflects the *resolved* theme so
 * the user can see at a glance what's currently applied (especially
 * useful when on "system").
 */

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

const ORDER = ["light", "dark", "system"] as const;

export function ThemeToggle() {
  const { theme, setTheme, resolvedTheme } = useTheme();
  // Hydration-safe: theme is undefined on the server. Render a neutral
  // placeholder until mounted so the SSR-emitted button matches the
  // first client paint.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return (
      <Button
        variant="ghost"
        size="sm"
        aria-label="Theme"
        className="w-full justify-start gap-2"
      >
        <Monitor className="h-4 w-4" /> Theme
      </Button>
    );
  }

  const current = (theme as (typeof ORDER)[number] | undefined) ?? "system";
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
  const Icon =
    current === "system"
      ? Monitor
      : (resolvedTheme ?? current) === "dark"
        ? Moon
        : Sun;
  const label =
    current === "system"
      ? `System (${resolvedTheme ?? "?"})`
      : current === "dark"
        ? "Dark"
        : "Light";

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => setTheme(next)}
      aria-label={`Switch theme — currently ${label}`}
      className="w-full justify-start gap-2"
    >
      <Icon className="h-4 w-4" /> {label}
    </Button>
  );
}
