import assert from "node:assert/strict";
import test from "node:test";
import { ARCHETYPES, resolveFindingTemplate } from "./findingTemplates.js";
import { familyExposureLabel } from "./familySummaryMath.js";
import { inventoryFamilyFor, typeKeyFor } from "./inventoryDisplay.js";

test("monthly improvement remains a trend template with opportunity interpretation", () => {
  const template = resolveFindingTemplate({
    metric_id: "stockout_cancellation_rate",
    domain: "inventory",
    horizon: "monthly",
    category: "opportunity",
    metric: { direction: "higher_is_bad" },
  });
  assert.equal(template.archetype, ARCHETYPES.TREND_DEVIATION);
  assert.equal(template.interpretation, "opportunity");
  assert.equal(typeKeyFor({ metric_id: "stockout_cancellation_rate", domain: "inventory", horizon: "monthly", category: "opportunity" }), "trend");
});

test("known metric direction interprets change; unknown direction requires verification", () => {
  const favorable = resolveFindingTemplate({
    metric_id: "supplier_fill_rate", domain: "supplier", horizon: "monthly",
    confirm: { current: 98, baseline: 93 }, metric: { direction: "higher_is_good" },
  });
  const uncertain = resolveFindingTemplate({
    metric_id: "custom_metric", domain: "financial", horizon: "monthly",
    confirm: { current: 98, baseline: 93 },
  });
  assert.equal(favorable.interpretation, "favorable");
  assert.equal(uncertain.interpretation, "verification_required");
});

test("known finding families map to the intended result archetypes", () => {
  assert.equal(resolveFindingTemplate({ metric_id: "stock_availability", domain: "inventory" }).archetype, ARCHETYPES.STATE_SNAPSHOT);
  assert.equal(resolveFindingTemplate({ metric_id: "transfer_discrepancy_s46_missing_item", domain: "inventory" }).archetype, ARCHETYPES.RECURRING_PATTERN);
  assert.equal(resolveFindingTemplate({ metric_id: "extreme_event_missing_item", domain: "inventory" }).archetype, ARCHETYPES.EXTREME_EVENT);
  assert.equal(resolveFindingTemplate({ metric_id: "negative_stock_control", domain: "controls" }).archetype, ARCHETYPES.CONTROL_EXCEPTION);
  assert.equal(resolveFindingTemplate({ metric_id: "damage_writeoff", domain: "inventory" }).archetype, ARCHETYPES.RECORDED_LOSS);
});

test("inventory finding IDs resolve to their management family labels", () => {
  const cases = [
    ["transfer_discrepancy_s46_missing_item", "Warehouse transfer shortages"],
    ["transfer_discrepancy_extra_quantity", "Warehouse transfer overages"],
    ["dispatch_sent_above_plan", "Warehouse dispatch overage"],
    ["transfer_discrepancy_product_damage", "Damage and write-off events"],
    ["stock_availability", "Stock availability"],
    ["completed_stock_count_variance-all-90d", "Stock-count controls"],
    ["damage_writeoff", "Damage and write-off events"],
  ];
  for (const [metric_id, label] of cases) {
    assert.equal(inventoryFamilyFor({ metric_id, domain: "inventory" })?.label, label, metric_id);
  }
  assert.equal(inventoryFamilyFor({ metric_id: "stockout_cancellation_rate", domain: "inventory", category: "opportunity" })?.label, "Inventory opportunities");
  assert.equal(inventoryFamilyFor({ metric_id: "revenue", domain: "financial" }), null);
});

test("representative saved findings keep their evidence in the right archetype", () => {
  const examples = [
    {
      name: "recurring transfer shortage",
      finding: { metric_id: "transfer_discrepancy_missing_item", domain: "inventory", trace_rows: [{}], transfer_rows: [{}] },
      archetype: ARCHETYPES.RECURRING_PATTERN,
      result: "transfer_discrepancy",
    },
    {
      name: "extreme shortage",
      finding: { metric_id: "extreme_event_missing_item", domain: "inventory", extreme_rows: [{}] },
      archetype: ARCHETYPES.EXTREME_EVENT,
      result: "extreme_event",
    },
    {
      name: "completed stock-count variance",
      finding: { metric_id: "completed_stock_count_variance", domain: "inventory", sessions: [{}] },
      archetype: ARCHETYPES.CONTROL_EXCEPTION,
      result: "completed_variance",
    },
    {
      name: "damage write-off",
      finding: { metric_id: "damage_writeoff", domain: "inventory", top_products: [{}], impact: { kind: "damage_writeoff" } },
      archetype: ARCHETYPES.RECORDED_LOSS,
      result: "inventory_case",
    },
    {
      name: "stockout cancellation improvement",
      finding: {
        metric_id: "stockout_cancellation_rate", domain: "inventory", horizon: "monthly", category: "opportunity",
        metric: { direction: "higher_is_bad" }, confirm: { current: 0.49, baseline: 0.82 }, trend: [{}, {}],
      },
      archetype: ARCHETYPES.TREND_DEVIATION,
      result: "trend",
      interpretation: "opportunity",
    },
    {
      name: "customer trend",
      finding: { metric_id: "customer_retention_rate", domain: "order_customer", horizon: "monthly", category: "critical", trend: [{}, {}] },
      archetype: ARCHETYPES.TREND_DEVIATION,
      result: "trend",
      interpretation: "adverse",
    },
    {
      name: "supplier trend",
      finding: {
        metric_id: "supplier_fill_rate", domain: "supplier", horizon: "monthly", category: "critical",
        metric: { direction: "higher_is_good", notes: "Blended on-time and fill measure." }, trend: [{}, {}],
      },
      archetype: ARCHETYPES.TREND_DEVIATION,
      result: "supplier",
      interpretation: "adverse",
    },
    {
      name: "missing stock deduction reconciliation",
      finding: { metric_id: "stock_move_missing", domain: "inventory" },
      archetype: ARCHETYPES.CONTROL_EXCEPTION,
      result: "stock_reconciliation",
    },
    {
      name: "purchase order price deviation",
      finding: { metric_id: "po_price_deviation", domain: "supplier" },
      archetype: ARCHETYPES.EXTREME_EVENT,
      result: "price_intelligence",
    },
    {
      name: "fresh production missed round",
      finding: { metric_id: "fresh_production_missed", domain: "inventory" },
      archetype: ARCHETYPES.CONTROL_EXCEPTION,
      result: "fresh_production",
    },
    {
      name: "fresh production output shortfall",
      finding: { metric_id: "fresh_production_shortfall", domain: "inventory" },
      archetype: ARCHETYPES.CONTROL_EXCEPTION,
      result: "fresh_production",
    },
    {
      name: "fresh production cycle time",
      finding: { metric_id: "fresh_production_cycle_time", domain: "inventory" },
      archetype: ARCHETYPES.CONTROL_EXCEPTION,
      result: "fresh_production",
    },
  ];

  for (const example of examples) {
    const template = resolveFindingTemplate(example.finding);
    assert.equal(template.archetype, example.archetype, example.name);
    assert.equal(template.result, example.result, example.name);
    if (example.interpretation) assert.equal(template.interpretation, example.interpretation, example.name);
  }
});

test("unknown legacy findings stay on the generic fallback", () => {
  const template = resolveFindingTemplate({ metric_id: "unknown_old_finding", domain: "unclassified" });
  assert.equal(template.archetype, ARCHETYPES.LEGACY);
  assert.equal(template.result, null);
  assert.equal(template.isLegacy, true);
});

test("family exposure labels preserve value uncertainty and do not turn null into zero", () => {
  assert.equal(familyExposureLabel({ impact_summary: { financial_impact_rs: null } }), "Not quantified");
  assert.match(familyExposureLabel({ impact_summary: { financial_impact_rs: 0, valued_units_pct: 0 } }), /Estimated exposure/);
  assert.match(familyExposureLabel({ category: "opportunity", impact_summary: { financial_impact_rs: 100 } }), /Estimated benefit/);
});

test("family exposure labels never imply a duplicate or overlapping child is additive", () => {
  assert.equal(familyExposureLabel({
    impact_summary: { financial_impact_rs: 100 },
    related_evidence: [{ relationship_type: "duplicate_of" }],
  }), "Duplicate evidence · do not add");
  assert.equal(familyExposureLabel({
    impact_summary: { financial_impact_rs: 100 },
    related_evidence: [{ relationship_type: "overlaps" }],
  }), "Overlapping evidence · may not be additive");
});
