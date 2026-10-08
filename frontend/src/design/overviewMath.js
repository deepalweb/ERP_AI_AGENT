// Pure aggregations behind the Investigate overview. No React, no formatting,
// so they can be unit-tested directly. Inputs are the management summary
// payload (cases, case_priorities, case_impacts) -- nothing is invented here.

const DAY_MS = 24 * 60 * 60 * 1000;

function parseStamp(value) {
  if (!value) return null;
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function kpiSummary({ cases, casePriorities, impacts, now = new Date() }) {
  const newCutoff = now.getTime() - 7 * DAY_MS;
  const newThisWeek = impacts.filter((row) => {
    const first = parseStamp(row.first_observed);
    return first && first.getTime() >= newCutoff;
  }).length;
  const quantified = impacts.filter((row) => Number.isFinite(row.impact?.financial_impact_rs)).length;
  return [
    { id: "open", label: "Open cases", value: cases?.open ?? 0 },
    { id: "critical_high", label: "Critical / High", value: (casePriorities?.critical ?? 0) + (casePriorities?.high ?? 0) },
    { id: "verification", label: "Awaiting verification", value: cases?.verification_pending ?? 0 },
    { id: "new_week", label: "New this week", value: newThisWeek },
    { id: "resolved", label: "Resolved (all time)", value: cases?.resolved ?? 0 },
    { id: "quantified", label: "Cases with quantified exposure", value: quantified, of: impacts.length },
  ];
}

export function needsAttention({ cases, casePriorities, impacts }) {
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const items = [];
  const criticalOpen = casePriorities?.critical ?? 0;
  if (criticalOpen) items.push({ id: "critical", tone: "critical", label: `${plural(criticalOpen, "critical case")} need action` });
  const awaiting = cases?.verification_pending ?? 0;
  if (awaiting) items.push({ id: "verification", tone: "warning", label: `${plural(awaiting, "case")} awaiting verification` });
  const mostlyUnvalued = impacts.filter(
    (row) => Number.isFinite(row.impact?.financial_impact_rs) && (row.impact?.valued_units_pct ?? 100) < 50,
  ).length;
  if (mostlyUnvalued) items.push({ id: "unvalued", tone: "warning", label: `${plural(mostlyUnvalued, "rupee estimate")} rest on under half their units` });
  return items;
}

// Impact kinds priced at current catalog cost (quantity x celeste_cost_price_ex_vat) --
// shared with IssuesTable.jsx's "Est." vs "Recorded" label, so the two stay consistent.
// Revenue and cancellation figures are on a different basis and must not be summed
// into the same chart, so they stay out until each is validated.
export const CATALOG_COST_ESTIMATE_KINDS = new Set(["transfer_discrepancy", "dispatch_over_plan"]);

export function exposureByDomain(impacts) {
  const groups = new Map();
  for (const row of impacts) {
    if (!CATALOG_COST_ESTIMATE_KINDS.has(row.impact?.kind)) continue;
    const domain = row.domain || "unclassified";
    const group = groups.get(domain) ?? { domain, exposure: 0, cases: 0, quantifiedCases: 0, units: 0, valuedUnits: 0 };
    group.cases += 1;
    const amount = row.impact?.financial_impact_rs;
    if (Number.isFinite(amount)) {
      group.exposure += amount;
      group.quantifiedCases += 1;
    }
    const units = Number(row.impact?.units_affected);
    if (Number.isFinite(units) && units > 0) {
      group.units += units;
      group.valuedUnits += units * ((row.impact?.valued_units_pct ?? 0) / 100);
    }
    groups.set(domain, group);
  }
  return [...groups.values()]
    .map((g) => ({
      domain: g.domain,
      exposure: g.exposure,
      cases: g.cases,
      quantifiedCases: g.quantifiedCases,
      coverage: g.units > 0 ? Math.round((g.valuedUnits / g.units) * 1000) / 10 : null,
    }))
    .sort((a, b) => b.exposure - a.exposure);
}
