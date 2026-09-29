import { test, expect } from "@playwright/test";
import { authStatus, storeScope } from "./fixtures.js";

test("September is visible with matched dates, unavailable sources and explicit refresh", async ({ page }) => {
  let refreshes = 0;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body = { status: "ok", items: [], total: 0 };
    if (url.pathname.endsWith("/auth/status")) body = authStatus;
    if (url.pathname.endsWith("/store-scope")) body = storeScope;
    if (url.pathname.endsWith("/intelligence/current-period")) {
      if (url.searchParams.get("refresh") === "true") refreshes++;
      body = { status: "ok", checked_at: "2026-09-28T06:00:00Z", items: [
        { metric_id: "revenue", metric: { label: "Revenue", unit: "currency" }, scope: "Celeste outlets", status: "measured", category: "critical", provisional: true, freshness: "fresh", source_data_through: "2026-09-27T11:04:58", trend: [{ month: "2026-07", value: 100 }, { month: "2026-08", value: 120 }, { month: "2026-09", value: 60 }], confirm: { period: "2026-09", current: 60, baseline: 110, pct_change: -45.45, unit: "currency" }, comparison: { start: "2026-09-01T00:00:00", end_exclusive: "2026-09-27T11:04:58.000001", method: "Mean of earlier months over matching elapsed durations.", baseline_windows: [{ start: "2026-08-01T00:00:00", end_exclusive: "2026-08-27T11:04:58.000001" }], excluded_short_months: [] } },
        { metric_id: "order_volume", metric: { label: "Confirmed order volume", unit: "count" }, scope: "Celeste outlets", status: "measured", category: "critical", freshness: "stale", source_data_through: "2026-09-27T11:04:58", confirm: { period: "2026-09", current: 0, baseline: 10, unit: "count" } },
        { metric_id: "supplier_fill_rate", metric: { label: "Supplier on-time fill rate" }, scope: "Business-wide purchasing", status: "unavailable", category: null, reason: "Source measurement unavailable." },
      ] };
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/investigate");
  const region = page.getByRole("region", { name: "Current period investigations" });
  await expect(region.getByText("Provisional", { exact: true })).toBeVisible();
  await expect(region.getByText("Source unavailable", { exact: true })).toBeVisible();
  const revenue = region.getByRole("article", { name: "Revenue", exact: true });
  await expect(revenue.getByRole("figure", { name: "Revenue comparison chart" })).toBeVisible();
  const zero = region.getByRole("article", { name: "Confirmed order volume", exact: true });
  await expect(zero.getByRole("figure")).toBeVisible();
  await zero.getByText("Chart values", { exact: true }).click();
  await expect(zero.getByRole("row", { name: "Current 0", exact: true })).toBeVisible();
  await zero.getByText("Chart values", { exact: true }).click();
  await expect(region.getByRole("article", { name: "Supplier on-time fill rate" }).getByRole("figure")).toHaveCount(0);
  await revenue.getByText("Chart values", { exact: true }).click();
  await expect(revenue.getByRole("row", { name: "Sep 2026 (current) Rs 60" })).toBeVisible();
  await revenue.getByText("Chart values", { exact: true }).click();
  await revenue.getByText("View dates and comparison", { exact: true }).click();
  await expect(region.locator("p").filter({ hasText: "Period measured:" })).toContainText("2026-09-01");
  await expect(region.locator("p").filter({ hasText: "Period measured:" })).toContainText("2026-09-27");
  await region.getByText("Baseline date windows (1)", { exact: true }).click();
  await expect(region.getByText(/2026-08-01.*2026-08-27/)).toBeVisible();
  await region.getByRole("button", { name: "Check latest data" }).click();
  await expect.poll(() => refreshes).toBe(1);
  await expect(region.getByRole("button", { name: "Check latest data" })).toBeEnabled();
  await page.screenshot({ path: "test-results/current-period-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results/current-period-mobile.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
