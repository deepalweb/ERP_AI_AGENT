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

  if (c.metric_id === "completed_stock_count_variance") {
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

  if (c.metric_id.startsWith("transfer_discrepancy_")) {
    // 2026-10-01, manager review: the generic 3-card fallback showed only
    // "Current measurement: 34" (the line count) and spent a card slot on
    // Status, which the header badge already shows. Units affected is
    // arguably more operationally telling than the line count alone (34
    // lines/294 units reads differently than 34 lines/34 units) -- both
    // belong on the strip, and "Transfer days affected" makes the
    // recurrence pattern a headline fact instead of only prose below.
    return [
      { label: "Discrepancy lines", value: formatNumber(latest.confirm?.current) },
      {
        label: "Missing units",
        value: formatNumber(latest.impact?.units_affected),
        caption: latest.impact?.unit_rate != null ? `${latest.impact.unit_rate}% of units received at outlets` : undefined,
      },
      { label: "Transfer days affected", value: latest.persistence ? `${latest.persistence.days_appeared} of last ${latest.persistence.days_observed}` : "—" },
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
