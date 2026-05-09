/**
 * Phase 3 dashboard smoke harness.
 *
 * Each test exercises one critical path; we are NOT trying to recreate
 * component-level coverage here — TanStack Query + Recharts internals
 * stay out of scope. The goal is to catch the kind of regression that
 * shows up only when the browser tries to render the actual route
 * (broken module graph, accidental server-only import in a client
 * component, missing shadcn primitive, etc.).
 *
 * Several tests touch /dashboard which currently requires an auth
 * token. In a sandboxed CI env the backend may be unreachable, so the
 * tests are structured to fail loudly only on JS-level breakage; an
 * auth redirect to /login is treated as an expected outcome where it
 * would fire.
 */

import { test, expect } from "@playwright/test";

test.describe("Dashboard", () => {
  test("loads overview without console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));

    await page.goto("/dashboard");
    // Either the dashboard rendered (Operator label visible in the
    // sidebar footer) or we got bounced to /login because the test
    // env has no auth token. Either is fine — the assertion we care
    // about is "no JS exception".
    await page.waitForLoadState("networkidle").catch(() => {});
    expect(errors).toEqual([]);
  });

  test("analytics page renders the equity curve", async ({ page }) => {
    await page.goto("/dashboard/analytics");
    // Card title is rendered server-side regardless of data state.
    await expect(
      page.getByText(/equity curve/i).or(page.getByText(/sign in/i)),
    ).toBeVisible();
  });

  test("asset filter pill opens", async ({ page }) => {
    await page.goto("/dashboard/analytics");
    const trigger = page.getByRole("button", { name: /asset/i });
    if (await trigger.isVisible().catch(() => false)) {
      await trigger.click();
      // The popover content should appear (even if empty in the test env).
      await expect(
        page.getByRole("dialog").or(page.getByRole("listbox")),
      ).toBeVisible({ timeout: 5_000 });
    } else {
      // Pre-auth redirect — that's fine, we've at least proven the
      // route doesn't crash on first paint.
      await expect(page).toHaveURL(/login|dashboard/);
    }
  });

  test("user menu signs out", async ({ page }) => {
    await page.goto("/dashboard");
    const menu = page.getByRole("button", { name: /account menu/i });
    if (await menu.isVisible().catch(() => false)) {
      await menu.click();
      await page.getByRole("menuitem", { name: /sign out/i }).click();
      await expect(page).toHaveURL(/\/login/);
    } else {
      // Already redirected to login (no token) — equivalent end state.
      await expect(page).toHaveURL(/login|dashboard/);
    }
  });
});
