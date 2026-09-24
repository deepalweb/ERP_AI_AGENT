import assert from "node:assert/strict";
import { test } from "node:test";
import {
  additionalObservationsText, baselineVolatilityCaution, decisionPromptText, evidenceRollupText,
  financialImpactText, metricSnapshotText, negativeStockText,
} from "./plainLanguage.js";

function check(verdict) {
  return { source: verdict, verdict, detail: `a ${verdict} check` };
}

test("no root_cause at all is reported honestly, not invented as a cause", () => {
  assert.equal(evidenceRollupText(undefined), "No evidence checks have been recorded yet.");
  assert.equal(evidenceRollupText(null), "No evidence checks have been recorded yet.");
});

test("root_cause with an empty checks array is reported the same way, not as a confirmed absence of a cause", () => {
  assert.equal(evidenceRollupText({ checks: [] }), "No evidence checks have been recorded yet.");
});

test("all checks confirmed: concentration is stated as fact, causation is explicitly not", () => {
  // Regression (Visual Investigation V2, Phase 1): "confirmed" is exclusively
  // produced by the concentration checks (evidence_engine.py's own
  // docstring) -- it must never be reworded into "the cause is confirmed".
  const text = evidenceRollupText({ checks: [check("confirmed"), check("confirmed")] });
  assert.match(text, /confirms where this is concentrated/);
  assert.doesNotMatch(text, /cause is confirmed/);
  assert.doesNotMatch(text, /underlying cause is confirmed/);
});

test("a mix of confirmed and unconfirmed checks does not overclaim full confirmation", () => {
  const text = evidenceRollupText({ checks: [check("confirmed"), check("no_evidence_found")] });
  assert.match(text, /confirms where part of this is concentrated.*remain unconfirmed/);
  assert.doesNotMatch(text, /cause is confirmed/);
});

test("possible_contributor alone (no confirmed) is hedged, never stated as an established cause", () => {
  // This is the regression case: possible_contributor and confirmed are
  // different claims (evidence_engine.py's own vocabulary), and treating
  // a purely-possible contributor as "the cause is supported by evidence"
  // would overclaim what the backend actually established.
  const text = evidenceRollupText({ checks: [check("possible_contributor")] });
  assert.match(text, /Possible contributing factors have been identified/);
  // "none is confirmed" (a hedge) is fine; "the cause IS confirmed" (an
  // unhedged claim) is exactly the overclaim this must never produce.
  assert.doesNotMatch(text, /cause is confirmed/);
});

test("a mix of possible_contributor and no_evidence_found (no confirmed) still only claims 'possible', not confirmed", () => {
  const text = evidenceRollupText({ checks: [check("possible_contributor"), check("no_evidence_found")] });
  assert.match(text, /Possible contributing factors/);
});

test("only no_evidence_found / unknown / related_not_independent: nothing established, nothing invented", () => {
  const text = evidenceRollupText({ checks: [check("no_evidence_found"), check("unknown"), check("related_not_independent")] });
  assert.equal(text, "Where this is concentrated, and why it happened, have not yet been established.");
});

test("financialImpactText: no impact object at all -> not quantified", () => {
  assert.equal(financialImpactText(null), "Financial impact has not been quantified.");
  assert.equal(financialImpactText(undefined), "Financial impact has not been quantified.");
});

test("financialImpactText: a real priced impact (financial_impact_rs set) uses its own detail sentence", () => {
  const impact = { financial_impact_rs: 12345, detail: "Rs. 12,345 below the usual range this month." };
  assert.equal(financialImpactText(impact), impact.detail);
});

test("REGRESSION: a state finding's impact (financial_impact_rs: None, but a real operational detail/count) must still read as not-quantified, not restate the operational detail as a financial claim", () => {
  // Exactly state_findings.py's shape (e.g. _top_seller_stock_finding):
  // a real, non-fabricated `detail` describing a count, but no Rs figure.
  const impact = {
    financial_impact_rs: null,
    count: 1,
    detail: "1 of your 15 best-selling products are completely out of stock.",
  };
  assert.equal(financialImpactText(impact), "Financial impact has not been quantified.");
});

// -- Celeste Investigate Phase 2 (flagship stock-out workflow) --------------

test("metricSnapshotText: no confirm at all -> null", () => {
  assert.equal(metricSnapshotText(null, "top_seller_stock"), null);
  assert.equal(metricSnapshotText({}, "top_seller_stock"), null);
});

test("metricSnapshotText: a real baseline/pct_change (trend finding) -> null, caller keeps its own 'vs baseline' rendering", () => {
  const latest = { confirm: { current: 10, baseline: 8, pct_change: 25.0, unit: "count", period: "2026-09" } };
  assert.equal(metricSnapshotText(latest, "revenue"), null);
});

test("metricSnapshotText: top_seller_stock with a real total_top_sellers uses the specific sentence", () => {
  // REGRESSION: this is the "1n/a vs baseline" bug -- a snapshot finding
  // has no baseline at all, so it must never render a baseline comparison.
  const latest = {
    confirm: { current: 1, baseline: null, pct_change: null, unit: "count", period: "2026-09-15" },
    impact: { total_top_sellers: 15 },
  };
  // Metric Truthfulness Correction, Task 1: "out of stock" claimed verified
  // unavailability; this only ever measures recorded on-hand quantity.
  assert.equal(metricSnapshotText(latest, "top_seller_stock"), "1 of 15 top-selling products recorded at zero or negative stock.");
});

test("metricSnapshotText: top_seller_stock without the total_top_sellers field falls back to the generic snapshot text, never a fabricated denominator", () => {
  const latest = { confirm: { current: 1, baseline: null, pct_change: null, unit: "count", period: "2026-09-15" }, impact: {} };
  const text = metricSnapshotText(latest, "top_seller_stock");
  assert.match(text, /^1 as of .+\.$/);
});

test("metricSnapshotText: any other snapshot metric_id gets the generic honest fallback, not an invented sentence", () => {
  const latest = { confirm: { current: 2, baseline: null, pct_change: null, unit: "count", period: "2026-09-18" } };
  const text = metricSnapshotText(latest, "outlet_no_orders");
  assert.match(text, /^2 as of .+\.$/);
});

test("negativeStockText: no breakdowns -> null", () => {
  assert.equal(negativeStockText({}), null);
  assert.equal(negativeStockText(null), null);
});

test("negativeStockText: breakdowns exist but nothing is flagged negative -> null", () => {
  const latest = { breakdowns: [{ top_contributors: [{ name: "Milk 1L", current: 40, negative_stock: false }] }] };
  assert.equal(negativeStockText(latest), null);
});

test("negativeStockText: only fires for a product whose OWN contributor entry carries negative_stock -- the same row the SKU/current-stock figure comes from, never inferred by name-matching", () => {
  const latest = {
    breakdowns: [{ top_contributors: [{ name: "Set Yogurt 80G - SKU 000638", sku: "000638", current: -1, negative_stock: true }] }],
  };
  const text = negativeStockText(latest);
  assert.match(text, /Set Yogurt 80G - SKU 000638 also shows a reported negative on-hand quantity/);
  assert.match(text, /underlying cause has not been verified/);
});

test("negativeStockText: multiple negative products use plural phrasing", () => {
  const latest = {
    breakdowns: [{
      top_contributors: [
        { name: "A", negative_stock: true },
        { name: "B", negative_stock: true },
      ],
    }],
  };
  assert.match(negativeStockText(latest), /A, B also show a reported negative/);
});

test("additionalObservationsText: no breakdowns, or only the primary one -> null", () => {
  assert.equal(additionalObservationsText({}), null);
  assert.equal(additionalObservationsText({ breakdowns: [{ label: "Current stock (units)", top_contributors: [{ name: "x" }] }] }), null);
});

test("additionalObservationsText: a second breakdown with no contributors -> null, not an empty claim", () => {
  const latest = {
    breakdowns: [
      { label: "Current stock (units)", top_contributors: [{ name: "x" }] },
      { label: "Other top sellers under 3 days of cover", top_contributors: [] },
    ],
  };
  assert.equal(additionalObservationsText(latest), null);
});

test("additionalObservationsText: a real second breakdown becomes a plain count + label summary", () => {
  const latest = {
    breakdowns: [
      { label: "Current stock (units)", top_contributors: [{ name: "x" }] },
      { label: "Other top sellers under 3 days of cover", top_contributors: [{ name: "a" }, { name: "b" }] },
    ],
  };
  assert.equal(additionalObservationsText(latest), "2 more: other top sellers under 3 days of cover.");
});

test("decisionPromptText: an active case prompts a decision; a closed case does not", () => {
  assert.match(decisionPromptText("open"), /review the evidence and decide/);
  assert.match(decisionPromptText("reopened"), /review the evidence and decide/);
  assert.match(decisionPromptText("verification_pending"), /Recovery is being verified/);
  assert.equal(decisionPromptText("resolved"), null);
  assert.equal(decisionPromptText("dismissed"), null);
});

// -- Celeste Investigate Phase 3 (revenue investigation) --------------------

test("baselineVolatilityCaution: REGRESSION -- the real revenue case (85% pct_change, 1.19 z-score) triggers the caution", () => {
  const text = baselineVolatilityCaution({ pct_change: 84.85, z_score: 1.19 });
  assert.match(text, /pct-change rule alone/);
  assert.match(text, /1\.19/);
  assert.match(text, /below the 1\.5 significance threshold/);
});

test("baselineVolatilityCaution: a large pct_change corroborated by a large z-score needs no caution", () => {
  assert.equal(baselineVolatilityCaution({ pct_change: 40, z_score: 2.1 }), null);
});

test("baselineVolatilityCaution: a small pct_change never triggers the caution regardless of z-score", () => {
  assert.equal(baselineVolatilityCaution({ pct_change: 5, z_score: 0.2 }), null);
});

test("baselineVolatilityCaution: missing confirm, pct_change, or z_score -> null, never a fabricated caution", () => {
  assert.equal(baselineVolatilityCaution(null), null);
  assert.equal(baselineVolatilityCaution({}), null);
  assert.equal(baselineVolatilityCaution({ pct_change: 50, z_score: null }), null);
  assert.equal(baselineVolatilityCaution({ pct_change: null, z_score: 0.5 }), null);
});

test("baselineVolatilityCaution: works symmetrically for a large negative pct_change with a weak z-score", () => {
  const text = baselineVolatilityCaution({ pct_change: -30, z_score: -0.8 });
  assert.match(text, /pct-change rule alone/);
});
