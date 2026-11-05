import { test, expect } from "@playwright/test";
import { authStatus, storeScope } from "./fixtures.js";

test("Investigate reports a refresh request failure and allows retry", async ({ page }) => {
  let refreshRequests = 0;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body = { status: "ok", items: [], total: 0 };
    if (url.pathname.endsWith("/auth/status")) body = authStatus;
    if (url.pathname.endsWith("/store-scope")) body = storeScope;
    if (url.pathname.endsWith("/management/summary")) {
      body = {
        status: "ok",
        cases: { open: 0 },
        case_domains: [],
        case_impacts: [],
        sync: { refresh: { status: "idle" } },
      };
    }
    if (url.pathname.endsWith("/brief/refresh")) {
      refreshRequests += 1;
      if (refreshRequests === 1) {
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ status: "error", detail: "Refresh request failed." }),
        });
      }
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ status: "accepted" }),
      });
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/investigate");
  await page.getByRole("button", { name: "Run investigation" }).click();
  await expect(page.getByRole("alert")).toContainText("Refresh request failed.");
  const retry = page.getByRole("button", { name: "Run investigation" });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect.poll(() => refreshRequests).toBe(2);
});
