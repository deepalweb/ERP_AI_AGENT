// Manager acceptance checklist -- one continuous real-browser walkthrough
// against the real backend/reporting DB (isolated temp app DB, same harness
// as every other spec here), exercising the exact 10 observations a manager
// would need. Each test is labeled with its checklist item number so a
// failure points straight at which observation broke. Uses the real
// top_seller_stock case throughout (one real, already-validated finding)
// so "return to the original case" (item 10) is a genuine round trip, not
// a different case each time.
import { rmSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { APPDB_SQLITE_PATH, BACKEND_BASE_URL } from "../playwright.real-backend.config.js";

const SCAN_TIMEOUT_MS = 600_000;
test.describe.configure({ mode: "serial" });

let ready = false;
let skipReason = "";
let stockOutCase = null; // { case_id, finding_id }
let evidenceCase = null; // case_id -- any real case with confirmed AND unknown checks, for item 4 specifically
let evidenceCaseSkipReason = "";

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
    skipReason = "No real top_seller_stock case from today's scan -- nothing to walk through.";
    return;
  }
  const detail = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(cases[0].case_id)}`)).json();
  stockOutCase = { case_id: cases[0].case_id, finding_id: detail.latest?.finding_id };
  if (!stockOutCase.finding_id) {
    skipReason = `Real case ${stockOutCase.case_id} has no latest finding_id.`;
    return;
  }
  ready = true;

  // Item 4 needs a case whose root_cause.checks includes BOTH a confirmed
  // and an unconfirmed verdict to actually demonstrate the distinction --
  // the flagship stock-out case above has no root_cause at all (a state
  // finding with no baseline to check concentration against), so a
  // separate lookup is genuinely necessary, not an arbitrary swap.
  //
  // list_cases() (GET /cases) deliberately omits root_cause from its
  // per-row summary (case_engine._summarize_snapshot -- kept detail-only
  // to avoid bloating the list payload, same reasoning as `trend`), so
  // this has to check each candidate's own full detail, not the list.
  const allCasesResponse = await request.get(`${BACKEND_BASE_URL}/cases?limit=100`);
  const allCases = (await allCasesResponse.json()).items ?? [];
  // Trend-based (monthly) findings are the ones that run segment_concentration_checks
  // at all -- check those first rather than every case, to keep this to a
  // handful of extra requests.
  const trendCandidates = allCases.filter((c) => c.horizon === "monthly" || c.metric_id === "revenue");
  for (const c of trendCandidates.slice(0, 6)) {
    const detail = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(c.case_id)}`)).json();
    if (detail.latest?.root_cause?.checks?.length > 0) {
      evidenceCase = c.case_id;
      break;
    }
  }
  if (!evidenceCase) {
    evidenceCaseSkipReason = "No real case today has any root_cause checks recorded -- nothing to demonstrate item 4 against.";
  }
});

test("1. Understand overall business performance from Home", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/");
  await expect(page.getByText("Business Health")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/overall assessment/i)).toBeVisible();
  await expect(page.getByText("Finding counts are not case counts.")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "test-results-real/acceptance-01-home.png", fullPage: true });
});

test("2. Identify the most important issue and explain why it matters", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/");
  await expect(page.getByText(/most important/i)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "What needs attention" })).toBeVisible();
  // The first priority card is ranked Critical (highest severity) -- its own
  // "Suggested next step" text is the "why it matters" a manager reads.
  const firstCard = page.locator(".hp-priority").first();
  await expect(firstCard.getByText("Critical")).toBeVisible();
  await expect(firstCard.getByText("Suggested next step")).toBeVisible();
});

test("3. Open the correct investigation without assistance", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What needs attention" })).toBeVisible({ timeout: 20_000 });
  const firstCard = page.locator(".hp-priority").first();
  const openCase = firstCard.getByRole("link", { name: /Open case/ });
  await expect(openCase).toBeVisible();
  await openCase.click();
  // No search, no manual case-id entry -- a single click reaches the real,
  // persistent case (not a fabricated or guessed identifier).
  await expect(page).toHaveURL(/\/investigate\/[a-f0-9]{32}/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible({ timeout: 15_000 });
});

test("4. Distinguish established evidence from an unknown cause", async ({ page }) => {
  test.skip(!ready, skipReason);
  test.skip(!evidenceCase, evidenceCaseSkipReason);
  await page.goto(`/investigate/${encodeURIComponent(evidenceCase)}`);
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible({ timeout: 15_000 });
  // These headers render visually uppercase via CSS text-transform, but the
  // actual DOM text is mixed-case -- match case-insensitively.
  await expect(page.getByText(/C\. Confirmed explanatory evidence/i)).toBeVisible();
  await expect(page.getByText(/E\. Unknowns/i)).toBeVisible();
  await page.screenshot({ path: "test-results-real/acceptance-04-evidence.png", fullPage: true });
});

test("5. Understand operational impact versus financial impact", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stockOutCase.case_id)}`);
  await expect(page.getByRole("heading", { name: "Business impact" })).toBeVisible({ timeout: 15_000 });
  // Two distinct, separately-labeled figures -- never merged into one number.
  // "Financial impact" legitimately appears more than once (the section
  // label plus the executive-conclusion prose that also names it) -- .first()
  // is disambiguation, not narrowing what's being proven.
  await expect(page.getByText(/Operational magnitude/i).first()).toBeVisible();
  await expect(page.getByText(/Financial impact/i).first()).toBeVisible();
});

test("6. Accept a recommendation without confusing it with action creation", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stockOutCase.case_id)}`);
  await expect(page.getByRole("heading", { name: "Proposed by Celeste" })).toBeVisible({ timeout: 15_000 });
  const actionsHeading = page.getByRole("heading", { name: /Actions taken \(\d+\)/ });
  const before = await actionsHeading.textContent();

  const acceptButton = page.getByRole("button", { name: "Accept recommendation" }).first();
  await acceptButton.click();
  await expect(page.getByText("Accepted").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Recommendation approved").first()).toBeVisible();
  // Accepting must NOT change the action count -- the two are architecturally separate.
  await expect(actionsHeading).toHaveText(before);
});

test("7. Record an actual action with owner and deadline", async ({ page, request }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stockOutCase.case_id)}`);
  await expect(page.getByRole("heading", { name: "Resolution status" })).toBeVisible({ timeout: 15_000 });

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
    (async () => { await ownerInput.fill("Ops manager"); await ownerInput.blur(); })(),
  ]);
  const deadlineInput = page.locator('input[type="date"][title="Deadline"]');
  await Promise.all([
    page.waitForResponse((r) => r.url().includes(`/findings/${encodeURIComponent(stockOutCase.finding_id)}/action`)),
    (async () => { await deadlineInput.fill("2026-11-30"); await deadlineInput.blur(); })(),
  ]);

  const persisted = await (await request.get(`${BACKEND_BASE_URL}/cases/${encodeURIComponent(stockOutCase.case_id)}`)).json();
  const action = (persisted.actions ?? []).find((a) => a.finding_id === stockOutCase.finding_id);
  expect(action.owner).toBe("Ops manager");
  expect(action.deadline).toBe("2026-11-30");
  await page.screenshot({ path: "test-results-real/acceptance-07-action-recorded.png", fullPage: true });
});

test("8. Locate that action in the Actions workspace", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Actions" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Ops manager").first()).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: "test-results-real/acceptance-08-actions-workspace.png", fullPage: true });
});

test("9. Understand whether an outcome is verified, pending or unsupported", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto(`/investigate/${encodeURIComponent(stockOutCase.case_id)}`);
  await expect(page.getByRole("heading", { name: "Resolution status" })).toBeVisible({ timeout: 15_000 });
  // No target was declared on this problem_recovery case, so the honest
  // status is "not yet cleared its own threshold" -- never a fabricated
  // "verified" claim.
  await expect(page.getByText(/has not yet seen this clear its own anomaly threshold/)).toBeVisible();
});

test("10. Return to the original case and review its history", async ({ page }) => {
  test.skip(!ready, skipReason);
  await page.goto("/actions");
  await expect(page.getByText("Ops manager").first()).toBeVisible({ timeout: 15_000 });
  const viewAction = page.locator(`a[href^="/investigate/${encodeURIComponent(stockOutCase.case_id)}?action="]`);
  await expect(viewAction.first()).toBeVisible();
  await viewAction.first().click();
  await expect(page).toHaveURL(new RegExp(`/investigate/${stockOutCase.case_id}\\?action=`));

  // Everything from steps 4-7 must still be intact on return: evidence,
  // the accepted recommendation, and the just-recorded action.
  await expect(page.getByRole("heading", { name: "What the evidence establishes" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Accepted").first()).toBeVisible();
  await expect(page.getByText("Ops manager").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Case history" })).toBeVisible();
  await page.screenshot({ path: "test-results-real/acceptance-10-return-to-case.png", fullPage: true });
});
