import test from "node:test";
import assert from "node:assert/strict";
import { exposureByDomain, kpiSummary, needsAttention } from "./overviewMath.js";

const NOW = new Date("2026-10-06T00:00:00Z");

test("KPI values come straight from the summary; resolved is labeled all-time", () => {
  const kpis = kpiSummary({
    cases: { open: 7, verification_pending: 2, resolved: 9 },
    casePriorities: { critical: 3, high: 1, medium: 4 },
    impacts: [],
    now: NOW,
  });
  const byId = Object.fromEntries(kpis.map((k) => [k.id, k]));
  assert.equal(byId.open.value, 7);
  assert.equal(byId.critical_high.value, 4);
  assert.equal(byId.verification.value, 2);
  assert.equal(byId.resolved.label, "Resolved (all time)");
  assert.equal(byId.resolved.value, 9);
});

test("New this week counts first observations in the last 7 days only", () => {
  const impacts = [
    { first_observed: "2026-10-03T10:00:00" },
    { first_observed: "2026-09-25T10:00:00" },
    { first_observed: null },
  ];
  const kpis = kpiSummary({ cases: {}, casePriorities: {}, impacts, now: NOW });
  assert.equal(kpis.find((k) => k.id === "new_week").value, 1);
});

test("Quantified-exposure KPI reports the count of total active cases beside it", () => {
  const impacts = [
    { impact: { financial_impact_rs: 1000 } },
    { impact: { financial_impact_rs: null } },
    { impact: {} },
  ];
  const kpi = kpiSummary({ cases: {}, casePriorities: {}, impacts, now: NOW }).find((k) => k.id === "quantified");
  assert.equal(kpi.value, 1);
  assert.equal(kpi.of, 3);
});

test("needs-attention lists only non-zero items and flags low-coverage estimates", () => {
  const items = needsAttention({
    cases: { verification_pending: 1 },
    casePriorities: { critical: 2 },
    impacts: [
      { impact: { financial_impact_rs: 500, valued_units_pct: 3 } },
      { impact: { financial_impact_rs: 500, valued_units_pct: 90 } },
    ],
  });
  assert.deepEqual(items.map((i) => i.id), ["critical", "verification", "unvalued"]);
  assert.equal(items[0].label, "2 critical cases need action");
  assert.equal(items[2].label, "1 rupee estimate rest on under half their units");
});

test("needs-attention is empty when nothing needs action", () => {
  assert.deepEqual(needsAttention({ cases: {}, casePriorities: {}, impacts: [] }), []);
});

test("exposure chart leaves out cases on other bases, such as revenue", () => {
  const rows = exposureByDomain([
    { domain: "revenue", impact: { kind: "revenue", financial_impact_rs: 92900000 } },
    { domain: "inventory", impact: { kind: "transfer_discrepancy", financial_impact_rs: 10, units_affected: 5, valued_units_pct: 100 } },
  ]);
  assert.deepEqual(rows.map((r) => r.domain), ["inventory"]);
  assert.equal(rows[0].exposure, 10);
});

test("domain coverage weights each case by its units, not by case count", () => {
  const rows = exposureByDomain([
    { domain: "inventory", impact: { kind: "transfer_discrepancy", financial_impact_rs: 100, units_affected: 900, valued_units_pct: 100 } },
    { domain: "inventory", impact: { kind: "transfer_discrepancy", financial_impact_rs: 50, units_affected: 100, valued_units_pct: 0 } },
  ]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].exposure, 150);
  assert.equal(rows[0].quantifiedCases, 2);
  assert.equal(rows[0].coverage, 90);
});

test("domain coverage is null when no units are recorded", () => {
  const [row] = exposureByDomain([{ domain: "inventory", impact: { kind: "transfer_discrepancy", financial_impact_rs: 10 } }]);
  assert.equal(row.coverage, null);
});
