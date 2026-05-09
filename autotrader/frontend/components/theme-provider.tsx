"use client";

/**
 * Thin wrapper around next-themes that:
 * - uses the `class` attribute strategy (matches our globals.css `.dark` block)
 * - defaults to "system" so a fresh load follows OS preference
 * - persists the user's pick to localStorage under the next-themes default key
 *
 * Mounted once, in the root layout, above all other providers.
 *
 * `attribute="class"` is a hard override — it's coupled to the `.dark {}`
 * block in globals.css, so callers MUST NOT override it. The other props
 * are soft defaults a caller may legitimately replace.
 */

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

export function ThemeProvider(
  props: ComponentProps<typeof NextThemesProvider>,
) {
  return (
    <NextThemesProvider
      {...props}
      attribute="class"
      defaultTheme={props.defaultTheme ?? "system"}
      enableSystem={props.enableSystem ?? true}
      disableTransitionOnChange={props.disableTransitionOnChange ?? true}
    />
  );
}
