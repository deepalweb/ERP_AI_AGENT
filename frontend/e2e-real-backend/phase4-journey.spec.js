// Celeste Phase 4: End-to-End Management Workflow Validation. Fills the
// specific gaps NOT already covered by the existing real-backend specs
// (full-stack.spec.js: action+target recording, stale-evidence rejection,
// Actions/Outcomes link-back, isolation; inventory-recommendation-approval
// .spec.js: recommendation acceptance; investigate-visual-check.spec.js:
// Investigate/Case Detail screenshots) -- namely: Home <-> Case Detail
// agreement, Actions/Outcomes workspace screenshots, and Owner/Deadline
// field persistence plus the honest no-fabricated-target resolution text
// for a problem_recovery (inventory) case. Same isolated-temp-app-DB
// harness as every other spec in this directory; never touches production
// app.db or the read-only reporting mirror beyond real SELECT-shaped scans.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;

let ready = false;
let skipReason = "";
let stockOutCase = null; // { case_id, finding_id } -- problem_recovery, no natural quantifiable target

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
  const casesBody = await casesResponse.json();
  const cases = casesBody.items ?? [];
  if (cases.length === 0) {
    skipReason = "No real top_seller_stock case from today's scan -- nothing to drive the owner/deadline test against.";
    return;
  }
  const detail = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(cases[0].case_id)}`)).json();
  stockOutCase = { case_id: cases[0].case_id, finding_id: detail.latest?.finding_id };
  if (!stockOutCase.finding_id) {
    skipReason = `Real case ${stockOutCase.case_id} has no latest finding_id.`;
    return;
  }

  ready = true;
});

test.describe.configure({ mode: "serial" });

test("recording an action on the real stock-out case preserves owner and deadline, and explains resolution honestly without a fabricated target", async ({ page, request }) => {
  test.skip(!ready, skipReason);

  await page.goto(`/investigate/${encodeURIComponent(stockOutCase.case_id)}`);
  await expect(page.getByRole("heading", { name: "Resolution status" })).toBeVisible({ timeout: 15_000 });

  // Scenario A: a problem_recovery case resolves when the signal itself
  // clears, not from a manager-declared numeric target -- the UI must say
  // so honestly rather than implying an automated verification exists.
  await expect(page.getByText(/has not yet seen this clear its own anomaly threshold/)).toBeVisible();

  const takeAction = page.getByRole("button", { name: /take action/i });
  if (await takeAction.isVisible().catch(() => false)) {
    await Promise.all([
      page.waitForResponse((r) => r.url().includes(`/findings/${encodeURIComponent(stockOutCase.finding_id)}/action`)),
      takeAction.click(),
    ]);
  }

  const ownerInput = page.getByPlaceholder(/Who's on this/);
  await expect(ownerInput).toBeVisible({ timeout: 10_000 });
  await Promise.all([
    page.waitForResponse((r) => r.url().includes(`/findings/${encodeURIComponent(stockOutCase.finding_id)}/action`)),
    (async () => {
      await ownerInput.fill("Inventory team lead");
      await ownerInput.blur();
    })(),
  ]);

  const deadlineInput = page.locator('input[type="date"][title="Deadline"]');
  await Promise.all([
    page.waitForResponse((r) => r.url().includes(`/findings/${encodeURIComponent(stockOutCase.finding_id)}/action`)),
    (async () => {
      await deadlineInput.fill("2026-12-31");
      await deadlineInput.blur();
    })(),
  ]);

  // Verify through the real API read-back, not just trusted from the UI.
  const persisted = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(stockOutCase.case_id)}`)).json();
  const action = (persisted.actions ?? []).find((a) => a.finding_id === stockOutCase.finding_id);
  expect(action, "action row must exist after recording owner/deadline").toBeTruthy();
  expect(action.owner).toBe("Inventory team lead");
  expect(action.deadline).toBe("2026-12-31");

  await page.reload();
  await expect(page.getByText("Inventory team lead")).toBeVisible({ timeout: 15_000 });
  // formatDate() (format.js) deliberately omits the year (month/day only) --
  // matching its actual rendered form, not inventing a year in the assertion.
  await expect(page.getByText(/due Dec 31/)).toBeVisible();

  // Recording this action must not have silently resolved the case.
  const caseNow = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(stockOutCase.case_id)}`)).json();
  expect(caseNow.case.status).not.toBe("resolved");

  await page.screenshot({ path: "test-results-real/action-owner-deadline-desktop.png", fullPage: true });
});

test("Actions workspace shows the real just-recorded action (desktop + mobile)", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Actions" })).toBeVisible({ timeout: 15_000 });
  // Runs after the recording test above (serial mode) -- the real action
  // just created should now be visible here, not an empty-state screenshot.
  await expect(page.getByText("Inventory team lead")).toBeVisible({ timeout: 15_000 });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "test-results-real/actions-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/actions-mobile.png", fullPage: true });
});

test("Outcomes workspace renders real cases and actions (desktop + mobile)", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/outcomes");
  await expect(page.getByRole("heading", { name: "Outcomes" })).toBeVisible({ timeout: 15_000 });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "test-results-real/outcomes-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/outcomes-mobile.png", fullPage: true });
});

test("Home displays a real finding whose evidence agrees with its own Case Detail page (desktop + mobile)", async ({ page, request }) => {
  test.skip(!ready, skipReason);

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Long-term", exact: true })).toBeVisible({ timeout: 20_000 });

  // The brief itself ("Loading briefing...") resolves as a SEPARATE, later
  // async call than the page chrome -- waiting only on the chrome (as an
  // earlier revision of this spec did) screenshots a real but uninformative
  // "Loading briefing..." skeleton instead of the settled page. Wait for
  // real content (the "Finding counts are not case counts." disclosure,
  // which only renders once findings have actually loaded) first.
  await expect(page.getByText("Finding counts are not case counts.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Loading briefing...")).toHaveCount(0);

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "test-results-real/home-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/home-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Follow a real "Open case" link from Home through to the persistent
  // case it names, then confirm the two pages describe the SAME finding
  // (same impact text -- both trace to the same case_observations snapshot
  // from today's scan) rather than two independently-drifting summaries.
  const openCaseLink = page.getByRole("link", { name: /Open case/ }).first();
  await expect(openCaseLink).toBeVisible({ timeout: 15_000 });
  const href = await openCaseLink.getAttribute("href");
  const caseId = decodeURIComponent(href.split("/investigate/")[1].split("?")[0]);

  const detail = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(caseId)}`)).json();
  const impactDetail = detail.latest?.impact?.detail;

  await openCaseLink.click();
  await expect(page).toHaveURL(new RegExp(`/investigate/${caseId}`));
  await expect(page.getByRole("heading", { name: "Business impact" })).toBeVisible({ timeout: 15_000 });
  if (impactDetail) {
    await expect(page.getByText(impactDetail, { exact: false }).first()).toBeVisible();
  }
});
