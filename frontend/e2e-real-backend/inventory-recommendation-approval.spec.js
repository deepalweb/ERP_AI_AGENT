// Celeste Investigate Phase 2, final correction ("Recommendation vs
// Action"): exercises the real "Accept recommendation" workflow end to
// end against the real backend/reporting DB, using the ISOLATED temp app
// DB this harness creates fresh every run (see playwright.real-backend.
// config.js) -- the one place in this suite that's allowed to write, and
// it never touches production app.db or the read-only reporting mirror.
//
// The behavior under test is deliberately the OPPOSITE of an earlier
// revision: accepting a recommendation is an auditable planning decision,
// never an operational action, so "Actions taken" must stay at zero
// throughout acceptance -- confirming no "phantom" finding_actions row
// gets created merely by accepting a proposal.
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
    skipReason = "No real top_seller_stock case from today's scan -- nothing to accept against.";
    return;
  }

  stockOutCaseId = cases[0].case_id;
  ready = true;
});

test("accepting two recommendations on the real stock-out case never creates a phantom action", async ({ page, request }) => {
  test.skip(!ready, skipReason);

  const detailResponse = await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(stockOutCaseId)}`);
  const detailBody = await detailResponse.json();
  const recommendations = detailBody.latest?.recommendations ?? [];
  test.skip(recommendations.length < 2, "Today's real case doesn't have at least 2 recommendations to test with.");
  test.skip((detailBody.actions ?? []).length > 0, "Today's real case already has a recorded action -- needs a clean zero-action start.");

  await page.goto(`/investigate/${encodeURIComponent(stockOutCaseId)}`);
  await expect(page.getByRole("heading", { name: "Proposed by Celeste" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: /Actions taken \(0\)/ })).toBeVisible();

  const acceptButtons = page.getByRole("button", { name: "Accept recommendation" });
  const firstCount = await acceptButtons.count();
  expect(firstCount).toBeGreaterThanOrEqual(2);

  // Accept the first recommendation and wait for its own card to flip to
  // "Accepted" -- confirms the click round-tripped through the real
  // evidence-version-checked acceptance endpoint, not just a local toggle.
  await acceptButtons.first().click();
  await expect(page.getByText("Accepted").first()).toBeVisible({ timeout: 15_000 });

  // The key corrected behavior: accepting a recommendation must NOT
  // create an operational action -- "Actions taken" stays at zero.
  await expect(page.getByRole("heading", { name: /Actions taken \(0\)/ })).toBeVisible();
  await expect(page.getByText("Recommendation approved").first()).toBeVisible();

  // Accept a second, different recommendation -- must not disturb the first.
  const remainingAcceptButtons = page.getByRole("button", { name: "Accept recommendation" });
  await remainingAcceptButtons.first().click();
  await expect(page.getByText("Accepted")).toHaveCount(2, { timeout: 15_000 });

  // Still zero action rows, and both acceptances are now visible as two
  // distinct history entries.
  await expect(page.getByRole("heading", { name: /Actions taken \(0\)/ })).toBeVisible();
  const historyCount = await page.getByText("Recommendation approved").count();
  expect(historyCount).toBe(2);

  await page.screenshot({ path: "test-results-real/recommendation-acceptance-desktop.png", fullPage: true });

  // Reload to confirm this is real, persisted server state -- not just
  // client-side optimistic UI that would vanish on a fresh load.
  await page.reload();
  await expect(page.getByText("Accepted")).toHaveCount(2, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: /Actions taken \(0\)/ })).toBeVisible();

  // "Record action" is a separate, always-available control -- clicking
  // it lands on the real tracker where a manager can record actual work,
  // never auto-created by accepting a recommendation.
  await expect(page.getByRole("link", { name: /Record action/ }).first()).toBeVisible();
  await page.getByRole("link", { name: /Record action/ }).first().click();
  await expect(page.getByRole("button", { name: "Take action" })).toBeInViewport();
});
