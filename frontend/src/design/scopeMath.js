// Counts for the Investigate landing page, built from the management summary's
// case_domains rows (one row per domain, status, category and priority, with a count).
// Pure functions, no React, so the business-area cards, the scope tiles and the
// domain chart all read the same numbers.

import { DOMAIN_META, DOMAIN_ORDER } from "./domains.js";

const DAY_MS = 24 * 60 * 60 * 1000;
const CLOSED = new Set(["resolved", "dismissed"]);
const ACTIVE = new Set(["open", "reopened"]);

function emptyBands(domain) {
  return { domain, active: 0, urgent: 0, other: 0, opportunity: 0, verification: 0, closed: 0 };
}

function addRow(band, row) {
  const count = row.count ?? 0;
  if (row.status === "verification_pending") {
    band.verification += count;
  } else if (ACTIVE.has(row.status)) {
    band.active += count;
    if (row.category === "opportunity") band.opportunity += count;
    else if (["critical", "high"].includes(row.priority)) band.urgent += count;
    else band.other += count;
  } else if (CLOSED.has(row.status)) {
    band.closed += count;
  }
}

/** One entry per business area, in the product's domain order. Areas with no cases still appear. */
export function domainBreakdown(rows) {
  const byDomain = new Map(DOMAIN_ORDER.map((id) => [id, emptyBands(id)]));
  for (const row of rows ?? []) {
    const id = row.domain || "unclassified";
    if (!byDomain.has(id)) byDomain.set(id, emptyBands(id));
    addRow(byDomain.get(id), row);
  }
  return [...byDomain.values()].map((band) => ({
    ...band,
    label: DOMAIN_META[band.domain]?.label ?? band.domain,
  }));
}

/** Totals for the whole product (domain = "") or for one business area. */
export function scopeTotals(rows, domain = "") {
  const total = emptyBands(domain || "all");
  for (const band of domainBreakdown(rows)) {
    if (domain && band.domain !== domain) continue;
    for (const key of ["active", "urgent", "other", "opportunity", "verification", "closed"]) total[key] += band[key];
  }
  return total;
}

/** Cases first observed in the last 7 days, from the management summary's case impacts. */
export function newThisWeek(impacts, domain = "", now = new Date()) {
  const cutoff = now.getTime() - 7 * DAY_MS;
  return (impacts ?? []).filter((row) => {
    if (domain && row.domain !== domain) return false;
    const value = row.first_observed;
    if (!value) return false;
    const first = new Date(value.endsWith("Z") ? value : `${value}Z`).getTime();
    return Number.isFinite(first) && first >= cutoff;
  }).length;
}
