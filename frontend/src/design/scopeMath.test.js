import { test } from "node:test";
import assert from "node:assert/strict";
import { domainBreakdown, newThisWeek, scopeTotals } from "./scopeMath.js";

const rows = [
  { domain: "inventory", status: "open", category: "critical", priority: "critical", count: 2 },
  { domain: "inventory", status: "open", category: "critical", priority: "medium", count: 3 },
  { domain: "inventory", status: "reopened", category: "opportunity", priority: "medium", count: 1 },
  { domain: "inventory", status: "verification_pending", category: "critical", priority: "high", count: 4 },
  { domain: "inventory", status: "resolved", category: "critical", priority: "high", count: 5 },
  { domain: "financial", status: "open", category: "critical", priority: "high", count: 1 },
];

test("status and priority map to the four bands, with verification and closed counted separately", () => {
  const inventory = domainBreakdown(rows).find((b) => b.domain === "inventory");
  assert.equal(inventory.urgent, 2);
  assert.equal(inventory.other, 3);
  assert.equal(inventory.opportunity, 1);
  assert.equal(inventory.active, 6);
  assert.equal(inventory.verification, 4);
  assert.equal(inventory.closed, 5);
});

test("every business area appears, even with no cases", () => {
  const labels = domainBreakdown(rows).map((b) => b.domain);
  assert.ok(labels.includes("supplier"));
  assert.ok(labels.includes("controls"));
});

test("product totals sum every area; one area's totals are its own", () => {
  assert.equal(scopeTotals(rows, "").active, 7);
  assert.equal(scopeTotals(rows, "inventory").active, 6);
  assert.equal(scopeTotals(rows, "financial").urgent, 1);
});

test("new this week counts recent first observations in the chosen area only", () => {
  const now = new Date("2026-10-06T12:00:00Z");
  const impacts = [
    { domain: "inventory", first_observed: "2026-10-05T08:00:00" },
    { domain: "inventory", first_observed: "2026-09-01T08:00:00" },
    { domain: "financial", first_observed: "2026-10-04T08:00:00" },
  ];
  assert.equal(newThisWeek(impacts, "", now), 2);
  assert.equal(newThisWeek(impacts, "inventory", now), 1);
});
