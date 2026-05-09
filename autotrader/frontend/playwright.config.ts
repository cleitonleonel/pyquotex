/**
 * Playwright config for the autotrader dashboard smoke harness (Phase 3
 * Task 11). The webServer block boots Next.js via ``bun run dev`` so a
 * developer can run ``bun run test:e2e`` from a clean checkout — the
 * runner will reuse an already-running dev server when present.
 *
 * NOTE: the smoke specs assume the backend is reachable at the URL the
 * frontend's ``NEXT_PUBLIC_API_BASE`` env var points at (default
 * ``http://localhost:8000``). In a sandboxed CI env the backend may not
 * be available; the harness still validates that the frontend boots and
 * the route shells render, which is what guards us against accidentally
 * breaking the build with a panel reorg.
 */

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "bun run dev",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
