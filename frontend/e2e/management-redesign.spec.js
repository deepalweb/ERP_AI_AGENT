import { test, expect } from "@playwright/test";
import {
  authStatus,
  caseDetailFixture,
  caseListFixture,
  emptyBrief,
  metrics,
  notificationsSummary,
} from "./fixtures.js";

const older = {
  finding_id: "older-action",
  case_id: "case-204",
  relevant: true,
  status: "in_progress",
  action_taken: "Review outlet replenishment",
  owner: "Operations",
  deadline: "2026-09-22",
  target_metric_id: "revenue",
  target_value: 10000,
  target_comparison: ">=",
  target_unit: "currency",
  target_measurement_period: "monthly",
  target_baseline_relative: true,
  outcome_status: "blocked",
  case: { case_id: "case-204", metric_id: "revenue", status: "open" },
};
const completed = {
  ...older,
  finding_id: "completed-action",
  status: "completed",
  action_taken: "Confirm supplier delivery schedule",
  outcome_status: "met",
  outcome_verdict: "met",
  outcome_value: 11000,
};
const dismissed = {
  ...older,
  finding_id: "dismissed-action",
  status: "not_started",
  action_taken: "Newer dismissed action",
  relevant: false,
  outcome_status: null,
  target_metric_id: null,
};

async function setup(page) {
  const actions = [dismissed, completed, older];
  const cases = Array.from({ length: 205 }, (_, i) => ({
    ...caseListFixture().items[0],
    case_id: `case-${i}`,
    latest: {
      ...caseListFixture().items[0].latest,
      title:
        i === 0
          ? "Review the change in outlet revenue"
          : `Business case ${i + 1}`,
    },
  }));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, "");
    let body;
    if (path === "/auth/status") body = authStatus;
    else if (path === "/store-scope")
      body = {
        status: "ok",
        stores: ["Colombo 6", "Pannipitiya", "Attidiya", "Ethul Kotte"],
      };
    else if (path === "/notifications/summary") body = notificationsSummary;
    else if (path === "/notifications/acknowledge") body = { status: "ok" };
    else if (path === "/intelligence/metrics") body = metrics;
    else if (path === "/brief")
      body = {
        ...emptyBrief,
        generated_at: "2026-09-18T04:30:00",
        data_freshness: {
          status: "stale",
          latest_order_at: "2026-09-16T18:30:00",
        },
      };
    else if (path === "/management/summary")
      body = {
        status: "ok",
        cases: { open: 205, recovered: 7, captured: 2 },
        case_total: 214,
        actions: { total: 3, outstanding: 1, blocked: 1, completed: 1 },
      };
    else if (path === "/management/actions") {
      let filtered = actions;
      const status = url.searchParams.get("status");
      if (status)
        filtered = filtered.filter((a) =>
          status === "outstanding"
            ? a.status !== "completed"
            : a.status === status,
        );
      if (url.searchParams.has("relevant"))
        filtered = filtered.filter(
          (a) => String(a.relevant) === url.searchParams.get("relevant"),
        );
      if (url.searchParams.get("outcome_status"))
        filtered = filtered.filter(
          (a) => a.outcome_status === url.searchParams.get("outcome_status"),
        );
      const offset = Number(url.searchParams.get("offset") || 0),
        limit = Number(url.searchParams.get("limit") || 20);
      body = {
        status: "ok",
        items: filtered.slice(offset, offset + limit),
        total: filtered.length,
        offset,
        limit,
      };
    } else if (path === "/cases") {
      const status = url.searchParams.get("status");
      const filtered = !status || status === "open" ? cases : [];
      const offset = Number(url.searchParams.get("offset") || 0),
        limit = Number(url.searchParams.get("limit") || 20);
      body = {
        status: "ok",
        items: filtered.slice(offset, offset + limit),
        total: filtered.length,
        offset,
        limit,
      };
    } else if (path === "/cases/case-204")
      body = {
        ...caseDetailFixture({ actions }),
        case: { ...caseDetailFixture().case, case_id: "case-204" },
      };
    else if (path === "/findings/older-action/case")
      body = { status: "ok", case_id: "case-204" };
    else body = { status: "error", detail: "Unconfigured test endpoint" };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

test("individual older actions remain visible and navigate to their stored case", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/actions");
  await expect(
    page.getByRole("heading", { name: older.action_taken }),
  ).toBeVisible();
  await expect(page.getByRole("article").first()).not.toContainText(
    "10000% from baseline",
  );
  await expect(
    page.getByText(/Rs 10\.0K \(change from baseline\)/).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: completed.action_taken }),
  ).toBeVisible();
  await expect(
    page.getByRole("article").getByText("Not relevant", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "In progress", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: completed.action_taken }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "View action" }).click();
  await expect(page).toHaveURL(/investigate\/case-204\?action=older-action/);
  await expect(page.locator("#action-older-action")).toBeInViewport();
});

test("Outcomes retrieves case 205 and keeps action results separate", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/outcomes");
  await expect(page.getByText("No cases in this state yet.")).toBeVisible();
  await expect(page.getByText("Action completed", { exact: true })).toHaveCount(
    2,
  );
  await expect(
    page.getByRole("article").getByText("Target met", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Case outcome status").selectOption("open");
  for (let i = 0; i < 10; i++) {
    await page
      .getByRole("button", { name: "Next", exact: true })
      .first()
      .click();
    await expect(
      page.getByText(
        `${(i + 1) * 20 + 1}–${Math.min((i + 2) * 20, 205)} of 205`,
        { exact: true },
      ),
    ).toBeVisible();
  }
  await expect(
    page.getByRole("heading", { name: "Business case 205", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Action outcome status").selectOption("blocked");
  await expect(
    page.getByRole("heading", { name: older.action_taken }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: completed.action_taken }),
  ).toHaveCount(0);
});

test("legacy finding deep links resolve through the saved association", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/?finding=older-action");
  await expect(page).toHaveURL(/investigate\/case-204\?action=older-action/);
});

test("briefing and top navigation work at desktop and mobile sizes", async ({ page }) => {
  await setup(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Long-term", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "This week", exact: true })).toBeVisible();
  await expect(page.locator(".workspace-sidebar")).toHaveCount(0);
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Next Order", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/original-layout-${width}.png`, fullPage: true });
  }
});

test("management screens and the assistant remain usable on a narrow screen", async ({
  page,
}) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of [
    "/actions",
    "/outcomes",
    "/investigate",
    "/investigate/case-204",
  ]) {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  const ask = page.getByRole("button", { name: "Ask Celeste", exact: true });
  await ask.click();
  await expect(page.getByRole("dialog", { name: "Ask Celeste" })).toBeVisible();
  await page
    .getByRole("textbox", { name: "Message to Celeste" })
    .fill("Review my inventory");
  for (let i = 0; i < 8; i++) await page.keyboard.press("Tab");
  expect(
    await page.evaluate(() => !!document.activeElement.closest("dialog")),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(ask).toBeFocused();
});

test("management errors stay visible and can be retried without inventing totals", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/management/actions**", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        status: "error",
        detail: "Test data temporarily unavailable",
      }),
    }),
  );
  await page.goto("/actions");
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".work-item")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});
