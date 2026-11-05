// Single source of truth for which rendering family ("tag") an inventory
// finding belongs to -- replaces what used to be separate, ad-hoc kind
// checks duplicated across inventoryDisplay.js (title/type) and
// InventoryCaseView.jsx (chart/table/records), which is exactly how a gap
// like stuck_stock_count's missing product breakdown or dispatch_pattern's
// unfiltered company-wide panel went unnoticed: there was no one place
// that named "these N kinds work the same way" to audit against.
//
// Each tag is a real, distinct DATA SHAPE, not a business category:
//   TRANSFER_TRACE  -- individual receiving-line records (sender, receiver,
//                      outlet, product), company-wide accountability context.
//   DISPATCH        -- outbound-only lines, dispatcher-centric, also feeds
//                      the accountability panel (scoped to the pattern's
//                      own people -- see CaseDetail.jsx's auto-scope effect).
//   NAMED_INCIDENT   -- a handful of individually-named physical events
//                      (stock-count sessions) with wildly uneven size; rank
//                      the actual records, not an outlet aggregate.
//   WRITE_OFF        -- a confirmed loss with both a store/product
//                      breakdown AND a real per-record detail table.
//   STOCK_POSITION   -- a pure snapshot: store and/or product breakdown
//                      only, no individual event to drill into.
// Anything not listed here (e.g. an opportunity case) gets tag `null` and
// falls back to the generic rendering.

export const INVENTORY_TAGS = {
  TRANSFER_TRACE: "transfer_trace",
  DISPATCH: "dispatch",
  NAMED_INCIDENT: "named_incident",
  WRITE_OFF: "write_off",
  STOCK_POSITION: "stock_position",
};

// metric_id -> tag, for ids whose tag doesn't depend on `kind` (list-page
// titleFor/typeKeyFor only ever has metric_id + impact_summary, not the
// full breakdowns InventoryCaseView.jsx has, so prefix/exact metric_id
// matches must be enough on their own here).
const METRIC_PREFIX_TAGS = [
  ["transfer_discrepancy_", INVENTORY_TAGS.TRANSFER_TRACE],
  ["extreme_event_", INVENTORY_TAGS.TRANSFER_TRACE],
  ["completed_stock_count_variance-", INVENTORY_TAGS.NAMED_INCIDENT],
];
const METRIC_EXACT_TAGS = {
  dispatch_sent_above_plan: INVENTORY_TAGS.DISPATCH,
  dispatch_pattern: INVENTORY_TAGS.DISPATCH,
  stuck_stock_count: INVENTORY_TAGS.NAMED_INCIDENT,
  completed_stock_count_variance: INVENTORY_TAGS.NAMED_INCIDENT,
  damage_writeoff: INVENTORY_TAGS.WRITE_OFF,
  stock_availability: INVENTORY_TAGS.STOCK_POSITION,
  top_seller_stock: INVENTORY_TAGS.STOCK_POSITION,
  dead_overstock_value: INVENTORY_TAGS.STOCK_POSITION,
};

/** metricId: item.metric_id (list) or latest.metric_id (detail).
 * hasExtremeRows: whether this finding carries extreme_rows -- extreme_event_*
 * findings are identified by metric_id prefix already, but this also covers
 * any future kind that reuses the same extreme-rows shape without a matching
 * metric_id prefix, same defensive check InventoryCaseView.jsx already did
 * ad hoc before this registry existed. */
export function tagFor(metricId, { hasExtremeRows = false } = {}) {
  const id = metricId ?? "";
  if (hasExtremeRows) return INVENTORY_TAGS.TRANSFER_TRACE;
  for (const [prefix, tag] of METRIC_PREFIX_TAGS) {
    if (id.startsWith(prefix)) return tag;
  }
  return METRIC_EXACT_TAGS[id] ?? null;
}
