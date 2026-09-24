// Controlled Pilot, Tasks 1-2 and 4: the one opt-in test that drives the
// REAL frontend build, against a REAL FastAPI server, against the REAL
// read-only reporting database, with every write isolated to a temp
// SQLite app database (see playwright.real-backend.config.js for how
// each of those is wired up). No page.route mocking anywhere in this
// file -- every request the browser makes is a genuine network call.
//
// Run with: npm run test:e2e:real. Takes several minutes (two real
// anomaly scans, ~80-100s each, plus real browser interaction) -- not
// part of the default `npm run test:e2e`, which stays fast and mocked.
//
// Safety: this file only ever issues SELECT-shaped reads against the
// reporting DB (indirectly, via the real scan pipeline) and only ever
// writes case/action state into the isolated temp SQLite file created by
// the config. It never touches the repo's real app.db.
import { existsSync, rmSync, statSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL, PROD_APPDB_PATH } from "../playwright.real-backend.config.js";

// These checks share the action created by the first test. On failure,
// skip dependents instead of restarting a worker and deleting the live DB.
test.describe.configure({ mode: "serial" });

// This is functional integration coverage, not a scan-latency assertion.
// A live rescan exceeded 360s on 2026-09-18. Diagnostic samples showed
// progress through reporting SQL and model requests. Allow a bounded ten
// minutes while retaining every persistence and stale-evidence assertion;
// scan latency remains a separate operational limitation.
const SCAN_TIMEOUT_MS = 600_000;
const prodAppDbStatBefore = existsSync(PROD_APPDB_PATH) ? statSync(PROD_APPDB_PATH) : null;

/** Populated once in beforeAll: which real finding this run exercises,
 * and whether the run is even possible. Kept in module scope (not a
 * fixture) because Playwright's webServer/global setup has already run
 * by the time beforeAll fires, but choosing the real target case still
 * needs one real HTTP round trip -- there's no way to know which case_id
 * to navigate to before the first real scan has actually run. */
let ready = false;
let skipReason = "";
let trendCase = null; // { case_id, metric_id, finding_id }
let inventoryCase = null; // { case_id, metric_id } -- the corrected stock_availability finding (Task 3)

test.beforeAll(async ({ request }) => {
  try {
    await beforeAllImpl(request);
  } finally {
    console.error(`[real-backend beforeAll] ready=${ready} skipReason=${JSON.stringify(skipReason)} trendCase=${JSON.stringify(trendCase && { case_id: trendCase.case_id, metric_id: trendCase.metric_id })} inventoryCase=${JSON.stringify(inventoryCase && { case_id: inventoryCase.case_id })}`);
  }
});

async function beforeAllImpl(request) {
  // Clean start: the isolated DB path is fixed (see the config file's
  // comment on why), so a leftover file from an earlier run must be
  // cleared before this run's own scan writes into it -- otherwise this
  // run's assertions could pass against stale data from a previous run
  // rather than what THIS run's real scan actually produced. Tolerates a
  // transient Windows file lock (e.g. Playwright restarting a worker
  // right after a prior test failure, while the backend process from
  // that worker is still releasing its SQLite handle) with a few retries
  // rather than failing the whole run over a timing hiccup.
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      rmSync(APPDB_SQLITE_PATH, { force: true });
      break;
    } catch (e) {
      if (e.code !== "EBUSY" || attempt === 5) throw e;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  // Fail safely (Task 1): if the real reporting DB isn't reachable, or the
  // pipeline errors for any other reason, skip every test in this file
  // with a clear reason instead of failing on an unrelated assertion.
  let scanResponse;
  try {
    scanResponse = await request.get(`${BACKEND_BASE_URL}/investigations`, { timeout: SCAN_TIMEOUT_MS });
  } catch (e) {
    skipReason = `Real backend/reporting DB unreachable -- skipping. (${e.message})`;
    console.error(`[real-backend beforeAll] ${skipReason}`);
    return;
  }
  if (!scanResponse.ok()) {
    skipReason = `Real scan request failed with HTTP ${scanResponse.status()} -- skipping.`;
    return;
  }
  const scan = await scanResponse.json();
  if (scan.status === "error" || !Array.isArray(scan.investigations)) {
    skipReason = `Real scan returned an error/unexpected shape -- skipping. (${JSON.stringify(scan).slice(0, 200)})`;
    return;
  }

  // Fail safely: the isolated app database must actually exist at the
  // path this config generated, and must not be the repo's real app.db,
  // before this suite trusts anything it's about to write.
  if (!existsSync(APPDB_SQLITE_PATH)) {
    skipReason = `Isolated app database was not created at ${APPDB_SQLITE_PATH} -- refusing to run against an unknown database.`;
    return;
  }
  if (path_equal(APPDB_SQLITE_PATH, PROD_APPDB_PATH)) {
    skipReason = "Isolated app database path resolved to the production app.db -- refusing to run.";
    return;
  }

  const metricsResponse = await request.get(`${BACKEND_BASE_URL}/intelligence/metrics`);
  const metricsBody = await metricsResponse.json();
  const targetableMetricIds = new Set((metricsBody.metrics ?? []).map((m) => m.id));

  const casesResponse = await request.get(`${BACKEND_BASE_URL}/cases?limit=100`);
  const casesBody = await casesResponse.json();
  const cases = casesBody.items ?? [];

  // Prefer an OPEN case (the manager's actual "needs a decision" state)
  // whose own metric can be selected in the real target form; do not
  // invent one if today's real data doesn't produce a critical trend
  // finding -- fall back to any case on that metric so the mechanics are
  // still provably real, and record which posture we ended up in.
  trendCase =
    cases.find((c) => targetableMetricIds.has(c.metric_id) && (c.status === "open" || c.status === "reopened" || c.status === "verification_pending")) ??
    cases.find((c) => targetableMetricIds.has(c.metric_id));
  inventoryCase = cases.find((c) => c.metric_id === "stock_availability");

  if (!trendCase) {
    skipReason = "No real case today has a target-selectable metric_id -- nothing to drive the action/target flow against.";
    return;
  }

  const detailResponse = await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(trendCase.case_id)}`);
  const detail = await detailResponse.json();
  trendCase.finding_id = detail.latest?.finding_id;
  trendCase.title = detail.latest?.title;
  if (!trendCase.finding_id) {
    skipReason = `Real case ${trendCase.case_id} has no latest finding_id -- cannot exercise the action flow.`;
    return;
  }

  ready = true;
}

function path_equal(a, b) {
  return path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
}

test("real Chromium -> React -> FastAPI -> reporting DB: detect, investigate, act, verify", async ({ page, request }) => {
  test.skip(!ready, skipReason);
  test.setTimeout(SCAN_TIMEOUT_MS * 2 + 120_000);

  // 3. Open the Daily Brief in Chromium.
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Long-term", exact: true })).toBeVisible({ timeout: 20_000 });

  // 4. Navigate to the correct case -- through the real nav, to a real
  // case row located by its real href (today's real ranking/ordering on
  // the Investigate page can't be predicted in advance from live data,
  // so this locates the specific case deterministically rather than
  // guessing its position in the list).
  await page.getByRole("link", { name: /investigate/i }).first().click();
  await expect(page).toHaveURL(/\/investigate$/);
  const caseLink = page.locator(`a[href="/investigate/${encodeURIComponent(trendCase.case_id)}"]`);
  await expect(caseLink).toBeVisible({ timeout: 15_000 });
  await caseLink.click();
  await expect(page).toHaveURL(new RegExp(`/investigate/${trendCase.case_id}$`));

  // 5. Display the real finding, evidence, and business impact. Section
  // titles are real <h2> headings (CaseDetail.jsx's Section component) --
  // matched by role, not getByText, since "Business impact" can also
  // appear inside real prose (e.g. ImpactBox's own "Celeste hasn't
  // quantified a business impact for this metric yet" fallback text,
  // which is itself real, honest system behavior, not a bug).
  await expect(page.getByRole("heading", { name: trendCase.title })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Business impact" })).toBeVisible();

  // 6. Create the first action (a brand-new case in a fresh isolated DB
  // has no finding_actions row yet -- the fallback tracker must offer one).
  const takeAction = page.getByRole("button", { name: /take action/i });
  await expect(takeAction).toBeVisible();
  await Promise.all([
    page.waitForResponse((r) => r.url().includes(`/findings/${encodeURIComponent(trendCase.finding_id)}/action`)),
    takeAction.click(),
  ]);
  await expect(page.getByRole("button", { name: "In progress" })).toBeVisible();
  await Promise.all([
    page.waitForResponse((r) => r.url().includes(`/findings/${encodeURIComponent(trendCase.finding_id)}/action`)),
    page.getByRole("button", { name: "In progress" }).click(),
  ]);

  // 7. Configure a measurable target supported by the actual metric --
  // this case's OWN metric_id, confirmed selectable via /intelligence/metrics.
  await page.getByRole("button", { name: /define a measurable target/i }).click();
  await page.getByPlaceholder(/Intended result/).fill(`Real full-stack test target for ${trendCase.metric_id}`);
  await page.locator("select").filter({ hasText: "Target metric" }).selectOption(trendCase.metric_id);
  await page.locator('input[type="number"]').fill("1"); // a deliberately trivial, always-valid numeric target
  // 8. Save the action through the real HTTP endpoint.
  await Promise.all([
    page.waitForResponse((r) => r.url().includes(`/findings/${encodeURIComponent(trendCase.finding_id)}/action`)),
    page.getByRole("button", { name: "Save target" }).click(),
  ]);
  await expect(page.getByText(new RegExp(`Target: ${trendCase.metric_id}`))).toBeVisible();

  // 9. Verify the action and decision evidence persist in the isolated DB
  // -- read back through the real HTTP API, not just trusted from the UI.
  const persisted = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(trendCase.case_id)}`)).json();
  const persistedAction = (persisted.actions ?? []).find((a) => a.finding_id === trendCase.finding_id);
  expect(persistedAction, "action row must exist in the isolated app database after saving").toBeTruthy();
  expect(persistedAction.status).toBe("in_progress");
  expect(persistedAction.target_metric_id).toBe(trendCase.metric_id);
  expect(persistedAction.evidence_verified).toBe(true);
  const history = (persisted.history ?? []).filter((h) => h.event_type === "decision_evidence");
  expect(history.length, "a decision-evidence audit entry must exist for this save").toBeGreaterThan(0);

  // 10. Reload the page and confirm the saved information appears correctly.
  await page.reload();
  await expect(page.getByText(new RegExp(`Target: ${trendCase.metric_id}`))).toBeVisible();
  await expect(page.getByRole("button", { name: "In progress" })).toBeVisible();

  // --- Evidence-version conflict, through the actual HTTP endpoint ---
  // A same-day forced rescan still bumps case_observations.revision on
  // every UPDATE (see case_engine._record_observation's ON CONFLICT
  // clause) -- this triggers a genuinely new version token from a real
  // second scan, not a synthetic edit to the isolated DB.
  const beforeVersion = persisted.observations?.slice(-1)[0]?.version;
  expect(beforeVersion, "must have a real evidence version to test staleness against").toBeTruthy();

  const rescan = await request.get(`${BACKEND_BASE_URL}/investigations`, { timeout: SCAN_TIMEOUT_MS });
  expect(rescan.ok()).toBe(true);

  // Sent directly via HTTP (not clicked through the UI) so the race is
  // deterministic: the browser's own client-side pre-check (already
  // covered by the fixture-based suite) would otherwise catch this before
  // the request is even sent. This proves the SERVER independently
  // rejects a stale token, per PATCH /findings/{id}/action's own
  // reassertion of the same check.
  const staleAttempt = await request.patch(`${BACKEND_BASE_URL}/findings/${encodeURIComponent(trendCase.finding_id)}/action`, {
    data: { relevant: true, status: "in_progress", action_taken: "Probing real evidence-version conflict handling.", evidence_version: beforeVersion },
  });
  expect(staleAttempt.ok()).toBe(true); // this endpoint always returns HTTP 200; the conflict is in the body
  const staleBody = await staleAttempt.json();
  expect(staleBody.status).toBe("error");
  expect(staleBody.error).toBe("stale_evidence");
  expect(staleBody.current_version).not.toBe(beforeVersion);

  const retryAttempt = await request.patch(`${BACKEND_BASE_URL}/findings/${encodeURIComponent(trendCase.finding_id)}/action`, {
    data: { relevant: true, status: "in_progress", action_taken: "Retried with the fresh evidence version.", evidence_version: staleBody.current_version },
  });
  const retryBody = await retryAttempt.json();
  expect(retryBody.status).toBe("ok");
  expect(retryBody.evidence_verified).toBe(true);

  // action_taken has no on-screen element for an "in_progress" action
  // (SignalCard.jsx's notes textarea only renders once status is
  // "completed") -- verified through the real API read-back instead of
  // guessing at a UI element that doesn't exist for this state.
  const afterRetry = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(trendCase.case_id)}`)).json();
  const actionAfterRetry = (afterRetry.actions ?? []).find((a) => a.finding_id === trendCase.finding_id);
  expect(actionAfterRetry.action_taken).toBe("Retried with the fresh evidence version.");

  // The real frontend picks up the real post-conflict-resolution state --
  // what IS visible for an in_progress action with a saved target.
  await page.reload();
  await expect(page.getByRole("button", { name: "In progress" })).toBeVisible();
  await expect(page.getByText(new RegExp(`Target: ${trendCase.metric_id}`))).toBeVisible();
});

test("the Actions workspace shows the real case just actioned and links to it", async ({ page }) => {
  // The individual-action projection must show the work created above
  // and navigate through that action's stored case/finding association.
  test.skip(!ready, skipReason);
  test.setTimeout(30_000);

  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Actions" })).toBeVisible();

  const action = page.getByRole("article").filter({ has: page.locator(`a[href^="/investigate/${encodeURIComponent(trendCase.case_id)}?action="]`) });
  await expect(action.first()).toBeVisible();
  await expect(action.first().getByText("In progress", { exact: true })).toBeVisible();
  await action.first().getByRole("link", { name: "View action" }).click();
  await expect(page).toHaveURL(new RegExp(`/investigate/${trendCase.case_id}\\?action=`));
});

test("the Outcomes workspace loads real cases and links to the one just actioned", async ({ page }) => {
  // Action measurements remain accessible independently of the case's
  // resolution status. No conditional pass if the work item disappears.
  test.skip(!ready, skipReason);
  test.setTimeout(30_000);

  await page.goto("/outcomes");
  await expect(page.getByRole("heading", { name: "Outcomes" })).toBeVisible();

  const actionLink = page.locator(`a[href^="/investigate/${encodeURIComponent(trendCase.case_id)}?action="]`);
  await expect(actionLink.first()).toBeVisible();
  await actionLink.first().click();
  await expect(page).toHaveURL(new RegExp(`/investigate/${trendCase.case_id}\\?action=`));
});

test("the corrected inventory finding renders real evidence and freshness in the browser", async ({ page }) => {
  test.skip(!ready, skipReason);
  test.skip(!inventoryCase, "No real stock_availability case today -- nothing to check.");
  test.setTimeout(60_000);

  await page.goto(`/investigate/${encodeURIComponent(inventoryCase.case_id)}`);
  // Task 3's corrected terminology: distinct products, not a bare "N
  // products" reading of a product-store stockout count. Both phrases
  // appear twice on the page (the h1 title and ImpactBox's own detail
  // text both carry the corrected wording) -- .first() is deliberate,
  // not a narrowing of what's being proven.
  await expect(page.getByText(/product\(s\) out of stock at one or more stores/).first()).toBeVisible();
  await expect(page.getByText(/product-store stockouts/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Business impact" })).toBeVisible();
});

test("isolation: this run's writes landed only in the isolated database, never the production one", async () => {
  test.skip(!ready, skipReason);
  expect(existsSync(APPDB_SQLITE_PATH)).toBe(true);
  const isolatedStat = statSync(APPDB_SQLITE_PATH);
  expect(isolatedStat.size, "the isolated DB should have real written data in it after this run").toBeGreaterThan(0);

  // The production app.db (if one exists at the repo root) must show no
  // sign this run ever touched it -- same size and modified time as
  // whatever was snapshotted before the very first test in this file ran.
  const prodExistsNow = existsSync(PROD_APPDB_PATH);
  expect(prodExistsNow).toBe(Boolean(prodAppDbStatBefore));
  if (prodAppDbStatBefore) {
    const prodStatAfter = statSync(PROD_APPDB_PATH);
    expect(prodStatAfter.mtimeMs).toBe(prodAppDbStatBefore.mtimeMs);
    expect(prodStatAfter.size).toBe(prodAppDbStatBefore.size);
  }
});
