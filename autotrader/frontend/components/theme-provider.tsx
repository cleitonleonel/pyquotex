"use client";

/**
 * Thin wrapper around next-themes that:
 * - uses the `class` attribute strategy (matches our globals.css `.dark` block)
 * - defaults to "system" so a fresh load follows OS preference
 * - persists the user's pick to localStorage under the next-themes default key
 *
 * Mounted once, in the root layout, above all other providers.
 */

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

export function ThemeProvider(
  props: ComponentProps<typeof NextThemesProvider>,
) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      {...props}
    />
  );
}
