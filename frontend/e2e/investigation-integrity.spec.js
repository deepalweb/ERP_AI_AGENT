import { test, expect } from "@playwright/test";
import { CASE_ID, FINDING_ID, authStatus, caseDetailFixture, emptyBrief, metrics, storeScope } from "./fixtures.js";

async function serveCase(page, detail) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api", "");
    const body = path === `/cases/${CASE_ID}` ? detail
      : path === "/auth/status" ? authStatus
      : path === "/store-scope" ? storeScope
      : path === "/intelligence/metrics" ? metrics
      : path === "/brief" ? emptyBrief
      : { status: "ok", items: [], count: 0, findings: [] };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
}

test("case explains blocked verification and exposes measurement provenance", async ({ page }) => {
  const detail = caseDetailFixture();
  detail.measurement = {
    detector_id: "inventory", scope: "Ethul Kotte", period: "2026-09-28",
    observed_at: "2026-09-28T05:00:00Z", measurement_status: "measurement_unavailable",
    coverage: "incomplete", freshness: "unknown",
  };
  detail.verification_explanation = "Verification paused: inventory refresh coverage is unavailable.";
  await serveCase(page, detail);
  await page.goto(`/investigate/${CASE_ID}`);
  const panel = page.getByRole("region", { name: "Measurement and verification" });
  await expect(panel).toContainText("Verification paused: inventory refresh coverage is unavailable.");
  await panel.getByText("Evidence provenance", { exact: true }).click();
  await expect(panel).toContainText("Scope: Ethul Kotte");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test("case preserves old target outcomes and distinguishes condition recovery", async ({ page }) => {
  const action = {
    finding_id: FINDING_ID, relevant: true, status: "completed", action_taken: "Review availability",
    owner: "Operations", completed_at: "2026-09-28T05:00:00", target_metric_id: "revenue",
    target_value: 200, target_comparison: ">=", target_unit: "currency", target_version: 2,
    target_measurement_period: "monthly", outcome_verdict: null, outcome_status: "pending",
    target_history: [
      { version: 1, target: { target_metric_id: "revenue", target_value: 100, target_comparison: ">=" },
        superseded_at: "2026-09-28", outcomes: [{ outcome_value: 150, outcome_verdict: "met", outcome_period: "2026-08" }] },
      { version: 2, target: { target_metric_id: "revenue", target_value: 200, target_comparison: ">=" }, outcomes: [] },
    ],
  };
  const detail = caseDetailFixture({ actions: [action], caseOverrides: { status: "resolved", category: "critical", resolution_verdict: "recovered" } });
  await serveCase(page, detail);
  await page.goto(`/investigate/${CASE_ID}`);
  await page.getByText("Target and measurement history", { exact: true }).click();
  await expect(page.getByText("Observed 150 · met · 2026-08", { exact: true })).toBeVisible();
  await expect(page.getByText(/Condition recovered\. Clean measurements/)).toBeVisible();
  await page.screenshot({ path: "test-results/investigation-integrity.png", fullPage: true });
});


test("results expose rate comparisons, segment samples and stage timings on desktop and mobile", async ({ page }) => {
  const detail = caseDetailFixture({ caseOverrides: { metric_id: "delayed_delivery_rate", domain: "operations", category: "critical" } });
  detail.latest = { ...detail.latest,
    title: "Delayed orders increased", category: "critical", priority: "high", impact: null, recommendations: [], root_cause: null, metric: { label: "Delayed order rate", direction: "higher_is_bad", unit: "percent" },
    confirm: { period: "2026-08", current: 12, baseline: 8, pct_change: 50, unit: "percent" },
    trend: [{ month: "2026-06", value: 7 }, { month: "2026-07", value: 9 }, { month: "2026-08", value: 12 }, { month: "2026-09", value: 2, is_partial: true }],
    breakdowns: [
      { dimension: "store", mode: "rate", baseline_month: "2026-07", top_contributors: [{ name: "Ethul Kotte", baseline: 10, current: 16, delta: 6, current_volume: 250 }] },
      { dimension: "channel", label: "Order channels", mode: "composition", baseline_month: "2026-07", top_contributors: [{ name: "Direct", baseline: 200, current: 300, baseline_share_pct: 40, current_share_pct: 60, delta: 20 }] },
    ],
    how: { stages: [{ name: "Waiting for dispatch", flagged_minutes: 18, baseline_minutes: 6 }] },
  };
  await serveCase(page, detail);
  await page.goto(`/investigate/${CASE_ID}`);
  const results = page.locator("#comparison-and-affected-segments");
  await expect(results.getByText("+4.00 pp", { exact: true })).toBeVisible();
  await expect(results.getByText("250", { exact: true })).toBeVisible();
  await expect(results.getByText("Measured trend", { exact: true })).toBeVisible();
  await expect(results.getByText(/Still in progress, excluded/)).toContainText("Sep 2026");
  await expect(results.getByText("Flagged: 18 min · Other: 6 min", { exact: true })).toBeVisible();
  await results.getByText("Order channels · 1 segment", { exact: true }).click();
  await expect(results.getByText("+20.00 pp", { exact: true })).toBeVisible();
  await expect(results.getByText("60.00%", { exact: true })).toBeVisible();
  // Recharts animates its SVG path; let it finish before visual review.
  await page.waitForTimeout(1800);
  await page.screenshot({ path: "test-results/investigation-results-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await results.scrollIntoViewIfNeeded();
  await page.waitForTimeout(1800);
  await page.screenshot({ path: "test-results/investigation-results-mobile.png", fullPage: true });
});
