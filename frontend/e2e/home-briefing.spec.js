import { test, expect } from "@playwright/test";
import {
  authStatus,
  emptyBrief,
  caseDetailFixture,
  notificationsSummary,
  emptyCurrentPeriod,
} from "./fixtures.js";

async function setup(page, { empty = false, error = false } = {}) {
  const latest = caseDetailFixture().latest;
  const finding = {
    ...latest,
    id: "monthly-revenue",
    metric_id: "revenue",
    domain: "financial",
    case_id: "saved-case",
    business_impact_score: 30,
  };
  const unlinked = {
    ...finding,
    id: "unlinked-stock",
    case_id: null,
    metric_id: "stock_out",
    domain: "inventory",
    title: "Items need replenishment",
    priority: "critical",
    category: "critical",
    impact: null,
    root_cause: null,
    business_impact_score: 40,
    confirm: { current: 0, unit: "count", period: "2026-09-17" },
  };
  let forecastCalls = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "");
    let body = { status: "ok" };
    if (path === "/auth/status") body = authStatus;
    else if (path === "/store-scope")
      body = { status: "ok", stores: ["Colombo", "Outlet B"] };
    else if (path === "/notifications/summary") body = notificationsSummary;
    else if (path === "/intelligence/current-period") body = emptyCurrentPeriod;
    else if (path === "/brief")
      body = error
        ? { status: "error", detail: "Brief offline" }
        : {
            ...emptyBrief,
            home: {
              long: {ranked_ids: empty ? [] : ["unlinked-stock", "monthly-revenue"], priority_ids: empty ? [] : ["unlinked-stock", "monthly-revenue"], severity_counts: {critical: empty ? 0 : 1, high: 0, medium: 0, positive: 0}},
              week: {ranked_ids: empty ? [] : ["weekly-delivery"], priority_ids: empty ? [] : ["weekly-delivery"], severity_counts: {critical: 0, high: 0, medium: 0, positive: 0}},
            },
            generated_at: "2026-09-18T08:30:00Z",
            data_freshness: {
              status: "stale",
              latest_order_at: "2026-09-16T12:00:00Z",
            },
            investigations: empty ? [] : [finding],
            state_findings: empty ? [] : [unlinked],
            short_term_findings: empty
              ? []
              : [
                  {
                    ...finding,
                    id: "weekly-delivery",
                    title: "Delivery delays increased",
                    metric_id: "delivery_delay",
                    domain: "delivery",
                    horizon: "weekly",
                  },
                ],
            health: null,
            customer_finding: {
              approaching_count: 28,
              expected_revenue: 420000,
              high_confidence_count: 19,
              overdue_count: 7,
            },
            short_term_resolved: [
              {
                metric_id: "processing",
                metric_label: "Processing delays",
                consecutive_days: 4,
                last_flagged_date: "2026-09-15",
              },
            ],
          };
    else if (path === "/management/actions")
      body = {
        status: "ok",
        total: 1,
        items: [
          {
            finding_id: "old-action",
            status: "in_progress",
            action_taken: "Follow up an older finding",
            owner: "Operations",
            case: { case_id: "older-case" },
            outcome_status: "blocked",
          },
        ],
      };
    else if (path.includes("forward-look")) {
      forecastCalls++;
      body = {
        status: "ok",
        revenue: {
          projected_low: 11000000,
          projected_high: 14000000,
          based_on_month: "2026-08",
        },
        note: "Projection, not a guarantee.",
      };
    } else if (path === "/findings/legacy/case")
      body = { status: "ok", case_id: "saved-case" };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  return { forecastCalls: () => forecastCalls };
}

test("live briefing ranks real response fields, opens evidence, and preserves periods and links", async ({
  page,
}) => {
  const mock = await setup(page);
  await page.goto("/");
  await expect(page.locator(".hp-priority").first()).toContainText(
    "Items need replenishment",
  );
  await expect(page.locator(".hp-priority").first()).toContainText(
    "Impact not quantified",
  );
  await expect(page.locator(".hp-health-figure")).toContainText("—");
  await expect(page.locator(".hp-counts")).toContainText("2 findings in total");
  await expect(page.locator(".hp-priority a")).toHaveAttribute(
    "href",
    "/investigate/saved-case?action=monthly-revenue",
  );
  expect(mock.forecastCalls()).toBe(0);
  await page
    .getByRole("button", { name: "Review evidence", exact: true })
    .click();
  await expect(page.locator("#brief-finding-unlinked-stock")).toHaveAttribute(
    "open",
    "",
  );
  await expect(
    page.getByText("No stored case association.", { exact: false }),
  ).toBeVisible();
  await page.locator("#brief-finding-monthly-revenue > summary").click();
  await expect(page.getByText("Projection, not a guarantee.")).toBeVisible();
  await page.getByRole("button", { name: "This week", exact: true }).click();
  await expect(page.locator(".hp-priority")).toHaveCount(1);
  await expect(page.locator(".hp-priority")).toContainText(
    "Delivery delays increased",
  );
  await expect(page.locator(".hp-recovery")).toContainText(
    "does not establish",
  );
  await expect(page.locator(".hp-opportunity")).toContainText("28 customers");
  await expect(page.locator(".hp-follow-list a").first()).toHaveAttribute(
    "href",
    "/investigate/older-case?action=old-action",
  );
  await page.getByRole("button", { name: "Long-term", exact: true }).click();
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/home-live-${width}.png`,
      fullPage: true,
    });
  }
});

test("empty briefing keeps historical work visible without claiming business recovery", async ({
  page,
}) => {
  await setup(page, { empty: true });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "No findings in this briefing" }),
  ).toBeVisible();
  await expect(page.locator(".hp-follow-list").first()).toContainText(
    "Follow up an older finding",
  );
  await expect(page.locator(".hp-priority")).toHaveCount(0);
});

test("briefing failure does not manufacture counts or hide independently loaded work", async ({
  page,
}) => {
  await setup(page, { error: true });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Could not load briefing",
    { timeout: 15000 },
  );
  await expect(page.locator(".hp-counts")).toHaveCount(0);
  await expect(page.locator(".hp-follow-list").first()).toContainText(
    "Follow up an older finding",
  );
});

test("cold briefing progresses from loading to a successful empty briefing", async ({page}) => {
  await setup(page);
  let ready = false;
  await page.route("**/api/brief", route => {
    return route.fulfill({status: !ready ? 202 : 200, contentType: "application/json",
      body: JSON.stringify(!ready ? {status: "loading", refresh: {status: "running"}} : emptyBrief)});
  });
  await page.goto("/");
  await expect(page.getByText(/Preparing the first briefing/)).toBeVisible();
  await expect(page.locator(".hp-snapshot")).toHaveCount(0);
  ready = true;
  await expect(page.getByRole("heading", {name: "No findings in this briefing"})).toBeVisible();
  await expect(page.getByText(/Preparing the first briefing/)).toHaveCount(0);
});

test("stale evidence survives refresh failure and retry clears the error", async ({page}) => {
  await setup(page);
  let retried = false;
  await page.route("**/api/brief", route => route.fulfill({contentType: "application/json", body: JSON.stringify({
    ...emptyBrief, generated_at: "2026-09-15T10:00:00Z", health: {overall: 80},
    cache_status: retried ? "fresh" : "stale", refresh: {status: retried ? "idle" : "failed", error: retried ? null : "Reporting check failed"},
  })}));
  await page.route("**/api/brief/refresh", route => { retried = true; return route.fulfill({status: 202, contentType: "application/json", body: '{"status":"accepted"}'}); });
  await page.goto("/");
  await expect(page.getByText(/Showing a stale cached briefing/)).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Reporting check failed");
  await expect(page.locator(".hp-health-figure")).toContainText("80");
  await page.getByRole("button", {name: "Retry briefing"}).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText(/Showing a stale cached briefing/)).toHaveCount(0);
});

test("an HTTP polling error keeps cached evidence and enables retry", async ({page}) => {
  await setup(page);
  let fail = false;
  await page.route("**/api/brief", route => {
    return route.fulfill({status: !fail ? 200 : 503, contentType: "application/json", body: JSON.stringify(!fail ? {
      ...emptyBrief, health: {overall: 73}, cache_status: "stale", refresh: {status: "running"},
    } : {status: "error", detail: "Reporting unavailable", refresh: {status: "failed"}})});
  });
  await page.goto("/");
  await expect(page.locator(".hp-health-figure")).toContainText("73");
  fail = true;
  await expect(page.getByRole("alert")).toContainText("Reporting unavailable");
  await expect(page.locator(".hp-health-figure")).toContainText("73");
  await expect(page.getByRole("button", {name: "Retry briefing"})).toBeEnabled();
});

test("full snapshot renders health gauge, domain cards, severity tiles, and the decision banner at desktop, tablet, and mobile widths", async ({
  page,
}) => {
  const finding = {
    ...caseDetailFixture().latest,
    id: "monthly-revenue",
    metric_id: "revenue",
    domain: "sales",
    case_id: "saved-case",
    priority: "critical",
    category: "critical",
    business_impact_score: 90,
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "");
    let body = { status: "ok" };
    if (path === "/auth/status") body = authStatus;
    else if (path === "/store-scope")
      body = { status: "ok", stores: ["Colombo", "Kandy", "Galle", "Jaffna"] };
    else if (path === "/notifications/summary") body = notificationsSummary;
    else if (path === "/intelligence/current-period") body = emptyCurrentPeriod;
    else if (path === "/brief")
      body = {
        ...emptyBrief,
        home: {
          long: {
            ranked_ids: ["monthly-revenue"],
            priority_ids: ["monthly-revenue"],
            severity_counts: { critical: 4, high: 0, medium: 2, positive: 7 },
          },
          week: { ranked_ids: [], priority_ids: [], severity_counts: { critical: 0, high: 0, medium: 0, positive: 0 } },
        },
        generated_at: "2026-09-18T08:17:00Z",
        data_freshness: {
          status: "stale",
          days_stale: 2,
          latest_order_at: "2026-09-16T10:56:00Z",
        },
        investigations: [finding],
        state_findings: [],
        new_critical_findings: [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }],
        short_term_resolved: [
          { metric_id: "processing", metric_label: "Processing delays", consecutive_days: 4, last_flagged_date: "2026-09-15" },
          { metric_id: "delivery", metric_label: "Delivery delays", consecutive_days: 3, last_flagged_date: "2026-09-14" },
          { metric_id: "cancels", metric_label: "Cancellations", consecutive_days: 2, last_flagged_date: "2026-09-13" },
        ],
        health: {
          overall: 80,
          overall_delta: 6,
          status: "good",
          domains: [
            { id: "sales", label: "Sales", score: 78, delta: 5, status: "good", drivers: ["Weekend promo lift"] },
            { id: "inventory", label: "Inventory", score: 72, delta: -8, status: "warning", drivers: ["Stockouts at two outlets"] },
            { id: "operations", label: "Operations", score: 85, delta: 12, status: "good", drivers: ["Delivery times recovering"] },
            { id: "customers", label: "Customers", score: 86, delta: 4, status: "good", drivers: ["Repeat-purchase rate up"] },
          ],
        },
        customer_finding: null,
      };
    else if (path === "/management/actions") body = { status: "ok", total: 0, items: [] };
    else if (path === "/findings/legacy/case") body = { status: "error" };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Business Health" })).toBeVisible();
  await expect(page.locator(".hp-health-figure")).toContainText("80");
  await expect(page.locator(".hp-domain-card")).toHaveCount(4);
  await expect(page.locator(".hp-severity-tile")).toHaveCount(4);
  await expect(page.locator(".hp-decision-banner")).toContainText("4 critical findings require attention");
  await expect(page.locator(".hp-summary-rows")).toContainText("4 new critical findings");
  await expect(page.locator(".hp-summary-rows")).toContainText("3 no longer flagged");
  await expect(page.locator(".hp-freshness-dot.hp-freshness-warning")).toContainText("2 days behind");
  await expect(page.locator(".hp-pulse-badge.hp-freshness-warning")).toHaveAttribute("title", "Order data 2 days behind");
  await page.locator(".hp-decision-banner").click();
  await expect(page.locator("#hp-priorities-list")).toBeInViewport();
  for (const [label, width] of [["desktop-1440", 1440], ["tablet-1024", 1024], ["mobile-390", 390]]) {
    await page.setViewportSize({ width, height: 1100 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    ).toBe(true);
    await page.screenshot({ path: `test-results/home-snapshot-${label}.png`, fullPage: true });
  }
});
