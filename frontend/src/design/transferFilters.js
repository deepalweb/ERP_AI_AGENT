// Filters for one transfer finding's own lines (its trace rows). The same filter state
// drives the company-wide panel on the server; here the lines are narrowed in the browser
// with the same ids and keys the server uses:
//   outlet  -> outlet id (matched through the outlet name on each line)
//   product -> sku, or the product name when a line has no sku (the server's product key)
//   person  -> sent-by or received-by login (matches either role)
//   from/to -> receipt done date (yyyy-mm-dd, inclusive)
//   recurring -> the server's recurring product keys
// Lines are the trace rows stored with the finding, so the headline KPIs above the
// filtered sections still describe the whole finding.

const NOT_RECORDED = "Not recorded";

export function isFilterActive(filters) {
  return Boolean(filters && (filters.outlet?.length || filters.product?.length || filters.person?.length || filters.from || filters.to || filters.recurring));
}

export function filterTraceRows(rows, filters, options) {
  if (!isFilterActive(filters)) return rows;
  const outletIdByName = Object.fromEntries((options?.outlets ?? []).map((o) => [o.name, o.id]));
  const recurring = new Set(options?.recurring ?? []);
  const outlets = new Set(filters.outlet ?? []);
  const products = new Set(filters.product ?? []);
  const people = new Set(filters.person ?? []);
  return rows.filter((row) => {
    if (outlets.size && !outlets.has(outletIdByName[row.outlet])) return false;
    const productKey = row.sku || row.product;
    if (products.size && !products.has(productKey)) return false;
    if (people.size) {
      const roles = [row.out_sent_by ?? NOT_RECORDED, row.receipt_completed_by ?? NOT_RECORDED];
      if (!roles.some((who) => people.has(who))) return false;
    }
    const day = (row.received_on ?? "").slice(0, 10);
    if ((filters.from || filters.to) && !day) return false;
    if (filters.from && day < filters.from) return false;
    if (filters.to && day > filters.to) return false;
    if (filters.recurring && !recurring.has(productKey)) return false;
    return true;
  });
}

const sumOrNull = (values) => {
  const present = values.filter((v) => v != null);
  return present.length ? present.reduce((a, b) => a + b, 0) : null;
};

/** Recomputes the outlet rows and the flow totals for a set of trace lines, in the same
 * shape the finding's own charts and flow block read from the server snapshot. */
export function summarizeTraceRows(rows) {
  const byOutlet = new Map();
  const outboundByKey = new Map();
  const inboundByKey = new Map();
  for (const row of rows) {
    const outlet = byOutlet.get(row.outlet) ?? { outlet: row.outlet, plans: 0, planSeen: false, sents: 0, sentSeen: false, received: 0, units: 0 };
    outlet.units += row.missing_qty ?? 0;
    byOutlet.set(row.outlet, outlet);

    // Plan/sent belong to an outbound picking + product and are repeated on
    // each shortage row and sometimes on split inbound moves.
    const productKey = row.product_id ?? row.sku ?? row.product;
    const outKey = row.transfer_out_id != null
      ? `${row.transfer_out_id}:${productKey}`
      : `${row.transfer_out ?? "unknown"}:${productKey}`;
    if (!outboundByKey.has(outKey)) outboundByKey.set(outKey, row);

    // Expected/received belong to an incoming stock move; repeated shortage
    // reason rows can carry the same copied quantities.
    const inKey = row.receipt_move_id != null
      ? String(row.receipt_move_id)
      : `${row.receipt_id ?? row.receipt ?? "unknown"}:${productKey}`;
    if (!inboundByKey.has(inKey)) inboundByKey.set(inKey, row);
  }

  for (const row of outboundByKey.values()) {
    const outlet = byOutlet.get(row.outlet);
    if (row.plan_qty != null) { outlet.plans += row.plan_qty; outlet.planSeen = true; }
    if (row.sent_qty != null) { outlet.sents += row.sent_qty; outlet.sentSeen = true; }
  }
  for (const row of inboundByKey.values()) {
    const outlet = byOutlet.get(row.outlet);
    outlet.received += row.received_qty ?? 0;
  }

  const outletRows = [...byOutlet.values()]
    .map((o) => ({
      outlet: o.outlet,
      plan_units: o.planSeen ? o.plans : null,
      sent_units: o.sentSeen ? o.sents : null,
      line_received_units: o.received,
      physical_gap_units: o.sentSeen ? o.sents - o.received : null,
      units: o.units,
    }))
    .sort((a, b) => b.units - a.units);

  const outbound = [...outboundByKey.values()];
  const inbound = [...inboundByKey.values()];
  const planUnits = sumOrNull(outbound.map((r) => r.plan_qty ?? null));
  const sentUnits = sumOrNull(outbound.map((r) => r.sent_qty ?? null));
  const receivedUnits = inbound.reduce((sum, r) => sum + (r.received_qty ?? 0), 0);
  const planningGap = outbound.filter((r) => r.plan_qty != null && r.sent_qty != null).reduce((sum, r) => sum + (r.plan_qty - r.sent_qty), 0);
  const transitGap = outbound.filter((r) => r.sent_qty != null).reduce((sum, r) => sum + (r.sent_qty ?? 0), 0) - receivedUnits;
  const totalGap = (planUnits ?? 0) - receivedUnits;
  return {
    outletRows,
    impact: {
      plan_units: planUnits,
      sent_units: sentUnits,
      line_received_units: receivedUnits,
      units_affected: rows.reduce((sum, r) => sum + (r.missing_qty ?? 0), 0),
      planning_gap_units: planningGap,
      transit_gap_units: transitGap,
      total_gap_units: totalGap,
    },
  };
}
