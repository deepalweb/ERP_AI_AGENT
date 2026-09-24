// Visual Investigation V2: the real stuck_stock_count case's redesigned
// Case Detail, plus regression checks on the existing inventory/revenue
// panels. Same isolated-temp-app-DB harness as every other spec here.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;
test.describe.configure({ mode: "serial" });

let ready = false;
let skipReason = "";
let stuckCaseId = null;
let inventoryCaseId = null;
let inventorySkipReason = "";
let revenueCaseId = null;
let revenueSkipReason = "";

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

  const stuckCases = (await (await request.get(`${BACKEND_BASE_URL}/cases?metric_id=stuck_stock_count&limit=5`)).json()).items ?? [];
  if (stuckCases.length === 0) {
    skipReason = "No real stuck_stock_count case from today's scan.";
    return;
  }
  stuckCaseId = stuckCases[0].case_id;
  ready = true;

  const invCases = (await (await request.get(`${BACKEND_BASE_URL}/cases?metric_id=top_seller_stock&limit=5`)).json()).items ?? [];
  if (invCases.length === 0) inventorySkipReason = "No real top_seller_stock case today.";
  else inventoryCaseId = invCases[0].case_id;

  const revCases = (await (await request.get(`${BACKEND_BASE_URL}/cases?metric_id=revenue&limit=5`)).json()).items ?? [];
  if (revCases.length === 0) revenueSkipReason = "No real revenue case today.";
  else revenueCaseId = revCases[0].case_id;
});

test("Stuck stock-count case: redesigned page, real structured data, corrected wording (desktop + mobile)", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stuckCaseId)}`);
  await expect(page.getByRole("heading", { name: "Executive summary" })).toBeVisible({ timeout: 15_000 });

  // Situation at a glance: the 4 real KPIs, no fabricated zeros.
  await expect(page.getByText("Unfinished count sessions")).toBeVisible();
  await expect(page.getByText("Estimated value of unscanned lines", { exact: true })).toBeVisible();
  await expect(page.getByText("Largest affected outlet")).toBeVisible();
  await expect(page.getByText("Oldest unfinished session")).toBeVisible();

  // Visual investigation: A/B/C from the new StockCountInvestigationPanel.
  await expect(page.getByRole("heading", { name: "Visual investigation" })).toBeVisible();
  await expect(page.getByText(/A\. Unfinished sessions over time/)).toBeVisible();
  await expect(page.getByText(/B\. Estimated value of unscanned lines by outlet/)).toBeVisible();
  await expect(page.getByText(/C\. Affected count sessions/)).toBeVisible();
  // The affected-session table shows a real session id and outlet.
  await expect(page.getByText("SC/2026/00428").first()).toBeVisible();

  // Task 1 correction: "confirmed cause" overclaim removed.
  await expect(page.getByText(/underlying cause is confirmed/i)).toHaveCount(0);
  await expect(page.getByText(/confirms where this is concentrated/i).first()).toBeVisible();

  // Incident Investigation Phase 6: no longer prescribes a specific fix
  // (complete/void) -- points at evidence review + an authorized decision.
  await expect(page.getByText(/complete it for a real count/i)).toHaveCount(0);
  await expect(page.getByText(/authorized disposition decision/i).first()).toBeVisible();

  // History behind an expander, not dumped inline.
  await expect(page.getByText(/Case history \(\d+ event/)).toBeVisible();

  await page.screenshot({ path: "test-results-real/v2-stuck-stock-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/v2-stuck-stock-mobile.png", fullPage: true });
});

test("Recommendation acceptance and action recording still work end to end on the redesigned page", async ({ page, request }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stuckCaseId)}`);
  await expect(page.getByRole("heading", { name: "Proposed by Celeste" })).toBeVisible({ timeout: 15_000 });

  const actionsHeading = page.getByRole("heading", { name: /Actions taken \(\d+\)/ });
  const before = await actionsHeading.textContent();
  await page.getByRole("button", { name: "Accept recommendation" }).first().click();
  await expect(page.getByText("Accepted").first()).toBeVisible({ timeout: 15_000 });
  await expect(actionsHeading).toHaveText(before);

  const caseDetail = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(stuckCaseId)}`)).json();
  expect((caseDetail.history ?? []).some((h) => h.event_type === "recommendation_approved")).toBe(true);
});

test("Regression: inventory case still renders correctly (desktop)", async ({ page }) => {
  test.skip(!inventoryCaseId, inventorySkipReason);
  await page.goto(`/investigate/${encodeURIComponent(inventoryCaseId)}`);
  await expect(page.getByRole("heading", { name: "Visual investigation" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/not reduced for stock already reserved/i)).toBeVisible();
  await page.screenshot({ path: "test-results-real/v2-inventory-regression-desktop.png", fullPage: true });
});

test("Regression: revenue case still renders correctly (desktop)", async ({ page }) => {
  test.skip(!revenueCaseId, revenueSkipReason);
  await page.goto(`/investigate/${encodeURIComponent(revenueCaseId)}`);
  await expect(page.getByRole("heading", { name: "Visual investigation" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/What changed alongside revenue/i)).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "test-results-real/v2-revenue-regression-desktop.png", fullPage: true });
});
