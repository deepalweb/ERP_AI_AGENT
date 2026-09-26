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
