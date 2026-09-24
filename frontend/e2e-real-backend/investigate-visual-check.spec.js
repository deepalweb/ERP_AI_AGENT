// Read-only visual check for the Investigate Phase 1 review: renders the
// real Investigate library and a real Case Detail page against the real
// backend/reporting DB (isolated temp app DB, same harness as
// full-stack.spec.js -- see playwright.real-backend.config.js), and saves
// desktop + mobile screenshots. Never clicks a write control (no actions,
// no dismiss/reopen) -- the only writes that happen are the real scan's
// own case-sync, the same safe isolated-DB write path full-stack.spec.js
// already relies on.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;

let ready = false;
let skipReason = "";
let evidenceCase = null; // a case with real root_cause checks, for the evidence rollup / executive conclusion
let noActionCase = null; // any case, to check the stepper's un-reached-stage handling

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

  const casesResponse = await request.get(`${BACKEND_BASE_URL}/cases?limit=100`);
  const casesBody = await casesResponse.json();
  const cases = casesBody.items ?? [];
  if (cases.length === 0) {
    skipReason = "No real cases from today's scan -- nothing to screenshot.";
    return;
  }

  evidenceCase = cases.find((c) => c.latest?.root_cause?.checks?.length > 0) ?? cases[0];
  noActionCase = cases[0];
  ready = true;
});

test("Investigate library renders (desktop + mobile)", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/investigate");
  await expect(page.getByRole("heading", { name: "Investigate" })).toBeVisible({ timeout: 15_000 });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: "test-results-real/investigate-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/investigate-mobile.png", fullPage: true });
});

test("Case Detail renders the executive conclusion, evidence rollup, and stepper (desktop + mobile)", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(evidenceCase.case_id)}`);
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Case evolution" })).toBeVisible();

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: "test-results-real/case-detail-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/case-detail-mobile.png", fullPage: true });
});

test("a second, possibly different-shaped case also renders without crashing (desktop)", async ({ page }) => {
  test.skip(!ready, skipReason);
  test.skip(!noActionCase || noActionCase.case_id === evidenceCase?.case_id, "No distinct second case today.");
  await page.goto(`/investigate/${encodeURIComponent(noActionCase.case_id)}`);
  await expect(page.getByRole("heading", { name: "Case evolution" })).toBeVisible({ timeout: 15_000 });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: "test-results-real/case-detail-2-desktop.png", fullPage: true });
});
