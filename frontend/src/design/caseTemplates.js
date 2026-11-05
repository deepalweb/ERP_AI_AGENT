import { formatByUnit, formatCurrency, formatNumber, formatPct, moveToneClass } from "../format";

/** Case Detail V3: one function per finding-type's primary metric strip --
 * the "generic slot system" the redesign asked for, implemented as a data
 * function (not five separate React components) so every card is still
 * just a MetricSummaryCard. Controls cases return [] here deliberately --
 * ControlsInvestigationPanel (design/ControlsInvestigationPanel.jsx)
 * already renders its own KPI row (products affected / coverage / status)
 * as part of the investigation content, and repeating a near-identical row
 * above it was the exact "same number shown too many times" complaint this
 * redesign addresses -- so the caller skips this strip entirely for that
 * domain rather than rendering an empty one. Every value here traces to a
 * real field already on `latest`; nothing computed or guessed here. */
export function primaryMetricsFor({ c, latest, statusLabel }) {
  if (!latest || c.domain === "controls") return [];

  if (c.metric_id === "stuck_stock_count") {
    // Same 4 real fields CaseDetail's own "situation at a glance" always
    // used for this metric -- kept exactly as-is, just relocated here.
    const sessions = latest.sessions ?? [];
    const outletBreakdown = (latest.breakdowns ?? []).find((b) => b.dimension === "store");
    const largest = outletBreakdown?.top_contributors?.[0];
    const oldest = sessions.length > 0 ? Math.max(...sessions.map((s) => s.days_stuck)) : null;
    const coverage = latest.impact?.valuation_coverage;
    return [
      { label: "Unfinished sessions", value: formatNumber(latest.confirm?.current) },
      {
        label: "Estimated value at risk",
        value: formatCurrency(latest.impact?.financial_impact_rs),
        caption: coverage
          ? `Covers ${coverage.source_cost_lines + coverage.catalog_estimate_lines} of ${coverage.total_unscanned_lines} unscanned lines`
          : undefined,
      },
      { label: "Largest affected outlet", value: largest?.name ?? "—", caption: largest ? formatCurrency(largest.current) : undefined },
      { label: "Oldest unfinished session", value: Number.isFinite(oldest) ? `${oldest.toFixed(0)} days` : "—" },
    ];
  }

  if (c.metric_id === "completed_stock_count_variance" || c.metric_id.startsWith("completed_stock_count_variance-")) {
    // Stock-count variance RATE, 2026-10-16, revised same day after review:
    // raw Rs variance alone can't say whether this is actually bad, but
    // ONE rate isn't enough either -- "vs counted" can read misleadingly
    // high on a complete shortage (a line counted to 0 contributes nothing
    // to counted value despite carrying the full variance). Both shown,
    // explicitly labeled by denominator, never blended into one number.
    const rate = (pct) => (pct != null ? `${pct}%` : "Unknown");
    return [
      { label: "Completed sessions", value: formatNumber(latest.confirm?.current) },
      { label: "Gross variance", value: formatCurrency(latest.impact?.financial_impact_rs) },
      {
        label: "Variance rate, vs. system qty",
        value: rate(latest.variance_rate_vs_system_pct),
        caption: "Against what should have been there -- doesn't understate complete shortages",
      },
      {
        label: "Variance rate, vs. counted qty",
        value: rate(latest.variance_rate_vs_counted_pct),
        caption: "Against what's still there now -- can read high on a shortage",
      },
      { label: "Status", value: statusLabel },
    ];
  }

  if (c.metric_id.startsWith("extreme_event_")) {
    // The card date is when the largest event happened, not when data was synced:
    // the investigation sync time is shown in the page header instead.
    const rows = latest?.extreme_rows ?? [];
    const latestEvent = rows.map((r) => r.date).filter(Boolean).sort().pop();
    return [
      { label: "Unusual lines", value: formatNumber(latest?.confirm?.current) },
      { label: "Units affected", value: formatNumber(latest?.impact?.units_affected) },
      { label: "Latest event", value: latestEvent ?? "Not recorded", caption: "Date the most recent line was recorded" },
      {
        label: "Estimated exposure",
        value: latest?.impact?.financial_impact_rs != null ? formatCurrency(latest.impact.financial_impact_rs) : "Not quantified",
        caption: "At current catalog cost; not confirmed loss",
      },
    ];
  }

  if (c.metric_id === "dispatch_pattern") {
    const pairs = latest.pattern_rows ?? [];
    const dispatchers = new Set(pairs.map((row) => row.dispatcher).filter(Boolean));
    return [
      { label: "Qualifying pairs", value: formatNumber(latest.impact?.count ?? latest.confirm?.current) },
      { label: "Recorded shortage units", value: formatNumber(latest.impact?.units_affected) },
      { label: "Dispatchers in pattern", value: dispatchers.size ? formatNumber(dispatchers.size) : "Not recorded" },
    ];
  }

  if (latest.impact?.kind === "dispatch_over_plan") {
    const impact = latest.impact;
    return [
      { label: "Lines sent above plan", value: formatNumber(impact.count) },
      { label: "Units over plan", value: formatNumber(impact.units_affected) },
      { label: "Products affected", value: formatNumber(impact.distinct_products) },
      {
        label: "Estimated exposure",
        value: impact.financial_impact_rs == null ? "Not quantified" : formatCurrency(impact.financial_impact_rs),
        caption: impact.financial_impact_rs == null ? "No usable catalog cost recorded" : `At catalog cost; ${impact.valued_units_pct ?? "Unknown"}% of excess units valued, not confirmed loss`,
      },
    ];
  }

  if (c.metric_id.startsWith("transfer_discrepancy_")) {
    const isExtra = c.metric_id.endsWith("_extra_quantity");
    // 2026-10-01, manager review: the generic 3-card fallback showed only
    // "Current measurement: 34" (the line count) and spent a card slot on
    // Status, which the header badge already shows. Units affected is
    // arguably more operationally telling than the line count alone (34
    // lines/294 units reads differently than 34 lines/34 units) -- both
    // belong on the strip, and "Transfer days affected" makes the
    // recurrence pattern a headline fact instead of only prose below.
    return [
      { label: isExtra ? "Extra-quantity records" : "Shortage-reason records", value: formatNumber(latest.impact?.count ?? latest.confirm?.current) },
      {
        label: isExtra ? "Extra units recorded" : "Shortage units recorded",
        value: formatNumber(latest.impact?.units_affected),
        caption: latest.impact?.unit_rate != null && latest.impact?.unit_rate_denominator_units != null
          ? "Recorded-shortage rate: " + formatNumber(latest.impact.unit_rate_numerator_units ?? latest.impact.units_affected) + " reason units out of " + formatNumber(latest.impact.unit_rate_denominator_units) + " units received across Celeste outlets in the trailing " + (latest.impact.window_days ?? 14) + " days (" + latest.impact.unit_rate + "%). This is separate from planned-quantity fulfillment and sent-to-received variance."
          : latest.impact?.unit_rate != null
            ? "Recorded-shortage rate denominator is missing from this saved observation. Planned-quantity fulfillment shown below is a separate measure."
            : "Sum of the selected shortage-reason records. Planned-quantity fulfillment below is calculated separately from movement quantities.",
      },
      { label: "Affected outlets", value: latest.outlet_rows?.length ? formatNumber(latest.outlet_rows.length) : "Not recorded" },
      {
        label: "Estimated exposure",
        value: latest.impact?.financial_impact_rs == null
          ? "Not quantified"
          : (latest.impact.valued_units_pct ?? 0) < 50
            ? "Mostly unvalued"
            : formatCurrency(latest.impact.financial_impact_rs),
        caption: (latest.impact?.valued_units_pct ?? 0) < 50
          ? `Only ${latest.impact?.valued_units_pct ?? 0}% of units have a usable cost; rely on units`
          : `At current catalog cost, ${latest.impact?.valued_units_pct ?? 0}% of units valued; not confirmed loss`,
      },
    ];
  }

  if (c.metric_id === "stock_availability") {
    const topStore = latest.breakdowns?.find((row) => row.dimension === "store")?.top_contributors?.[0];
    return [
      { label: "Product/outlet stockouts", value: formatNumber(latest.impact?.count ?? latest.confirm?.current) },
      { label: "Distinct products", value: formatNumber(latest.impact?.distinct_products) },
      { label: "Most affected outlet", value: topStore?.name ?? "Not recorded", caption: topStore ? `${formatNumber(topStore.current)} product stockouts` : undefined },
    ];
  }

  if (c.metric_id === "top_seller_stock") {
    const impact = latest.impact ?? {};
    const topProduct = latest.breakdowns?.find((row) => row.dimension === "product")?.top_contributors?.[0]?.name;
    return [
      { label: "Top sellers out of stock", value: `${formatNumber(impact.count ?? latest.confirm?.current)} of ${formatNumber(impact.total_top_sellers)}` },
      { label: "First product to check", value: topProduct ?? "Not recorded" },
      { label: "Valuation", value: "Not quantified", caption: "Recorded stock state and recent demand; no loss value calculated" },
    ];
  }

  if (c.metric_id === "damage_writeoff") {
    const impact = latest.impact ?? {};
    const coverage = impact.valuation_coverage ?? {};
    return [
      { label: "Write-off orders", value: formatNumber(impact.count) },
      { label: "Recorded write-off value", value: impact.financial_impact_rs == null ? "Not quantified" : formatCurrency(impact.financial_impact_rs), caption: "Write-off recorded; value estimated at catalog cost" },
      { label: "Cost coverage", value: coverage.total_lines ? `${Math.round(((coverage.catalog_estimate_lines ?? 0) / coverage.total_lines) * 100)}%` : "Not provided", caption: coverage.total_lines ? `${coverage.catalog_estimate_lines ?? 0} of ${coverage.total_lines} lines valued` : undefined },
    ];
  }

  if (c.metric_id === "dead_overstock_value") {
    const dead = latest.breakdowns?.find((row) => /dead stock/i.test(row.label));
    const overstock = latest.breakdowns?.find((row) => /overstock/i.test(row.label));
    return [
      { label: "Dead stock value", value: dead?.total == null ? "Not recorded" : formatCurrency(dead.total), caption: "At recorded inventory value; not confirmed loss" },
      { label: "Overstock value", value: overstock?.total == null ? "Not recorded" : formatCurrency(overstock.total), caption: "At recorded inventory value; not confirmed loss" },
      { label: "Total value at risk", value: latest.impact?.financial_impact_rs == null ? "Not quantified" : formatCurrency(latest.impact.financial_impact_rs), caption: "Dead and overstock categories combined" },
    ];
  }

  if (c.metric_id === "stockout_cancellation_rate") {
    const confirm = latest.confirm ?? {};
    const rate = (value) => value == null ? "Not recorded" : `${Number(value).toFixed(1)}%`;
    const change = confirm.current == null || confirm.baseline == null ? null : Number(confirm.current) - Number(confirm.baseline);
    return [
      { label: "Baseline cancellation rate", value: rate(confirm.baseline) },
      { label: "Current cancellation rate", value: rate(confirm.current) },
      { label: "Change", value: change == null ? "Not recorded" : `${change > 0 ? "+" : ""}${change.toFixed(1)} percentage points`, caption: "Measured change; cause and realized benefit are not established" },
    ];
  }

  // Generic trend/snapshot fallback -- current measurement (+ change vs
  // baseline when one exists), financial impact, case status. This is the
  // same 4-field shape every metric without a dedicated slot above already
  // used before this redesign, just computed once here instead of inline
  // in CaseDetail.jsx.
  const confirm = latest.confirm;
  const hasBaseline = confirm && (confirm.baseline != null || confirm.pct_change != null);
  const cards = [];
  if (confirm) {
    cards.push({
      label: latest.metric?.label ?? "Current measurement",
      value: formatByUnit(confirm.current, confirm.unit ?? latest.metric?.unit),
      caption: hasBaseline ? undefined : confirm.period ? `As of ${confirm.period}` : undefined,
      valueClassName: hasBaseline ? moveToneClass(confirm.pct_change, latest.metric?.direction) : undefined,
    });
  }
  if (hasBaseline) {
    cards.push({ label: "Change vs baseline", value: formatPct(confirm.pct_change) });
  }
  cards.push({
    label: "Financial impact",
    value: latest.impact?.financial_impact_rs != null ? formatCurrency(latest.impact.financial_impact_rs) : "Not quantified",
  });
  cards.push({ label: "Status", value: statusLabel });
  return cards;
}
