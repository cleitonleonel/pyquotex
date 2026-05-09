import type { Metadata } from "next";
import { Providers } from "./providers";
import { ThemeProvider } from "@/components/theme-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Autotrader",
  description: "Telegram-driven autotrader for pyquotex",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // ``suppressHydrationWarning`` is required by next-themes: the provider
  // injects the class on the client before React hydration completes, so
  // the server-rendered <html> momentarily differs from the client
  // tree. The warning is the documented escape valve.
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <Providers>{children}</Providers>
        </ThemeProvider>
      </body>
    </html>
  );
}
