// Display-only naming for inventory cases. Titles are built only from latest.confirm
// and latest.impact_summary -- the two fields present in both the case list (a slim
// summary) and the case-detail page (the full snapshot) -- so one title function works
// in both places. Detector logic and stored history are unchanged. Falls back to the
// stored title for any case type not listed here, or when the needed number is absent.

import { formatCurrency, formatNumber } from "../format.js";
import { ARCHETYPES, resolveFindingTemplate } from "./findingTemplates.js";
import { INVENTORY_FAMILIES, inventoryFindingType } from "./inventoryTaxonomy.js";

const plural = (n, word) => `${formatNumber(n)} ${word}${n === 1 ? "" : "s"}`;

export function titleFor(item) {
  const latest = item.latest;
  const id = item.metric_id ?? "";
  const impact = latest?.impact_summary;
  const kind = impact?.kind;
  const fallback = latest?.title ?? id;

  if (id.startsWith("extreme_event_")) {
    const lines = latest?.confirm?.current;
    if (lines == null) return fallback;
    return `Large single shortages · ${plural(lines, "line")} · last 30 days`;
  }
  if (id.startsWith("transfer_discrepancy_")) {
    const outlets = impact?.outlet_count;
    if (outlets == null) return fallback;
    const scope = plural(outlets, "outlet");
    if (id.endsWith("_missing_item")) return `Missing items at outlet receiving · ${scope} · last 14 days`;
    if (id.endsWith("_extra_quantity")) return `Extra items at outlet receiving · ${scope}`;
    if (id.endsWith("_product_damage")) return `Damaged items on transfers · ${scope}`;
    return fallback;
  }
  if (id === "dispatch_sent_above_plan") {
    const outlets = impact?.outlet_count;
    if (outlets == null) return fallback;
    return `Sent far above plan · ${plural(outlets, "outlet")} · last 14 days`;
  }
  if (id === "dispatch_pattern") {
    const count = latest?.confirm?.current;
    if (count == null) return fallback;
    return `Higher dispatcher/product shortage rate · ${plural(count, "pair")} · last 14 days`;
  }
  if (kind === "top_sellers_out_of_stock") {
    if (impact.count == null || impact.total_top_sellers == null) return fallback;
    return `Top sellers out of stock · ${formatNumber(impact.count)} of ${formatNumber(impact.total_top_sellers)}`;
  }
  if (kind === "products_out_of_stock") {
    if (impact.distinct_products == null) return fallback;
    return `Products out of stock · ${plural(impact.distinct_products, "product")}${impact.outlet_count ? ` · ${plural(impact.outlet_count, "store")}` : ""}`;
  }
  if (kind === "stuck_stock_count_value_at_risk") {
    if (impact.count == null) return fallback;
    return `Unfinished stock counts · ${plural(impact.count, "count")}`;
  }
  if (kind === "completed_stock_count_variance") {
    if (impact.financial_impact_rs == null) return fallback;
    return `Stock-count variance · ${formatCurrency(impact.financial_impact_rs)}`;
  }
  if (kind === "damage_writeoff") {
    if (impact.financial_impact_rs == null) return fallback;
    return `Damage write-offs · ${formatCurrency(impact.financial_impact_rs)} · last 14 days`;
  }
  if (id === "stockout_cancellation_rate" && latest?.confirm?.pct_change != null) {
    const pct = Math.abs(latest.confirm.pct_change);
    const direction = latest.confirm.pct_change < 0 ? "down" : "up";
    return `Out-of-stock cancellations ${direction} ${formatNumber(Math.round(pct))}%`;
  }
  return fallback;
}

/** Display grouping for inventory investigation cases; detection and API IDs stay unchanged. */
export function inventoryFamilyFor(item = {}) {
  const id = item.metric_id ?? "";
  let key = null;
  if (id.startsWith("extreme_event_") || id === "dispatch_pattern") key = "warehouse_transfer_shortages";
  else if (id.startsWith("transfer_discrepancy_")) {
    key = id.endsWith("_extra_quantity") ? "warehouse_transfer_overages"
      : id.endsWith("_product_damage") ? "damage_and_writeoffs" : "warehouse_transfer_shortages";
  } else if (id === "dispatch_sent_above_plan") key = "warehouse_dispatch_overage";
  else if (["stock_availability", "top_seller_stock"].includes(id)) key = "stock_availability";
  else if (["stuck_stock_count", "completed_stock_count_variance"].includes(id) || id.startsWith("completed_stock_count_variance-")) key = "stock_count_controls";
  else if (id === "damage_writeoff") key = "damage_and_writeoffs";
  else if (id === "dead_overstock_value") key = "inventory_health";
  else if (item.domain === "inventory" && item.category === "opportunity") key = "inventory_opportunities";
  const family = key ? INVENTORY_FAMILIES[key] : null;
  return family ? { key, label: family.label, domainKey: family.domainKey, findingTypes: family.findingTypes } : null;
}

/** The concrete issue pattern. Kept distinct from the evidence archetype. */
export function findingTypeFor(item = {}) {
  return inventoryFindingType(item);
}

// Business-facing type. Stuck counts and count variance are controls, not states.
export function typeKeyFor(item) {
  const archetype = resolveFindingTemplate(item).archetype;
  return ({
    [ARCHETYPES.STATE_SNAPSHOT]: "state",
    [ARCHETYPES.TREND_DEVIATION]: "trend",
    [ARCHETYPES.RECURRING_PATTERN]: "recurring",
    [ARCHETYPES.EXTREME_EVENT]: "extreme",
    [ARCHETYPES.CONTROL_EXCEPTION]: "control",
    [ARCHETYPES.RECORDED_LOSS]: "recorded_loss",
    [ARCHETYPES.OPPORTUNITY_OUTCOME]: "opportunity",
    [ARCHETYPES.LEGACY]: "legacy",
  })[archetype];
}

export const TYPE_LABELS = {
  trend: "Trend",
  state: "State",
  recurring: "Recurring pattern",
  extreme: "Extreme event",
  control: "Control",
  recorded_loss: "Recorded financial event",
  opportunity: "Opportunity outcome",
  legacy: "Legacy",
};

// Existing issue-list type filters classify evidence shape; use the explicit
// name so this dimension cannot be confused with the business finding type.
export const EVIDENCE_CLASS_LABELS = TYPE_LABELS;

// Opportunities have no severity rank, so they show their own label rather than a priority.
export function priorityLabelFor(item, prettify) {
  if (item.category === "opportunity") return "Opportunity";
  return prettify(item.priority || "Unranked");
}
