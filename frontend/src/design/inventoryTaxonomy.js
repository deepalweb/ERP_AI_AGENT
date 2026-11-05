/**
 * Manager-facing inventory taxonomy.
 *
 * Business domains and finding families organize cases; finding type and
 * evidence class describe the individual case. Priority, status, and
 * measurement window remain separate dimensions.
 */
export const INVENTORY_DOMAINS = Object.freeze([
  {
    key: "stock_movement",
    label: "Stock Movement & Transfer Discrepancies",
    description: "Compare what was planned, sent, and recorded at outlet receiving.",
  },
  {
    key: "stock_availability_risk",
    label: "Stock Availability & Stockout Risk",
    description: "See where recorded stock may not meet current or high-demand needs.",
  },
  {
    key: "stock_record_controls",
    label: "Stock Count & Inventory Record Controls",
    description: "Track unfinished counts and reconcile completed-count variances.",
  },
  {
    key: "damage_writeoff_losses",
    label: "Inventory Damage & Write-off Losses",
    description: "Review transfer damage records separately from completed write-offs.",
  },
  {
    key: "inventory_performance",
    label: "Inventory Performance & Opportunities",
    description: "Review inventory health and measurable improvements without assuming causation.",
  },
]);

export const INVENTORY_FAMILIES = Object.freeze({
  warehouse_transfer_shortages: {
    label: "Warehouse transfer shortages",
    domainKey: "stock_movement",
    findingTypes: ["Missing items at outlet receiving", "Large single shortages", "Recurring dispatcher/product shortage pattern"],
  },
  warehouse_transfer_overages: {
    label: "Warehouse transfer overages",
    domainKey: "stock_movement",
    findingTypes: ["Extra items recorded at outlet receiving"],
  },
  warehouse_dispatch_overage: {
    label: "Warehouse dispatch overage",
    domainKey: "stock_movement",
    findingTypes: ["Dispatch sent far above plan"],
  },
  stock_availability: {
    label: "Stock availability",
    domainKey: "stock_availability_risk",
    findingTypes: ["Products out of stock", "Top-selling products out of stock"],
  },
  stock_count_controls: {
    label: "Stock-count controls",
    domainKey: "stock_record_controls",
    findingTypes: ["Unfinished stock counts", "Completed stock-count variance"],
  },
  damage_and_writeoffs: {
    label: "Damage and write-off events",
    domainKey: "damage_writeoff_losses",
    findingTypes: ["Damage recorded on transfers", "Recorded damage write-offs"],
  },
  inventory_health: {
    label: "Inventory health & overstock",
    domainKey: "inventory_performance",
    findingTypes: ["Dead and overstocked inventory value"],
  },
  inventory_opportunities: {
    label: "Inventory opportunities",
    domainKey: "inventory_performance",
    findingTypes: ["Improvement in out-of-stock cancellations"],
  },
});

export function inventoryFamilyMeta(key) {
  return INVENTORY_FAMILIES[key] ?? null;
}

export function inventoryDomainMeta(key) {
  return INVENTORY_DOMAINS.find((domain) => domain.key === key) ?? null;
}

export function inventoryFindingType(item = {}) {
  const id = item.metric_id ?? "";
  if (item.domain && item.domain !== "inventory") return null;
  if (id.startsWith("extreme_event_")) return "Large single shortages";
  if (id === "dispatch_pattern") return "Recurring dispatcher/product shortage pattern";
  if (id.startsWith("transfer_discrepancy_")) {
    if (id.endsWith("_missing_item")) return "Missing items at outlet receiving";
    if (id.endsWith("_extra_quantity")) return "Extra items recorded at outlet receiving";
    if (id.endsWith("_product_damage")) return "Damage recorded on transfers";
  }
  if (id === "dispatch_sent_above_plan") return "Dispatch sent far above plan";
  if (id === "stock_availability") return "Products out of stock";
  if (id === "top_seller_stock") return "Top-selling products out of stock";
  if (id === "stuck_stock_count") return "Unfinished stock counts";
  if (id === "completed_stock_count_variance" || id.startsWith("completed_stock_count_variance-")) {
    return "Completed stock-count variance";
  }
  if (id === "damage_writeoff") return "Recorded damage write-offs";
  if (id === "dead_overstock_value") return "Dead and overstocked inventory value";
  if (id === "stockout_cancellation_rate") return "Improvement in out-of-stock cancellations";
  return item.title ?? item.latest?.title ?? (id || "Unclassified inventory finding");
}

/**
 * Use detector-provided window length where available. A case lifecycle
 * horizon such as "snapshot" is not itself the evidence observation window.
 */
export function observationWindowLabel(item = {}) {
  const impact = item.impact ?? item.latest?.impact ?? item.impact_summary ?? {};
  const days = impact.window_days ?? item.latest?.window_days;
  if (Number.isFinite(Number(days)) && Number(days) > 0) return "Trailing " + formatDays(Number(days));
  const id = item.metric_id ?? "";
  if (id.startsWith("extreme_event_")) return "Trailing 30 days";
  if (id.startsWith("transfer_discrepancy_") || id === "dispatch_pattern" || id === "dispatch_sent_above_plan") {
    return "Trailing 14 days";
  }
  if (id === "damage_writeoff") return "Trailing 14 days";
  if (id === "completed_stock_count_variance" || id.startsWith("completed_stock_count_variance-")) return "Trailing 7 days";
  const period = item.confirm?.period ?? item.latest?.confirm?.period;
  if (period && /^\d{4}-\d{2}$/.test(period)) return "Monthly period " + period;
  if (period && /^\d{4}-\d{2}-\d{2}$/.test(period)) return "As of " + period;
  return item.horizon === "snapshot" ? "Point-in-time measure" : "Window not recorded";
}

function formatDays(days) {
  return days + " day" + (days === 1 ? "" : "s");
}
