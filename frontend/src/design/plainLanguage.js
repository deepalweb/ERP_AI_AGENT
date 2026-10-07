import { formatByUnit, formatConfirmPeriod, formatNumber } from "../format.js";

/** One deterministic, plain-English sentence per finding — same "stated
 * rule, not a judgment call" posture as investigation.py's _priority_for
 * and impact_engine.py's business_impact_score. Built entirely from fields the
 * backend already computes (priority, persistence pattern/consecutive
 * periods, the weekly streak, confirm.pct_change) — never invented text,
 * never an LLM call. The goal is a sentence a manager can read without
 * first decoding a badge color and a percentage: "is this good or bad,
 * how long has it been going on, how urgent is it."
 */

const URGENCY = {
  critical: "needs attention now",
  high: "needs attention soon",
  medium: "worth keeping an eye on",
};

export function headlineFor(finding) {
  const { category, priority, persistence, confirm, horizon, streak } = finding;

  if (category === "opportunity") {
    if (persistence?.pattern?.startsWith("consistent_improving") && persistence.consecutive_periods >= 2) {
      const unit = horizon === "weekly" ? "weeks" : "months";
      return `Good news — this has kept improving for ${persistence.consecutive_periods} ${unit} in a row.`;
    }
    if (persistence?.pattern === "recovering") {
      return "Good news — this is bouncing back after a rough patch.";
    }
    return "Good news — this is trending in the right direction.";
  }

  const urgency = URGENCY[priority] ?? "worth keeping an eye on";

  if (horizon === "weekly" && streak?.consecutive_days > 1) {
    return `This has been a problem for ${streak.consecutive_days} days straight — ${urgency}.`;
  }

  if (persistence?.pattern === "recovering") {
    return `This was a problem, but it's turning around — still worth confirming it holds.`;
  }

  if (persistence?.pattern?.startsWith("consistent_declining") && persistence.consecutive_periods >= 2) {
    const unit = horizon === "weekly" ? "weeks" : "months";
    return `This has been getting worse for ${persistence.consecutive_periods} ${unit} in a row — ${urgency}.`;
  }

  if (confirm?.pct_change == null) {
    // Visual Investigation V2, Phase 2: "This is a real problem right now"
    // was the generic phrase this milestone explicitly asked to remove --
    // fires for every snapshot/state finding (no baseline to compare
    // against, e.g. stuck_stock_count), so it said nothing case-specific.
    // "Detected in today's snapshot" names the actual mechanism (a
    // point-in-time measurement, not a trend) instead of a placeholder.
    return `Detected in today's snapshot — ${urgency}.`;
  }

  return `This just showed up — ${urgency}.`;
}

/** Maps a confirmed check's own `source` field to the specific WHAT it
 * establishes -- never the why. The docstring below predicted this: "If a
 * future evidence check type ever emits 'confirmed' for something other
 * than concentration, this function needs revisiting alongside it." That
 * happened twice since (2026-10-01, caught on the exact scenario a manager
 * asked about live): incident_engine.py's time_lock/sku_cause/persistence
 * checks and transfer_discrepancy.py's recurrence check all emit
 * "confirmed" for claims that are not concentration at all. Before this,
 * evidenceRollupText unconditionally said "confirms where this is
 * concentrated" for EVERY confirmed check regardless of source -- for a
 * transfer_discrepancy case (recurrence-only, no concentration check even
 * exists) that produced a flatly wrong executive sentence. Unrecognized/
 * future sources fall back to a generic-but-still-honest phrase rather than
 * silently mislabeling them as concentration again. */
function confirmedClaimFor(source) {
  if (source?.endsWith("_concentration")) return "where this is concentrated";
  if (source === "recurrence" || source === "persistence") return "that this is a recurring pattern, not an isolated event";
  if (source === "time_lock") return "that the spike is tied to a specific hour, not spread evenly across the day";
  if (source === "sku_cause") return "that the pattern is specific to this product, not general";
  return "part of what the evidence shows";
}

/** One deterministic sentence on what the evidence has and hasn't
 * established — same verdict vocabulary confidenceLabelFor (SignalCard.jsx)
 * already uses for a single badge, extended here to a full sentence for the
 * case-detail executive conclusion. Never invents a cause; only describes
 * whether root_cause.checks already found one.
 *
 * Deliberately three tiers, not two -- "confirmed" and "possible_contributor"
 * are NOT the same claim (see evidence_engine.py's own vocabulary and
 * investigation.py's LLM prompt, which is explicitly barred from stating a
 * possible_contributor as fact). Collapsing them into one "established"
 * bucket would let a purely hedged, unconfirmed contributor read as a
 * settled cause -- exactly the overclaim this app is designed to avoid.
 *
 * Visual Investigation V2, Phase 1 (confirmed regression, caught on the real
 * stuck_stock_count case, but structural everywhere): "confirmed" always
 * means WHAT was established (confirmedClaimFor, above), never WHY it
 * happened -- the previous wording ("the underlying cause is confirmed")
 * collapsed those two different claims into one. */
export function evidenceRollupText(rootCause) {
  const checks = rootCause?.checks;
  if (!checks || checks.length === 0) return "No evidence checks have been recorded yet.";
  const confirmed = checks.filter((c) => c.verdict === "confirmed");
  const possibleCount = checks.filter((c) => c.verdict === "possible_contributor").length;
  const claims = () => [...new Set(confirmed.map((c) => confirmedClaimFor(c.source)))].join(" and ");

  if (confirmed.length > 0 && confirmed.length === checks.length) {
    return `The evidence confirms ${claims()}; it does not establish why it happened.`;
  }
  if (confirmed.length > 0) {
    return `The evidence confirms ${claims()}; the rest, and why it happened, remain unconfirmed.`;
  }
  if (possibleCount > 0) {
    return "Possible contributing factors have been identified, but none is confirmed yet.";
  }
  return "What's driving this, and why it happened, have not yet been established.";
}

/** The executive conclusion's impact clause -- deliberately checks
 * financial_impact_rs specifically, not just "does an impact object
 * exist." State findings (state_findings.py, e.g. stock-outs) always
 * carry a real impact object with a descriptive `detail` (a product/
 * incident count) but financial_impact_rs: None -- using `.detail`
 * unconditionally there would (a) claim a "financial impact" clause that
 * is actually just a restatement of the finding's own count, not money,
 * and (b) duplicate the page's own title verbatim, since state findings'
 * `detail` text is written as a near-copy of `title`. Only a real,
 * priced impact_engine.compute_impact() result (revenue/order_volume/
 * lost_orders metrics) has a non-null financial_impact_rs, and only that
 * case's `.detail` (which itself names a real Rs figure) is used here. */
export function financialImpactText(impact) {
  if (impact?.financial_impact_rs != null) return impact.detail;
  return "Financial impact has not been quantified.";
}

/** Celeste Investigate Phase 2 (flagship stock-out workflow): a snapshot
 * finding (state_findings.py -- no month-over-month baseline to compare
 * against) was rendering "n/a vs baseline" under the primary metric --
 * technically correct (there genuinely is no baseline) but read as broken,
 * and never told the manager what was actually measured. Returns null for
 * a trend/percentage finding that DOES have a real baseline/pct_change --
 * the caller keeps using the existing "vs baseline" comparison for those,
 * unchanged.
 *
 * top_seller_stock is the one metric_id with a purpose-built sentence,
 * because impact.total_top_sellers (a real, already-computed denominator
 * -- see state_findings._top_seller_stock_finding) makes a specific claim
 * possible ("1 of 15..."). Every other snapshot metric falls back to a
 * generic, honest "<value> as of <date>" rather than guessing at a noun
 * this function has no real field for. */
export function metricSnapshotText(latest, metricId) {
  const confirm = latest?.confirm;
  if (!confirm) return null;
  if (confirm.baseline != null || confirm.pct_change != null) return null;

  if (metricId === "top_seller_stock" && latest.impact?.total_top_sellers != null) {
    // Metric Truthfulness Correction, Task 1: "out of stock" claimed verified
    // unavailability this snapshot only ever measured as recorded on-hand
    // quantity (company-wide, unreserved) -- see InventoryInvestigationPanel.jsx
    // for the fuller scope disclosure shown alongside this same case.
    return `${formatNumber(confirm.current)} of ${formatNumber(latest.impact.total_top_sellers)} top-selling products recorded at zero or negative stock.`;
  }
  // formatConfirmPeriod, not formatDate: confirm.period's shape depends on
  // horizon (a full date only for a snapshot finding; "YYYY-MM" for
  // monthly; already-human for a trailing window) -- see format.js's own
  // "Invalid Date" regression note for the real bug this fixes.
  return `${formatByUnit(confirm.current, confirm.unit)} as of ${formatConfirmPeriod(confirm.period)}.`;
}

/** The executive conclusion's negative-quantity clause -- only ever built
 * from a product's own `negative_stock` flag as it already sits on the
 * SAME breakdown contributor entry the SKU/current-stock figure comes
 * from (see state_findings._top_seller_stock_finding's top_contributors).
 * That's what "verified" means here: the relationship between the SKU and
 * the negative figure is structural (one query, one row), never inferred
 * by matching names across two different data sources. Deliberately
 * doesn't say why the quantity is negative -- that's an unverified data-
 * integrity question, not something this can answer. */
export function negativeStockText(latest) {
  const negative = (latest?.breakdowns ?? [])
    .flatMap((b) => b.top_contributors ?? [])
    .filter((c) => c.negative_stock);
  if (negative.length === 0) return null;
  const names = negative.map((p) => p.name).join(", ");
  const verb = negative.length === 1 ? "shows" : "show";
  return `${names} also ${verb} a reported negative on-hand quantity -- the underlying cause has not been verified.`;
}

/** "B. Additional supporting observations" (Celeste Investigate Phase 2's
 * evidence taxonomy) -- a plain summary of any breakdown beyond the
 * primary measured-finding one (e.g. state_findings._top_seller_stock_
 * finding's "other top sellers under N days of cover" list), not a new
 * evidence computation. Generic across finding shapes: reads the
 * breakdown's own label and count, never assumes an inventory-specific
 * meaning. Returns null when there's nothing beyond the primary breakdown
 * -- the caller shows an explicit "none recorded" message instead of
 * silently omitting the category. */
export function additionalObservationsText(latest) {
  const secondary = (latest?.breakdowns ?? []).slice(1).find((b) => (b.top_contributors ?? []).length > 0);
  if (!secondary) return null;
  const count = secondary.top_contributors.length;
  return `${count} more: ${secondary.label.toLowerCase()}.`;
}

/** The executive conclusion's closing "what decision is needed" clause --
 * deliberately domain-neutral (never names a specific action like
 * "replenishment") so the same executive-conclusion composition works for
 * any case, not just a stock-out. A closed case (resolved/dismissed) has
 * no pending decision, so this returns null rather than a stale prompt. */
export function decisionPromptText(status) {
  if (status === "open" || status === "reopened") return "Management should review the evidence and decide on next steps.";
  if (status === "verification_pending") return "Recovery is being verified -- no new decision is needed unless it resets.";
  return null;
}

/** Celeste Investigate Phase 3 revenue audit: a real, confirmed defect --
 * pct_change alone can read as a dramatic move when the baseline months it
 * was averaged from varied a lot (e.g. a business's early ramp-up period
 * pulling the mean down), inflating the percentage past what a stable
 * baseline would show. detect_anomaly() (mcp_server/tools/intelligence.py)
 * already computes z_score for exactly this reason -- corroborated by
 * z-score too means a stable baseline; pct-change-only means read the
 * percentage cautiously. Z_THRESHOLD mirrors detect_anomaly's own
 * ANOMALY_Z_THRESHOLD (can't share a constant across the Python/JS
 * boundary, so it's restated here with this comment as the cross-
 * reference) -- not a new rule, the exact same one already used server-
 * side to decide significance. Fires only when the percentage rule alone
 * triggered significance and the z-score rule did not; returns null
 * (no caution) whenever both agree, or when either value is missing. */
/** A Business Controls case (data-control gap, not a trend anomaly) reads
 * as generic/understated through the default executiveConclusion sentence
 * chain (headline + evidenceRollupText + financialImpactText, etc.) --
 * "Detected in today's snapshot -- worth keeping an eye on" doesn't say
 * WHAT's missing, WHICH records, or WHAT that breaks downstream, and
 * financialImpactText always falls back to "not quantified" for these
 * (they're record counts, not priced findings). One purpose-built sentence
 * per known control metric_id, built entirely from fields already on the
 * finding (total_products/coverage_pct for cost coverage, products.length
 * for negative stock -- same fields ControlsInvestigationPanel already
 * renders) -- never a generic fallback for an unrecognized controls
 * metric_id, since a made-up sentence would be worse than none. */
export function controlsExecutiveText(latest) {
  if (!latest) return null;
  if (latest.metric_id === "cost_coverage_control") {
    const noCost = latest.products?.length ?? latest.confirm?.current;
    const coveragePct = latest.coverage_pct;
    if (noCost == null) return null;
    const plural = noCost !== 1;
    // Shortened per direct feedback: the old version repeated almost the
    // same claim "What this affects" already covers in more detail below
    // -- this now states the count/coverage/consequence once, plainly,
    // and lets the investigation section carry the rest.
    return `${formatNumber(noCost)} active product${plural ? "s" : ""} ${plural ? "have" : "has"} no usable cost `
      + `value. Cost coverage remains ${coveragePct != null ? `${coveragePct}%` : "nearly complete"}, but valuation `
      + `and margin analysis involving ${plural ? "those products" : "that product"} can be incomplete. No `
      + `financial loss has been established.`;
  }
  if (latest.metric_id === "negative_stock_control") {
    const count = latest.products?.length ?? latest.confirm?.current;
    if (count == null) return null;
    const plural = count !== 1;
    return `${formatNumber(count)} product${plural ? "s" : ""} currently show negative recorded stock company-wide `
      + `-- a data-integrity condition, not confirmed loss. Stock availability and inventory valuation involving `
      + `${plural ? "these records" : "this record"} may be unreliable until reconciled.`;
  }
  return null;
}

/** The generic "Celeste has not yet seen this clear its own anomaly
 * threshold" resolution line is accurate but says nothing case-specific
 * for a controls case -- a manager wants to know WHAT still needs fixing,
 * not that a threshold hasn't cleared. Overrides only the still-open
 * states (open/reopened); other statuses (resolved/verification_pending/
 * dismissed) already have their own specific text in CaseDetail.jsx and
 * aren't touched here. Returns null for any other metric_id, or if the
 * count itself isn't available -- caller falls back to the generic text. */
export function controlsResolutionText(latest, status) {
  if (status !== "open" && status !== "reopened") return null;
  if (latest?.metric_id === "cost_coverage_control") {
    const noCost = latest.products?.length ?? latest.confirm?.current;
    if (noCost == null) return null;
    const plural = noCost !== 1;
    return `Still open -- ${formatNumber(noCost)} product${plural ? "s" : ""} still lack${plural ? "" : "s"} usable `
      + `cost data. The case can move toward resolution once the affected products have valid cost records and a `
      + `later scan confirms the gap has cleared.`;
  }
  if (latest?.metric_id === "negative_stock_control") {
    const count = latest.products?.length ?? latest.confirm?.current;
    if (count == null) return null;
    const plural = count !== 1;
    return `Still open -- ${formatNumber(count)} product${plural ? "s" : ""} still show negative recorded stock. `
      + `The case can move toward resolution once the affected products are reconciled and a later scan confirms `
      + `the gap has cleared.`;
  }
  return null;
}

/** Incident cards (IncidentInvestigationPanel.jsx) showed the hourly
 * pct-change but never the incident_score itself or which band it cleared --
 * the one number incident_engine.py actually ranks and gates findings on
 * (hourly z-score x product lift x sqrt(order count), then a persistence
 * multiplier). Band cutoffs mirror incident_engine.py's own SCORE_CONFIRMED/
 * SCORE_ESCALATE/SCORE_AUTO_ALERT constants -- restated here for the same
 * cross-Python/JS-boundary reason baselineVolatilityCaution's Z_THRESHOLD
 * is. SCORE_WATCHLIST (5) is omitted: build_incident_findings() only ever
 * returns findings that already cleared SCORE_CONFIRMED (15), so a card can
 * never actually be in the watchlist band. */
const INCIDENT_SCORE_BANDS = [
  { min: 200, label: "auto-alert tier" },
  { min: 50, label: "escalation tier" },
  { min: 15, label: "confirmed tier" },
];

export function incidentScoreText(score) {
  if (score == null) return null;
  const band = INCIDENT_SCORE_BANDS.find((b) => score >= b.min) ?? INCIDENT_SCORE_BANDS[INCIDENT_SCORE_BANDS.length - 1];
  return `Incident score ${score.toFixed(1)} (${band.label}) -- one ranking number combining how unusual this `
    + `hour is, how specific to this product, order volume, and how many days it has recurred. Not an external `
    + `probability or a promise that a notification was sent.`;
}

/** transfer_discrepancy.py's cases fell through to the generic headline
 * chain ("Detected in today's snapshot -- needs attention now") -- accurate
 * but says nothing case-specific, when every number a manager actually
 * needs (lines, units, days, Rs exposure, what's established vs. not) is
 * already sitting on the finding. Same purpose-built-sentence pattern as
 * controlsExecutiveText; returns null for anything that isn't this case
 * type, so the generic chain stays the fallback. */
export function transferDiscrepancyExecutiveText(latest) {
  if (!latest || !latest.persistence) return null;
  const { days_appeared, days_observed } = latest.persistence;
  const lines = latest.confirm?.current;
  const units = latest.impact?.units_affected;
  const rs = latest.impact?.financial_impact_rs;
  const outlets = latest.outlet_rows ?? [];
  if (lines == null) return null;
  const valuedPct = latest.impact?.valued_units_pct;
  const exposure = rs != null && (valuedPct ?? 0) >= 50
    ? `, with an estimated exposure of ${formatByUnit(rs, "currency")} at current catalog cost (${valuedPct}% of affected units had usable cost)`
    : `, with value mostly not established (only ${valuedPct ?? 0}% of affected units have a usable catalog cost); rely on the unit count`;
  const rated = outlets.filter((o) => o.unit_rate != null).sort((a, b) => b.unit_rate - a.unit_rate);
  const top = rated[0];
  const topText = top ? ` ${top.outlet.replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim()} has the highest rate, at ${top.unit_rate}% of the units it received.` : "";
  const excludedLines = latest.impact?.excluded_lines ?? 0;
  const excludedText = excludedLines
    ? ` ${formatNumber(excludedLines)} more line${excludedLines === 1 ? " is" : "s are"} on cancelled or not-yet-received receipts and excluded from these figures.`
    : "";
  return `${formatNumber(lines)} ${(latest.reason ?? "shortage").toLowerCase()} line${lines === 1 ? "" : "s"} `
    + `${units != null ? `(${formatNumber(units)} units) ` : ""}were recorded at outlet receiving across ${outlets.length} outlet${outlets.length === 1 ? "" : "s"}. `
    + `They fall on ${days_appeared} of the ${days_observed} days in the window that had a warehouse transfer${exposure}.`
    + topText + excludedText
    + " Where in the pick-load-transport-receive process the gap begins is not yet known.";
}

/** Counterpart to transferDiscrepancyExecutiveText -- a concrete next step
 * instead of the generic "review the evidence" prompt, grounded only in
 * fields already on the finding (the top affected product, if any). Never
 * claims a cause; the ask is to compare records, not "investigate theft"
 * or similar. Null for a closed case, same as decisionPromptText. */
export function transferDiscrepancyNextDecisionText(latest, status) {
  if (status !== "open" && status !== "reopened") return null;
  const topProduct = latest?.breakdowns?.find((b) => b.dimension === "product")?.top_contributors?.[0]?.name?.split(" - Celeste Daily")[0];
  return `Review the affected transfer records, comparing warehouse picking/loading quantities against outlet `
    + `receiving records${topProduct ? ` -- starting with ${topProduct}, the most-affected product` : ""}.`;
}

export function baselineVolatilityCaution(confirm) {
  const Z_THRESHOLD = 1.5;
  const PCT_THRESHOLD = 15;
  if (confirm?.pct_change == null || confirm?.z_score == null) return null;
  if (Math.abs(confirm.pct_change) < PCT_THRESHOLD) return null;
  if (Math.abs(confirm.z_score) >= Z_THRESHOLD) return null;
  return `This percentage was driven by the pct-change rule alone (z-score ${confirm.z_score.toFixed(2)}, `
    + `below the ${Z_THRESHOLD} significance threshold) -- the baseline months varied enough that the `
    + `percentage may overstate how unusual this period really was.`;
}

export function extremeEventExecutiveText(latest) {
  const rows = latest?.extreme_rows;
  if (!rows?.length) return null;
  const units = rows.reduce((sum, r) => sum + r.recorded_qty, 0);
  const outlets = latest.breakdowns?.find((b) => b.dimension === "outlet")?.top_contributors ?? [];
  const products = latest.breakdowns?.find((b) => b.dimension === "product")?.top_contributors ?? [];
  const cancelled = rows.filter((r) => r.receipt_status === "cancel").length;
  const mismatched = rows.filter((r) => r.flags?.some((f) => f.startsWith("Quantity does not match"))).length;
  const topOutlet = outlets[0];
  const topProduct = products[0];
  const parts = [
    `${rows.length} transfer ${rows.length === 1 ? "line" : "lines"} recorded unusually large Missing Item shortages `
      + `(${formatNumber(units)} units) across ${outlets.length} outlet${outlets.length === 1 ? "" : "s"} in the last 30 days.`,
  ];
  if (topOutlet) {
    parts.push(`Most units are at ${topOutlet.name} (${formatNumber(topOutlet.current)} units)`
      + (topProduct ? `, mainly ${topProduct.name.split(" - Celeste Daily")[0]} (${formatNumber(topProduct.current)} units).` : "."));
  }
  if (cancelled) parts.push(`${cancelled} ${cancelled === 1 ? "is" : "are"} on receipts Odoo shows as cancelled.`);
  if (mismatched) parts.push(`${mismatched} record a quantity that does not match expected minus received.`);
  parts.push("Whether these are real losses, and who sent the stock, is not established from this data.");
  return parts.join(" ");
}

export function extremeEventNextDecisionText(latest, status) {
  if (status !== "open" && status !== "reopened") return null;
  const rows = latest?.extreme_rows ?? [];
  const cancelled = rows.filter((r) => r.receipt_status === "cancel").length;
  if (cancelled) {
    return "Check the cancelled receipts first: confirm whether the goods arrived, starting with the largest line.";
  }
  const topProduct = latest?.breakdowns?.find((b) => b.dimension === "product")?.top_contributors?.[0]?.name?.split(/\s+-\s+(?:Celeste\b|SKU\b)/)[0];
  return `Check the largest receipts against physical counts${topProduct ? `, starting with ${topProduct}` : ""}.`;
}

const shortName = (name) => (name ?? "").split(/\s+-\s+(?:Celeste\b|SKU\b)/)[0];
const shortOutletName = (name) => (name ?? "").replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim() || name;

/** One-sentence answer for an inventory transfer case: what, where, what first. */
export function inventoryAnswerText(latest) {
  if (!latest) return null;
  const rows = latest.extreme_rows ?? [];
  const isExtreme = rows.length > 0;
  const outlets = latest.outlet_rows ?? [];
  const products = latest.breakdowns?.find((b) => b.dimension === "product")?.top_contributors ?? [];
  const units = latest.impact?.units_affected;
  const outletCount = isExtreme
    ? new Set(rows.map((r) => r.outlet)).size
    : outlets.length;
  if (isExtreme) {
    if (!rows.length) return null;
    const largest = rows[0];
    return `${formatNumber(rows.length)} unusually large Missing Item ${rows.length === 1 ? "line" : "lines"} `
      + `affected ${formatNumber(units ?? 0)} units across ${outletCount} outlet${outletCount === 1 ? "" : "s"}; `
      + `the largest is ${shortName(largest.product)} at ${shortOutletName(largest.outlet)}. `
      + "Verify those receiving records first.";
  }
  if (!outlets.length) return null;
  const lines = latest.confirm?.current ?? 0;
  const reason = (latest.reason ?? "shortage").toLowerCase();
  const rated = outlets.filter((o) => o.unit_rate != null).sort((a, b) => b.unit_rate - a.unit_rate);
  const topRate = rated[0];
  const topText = topRate ? `, highest discrepancy rate at ${shortOutletName(topRate.outlet)}` : "";
  const productText = products.length >= 2
    ? `most affected products are ${shortName(products[0].name)} and ${shortName(products[1].name)}`
    : products.length === 1 ? `most affected product is ${shortName(products[0].name)}` : null;
  return `${formatNumber(lines)} ${reason} ${lines === 1 ? "line" : "lines"} show ${formatNumber(units ?? 0)} units missing `
    + `across ${outletCount} outlet${outletCount === 1 ? "" : "s"}${topText}`
    + (productText ? `; ${productText}` : "")
    + ". Review the largest receipts and receiving records first.";
}

/** Footer line: exposure at current catalog cost, with the valued share beside it. */
export function inventoryExposureLine(latest) {
  const amount = latest?.impact?.financial_impact_rs;
  let coverage = latest?.impact?.valued_units_pct;
  if (coverage == null && latest?.extreme_rows?.length) {
    const total = latest.extreme_rows.reduce((sum, r) => sum + r.recorded_qty, 0);
    const valued = latest.extreme_rows.filter((r) => r.unit_cost).reduce((sum, r) => sum + r.recorded_qty, 0);
    coverage = total ? Math.round((valued / total) * 1000) / 10 : null;
  }
  const amountText = amount != null ? `Estimated exposure at current catalog cost: ${formatByUnit(amount, "currency")}` : "Exposure not quantified";
  const coverageText = coverage != null ? ` · ${coverage}% of affected units valued` : "";
  return `${amountText}${coverageText} · not confirmed loss`;
}

/** Type-specific next check for an inventory case. Null when the case type has no
 * specific wording, so the caller keeps its own prompt. */
export function inventoryNextCheckText(latest) {
  if (!latest) return null;
  const kind = latest.impact?.kind;
  const topStore = latest.breakdowns?.find((b) => b.dimension === "store" || b.dimension === "outlet")?.top_contributors?.[0]?.name;
  const storeText = topStore ? topStore.replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim() : null;
  if (kind === "products_out_of_stock") {
    return "Check the highest-impact products first: confirm stock on hand against the last 7 days of sales, and reorder where cover is short.";
  }
  if (kind === "top_sellers_out_of_stock") {
    const top = latest.breakdowns?.[0]?.top_contributors?.[0]?.name?.split(/\s+-\s+(?:Celeste\b|SKU\b)/)[0];
    return `Check replenishment for these top sellers first${top ? `, starting with ${top}` : ""}: confirm the purchase order and its receiving status.`;
  }
  if (kind === "stuck_stock_count_value_at_risk") {
    return `Finish or close the unfinished counts first${storeText ? `, starting with ${storeText}` : ""}: confirm which unscanned lines still have stock.`;
  }
  if (kind === "inventory_value_at_risk") {
    return "Confirm whether the dead and overstocked lines can be sold, returned, or written off, starting with the largest lines.";
  }
  if (kind === "completed_stock_count_variance") {
    return `Review the count sheets${storeText ? ` for ${storeText}` : ""} first: compare each counted quantity with the system quantity and check the receiving records for the same products.`;
  }
  return null;
}
