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
 * stuck_stock_count case, but structural everywhere): a "confirmed" verdict
 * in this app is EXCLUSIVELY produced by the concentration checks
 * (segment_concentration_checks / current_total_concentration_checks) --
 * evidence_engine.py's own module docstring states this twice, once for the
 * vocabulary itself ("'confirmed' (own-breakdown concentration only...)")
 * and once for the sibling corroboration check ("cross_metric_corroboration_
 * checks never emits 'confirmed'... co-location... is evidence of an
 * affected segment, not evidence of a cause"). So "confirmed" always means
 * WHERE the move is concentrated, never WHY it happened -- the previous
 * wording ("the underlying cause is confirmed") collapsed those two
 * different claims into one. If a future evidence check type ever emits
 * "confirmed" for something other than concentration, this function needs
 * revisiting alongside it. */
export function evidenceRollupText(rootCause) {
  const checks = rootCause?.checks;
  if (!checks || checks.length === 0) return "No evidence checks have been recorded yet.";
  const confirmedCount = checks.filter((c) => c.verdict === "confirmed").length;
  const possibleCount = checks.filter((c) => c.verdict === "possible_contributor").length;

  if (confirmedCount === checks.length) {
    return "The evidence confirms where this is concentrated; it does not establish why it happened.";
  }
  if (confirmedCount > 0) {
    return "The evidence confirms where part of this is concentrated; the rest, and why it happened, remain unconfirmed.";
  }
  if (possibleCount > 0) {
    return "Possible contributing factors have been identified, but none is confirmed -- including where this is concentrated.";
  }
  return "Where this is concentrated, and why it happened, have not yet been established.";
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
    return `Cost coverage is ${coveragePct != null ? `${coveragePct}%` : "nearly complete"}, but `
      + `${formatNumber(noCost)} product${plural ? "s" : ""} still ${plural ? "have" : "has"} no usable cost value. `
      + `Any inventory-value or margin analysis involving ${plural ? "those products" : "that product"} can be `
      + `incomplete until ${plural ? "their" : "its"} cost data is corrected.`;
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
