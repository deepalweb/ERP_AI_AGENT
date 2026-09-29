// Phase 2e post-acceptance audit, Task 2: the core detect -> investigate
// -> act -> verify workflow, driven through a REAL browser against the
// REAL frontend build, with every backend call intercepted and answered
// from fixtures.js (see playwright.config.js for why: no real database,
// read or write, is ever touched -- this is what "safe test data" means
// here, not a compromise on realism of the frontend itself).
//
// Run with: npm run test:e2e (see package.json). Requires `npx playwright
// install chromium` once, and downloads/runs a real Chromium -- verified
// to work in this project's dev sandbox at the time this was written.
import { test, expect } from "@playwright/test";
import {
  CASE_ID, FINDING_ID, authStatus, caseDetailFixture, caseListFixture, customerPredictionsSummary,
  emptyBrief, forwardLook, metrics, notificationsSummary, outcomesSummary, storeScope,
} from "./fixtures.js";

async function mockJson(route, body) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

// Every ActionTracker save is async (handleUpdateAction refetches the
// case first, then PATCHes) -- a plain click() returns before that chain
// resolves, so any assertion right after it races the real save. Waiting
// for the matching PATCH response is the one signal that's actually tied
// to the save having landed, not to a fixed delay.
function waitForActionPatch(page) {
  return page.waitForResponse((response) => response.url().includes(`/api/findings/${encodeURIComponent(FINDING_ID)}/action`));
}

/** Wires every GET this workflow touches to a fixture, plus a stateful
 * PATCH handler for /findings/{id}/action that a test can drive through
 * `state` -- state.actions accumulates what's "saved" so a page refetch
 * after a save shows it, exactly like the real backend would. */
function mockBackend(page, { staleOnce = false } = {}) {
  const state = { actions: [], patchCalls: [] };

  page.route("**/api/**", (route) => mockJson(route, { status: "error", detail: "Unconfigured test endpoint" }));
  page.route("**/api/management/summary", (route) => mockJson(route, { status: "ok", cases: { open: 1 }, actions: { outstanding: state.actions.length, blocked: 0 } }));
  page.route("**/api/management/actions?**", (route) => mockJson(route, { status: "ok", items: state.actions, total: state.actions.length, limit: 20, offset: 0 }));
  page.route("**/api/auth/status", (route) => mockJson(route, authStatus));
  page.route("**/api/store-scope", (route) => mockJson(route, storeScope));
  page.route("**/api/brief", (route) => mockJson(route, emptyBrief));
  page.route("**/api/forecast/forward-look", (route) => mockJson(route, forwardLook));
  page.route("**/api/outcomes/summary", (route) => mockJson(route, outcomesSummary));
  page.route("**/api/customers/predictions/summary", (route) => mockJson(route, customerPredictionsSummary));
  page.route("**/api/notifications/summary", (route) => mockJson(route, notificationsSummary));
  page.route("**/api/notifications/acknowledge", (route) => mockJson(route, { status: "ok", last_acknowledged_at: "2026-09-17T00:00:00" }));
  page.route("**/api/intelligence/metrics", (route) => mockJson(route, metrics));
  page.route(`**/api/cases?**`, (route) => {
    const status = new URL(route.request().url()).searchParams.get("status");
    return mockJson(route, status && status !== "open" ? { status: "ok", items: [], total: 0, limit: 20, offset: 0 } : caseListFixture());
  });
  page.route(`**/api/cases/${CASE_ID}`, (route) => mockJson(route, caseDetailFixture({ actions: state.actions })));

  page.route(`**/api/findings/${encodeURIComponent(FINDING_ID)}/action`, async (route) => {
    const request = route.request();
    const body = JSON.parse(request.postData() || "{}");
    state.patchCalls.push(body);

    if (staleOnce && state.patchCalls.length === 1) {
      await mockJson(route, {
        status: "error",
        error: "stale_evidence",
        detail: "The evidence for this finding has changed since it was last reviewed.",
        current_version: "1:2",
        current_observed_at: "2026-09-17T09:00:00",
        current_snapshot: { confirm: { current: 13_000_000 } },
      });
      return;
    }

    state.actions = [{
      finding_id: FINDING_ID,
      relevant: body.relevant ?? true,
      status: body.status ?? "not_started",
      action_taken: body.action_taken ?? null,
      owner: body.owner ?? null,
      deadline: body.deadline ?? null,
      case_id: CASE_ID,
      action_kind: body.action_kind ?? null,
      completed_at: body.status === "completed" ? "2026-09-17T09:00:00" : null,
      intended_result: body.intended_result ?? null,
      target_metric_id: body.target_metric_id ?? null,
      target_value: body.target_value ?? null,
      target_comparison: body.target_comparison ?? null,
      target_unit: body.target_metric_id === "revenue" ? "currency" : null,
      target_baseline_relative: body.target_baseline_relative ?? false,
      target_measurement_period: body.target_measurement_period ?? null,
      outcome_checked_at: null,
      outcome_value: null,
      outcome_period: null,
      outcome_verdict: null,
      outcome_status: null,
      outcome_confidence: null,
      evidence_verified: body.evidence_version != null,
      evidence_version: body.evidence_version ?? null,
      overdue: false,
      updated_at: "2026-09-17T09:00:00",
    }];

    await mockJson(route, { status: "ok", evidence_verified: body.evidence_version != null });
  });

  return state;
}

test("Daily Brief -> Investigate -> Case Detail -> evidence -> action -> target -> save -> outcome status", async ({ page }) => {
  mockBackend(page);

  await page.goto("/");
  await expect(page.getByRole("link", { name: /investigate/i }).first()).toBeVisible();

  await page.getByRole("link", { name: /investigate/i }).first().click();
  await expect(page).toHaveURL(/\/investigate$/);
  // The attention queue and the full case list both link to this case.
  const caseLink = page.getByRole("link", { name: /Weekend revenue is running ahead of plan/ }).first();
  await expect(caseLink).toBeVisible();

  await caseLink.click();
  await expect(page).toHaveURL(new RegExp(`/investigate/${CASE_ID}`));

  // What the evidence establishes (root cause) and business impact are
  // both real fixture content, not placeholders -- confirms the page
  // actually renders evidence, not just a shell.
  await expect(page.getByText(/Concentrated in Ethul Kotte/)).toBeVisible();
  await expect(page.getByText(/Rs 1\.2M above plan/)).toBeVisible();

  // No finding_actions row exists yet -- the fallback tracker (the fix
  // this audit made) must still offer a way to act.
  await expect(page.getByRole("button", { name: /take action/i })).toBeVisible();
  await Promise.all([waitForActionPatch(page), page.getByRole("button", { name: /take action/i }).click()]);

  await expect(page.getByRole("button", { name: "In progress" })).toBeVisible();
  await Promise.all([waitForActionPatch(page), page.getByRole("button", { name: "In progress" }).click()]);

  // Configure a target through the form this audit's Task 2 added.
  await page.getByRole("button", { name: /define a measurable target/i }).click();
  await page.getByPlaceholder("Intended result (e.g. 'restore weekend revenue above 8M')").fill("Sustain weekend revenue above 13M");
  await page.locator("select").filter({ hasText: "Target metric" }).selectOption("revenue");
  await page.getByPlaceholder("Value").fill("13000000");
  await Promise.all([waitForActionPatch(page), page.getByRole("button", { name: "Save target" }).click()]);

  await expect(page.getByText(/Target: revenue >= 13000000/)).toBeVisible();
  await expect(page.getByText("Outcome pending")).toBeVisible();
});

test("a stale-evidence conflict is surfaced, not silently overwritten", async ({ page }) => {
  const state = mockBackend(page, { staleOnce: true });

  await page.goto(`/investigate/${CASE_ID}`);
  await expect(page.getByRole("button", { name: /take action/i })).toBeVisible();
  await Promise.all([waitForActionPatch(page), page.getByRole("button", { name: /take action/i }).click()]);

  // The first save attempt is answered with a stale_evidence conflict.
  await expect(page.getByText(/evidence for this finding has changed/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /save with current evidence/i })).toBeVisible();

  await Promise.all([
    waitForActionPatch(page),
    page.getByRole("button", { name: /save with current evidence/i }).click(),
  ]);
  await expect(page.getByText(/evidence for this finding has changed/i)).toHaveCount(0);
  expect(state.patchCalls.length).toBe(2);
  expect(state.patchCalls[1].evidence_version).toBe("1:2"); // retried with the fresh version, not the stale one
});

test("the target form validates before it ever calls the backend", async ({ page }) => {
  const state = mockBackend(page);

  await page.goto(`/investigate/${CASE_ID}`);
  await Promise.all([waitForActionPatch(page), page.getByRole("button", { name: /take action/i }).click()]);
  await Promise.all([waitForActionPatch(page), page.getByRole("button", { name: "In progress" }).click()]);

  const patchCountBeforeTarget = state.patchCalls.length;

  await page.getByRole("button", { name: /define a measurable target/i }).click();
  await page.getByRole("button", { name: "Save target" }).click(); // no metric, no value chosen yet

  await expect(page.getByText("Choose a target metric.")).toBeVisible();
  expect(state.patchCalls.length).toBe(patchCountBeforeTarget); // rejected client-side, never reached the backend
});
