// Metric Truthfulness Correction: verifies the corrected wording actually
// renders in the real browser against real data (isolated temp app DB,
// same harness as every other spec here) -- Task 1/2's inventory wording
// and Task 3's gross-margin terminology.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;

let ready = false;
let skipReason = "";
let stockOutCaseId = null;
let marginCaseId = null;
let marginSkipReason = "";

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

  const casesResponse = await request.get(`${BACKEND_BASE_URL}/cases?metric_id=top_seller_stock&limit=5`);
  const cases = (await casesResponse.json()).items ?? [];
  if (cases.length === 0) {
    skipReason = "No real top_seller_stock case from today's scan.";
    return;
  }
  stockOutCaseId = cases[0].case_id;
  ready = true;

  const marginResponse = await request.get(`${BACKEND_BASE_URL}/cases?metric_id=gross_margin&limit=5`);
  const marginCases = (await marginResponse.json()).items ?? [];
  if (marginCases.length === 0) {
    marginSkipReason = "No real gross_margin case from today's scan.";
  } else {
    marginCaseId = marginCases[0].case_id;
  }
});

test("Inventory case no longer claims verified out-of-stock; scope and negative-stock wording corrected", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stockOutCaseId)}`);
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible({ timeout: 15_000 });

  // Task 1: title/headline no longer claims "completely out of stock".
  await expect(page.getByText("completely out of stock")).toHaveCount(0);
  await expect(page.getByText(/recorded at zero or negative stock/i).first()).toBeVisible();

  // Task 1: the scope/measurement disclosure is shown in the investigation.
  await expect(page.getByText(/not reduced for stock already reserved/i)).toBeVisible();

  // Task 2: the negative-stock recommendation no longer states the cause as fact.
  await expect(page.getByText("sold past what's recorded on hand")).toHaveCount(0);
  await expect(page.getByText(/requires investigation/i).first()).toBeVisible();
  await expect(page.getByText(/reconcil/i).first()).toBeVisible();

  await page.screenshot({ path: "test-results-real/truthfulness-inventory-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/truthfulness-inventory-mobile.png", fullPage: true });
});

test("Gross-margin case shows corrected current-cost-basis terminology", async ({ page }) => {
  test.skip(!marginCaseId, marginSkipReason);
  await page.goto(`/investigate/${encodeURIComponent(marginCaseId)}`);
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible({ timeout: 15_000 });

  await expect(page.getByText(/Estimated gross margin/i).first()).toBeVisible();
  await expect(page.getByText(/current-cost basis/i).first()).toBeVisible();
  // The old bare "Gross margin" title must not appear anywhere.
  await expect(page.getByRole("heading", { name: /^Gross margin/ })).toHaveCount(0);

  await page.screenshot({ path: "test-results-real/truthfulness-margin-desktop.png", fullPage: true });
});
