// Celeste Investigate Phase 3 (revenue investigation): read-only visual
// check against the real backend/reporting DB, using the isolated temp app
// DB this harness creates fresh every run (see playwright.real-backend.
// config.js). Never clicks a write control -- the only writes that happen
// are the real scan's own case-sync, the same safe isolated-DB write path
// the other real-backend specs already rely on.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;

let ready = false;
let skipReason = "";
let revenueCaseId = null;

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

  const casesResponse = await request.get(`${BACKEND_BASE_URL}/cases?metric_id=revenue&limit=10`);
  const casesBody = await casesResponse.json();
  const cases = casesBody.items ?? [];
  if (cases.length === 0) {
    skipReason = "No real revenue case from today's scan -- nothing to screenshot.";
    return;
  }

  revenueCaseId = cases[0].case_id;
  ready = true;
});

test("Revenue case detail renders the trend/contributor investigation workflow (desktop + mobile)", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(revenueCaseId)}`);

  await expect(page.getByRole("heading", { name: "What happened" })).toBeVisible({ timeout: 15_000 });
  // The fixed evidence_engine wording -- a concentration check now always
  // names the exact two months it compared, never a bare "the increase".
  await expect(page.getByRole("heading", { name: "Revenue trend and contributors" })).toBeVisible();
  await expect(page.getByText(/Baseline: mean of \d+ complete month/)).toBeVisible();
  // order_volume/AOV are two SEPARATE, independently-resolving LIVE
  // intelligence.investigate() calls (not the cached scan result) -- each
  // can cost several seconds on a cold call (documented elsewhere in this
  // project as 3-14s/metric). The section heading appears as soon as
  // EITHER resolves, so waiting on the heading alone (as this spec used
  // to) can screenshot a real but transient partial state -- AOV showing
  // with the Order volume line not painted in yet, since it's still
  // in-flight. Wait for both lines by name so the captured screenshot
  // reflects the page's actual settled state, not a race between two
  // independent queries.
  await expect(page.getByText("What changed alongside revenue")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Order volume:/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Average order value:/)).toBeVisible({ timeout: 30_000 });
  // A brief settle wait before screenshotting: Recharts repaints its SVG
  // when the order_volume/AOV section above resolves and reflows the
  // page (confirmed via direct DOM inspection -- the chart/bar SVG paths
  // are present correctly a moment after this text appears, just not in
  // the exact same paint as the just-arrived async content).
  await page.waitForTimeout(1000);

  await page.setViewportSize({ width: 1440, height: 1400 });
  await page.screenshot({ path: "test-results-real/revenue-case-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: "test-results-real/revenue-case-mobile.png", fullPage: true });
});
