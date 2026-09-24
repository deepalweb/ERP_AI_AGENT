// Read-only visual check for the Celeste Investigate Phase 2 flagship
// workflow: the top-selling-product stock-out case detail, rendered
// against the real backend/reporting DB (isolated temp app DB -- same
// harness as investigate-visual-check.spec.js / full-stack.spec.js, see
// playwright.real-backend.config.js). Never clicks a write control -- no
// action approval, no dismiss/reopen -- the only writes that happen are
// the real scan's own case-sync, the same safe isolated-DB write path the
// other real-backend specs already rely on.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;

let ready = false;
let skipReason = "";
let stockOutCaseId = null;

test.beforeAll(async ({ request }) => {
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      rmSync(APPDB_SQLITE_PATH, { force: true });
      break;
    } catch (e) {
      if (e.code !== "EBUSY" || attempt === 5) throw e;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  let scanResponse;
  try {
    scanResponse = await request.get(`${BACKEND_BASE_URL}/investigations`, { timeout: SCAN_TIMEOUT_MS });
  } catch (e) {
    skipReason = `Real backend/reporting DB unreachable -- skipping. (${e.message})`;
    return;
  }
  if (!scanResponse.ok()) {
    skipReason = `Real scan request failed with HTTP ${scanResponse.status()} -- skipping.`;
    return;
  }

  const casesResponse = await request.get(`${BACKEND_BASE_URL}/cases?metric_id=top_seller_stock&limit=10`);
  const casesBody = await casesResponse.json();
  const cases = casesBody.items ?? [];
  if (cases.length === 0) {
    skipReason = "No real top_seller_stock case from today's scan -- nothing to screenshot (the finding only fires on a genuine stock-out among the top 15 sellers).";
    return;
  }

  stockOutCaseId = cases[0].case_id;
  ready = true;
});

test("Inventory stock-out case detail renders the flagship investigation workflow (desktop + mobile)", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stockOutCaseId)}`);

  // The fixed "n/a vs baseline" bug: a snapshot finding has no baseline at
  // all, so that literal string must never appear anywhere on this page.
  await expect(page.getByRole("heading", { name: "What happened" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("vs baseline")).toHaveCount(0);
  await expect(page.getByText(/n\/a vs baseline/)).toHaveCount(0);

  // The business-specific inventory panel (item 3) and the 5-category
  // evidence taxonomy (item 5) and 3-concept business impact (item 6).
  await expect(page.getByRole("heading", { name: "Affected products" })).toBeVisible();
  await expect(page.getByText("A. Measured finding")).toBeVisible();
  await expect(page.getByText("Operational magnitude")).toBeVisible();
  await expect(page.getByText("Internal impact score")).toBeVisible();
  await expect(page.getByText("Financial impact", { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.screenshot({ path: "test-results-real/inventory-case-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/inventory-case-mobile.png", fullPage: true });
});
