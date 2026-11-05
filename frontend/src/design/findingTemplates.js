/**
 * Presentation metadata for persisted findings. Detection still owns the
 * evidence; this module only classifies the saved result for rendering.
 * Keep business interpretation separate from archetype: an opportunity can
 * still be a trend deviation.
 */
export const ARCHETYPES = Object.freeze({
  STATE_SNAPSHOT: "state_snapshot",
  TREND_DEVIATION: "trend_deviation",
  RECURRING_PATTERN: "recurring_pattern",
  EXTREME_EVENT: "extreme_event",
  CONTROL_EXCEPTION: "control_exception",
  RECORDED_LOSS: "recorded_loss",
  OPPORTUNITY_OUTCOME: "opportunity_outcome",
  LEGACY: "legacy",
});

export const ARCHETYPE_LABELS = Object.freeze({
  [ARCHETYPES.STATE_SNAPSHOT]: "Current state",
  [ARCHETYPES.TREND_DEVIATION]: "Trend and baseline deviation",
  [ARCHETYPES.RECURRING_PATTERN]: "Recurring operational pattern",
  [ARCHETYPES.EXTREME_EVENT]: "Extreme event",
  [ARCHETYPES.CONTROL_EXCEPTION]: "Control and reconciliation",
  [ARCHETYPES.RECORDED_LOSS]: "Recorded financial event",
  [ARCHETYPES.OPPORTUNITY_OUTCOME]: "Opportunity outcome",
  [ARCHETYPES.LEGACY]: "Legacy finding",
});

const CONTROL_METRICS = new Set([
  "stuck_stock_count",
  "completed_stock_count_variance",
  "negative_stock_control",
  "cost_coverage_control",
]);

/** Resolve the result archetype, domain evidence module, and interpretation.
 * Unknown values get the legacy shell; they never inherit a specialized view. */
export function resolveFindingTemplate(finding = {}) {
  const metricId = finding.metric_id ?? "";
  const domain = finding.domain ?? "unclassified";
  const horizon = finding.horizon;
  let archetype = ARCHETYPES.LEGACY;
  let evidenceModule = "generic";
  let result = null;

  if (metricId.startsWith("operation_flow_")) {
    archetype = ARCHETYPES.CONTROL_EXCEPTION;
    evidenceModule = "operations";
    result = "operation_flow";
  } else if (metricId === "stock_move_missing") {
    // Episode cluster of confirmed sales with no stock deduction -- a
    // reconciliation control, same archetype as operation_flow_, not a
    // generic inventory-state snapshot (would otherwise fall into the
    // domain==="inventory" catch-all below, which has no matching renderer).
    archetype = ARCHETYPES.CONTROL_EXCEPTION;
    evidenceModule = "inventory";
    result = "stock_reconciliation";
  } else if (metricId === "po_price_deviation") {
    // Per-product outlier against its own purchase history -- same shape
    // as an extreme_event_ finding, not a generic supplier trend (would
    // otherwise fall into the domain==="supplier" catch-all below, which
    // maps to SupplierInvestigationPanel -- built for a different data shape).
    archetype = ARCHETYPES.EXTREME_EVENT;
    evidenceModule = "supplier";
    result = "price_intelligence";
  } else if (metricId === "fresh_production_missed" || metricId === "fresh_production_shortfall" || metricId === "fresh_production_cycle_time") {
    // Fresh-round production orders at the central warehouse -- missed
    // (never confirmed) or off-plan output. Same control-exception shape
    // as stock_move_missing/operation_flow, not a generic inventory-state
    // snapshot.
    archetype = ARCHETYPES.CONTROL_EXCEPTION;
    evidenceModule = "inventory";
    result = "fresh_production";
  } else if (metricId === "damage_writeoff") {
    archetype = ARCHETYPES.RECORDED_LOSS;
    evidenceModule = "inventory";
    result = "inventory_case";
  } else if (metricId.startsWith("extreme_event_") || metricId === "dispatch_sent_above_plan") {
    archetype = ARCHETYPES.EXTREME_EVENT;
    evidenceModule = domain;
    result = metricId.startsWith("extreme_event_") ? "extreme_event" : "inventory_case";
  } else if (CONTROL_METRICS.has(metricId) || metricId.startsWith("completed_stock_count_variance-")
      || domain === "controls") {
    archetype = ARCHETYPES.CONTROL_EXCEPTION;
    evidenceModule = domain;
    result = metricId === "stuck_stock_count" ? "stuck_stock_count"
      : metricId === "completed_stock_count_variance" || metricId.startsWith("completed_stock_count_variance-") ? "completed_variance"
        : domain === "controls" ? "controls" : null;
  } else if (metricId.startsWith("transfer_discrepancy_") || metricId.startsWith("incident_")
      || metricId === "dispatch_pattern") {
    archetype = ARCHETYPES.RECURRING_PATTERN;
    evidenceModule = domain;
    result = metricId.startsWith("incident_") ? "incident"
      : metricId.startsWith("transfer_discrepancy_") ? "transfer_discrepancy" : "inventory_case";
  } else if (["stock_availability", "top_seller_stock"].includes(metricId)
      || (horizon === "snapshot" && domain === "inventory")) {
    archetype = ARCHETYPES.STATE_SNAPSHOT;
    evidenceModule = domain;
    result = "inventory_case";
  } else if (metricId === "revenue") {
    archetype = ["monthly", "weekly"].includes(horizon) ? ARCHETYPES.TREND_DEVIATION : ARCHETYPES.LEGACY;
    evidenceModule = domain;
    result = "revenue";
  } else if (metricId === "second_order_conversion_rate_30d") {
    archetype = ["monthly", "weekly"].includes(horizon) ? ARCHETYPES.TREND_DEVIATION : ARCHETYPES.LEGACY;
    evidenceModule = domain;
    result = "second_order_conversion";
  } else if (domain === "supplier") {
    archetype = ["monthly", "weekly"].includes(horizon) ? ARCHETYPES.TREND_DEVIATION : ARCHETYPES.LEGACY;
    evidenceModule = domain;
    result = "supplier";
  } else if (["monthly", "weekly"].includes(horizon)) {
    archetype = ARCHETYPES.TREND_DEVIATION;
    evidenceModule = domain;
    result = "trend";
  } else if (finding.category === "opportunity") {
    archetype = ARCHETYPES.OPPORTUNITY_OUTCOME;
    evidenceModule = domain;
    result = "opportunity";
  } else if (horizon === "snapshot") {
    archetype = ARCHETYPES.STATE_SNAPSHOT;
    evidenceModule = domain;
  }

  const direction = finding.metric?.direction ?? null;
  const change = finding.confirm?.current != null && finding.confirm?.baseline != null
    ? finding.confirm.current - finding.confirm.baseline
    : null;
  const directionKnown = ["higher_is_good", "higher_is_bad"].includes(direction);
  const favorable = directionKnown && change != null && (change === 0 || (change > 0) === (direction === "higher_is_good"));
  const interpretation = finding.category === "critical"
    ? "adverse"
    : finding.category === "opportunity"
      ? "opportunity"
      : finding.category === "favorable"
        ? "favorable"
        : change != null
          ? directionKnown ? favorable ? "favorable" : "adverse" : "verification_required"
          : archetype === ARCHETYPES.LEGACY ? "verification_required" : "neutral";

  return {
    archetype,
    label: ARCHETYPE_LABELS[archetype],
    domain,
    evidenceModule,
    interpretation,
    direction,
    result,
    isLegacy: archetype === ARCHETYPES.LEGACY,
  };
}

