// Incident Investigation: the real stuck_stock_count case's session-level
// drill-in (Phase 5), corrected recommendation wording (Phase 6), and the
// stale-snapshot honesty fix (Phase 1/2). Same isolated-temp-app-DB harness
// as every other spec here -- a fresh real scan, wiped app.db.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;
test.describe.configure({ mode: "serial" });

let ready = false;
let skipReason = "";
let stuckCaseId = null;

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
});

test("Session table shows real Responsible names, sorts, and drills into a real session's detail", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stuckCaseId)}`);
  await expect(page.getByRole("heading", { name: "Visual investigation" })).toBeVisible({ timeout: 15_000 });

  // The stale-snapshot fallback text must NOT appear on a freshly-scanned case.
  await expect(page.getByText(/Session-level detail wasn't captured/i)).toHaveCount(0);
  await expect(page.getByText(/Evidence unavailable/i)).toHaveCount(0);

  // Real session row: SC/2026/00428, a real Responsible name (not blank/undefined).
  const row = page.getByRole("row", { name: /SC\/2026\/00428/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("Dilanka Ranasinghe")).toBeVisible();

  // Final Stock-Count Financial Disclosure: coverage shown beside the KPI, unpriced lines named "unknown," not "small."
  await expect(page.getByText("Estimated value of unscanned lines", { exact: true })).toBeVisible();
  await expect(page.getByText(/unpriced, value unknown/i)).toBeVisible();

  // Sort toggle changes ordering -- click "Sort by age" and confirm it's the active pill.
  await page.getByRole("button", { name: "Sort by age" }).click();
  await expect(page.getByRole("button", { name: "Sort by age" })).toHaveClass(/text-accent/);

  // Drill into SC/2026/00428's own session-detail panel.
  await row.getByText("Investigate →").click();
  await expect(page.getByText("Session investigation -- SC/2026/00428")).toBeVisible({ timeout: 10_000 });

  // People/roles: responsible vs. created_by shown as genuinely distinct fields.
  await expect(page.getByText("Responsible (session owner):")).toBeVisible();
  await expect(page.getByText("Record created by:")).toBeVisible();
  await expect(page.getByText(/These are different people on record/i)).toBeVisible();

  // This real session has zero scan activity -- must say so honestly, not fabricate a timeline.
  await expect(page.getByText("No scan activity has been recorded for this session.")).toBeVisible();

  // Established vs. unknown block present.
  await expect(page.getByText("Established vs. unknown")).toBeVisible();

  // Final Integrity Check: live-retrieval timestamp is labeled distinctly from the case's preserved evidence.
  await expect(page.getByText(/Current source-system data, retrieved/i)).toBeVisible();
  await expect(page.getByText(/distinct from the case's own preserved evidence/i)).toBeVisible();

  // Final Integrity Check: the per-line table exposes its valuation methodology and reconciliation caveat.
  await page.getByText(/Show all \d+ count line\(s\)/).click();
  await expect(page.getByText(/will not simply add up to the "Estimated value of unscanned lines"/i)).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Valuation basis" })).toBeVisible();

  await page.screenshot({ path: "test-results-real/incident-investigation-session-detail-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results-real/incident-investigation-session-detail-mobile.png", fullPage: true });
});

test("Leading recommendation reviews evidence and requires an authorized decision, never prescribes complete/void", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stuckCaseId)}`);
  await expect(page.getByRole("heading", { name: "Proposed by Celeste" })).toBeVisible({ timeout: 15_000 });

  await expect(page.getByText(/complete it for a real count/i)).toHaveCount(0);
  await expect(page.getByText(/void it and schedule a recount/i)).toHaveCount(0);
  await expect(page.getByText(/authorized disposition decision/i).first()).toBeVisible();
});
