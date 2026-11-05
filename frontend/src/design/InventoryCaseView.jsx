import { useState } from "react";
import { Link } from "react-router-dom";
import RankedBarChart from "../charts/RankedBarChart";
import { formatCurrency, formatNumber } from "../format";
import { AffectedRecordTable, PrimaryMetricStrip } from "./CaseVisuals";
import { INVENTORY_TAGS, tagFor } from "./inventoryTags";
import { inventoryAnswerText, inventoryExposureLine } from "./plainLanguage";
import { filterTraceRows, isFilterActive, summarizeTraceRows } from "./transferFilters";
import InvestigationResults from "./InvestigationResults";
import { inventoryFamilyFor } from "./inventoryDisplay";
import { resolveFindingTemplate } from "./findingTemplates";

const shortOutlet = (name) => (name ?? "").replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim() || name;
const shortProduct = (name) => (name ?? "").split(/\s+-\s+(?:Celeste\b|SKU\b)/)[0];
// MONEY_KINDS stays a plain kind set, not tag-driven: it's a THIRD, orthogonal
// axis (does this finding carry a real Rs figure) that cuts across tags --
// WRITE_OFF and most of STOCK_POSITION are count-only, not money.
const MONEY_KINDS = new Set(["stuck_stock_count_value_at_risk", "completed_stock_count_variance", "inventory_value_at_risk", "damage_writeoff"]);

function formatWhen(value) {
  if (!value) return "Not recorded";
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Odoo stores staff logins as email addresses; a device or shared account has none.
function personLabel(login) {
  if (!login) return "Not recorded";
  return login.includes("@") ? login.replace(/@.*/, "") : `${login} (shared account)`;
}

const RECORD_COLUMNS_TRANSFER = [
  { key: "transfer_out", label: "Transfer note (out)" },
  { key: "planned_by", label: "Planned by", format: personLabel },
  { key: "plan_qty", label: "Plan", format: (v) => formatNumber(v) },
  { key: "out_sent_on", label: "Sent on", format: formatWhen },
  { key: "out_sent_by", label: "Sent by (marked done)", format: personLabel },
  { key: "sent_qty", label: "Sent", format: (v) => formatNumber(v) },
  { key: "receipt", label: "Receipt (in)" },
  { key: "receipt_state", label: "Receipt status" },
  { key: "received_on", label: "Received on", format: formatWhen },
  { key: "receipt_completed_by", label: "Received by (marked done)", format: personLabel },
  { key: "product", label: "Product", format: (v) => shortProduct(v) },
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "expected_qty", label: "Expected on receipt", format: (v) => formatNumber(v) },
  { key: "received_qty", label: "Received", format: (v) => formatNumber(v) },
  { key: "planning_gap_qty", label: "Planning gap", format: (v) => (v == null ? "n/a" : formatNumber(v)) },
  { key: "missing_qty", label: "Shortage recorded", format: (v) => formatNumber(v) },
  { key: "recorded_by", label: "Shortage recorded by", format: personLabel },
  { key: "recorded_on", label: "Recorded on", format: formatWhen },
];

const RECORD_COLUMNS_DISPATCH = [
  { key: "transfer_out", label: "Transfer note (out)" },
  { key: "date", label: "Dispatched on" },
  { key: "planned_by", label: "Planned by", format: personLabel },
  { key: "plan_qty", label: "Plan", format: (v) => formatNumber(v) },
  { key: "dispatched_by", label: "Sent by (marked done)", format: personLabel },
  { key: "sent_qty", label: "Sent", format: (v) => formatNumber(v) },
  { key: "over_qty", label: "Over plan", format: (v) => formatNumber(v) },
  { key: "ratio", label: "Times plan", format: (v) => (v == null ? "n/a" : `${v}x`) },
  { key: "product", label: "Product", format: (v) => shortProduct(v) },
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "receipt", label: "Receipt (in)" },
];

const RECORD_COLUMNS_STUCK_SESSIONS = [
  { key: "name", label: "Count" },
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "responsible", label: "Responsible" },
  { key: "days_stuck", label: "Days stuck", format: (v) => formatNumber(Math.round(v)) },
  { key: "scanned_lines", label: "Scanned" },
  { key: "total_lines", label: "Total lines" },
  { key: "unscanned_value_rs", label: "Unscanned value", format: (v) => formatCurrency(v) },
];

const RECORD_COLUMNS_COMPLETED_SESSIONS = [
  { key: "name", label: "Count" },
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "completed_at", label: "Completed", format: formatWhen },
  { key: "lines", label: "Lines" },
  { key: "gross_abs_variance_value_rs", label: "Variance", format: (v) => formatCurrency(v) },
  { key: "variance_rate_vs_system_pct", label: "Rate (vs. system qty)", format: (v) => (v == null ? "Unknown" : `${v}%`) },
  { key: "variance_rate_vs_counted_pct", label: "Rate (vs. counted qty)", format: (v) => (v == null ? "Unknown" : `${v}%`) },
];

const RECORD_COLUMNS_DAMAGE_PRODUCTS = [
  { key: "name", label: "Product", format: (v) => shortProduct(v) },
  { key: "sku", label: "SKU" },
  { key: "lines", label: "Lines" },
  { key: "qty", label: "Units", format: (v) => formatNumber(v) },
  { key: "value_rs", label: "Value", format: (v) => formatCurrency(v) },
];

const RECORD_COLUMNS_EXTREME = [
  { key: "date", label: "Date" },
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "product", label: "Product", format: (v) => shortProduct(v) },
  { key: "receipt", label: "Receipt" },
  { key: "expected_qty", label: "Expected", format: (v) => formatNumber(v) },
  { key: "received_qty", label: "Received", format: (v) => formatNumber(v) },
  { key: "recorded_qty", label: "Missing quantity recorded", format: (v) => formatNumber(v) },
  { key: "recorded_by", label: "Recorded by" },
];

function Block({ title, children }) {
  return (
    <section className="rounded-lg border border-border bg-surface px-4 py-4">
      <h2 className="border-l-2 border-l-accent pl-2.5 font-display text-sm font-semibold text-foreground-strong">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function topOf(breakdown) {
  return breakdown?.top_contributors?.[0];
}

function answerFor(latest) {
  const kind = latest?.impact?.kind;
  const stores = latest?.breakdowns?.find((b) => b.dimension === "store")?.top_contributors ?? [];
  const topStore = stores[0] ? `${shortOutlet(stores[0].name)} (${formatNumber(stores[0].current)})` : null;
  if (tagFor(latest?.metric_id, { hasExtremeRows: (latest?.extreme_rows ?? []).length > 0 }) === INVENTORY_TAGS.TRANSFER_TRACE) return inventoryAnswerText(latest);
  if (kind === "products_out_of_stock") {
    const products = latest.impact.distinct_products;
    return `${formatNumber(products)} products are out of stock at one or more stores (${formatNumber(latest.impact.count)} product-store stockouts)`
      + (topStore ? `; most at ${topStore}` : "") + ". Check the highest-impact products first.";
  }
  if (kind === "top_sellers_out_of_stock") {
    const top = topOf(latest.breakdowns?.[0]);
    return `${formatNumber(latest.impact.count)} of your top ${formatNumber(latest.impact.total_top_sellers)} best-selling products are at zero or negative stock`
      + (top ? `, starting with ${shortProduct(top.name)}` : "") + ". Check replenishment for these first.";
  }
  if (kind === "stuck_stock_count_value_at_risk") {
    return `${formatNumber(latest.impact.count)} stock counts were started and never finished; their unscanned lines are valued at ${formatCurrency(latest.impact.financial_impact_rs)}`
      + (topStore ? `, most at ${topStore.replace(/ \(.*\)$/, "")}` : "") + ". Finish or close these counts first.";
  }
  if (kind === "inventory_value_at_risk") {
    const topLine = topOf(latest.breakdowns?.find((b) => b.dimension === "product"));
    return `${formatCurrency(latest.impact.financial_impact_rs)} is tied up in dead and overstocked inventory`
      + (topLine ? `, led by ${shortProduct(topLine.name)}` : "") + ". Review the largest lines first.";
  }
  if (kind === "completed_stock_count_variance") {
    const type = topOf(latest.breakdowns?.find((b) => b.dimension === "product_type"));
    return `Completed stock counts recorded ${formatCurrency(latest.impact.financial_impact_rs)} in variance across ${formatNumber(latest.impact.count)} counts`
      + (topStore ? `, most at ${topStore.replace(/ \(.*\)$/, "")}` : "") + (type ? `, in ${type.name.toLowerCase()}` : "")
      + ". Review the count sheets and receiving records first.";
  }
  if (kind === "damage_writeoff") {
    const top = topOf(latest.breakdowns?.find((b) => b.dimension === "product"));
    return `${formatCurrency(latest.impact.financial_impact_rs)} in confirmed damage write-offs across ${formatNumber(latest.impact.count)} order(s)`
      + (topStore ? `, most at ${topStore.replace(/ \(.*\)$/, "")}` : "") + (top ? `, led by ${shortProduct(top.name)}` : "")
      + ". Check whether this reflects real spoilage/breakage or a process gap.";
  }
  if (kind === "dispatch_over_plan") {
    const top = topOf(latest.breakdowns?.find((b) => b.dimension === "product"));
    return `${formatNumber(latest.impact.count)} outbound line(s) were sent well above their planned quantity`
      + (top ? `, led by ${shortProduct(top.name)} (${formatNumber(top.current)} units over plan)` : "")
      + ". Check the dispatch before treating the extra stock as real.";
  }
  if (kind === "dispatch_pattern") {
    const top = topOf(latest.breakdowns?.find((b) => b.dimension === "person"));
    return `${formatNumber(latest.impact.count)} dispatcher/product pair(s) have a recorded shortage rate well above the rate on the other products they marked as dispatched`
      + (top ? `, led by ${personLabel(top.name)} (${formatNumber(top.current)} missing units)` : "")
      + ". Review the associated shortage records; this does not establish who caused the gap.";
  }
  return latest?.title ?? "No summary available for this case yet.";
}

function valueLineFor(latest) {
  const kind = latest?.impact?.kind;
  if (tagFor(latest?.metric_id, { hasExtremeRows: (latest?.extreme_rows ?? []).length > 0 }) === INVENTORY_TAGS.TRANSFER_TRACE) return inventoryExposureLine(latest);
  if (kind === "stuck_stock_count_value_at_risk") {
    const v = latest.impact.valuation_coverage ?? {};
    const total = v.total_unscanned_lines ?? 0;
    return `Estimated value of unscanned lines: ${formatCurrency(latest.impact.financial_impact_rs)} at current cost. `
      + `Valuation basis: ${v.source_cost_lines ?? 0} lines at source cost, ${v.catalog_estimate_lines ?? 0} at catalog estimate, `
      + `${v.no_cost_data_lines ?? 0} with no cost, of ${total} lines · not confirmed loss.`;
  }
  if (kind === "inventory_value_at_risk") {
    return `Inventory value tied up: ${formatCurrency(latest.impact.financial_impact_rs)} at the recorded value · not confirmed loss.`;
  }
  if (kind === "completed_stock_count_variance") {
    const rate = latest.variance_rate_vs_system_pct;
    return `Estimated variance in completed counts: ${formatCurrency(latest.impact.financial_impact_rs)} at the recorded estimate`
      + (rate != null ? ` — ${rate}% of system-recorded inventory value` : "") + " · not confirmed loss.";
  }
  if (kind === "damage_writeoff") {
    const v = latest.impact.valuation_coverage ?? {};
    const total = v.total_lines ?? 0;
    return `Confirmed damage write-off value: ${formatCurrency(latest.impact.financial_impact_rs)} at catalog cost. `
      + `Valuation basis: ${v.catalog_estimate_lines ?? 0} lines at catalog estimate, ${v.no_cost_data_lines ?? 0} with no cost, `
      + `of ${total} lines · the write-off itself is confirmed in Odoo, this Rs figure is a lower-bound estimate.`;
  }
  if (latest?.impact?.financial_impact_rs != null) {
    return `Estimated value: ${formatCurrency(latest.impact.financial_impact_rs)} · not confirmed loss.`;
  }
  return null;
}

/** Opportunity cases describe a recovery, not a loss. They get their own short
 * view so a revenue estimate never reads as inventory exposure. */
function OpportunityView({ caseId, latest, primaryMetrics, followUp, trendNode, measurement, verificationText, relationships = [], relatedSignals = [] }) {
  return (
    <div className="mt-5 space-y-5">
      {primaryMetrics.length > 0 && <section aria-labelledby="inventory-key-metrics"><h2 id="inventory-key-metrics" className="mb-2 text-sm font-semibold text-foreground-strong">Key metrics</h2><PrimaryMetricStrip cards={primaryMetrics} /></section>}
      <section aria-labelledby="inventory-detected" className="rounded-xl border-l-4 border-accent-border bg-accent-soft px-5 py-4"><h2 id="inventory-detected" className="text-xs font-semibold uppercase tracking-wider text-accent">What Celeste detected</h2><p className="mt-2 max-w-4xl text-base font-medium text-foreground-strong">{latest?.title || "Favorable inventory movement was detected."}</p>{latest?.impact?.detail && <p className="mt-2 max-w-4xl text-sm text-foreground">{latest.impact.detail}</p>}</section>
      <section aria-labelledby="inventory-evidence" className="space-y-3"><h2 id="inventory-evidence" className="text-sm font-semibold text-foreground-strong">Evidence to review</h2><Block title="Change over time"><InvestigationResults latest={latest} showTrend /></Block>{trendNode && <details className="rounded-lg border border-border bg-surface px-4 py-3"><summary className="cursor-pointer text-sm font-medium text-foreground-strong">Provisional current-period check</summary><div className="mt-3">{trendNode}</div></details>}</section>
      <section aria-labelledby="inventory-interpretation" className="rounded-xl border border-border bg-surface p-4"><h2 id="inventory-interpretation" className="text-sm font-semibold text-foreground-strong">Celeste’s interpretation</h2><p className="mt-2 text-sm text-foreground">This is a favorable measured movement worth validating. It does not establish what caused the change.</p><p className="mt-2 text-sm text-foreground-subtle">Estimated benefit is not verified realized savings.</p></section>
      <section aria-labelledby="inventory-actions" className="rounded-xl border border-accent-border bg-accent-soft p-4"><h2 id="inventory-actions" className="text-sm font-semibold text-foreground-strong">Recommended management action</h2><p className="mt-2 text-sm text-foreground">Confirm the improvement continues before changing stock policy.</p><div className="mt-4">{followUp}</div></section>
      <RelatedInventoryFindings caseId={caseId} relationships={relationships} relatedSignals={relatedSignals} />
      <InventoryMethodology measurement={measurement} verificationText={verificationText} />
    </div>
  );
}

function EvidenceInterpretation({ latest, notKnown }) {
  const checks = latest?.root_cause?.checks ?? [];
  const measured = checks.filter((check) => check.verdict === "confirmed");
  const possible = checks.filter((check) => check.verdict === "possible_contributor");
  const unknown = checks.filter((check) => !["confirmed", "possible_contributor"].includes(check.verdict));
  return (
    <section aria-labelledby="inventory-interpretation" className="rounded-xl border border-border bg-surface p-4">
      <h2 id="inventory-interpretation" className="text-sm font-semibold text-foreground-strong">What the evidence means</h2>
      <p className="mt-2 text-xs text-foreground-subtle">This interpretation uses the saved finding checks; it does not add a cause that the records cannot establish.</p>
      {measured.length > 0 && <div className="mt-3"><h3 className="text-xs font-semibold uppercase tracking-wide text-foreground-subtle">Confirmed observations</h3><ul className="mt-1 space-y-1 text-sm text-foreground">{measured.map((check, index) => <li key={`${check.source}-${index}`}>{check.detail}</li>)}</ul></div>}
      {possible.length > 0 && <div className="mt-3"><h3 className="text-xs font-semibold uppercase tracking-wide text-foreground-subtle">Possibly related</h3><ul className="mt-1 space-y-1 text-sm text-foreground-subtle">{possible.map((check, index) => <li key={`${check.source}-${index}`}>{check.detail}</li>)}</ul></div>}
      {(unknown.length > 0 || notKnown.length > 0) && <div className="mt-3"><h3 className="text-xs font-semibold uppercase tracking-wide text-foreground-subtle">Not established</h3><ul className="mt-1 space-y-1 text-sm text-foreground-subtle">{unknown.map((check, index) => <li key={`${check.source}-${index}`}>{check.detail}</li>)}{notKnown.map((detail) => <li key={detail}>{detail}</li>)}</ul></div>}
      {measured.length === 0 && possible.length === 0 && unknown.length === 0 && notKnown.length === 0 && <p className="mt-3 text-sm text-foreground-subtle">No additional explanatory checks are attached to this finding.</p>}
      <p className="mt-3 border-t border-border pt-3 text-xs text-foreground-subtle">An observed association or discrepancy does not by itself establish cause or responsibility.</p>
    </section>
  );
}

function RelatedInventoryFindings({ caseId, relationships = [], relatedSignals = [] }) {
  if (relationships.length === 0 && relatedSignals.length === 0) return null;
  return <section aria-labelledby="inventory-related" className="rounded-xl border border-border bg-surface p-4"><h2 id="inventory-related" className="text-sm font-semibold text-foreground-strong">Related findings</h2>{relationships.length || relatedSignals.length ? <ul className="mt-3 space-y-2 text-sm">{relationships.map((row, index) => { const otherId = row.case_id === caseId ? row.related_case_id : row.case_id; return <li key={`${row.relationship_type}-${otherId}-${index}`} className="text-foreground-subtle">{row.relationship_type?.replaceAll("_", " ")}: {otherId ? <Link className="font-medium text-accent hover:underline" to={`/investigate/${encodeURIComponent(otherId)}`}>open related case</Link> : "related case ID not recorded"}{["duplicate_of", "overlaps"].includes(row.relationship_type) && " · amounts may overlap; do not add without checking the underlying records."}</li>; })}{relatedSignals.map((signal) => <li key={signal.metric_id} className="text-foreground-subtle">{signal.headline}{signal.case_id && <> · <Link className="font-medium text-accent hover:underline" to={`/investigate/${encodeURIComponent(signal.case_id)}`}>open related case</Link></>}</li>)}</ul> : <p className="mt-2 text-sm text-foreground-subtle">No related findings are linked to this case.</p>}</section>;
}

function InventoryMethodology({ measurement, verificationText, children }) {
  return <section aria-labelledby="inventory-methodology" className="rounded-xl border border-border bg-surface p-4"><h2 id="inventory-methodology" className="text-sm font-semibold text-foreground-strong">Data quality &amp; methodology</h2><p className="mt-2 text-sm text-foreground">{verificationText || "Verification explanation is not recorded."}</p><dl className="mt-3 grid gap-x-5 gap-y-2 border-t border-border pt-3 text-sm sm:grid-cols-3"><div><dt className="text-xs text-foreground-subtle">Source freshness</dt><dd className="font-medium text-foreground">{measurement?.freshness ?? "Not provided"}</dd></div><div><dt className="text-xs text-foreground-subtle">Data coverage</dt><dd className="font-medium text-foreground">{measurement?.coverage ?? "Not provided"}</dd></div><div><dt className="text-xs text-foreground-subtle">Cause confirmed</dt><dd className="font-medium text-foreground">Not established</dd></div></dl><p className="mt-2 text-xs text-foreground-subtle">Last measurement: {measurement?.observed_at ? formatWhen(measurement.observed_at) : "Not provided"}</p><div className="mt-2 space-y-2 text-sm text-foreground-subtle">{children}</div>{measurement && <details className="mt-3 border-t border-border pt-3"><summary className="cursor-pointer text-xs font-medium text-accent">Method and source details</summary><div className="mt-2 space-y-1 text-xs text-foreground-subtle"><p>Source check: {measurement.detector_id ?? "Not recorded"} · Observation period: {measurement.period ?? "Not recorded"}</p><p>{verificationText || "Verification explanation is not recorded."}</p></div></details>}</section>;
}

function FlowBlock({ impact, excludedRows = [], excludedTraceRows = [] }) {
  const quantity = (value) => value == null ? "Not recorded" : formatNumber(value);
  const cells = [
    ["Planned", impact.plan_units],
    ["Sent", impact.sent_units],
    ["Received", impact.line_received_units],
  ];
  return (
    <Block title="Quantity reconciliation">
      <p className="text-xs text-foreground-subtle">Odoo-recorded quantities for transfer/product movements linked to this finding.</p>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
        {cells.map(([label, value]) => <div key={label} className="rounded-lg bg-surface-sunken p-3"><p className="text-xs text-foreground-subtle">{label}</p><p className="mt-1 text-2xl font-semibold tabular-nums text-foreground-strong">{quantity(value)}</p><p className="text-xs text-foreground-subtle">units</p></div>)}
      </div>
      <dl className="mt-3 grid gap-x-5 gap-y-2 border-t border-border pt-3 text-sm sm:grid-cols-3">
        <div><dt className="text-xs text-foreground-subtle">Plan → sent</dt><dd className="font-medium tabular-nums text-foreground">{quantity(impact.planning_gap_units)} units fewer sent than planned</dd></div>
        <div><dt className="text-xs text-foreground-subtle">Sent → received</dt><dd className="font-medium tabular-nums text-foreground">{quantity(impact.transit_gap_units)} units fewer received than sent</dd></div>
        <div><dt className="text-xs text-foreground-subtle">Plan → received</dt><dd className="font-medium tabular-nums text-foreground">{quantity(impact.total_gap_units)} units fewer received than planned</dd></div>
      </dl>
      <p className="mt-3 rounded-md border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-foreground-subtle">
        Recorded shortage-reason units ({quantity(impact.units_affected)}) are a separate measure from these movement gaps. Their difference is not a reconciled loss; compare the underlying transfer and receipt records before drawing that conclusion.
      </p>
      {Number(impact.excluded_lines) > 0 && <div className="mt-3 rounded-md border border-warning/30 bg-warning/5 px-3 py-3 text-sm">
        <p className="font-semibold text-foreground-strong">Excluded from these totals: {formatNumber(impact.excluded_lines)} line(s), {formatNumber(impact.excluded_units)} units</p>
        <p className="mt-1 text-xs text-foreground-subtle">These shortage records are on cancelled or not-yet-received receipts. They are not counted as missing stock unless receipt and arrival evidence confirms that.</p>
        {excludedRows.length > 0 && <p className="mt-1 text-xs text-foreground-subtle">{excludedRows.map((row) => `${shortOutlet(row.outlet)}: ${formatNumber(row.lines)} line(s), ${formatNumber(row.units)} units (${(row.states ?? []).join(", ") || "receipt status not recorded"})`).join(" · ")}</p>}
        {excludedTraceRows.length > 0 && <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium text-accent">Inspect excluded receipt records ({formatNumber(excludedTraceRows.length)})</summary>
          <div className="mt-2 overflow-x-auto"><AffectedRecordTable columns={RECORD_COLUMNS_TRANSFER} rows={excludedTraceRows} rowKey="id" /></div>
        </details>}
        {excludedTraceRows.length === 0 && <p className="mt-1 text-xs text-foreground-subtle">The saved finding does not include receipt-level references for these excluded lines; refresh the finding to attach them.</p>}
      </div>}
      <details className="mt-3 rounded-lg border border-border px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium text-foreground-strong">How these quantities are counted</summary>
        <p className="mt-2 text-xs text-foreground-subtle">Plan and sent quantities are counted once per outbound transfer/product. Expected and received quantities are counted once per incoming stock move. Repeated shortage-reason rows remain separate for the recorded shortage total.</p>
      </details>
    </Block>
  );
}

function OutletReconciliationTable({ rows }) {
  if (!rows?.length) return <p className="text-sm text-foreground-subtle">No outlet-level quantities are available in this saved finding.</p>;
  const qty = (value) => value == null ? "Not recorded" : formatNumber(value);
  const fulfillmentRows = rows
    .map((row) => ({
      ...row,
      fulfillment_pct: Number(row.plan_units) > 0 && row.line_received_units != null
        ? (Number(row.line_received_units) / Number(row.plan_units)) * 100
        : null,
    }))
    .sort((a, b) => (a.fulfillment_pct ?? Infinity) - (b.fulfillment_pct ?? Infinity));
  const chartRows = fulfillmentRows.filter((row) => row.fulfillment_pct != null);
  const movementRows = fulfillmentRows.filter((row) => row.plan_units != null || row.sent_units != null || row.line_received_units != null);
  const movementMax = Math.max(1, ...movementRows.flatMap((row) => [row.plan_units, row.sent_units, row.line_received_units].filter((value) => value != null).map(Number)));
  return <div>
    {chartRows.length > 0 && <section aria-labelledby="outlet-fulfillment-heading" className="mb-5 rounded-lg bg-surface-sunken p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="outlet-fulfillment-heading" className="text-sm font-semibold text-foreground-strong">Planned quantity fulfillment</h3>
        <span className="text-xs text-foreground-subtle">Received ÷ planned · lowest first</span>
      </div>
      <p className="mt-1 text-xs text-foreground-subtle">Share of planned units recorded as received. This measures quantity, not on-time delivery (OTIF).</p>
      <div className="mt-4 space-y-3">
        {chartRows.map((row) => {
          const rate = row.fulfillment_pct;
          return <div key={row.outlet} className="grid grid-cols-[minmax(7rem,10rem)_1fr_auto] items-center gap-3 text-sm">
            <span className="truncate text-foreground">{shortOutlet(row.outlet)}</span>
            <div
              className="h-3 overflow-hidden rounded-full bg-surface"
              role="img"
              aria-label={`${shortOutlet(row.outlet)}: ${rate.toFixed(1)} percent of planned quantity received`}
              title={`${formatNumber(row.line_received_units)} received ÷ ${formatNumber(row.plan_units)} planned`}
            >
              <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(rate, 100))}%` }} />
            </div>
            <span className="w-16 text-right font-semibold tabular-nums text-foreground-strong">{rate.toFixed(1)}%</span>
          </div>;
        })}
      </div>
      {chartRows.some((row) => row.fulfillment_pct > 100) && <p className="mt-2 text-xs text-foreground-subtle">Rates above 100% mean recorded receipts exceeded the planned quantity; bars are capped at 100%.</p>}
    </section>}
    {movementRows.length > 0 && <section aria-labelledby="outlet-movement-heading" className="mb-5 rounded-lg bg-surface-sunken p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="outlet-movement-heading" className="text-sm font-semibold text-foreground-strong">What was planned, sent and received</h3>
        <div className="flex flex-wrap gap-3 text-xs text-foreground-subtle" aria-label="Chart legend">
          <span><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm bg-border" />Planned</span>
          <span><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm bg-accent/60" />Sent</span>
          <span><i className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm bg-accent" />Received</span>
        </div>
      </div>
      <p className="mt-1 text-xs text-foreground-subtle">Odoo-recorded units for each outlet. All bars share one scale; these records do not independently verify physical quantities.</p>
      <div className="mt-4 space-y-4">
        {movementRows.map((row) => <div key={row.outlet} className="grid gap-x-4 gap-y-2 sm:grid-cols-[9rem_minmax(0,1fr)] sm:items-center">
          <h4 className="text-sm font-medium text-foreground">{shortOutlet(row.outlet)}</h4>
          <div className="space-y-1.5">
            {[["Planned", row.plan_units, "bg-border"], ["Sent", row.sent_units, "bg-accent/60"], ["Received", row.line_received_units, "bg-accent"]].map(([label, value, color]) => <div key={label} className="grid grid-cols-[4.5rem_minmax(0,1fr)_3.5rem] items-center gap-2 text-xs">
              <span className="text-foreground-subtle">{label}</span>
              <div className="h-2 overflow-hidden rounded-full bg-surface" role="img" aria-label={`${shortOutlet(row.outlet)} ${label.toLowerCase()}: ${value == null ? "not recorded" : `${formatNumber(value)} units`}`}>
                {value != null && <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(0, Number(value) / movementMax * 100)}%` }} />}
              </div>
              <span className="text-right tabular-nums text-foreground">{value == null ? "—" : formatNumber(value)}</span>
            </div>)}
          </div>
        </div>)}
      </div>
    </section>}
    <div className="overflow-x-auto"><table className="w-full min-w-[840px] text-sm"><thead className="text-left text-xs text-foreground-subtle"><tr><th className="pb-2 pr-3 font-medium">Outlet</th><th className="pb-2 px-2 text-right font-medium">Planned</th><th className="pb-2 px-2 text-right font-medium">Sent</th><th className="pb-2 px-2 text-right font-medium">Received</th><th className="pb-2 px-2 text-right font-medium">Plan fulfillment</th><th className="pb-2 px-2 text-right font-medium">Plan − received</th><th className="pb-2 px-2 text-right font-medium">Sent − received</th><th className="pb-2 pl-2 text-right font-medium">Shortage reasons</th></tr></thead><tbody>{fulfillmentRows.map((row) => <tr key={row.outlet} className="border-t border-border"><th scope="row" className="py-2 pr-3 text-left font-medium text-foreground">{shortOutlet(row.outlet)}</th><td className="px-2 py-2 text-right tabular-nums">{qty(row.plan_units)}</td><td className="px-2 py-2 text-right tabular-nums">{qty(row.sent_units)}</td><td className="px-2 py-2 text-right tabular-nums">{qty(row.line_received_units)}</td><td className="px-2 py-2 text-right font-semibold tabular-nums text-foreground-strong">{row.fulfillment_pct == null ? "Unavailable" : `${row.fulfillment_pct.toFixed(1)}%`}</td><td className="px-2 py-2 text-right tabular-nums">{row.plan_units == null || row.line_received_units == null ? "Not recorded" : qty(row.plan_units - row.line_received_units)}</td><td className="px-2 py-2 text-right tabular-nums">{row.sent_units == null || row.line_received_units == null ? "Not recorded" : qty(row.sent_units - row.line_received_units)}</td><td className="py-2 pl-2 text-right tabular-nums">{qty(row.units)}</td></tr>)}</tbody></table></div>
    <p className="mt-2 text-xs text-foreground-subtle">Shortage-reason quantities are separate from movement gaps. Fulfillment is calculated from the planned and received quantities shown in each row.</p>
  </div>;
}

function DispatcherPairTable({ rows }) {
  if (!rows?.length) return <p className="text-sm text-foreground-subtle">No dispatcher/product pairs are available in this saved finding.</p>;
  const incompleteBaseline = rows.some((row) => Number(row.other_products_expected_units) <= 0 || row.other_products_expected_units == null);
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[780px] text-sm">
          <thead className="text-left text-xs text-foreground-subtle"><tr>
            <th className="pb-2 pr-3 font-medium">Dispatcher</th><th className="pb-2 pr-3 font-medium">Product</th>
            <th className="pb-2 px-2 text-right font-medium">Shortage units</th><th className="pb-2 px-2 text-right font-medium">Short lines</th>
            <th className="pb-2 px-2 text-right font-medium">Product units handled</th><th className="pb-2 px-2 text-right font-medium">Product rate</th>
            <th className="pb-2 px-2 text-right font-medium">Other-product baseline</th><th className="pb-2 pl-2 text-right font-medium">Rate multiple</th>
          </tr></thead>
          <tbody>{rows.map((row) => {
            const validProductRate = Number(row.expected_units) > 0 && row.rate_pct != null;
            const validBaseline = Number(row.other_products_expected_units) > 0 && row.own_rate_pct != null;
            const sourceRows = row.source_records ?? [];
            return (
              <tr key={`${row.dispatcher}-${row.sku ?? row.product}`} className="border-t border-border align-top">
                <td className="py-2 pr-3 text-foreground">{personLabel(row.dispatcher)}</td>
                <td className="py-2 pr-3 text-foreground">
                  {shortProduct(row.product)}{row.sku && <span className="block text-xs text-foreground-subtle">SKU {row.sku}</span>}
                  {sourceRows.length > 0 && <details className="mt-1"><summary className="cursor-pointer text-xs text-accent">Underlying shortage lines ({sourceRows.length})</summary><ul className="mt-1 space-y-1 text-xs text-foreground-subtle">{sourceRows.map((source) => <li key={source.id}>{source.receipt || "Receipt not recorded"} · {shortOutlet(source.outlet)} · {formatNumber(source.missing_units)} short of {formatNumber(source.expected_units)} · {source.date || "date not recorded"}</li>)}</ul></details>}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">{row.missing_units == null ? "Unavailable" : formatNumber(row.missing_units)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{row.short_lines == null ? "Unavailable" : formatNumber(row.short_lines)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{row.expected_units == null ? "Unavailable" : formatNumber(row.expected_units)}</td>
                <td className="px-2 py-2 text-right tabular-nums">{validProductRate ? `${row.rate_pct}%` : "Unavailable"}</td>
                <td className="px-2 py-2 text-right tabular-nums">{validBaseline ? <>{row.own_rate_pct}%<span className="block text-xs text-foreground-subtle">of {formatNumber(row.other_products_expected_units)} units</span></> : "Unavailable"}</td>
                <td className="py-2 pl-2 text-right tabular-nums">{validBaseline && row.ratio != null ? `${row.ratio}×` : "Unavailable"}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
      {incompleteBaseline && <p className="mt-2 rounded-md border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-foreground-subtle">Some saved rows lack the other-product handled volume needed to verify the comparison. Baseline rates and multiples are withheld for those rows.</p>}
      {!rows.some((row) => (row.source_records ?? []).length > 0) && <p className="mt-2 text-xs text-foreground-subtle">Underlying receipt lines are not included in this saved observation. Refresh the finding to attach source evidence.</p>}
      <p className="mt-2 text-xs text-foreground-subtle">A dispatcher label identifies who marked a transfer note done. The pattern does not establish who caused a shortage.</p>
    </div>
  );
}

function TransferProductTable({ rows }) {
  const byProduct = new Map();
  for (const r of rows) {
    const key = shortProduct(r.product);
    const g = byProduct.get(key) ?? { product: key, plan: 0, sent: 0, received: 0, missing: 0, hasPlan: false };
    const productId = r.product_id ?? r.sku ?? r.product;
    const outgoingKey = r.transfer_out_id != null ? `${r.transfer_out_id}:${productId}` : `${r.transfer_out ?? r.id}:${productId}`;
    const incomingKey = r.receipt_move_id != null ? String(r.receipt_move_id) : `${r.receipt_id ?? r.receipt ?? r.id}:${productId}`;
    g.outgoing = g.outgoing ?? new Set();
    g.incoming = g.incoming ?? new Set();
    if (!g.outgoing.has(outgoingKey)) {
      if (r.plan_qty != null) { g.plan += r.plan_qty; g.hasPlan = true; }
      if (r.sent_qty != null) g.sent += r.sent_qty;
      g.outgoing.add(outgoingKey);
    }
    if (!g.incoming.has(incomingKey)) {
      g.received += r.received_qty;
      g.incoming.add(incomingKey);
    }
    g.missing += r.missing_qty;
    byProduct.set(key, g);
  }
  const totalMissing = [...byProduct.values()].reduce((sum, g) => sum + g.missing, 0);
  const list = [...byProduct.values()].sort((a, b) => b.missing - a.missing);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-foreground-subtle">
          <tr>
            <th className="pb-2 pr-3 font-medium">Product</th>
            <th className="pb-2 pr-3 text-right font-medium">Plan</th>
            <th className="pb-2 pr-3 text-right font-medium">Sent</th>
            <th className="pb-2 pr-3 text-right font-medium">Received</th>
            <th className="pb-2 pr-3 text-right font-medium">Reason qty</th>
            <th className="pb-2 text-right font-medium">Share of reason qty</th>
          </tr>
        </thead>
        <tbody>
          {list.slice(0, 10).map((g) => (
            <tr key={g.product} className="border-t border-border">
              <td className="py-1.5 pr-3 text-foreground">{g.product}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{g.hasPlan ? formatNumber(g.plan) : "n/a"}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatNumber(g.sent)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatNumber(g.received)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatNumber(g.missing)}</td>
              <td className="py-1.5 text-right tabular-nums">{totalMissing ? Math.round((g.missing / totalMissing) * 1000) / 10 + "%" : "n/a"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {list.length > 10 && <p className="mt-2 text-xs text-foreground-subtle">Top 10 of {list.length} products; the full line-by-line trace is below.</p>}
    </div>
  );
}

/** Inventory case, laid out for an operations manager in order: what happened,
 * where, what is driving it, what to do, what is not known yet, then the raw
 * records and data details collapsed. Each finding type states its own value
 * basis; counts are never presented as rupees. */
export default function InventoryCaseView({ caseId, latest, primaryMetrics, followUp, trendNode, measurement, verificationText, accountabilityNode, transferView, relationships = [], relatedSignals = [] }) {
  const [showAllProducts, setShowAllProducts] = useState(false);
  if (latest?.category === "opportunity") return <OpportunityView caseId={caseId} latest={latest} primaryMetrics={primaryMetrics} followUp={followUp} trendNode={trendNode} measurement={measurement} verificationText={verificationText} relationships={relationships} relatedSignals={relatedSignals} />;

  const kind = latest?.impact?.kind;
  const findingTemplate = resolveFindingTemplate(latest);
  const hasExtremeRows = (latest?.extreme_rows ?? []).length > 0;
  const tag = tagFor(latest?.metric_id, { hasExtremeRows });
  const isTransfer = tag === INVENTORY_TAGS.TRANSFER_TRACE;
  const isExtreme = isTransfer && hasExtremeRows;
  const isDispatchOverage = tag === INVENTORY_TAGS.DISPATCH && kind === "dispatch_over_plan";
  const isDispatchPattern = tag === INVENTORY_TAGS.DISPATCH && kind === "dispatch_pattern";
  // NAMED_INCIDENT has a FEW named, wildly uneven records behind an
  // outlet-level total (one stuck count can be 90%+ of the whole case --
  // confirmed live) -- an outlet bar chart hides exactly that, so this tag
  // ranks the real records instead of the outlet aggregate, and gets its
  // own detail table below like TRANSFER_TRACE/DISPATCH already do. This
  // is the fix for findings that genuinely looked identical case to case:
  // same generic outlet bar chart regardless of what was actually driving
  // the number.
  const isStuckSessions = tag === INVENTORY_TAGS.NAMED_INCIDENT && kind === "stuck_stock_count_value_at_risk" && (latest?.sessions ?? []).length > 0;
  const isCompletedSessions = tag === INVENTORY_TAGS.NAMED_INCIDENT && kind === "completed_stock_count_variance" && (latest?.sessions ?? []).length > 0;
  const isDamageWriteoff = tag === INVENTORY_TAGS.WRITE_OFF;
  const transferFiltersOn = isTransfer && isFilterActive(transferView?.filters);
  const traceRows = isTransfer ? filterTraceRows(latest?.trace_rows ?? [], transferView?.filters, transferView?.options) : [];
  const transferSummary = isTransfer ? summarizeTraceRows(traceRows) : null;
  const records = isExtreme ? latest.extreme_rows : isDispatchOverage ? (latest.dispatch_rows ?? []) : isDispatchPattern ? (latest.pattern_rows ?? [])
    : isStuckSessions || isCompletedSessions ? (latest.sessions ?? [])
    : isDamageWriteoff ? (latest.top_products ?? [])
    : traceRows;
  const valueLine = valueLineFor(latest);
  const isMoney = MONEY_KINDS.has(kind);
  const storeBreakdown = latest?.breakdowns?.find((b) => b.dimension === "store" || b.dimension === "outlet");
  const sessionValueKey = isStuckSessions ? "unscanned_value_rs" : "gross_abs_variance_value_rs";
  const outletData = (isStuckSessions || isCompletedSessions)
    ? [...(latest.sessions ?? [])]
        .sort((a, b) => b[sessionValueKey] - a[sessionValueKey])
        .map((s) => ({ name: `${s.name} (${shortOutlet(s.outlet)})`, current: s[sessionValueKey] }))
    : (storeBreakdown?.top_contributors ?? []).map((c) => ({ ...c, name: shortOutlet(c.name) }));
  const outletRates = latest?.outlet_rows ?? [];
  const hasGitOrRelatedRows = (latest?.git_rows ?? []).length > 0
    || (latest?.source_location_rows ?? []).some((row) => row.usage === "transit")
    || (latest?.related_transfer_outcomes ?? []).length > 0;
  const latestSourceRecord = isTransfer
    ? traceRows.map((row) => row.recorded_on).filter(Boolean).sort().at(-1)
    : null;
  const whatBreakdowns = (latest?.breakdowns ?? []).filter((b) => b.dimension !== "store" && b.dimension !== "outlet");
  const stuckValuation = kind === "stuck_stock_count_value_at_risk" ? (latest.impact?.valuation_coverage ?? {}) : null;
  const notKnown = [
    ...(stuckValuation?.no_cost_data_lines ? [`${formatNumber(stuckValuation.no_cost_data_lines)} unscanned lines have no cost and are not valued, so the total is a lower bound.`] : []),
    ...(stuckValuation?.catalog_estimate_lines ? [`${formatNumber(stuckValuation.catalog_estimate_lines)} lines are valued from a catalog estimate rather than their own cost.`] : []),
    ((latest?.impact?.excluded_lines ?? 0) > 0
      ? [`${formatNumber(latest.impact.excluded_lines)} ${latest.impact.excluded_lines === 1 ? "line" : "lines"} on cancelled or not-yet-received receipts (${formatNumber(latest.impact.excluded_units ?? 0)} units) are excluded from the movement totals and shortage finding.`]
      : []),
  ];
  const inventoryFamily = inventoryFamilyFor({ ...latest, domain: "inventory" });

  return (
    <div className="mt-5 space-y-5">
      {primaryMetrics.length > 0 && <section aria-labelledby="inventory-key-metrics"><h2 id="inventory-key-metrics" className="mb-2 text-sm font-semibold text-foreground-strong">Key metrics</h2><PrimaryMetricStrip cards={primaryMetrics} /></section>}
      <section aria-labelledby="inventory-detected" className="rounded-xl border-l-4 border-accent bg-surface px-5 py-4">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-foreground-subtle">
          <span id="inventory-detected" className="text-accent">What Celeste detected</span>
          <span aria-hidden="true">·</span>
          {inventoryFamily && <span>{inventoryFamily.label}</span>}
          {inventoryFamily && <span aria-hidden="true">·</span>}
          <span>{findingTemplate.label}</span>
        </div>
        <h2 className="mt-2 w-full font-display text-xl font-semibold leading-snug text-foreground-strong">{answerFor(latest)}</h2>
        {valueLine ? <p className="mt-2 w-full text-sm text-foreground-subtle">{valueLine}</p> : (
          <p className="mt-2 text-sm text-foreground-subtle">No rupee value is recorded for this finding; the counts above are the measure.</p>
        )}
        {(isExtreme || isDispatchPattern) && <p className="mt-3 max-w-4xl rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-sm text-foreground-subtle">This is a severity or pattern view of recorded receiving shortages. Its lines may also appear in the broader shortage finding; do not add the unit totals across these findings.</p>}
      </section>

      <div className="space-y-5">
      <section aria-labelledby="inventory-evidence" className="min-w-0 space-y-3">
          <h2 id="inventory-evidence" className="text-sm font-semibold text-foreground-strong">Evidence to review</h2>
          {isTransfer && transferView?.filterBar && (
            <section aria-label="Source record filters" className="rounded-lg border border-border bg-surface px-4 py-3">
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Filter source records</h2>
              {transferView.filterBar}
              <p className="mt-3 text-xs text-foreground-subtle">
                {transferFiltersOn
                  ? `Showing ${formatNumber(traceRows.length)} of ${formatNumber(latest?.trace_rows?.length ?? 0)} lines. Headline metrics, quantity reconciliation and outlet comparison remain on the full finding scope.`
                  : "Filters narrow the source records and product ranking below. Headline metrics, quantity reconciliation and outlet comparison remain on the full finding scope."}
              </p>
              {isTransfer && transferFiltersOn && traceRows.length === 0 && <p className="mt-3 text-sm text-foreground">No lines in this finding match these filters.</p>}
            </section>
          )}

          {isTransfer && latest?.impact?.plan_units != null && <FlowBlock impact={latest.impact} excludedRows={latest.excluded_rows ?? []} excludedTraceRows={latest.excluded_trace_rows ?? []} />}

          {isTransfer && hasGitOrRelatedRows && (
            <Block title="GIT, damage and return evidence">
              <div className="space-y-4">
                <div>
                  <h3 className="text-sm font-semibold text-foreground-strong">Current GIT lane balances</h3>
                  <p className="mt-1 text-xs text-foreground-subtle">Current on-hand stock in Odoo locations marked as transit. These balances are lane/product context and cannot be attributed to a historical transfer.</p>
                  {(latest?.source_location_rows ?? []).filter((row) => row.usage === "transit").length > 0 ? (
                    <div className="mt-2 overflow-x-auto"><table className="w-full text-sm"><thead className="text-left text-xs text-foreground-subtle"><tr><th className="pb-2 pr-3 font-medium">Transit location</th><th className="pb-2 pr-3 text-right font-medium">Affected products</th><th className="pb-2 pr-3 text-right font-medium">Current on hand</th><th className="pb-2 text-right font-medium">Reserved</th></tr></thead><tbody>{latest.source_location_rows.filter((row) => row.usage === "transit").map((row) => <tr key={row.location} className="border-t border-border"><td className="py-1.5 pr-3">{row.location}</td><td className="py-1.5 pr-3 text-right tabular-nums">{formatNumber(row.affected_products)}</td><td className="py-1.5 pr-3 text-right tabular-nums">{formatNumber(row.current_on_hand_units)}</td><td className="py-1.5 text-right tabular-nums">{formatNumber(row.current_reserved_units)}</td></tr>)}</tbody></table></div>
                  ) : <p className="mt-1 text-sm text-foreground-subtle">No current GIT balance was attached to this saved observation. A new scan is needed to show the location balances.</p>}
                  {(latest?.git_rows ?? []).length > 0 && <details className="mt-2 text-sm"><summary className="cursor-pointer font-medium text-accent">View current transit stock by product ({formatNumber(latest.git_rows.length)})</summary><div className="mt-2 max-h-80 overflow-auto"><table className="w-full text-sm"><thead className="sticky top-0 bg-surface text-left text-xs text-foreground-subtle"><tr><th className="pb-2 pr-3 font-medium">Location</th><th className="pb-2 pr-3 font-medium">Product</th><th className="pb-2 pr-3 text-right font-medium">On hand</th><th className="pb-2 text-right font-medium">Reserved</th></tr></thead><tbody>{latest.git_rows.map((row) => <tr key={`${row.location}-${row.sku ?? row.product}`} className="border-t border-border"><td className="py-1.5 pr-3">{row.location}</td><td className="py-1.5 pr-3">{shortProduct(row.product)}</td><td className="py-1.5 pr-3 text-right tabular-nums">{formatNumber(row.on_hand_units)}</td><td className="py-1.5 text-right tabular-nums">{formatNumber(row.reserved_units)}</td></tr>)}</tbody></table></div></details>}
                  {(latest?.source_location_rows ?? []).some((row) => row.usage !== "transit") && <p className="mt-2 text-xs text-foreground-subtle">Some source paths under “Transits” are marked as internal locations in Odoo; they are not counted as GIT balances.</p>}
                </div>
                <div className="border-t border-border pt-3">
                  <h3 className="text-sm font-semibold text-foreground-strong">Other reasons on the same receipt and product</h3>
                  <p className="mt-1 text-xs text-foreground-subtle">These are linked receipt records, shown separately from missing-item quantities.</p>
                  {(latest?.related_transfer_outcomes ?? []).length > 0 ? <ul className="mt-2 space-y-1 text-sm">{latest.related_transfer_outcomes.map((row) => <li key={`${row.reason}-${row.reason_type}`}><span className="font-medium text-foreground">{row.reason}</span> ({row.reason_type?.replaceAll("_", " ") ?? "type not recorded"}): {formatNumber(row.records)} records, {formatNumber(row.units)} units across {formatNumber(row.receipt_product_pairs)} receipt/product pair(s)</li>)}</ul> : <p className="mt-1 text-sm text-foreground-subtle">No other reason records are linked to the affected receipt/product pairs in this saved observation. Damage and return records outside these pairs are not included here.</p>}
                </div>
              </div>
            </Block>
          )}

      {isTransfer ? (
      <Block title="Outlet reconciliation">
        {outletRates.length > 0 ? <OutletReconciliationTable rows={outletRates} /> : <OutletReconciliationTable rows={transferSummary?.outletRows ?? []} />}
        <p className="mt-3 text-xs text-foreground-subtle">This table shows the saved finding’s full outlet scope. Source-record filters do not change these totals. The fulfillment rate is received quantity divided by planned quantity; it does not measure delivery timing.</p>
      </Block>
      ) : outletData.length > 0 ? (
      <Block title="Outlet distribution">
            {(
            <RankedBarChart
              data={outletData}
              nameKey="name"
              valueKey="current"
              formatValue={(v) => (isMoney ? formatCurrency(v) : formatNumber(v))}
            />
            )}
      </Block>
      ) : null}

      <Block title={isDispatchPattern ? "Dispatcher/product pairs" : isTransfer ? "Ranked product discrepancies" : "Largest contributing records"}>
        {isTransfer ? <TransferProductTable rows={traceRows} /> : isDispatchPattern ? <DispatcherPairTable rows={latest?.pattern_rows ?? []} /> : whatBreakdowns.length > 0 ? whatBreakdowns.map((breakdown) => {
          const rows = breakdown.top_contributors ?? [];
          const shown = showAllProducts ? rows : rows.slice(0, 5);
          const total = rows.reduce((sum, r) => sum + Math.abs(r.current ?? 0), 0);
          const isCover = rows.some((r) => r.days_of_cover != null);
          const valueOf = (r) => (isCover ? r.days_of_cover : r.current);
          const valueHeader = isMoney ? "Value" : isCover ? "Days of cover" : isTransfer ? "Missing units" : isDispatchOverage ? "Units over plan"
            : isDispatchPattern ? "Missing units"
            // products_out_of_stock's product breakdown (added alongside the
            // stuck_stock_count product-breakdown fix) ranks by recent
            // demand, not current stock -- every row is already at/below
            // zero stock by definition, so "Units in stock" would mislabel
            // the actual sold_30d values shown.
            : kind === "products_out_of_stock" && breakdown.dimension === "product" ? "Recent demand (30d)"
            : "Units in stock";
          return (
            <div key={breakdown.label} className="mb-4 last:mb-0">
              <p className="mb-2 text-xs font-semibold text-foreground-subtle">{breakdown.label}</p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-foreground-subtle">
                    <tr>
                      <th className="pb-2 pr-3 font-medium">{breakdown.dimension === "product" ? "Product" : "Name"}</th>
                      <th className="pb-2 pr-3 text-right font-medium">{valueHeader}</th>
                      {(isMoney || isTransfer || isDispatchOverage || isDispatchPattern) && <th className="pb-2 pr-3 text-right font-medium">Share</th>}
                      {rows.some((r) => r.lines != null) && <th className="pb-2 text-right font-medium">Lines</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r) => (
                      <tr key={`${r.name}-${r.sku ?? ""}`} className="border-t border-border">
                        <td className="py-1.5 pr-3 text-foreground">{shortProduct(r.name)}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{isMoney ? formatCurrency(r.current) : isCover ? formatNumber(valueOf(r)) : formatNumber(r.current)}</td>
                        {(isMoney || isTransfer || isDispatchOverage || isDispatchPattern) && <td className="py-1.5 pr-3 text-right tabular-nums">{total ? `${Math.round((Math.abs(r.current) / total) * 1000) / 10}%` : "n/a"}</td>}
                        {rows.some((x) => x.lines != null) && <td className="py-1.5 text-right tabular-nums">{r.lines ?? ""}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        }) : (
          <p className="text-sm text-foreground-subtle">The product list for this finding is not stored on the case. Check the stock alerts for the affected products.</p>
        )}
        {whatBreakdowns.some((b) => (b.top_contributors ?? []).length > 5) && (
          <button type="button" className="mt-2 text-sm text-accent hover:underline" onClick={() => setShowAllProducts((v) => !v)}>
            {showAllProducts ? "Show top 5 per list" : "Show all rows"}
          </button>
        )}
      </Block>

          {(accountabilityNode || (isDispatchPattern && transferView?.filterBar)) && (
            <details className="rounded-lg border border-border bg-surface px-4 py-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Related company-wide transfer context (optional)</summary>
              <p className="mt-2 text-xs text-foreground-subtle">This context can help compare related records; concentration does not establish who caused a discrepancy.</p>
              {isDispatchPattern && transferView?.filterBar && <div className="mt-3"><p className="mb-2 text-xs text-foreground-subtle">These filters apply to the company-wide comparison below, not to the saved dispatcher/product pair measures.</p>{transferView.filterBar}</div>}
              <div className="mt-3">{accountabilityNode}</div>
            </details>
          )}

          {records.length > 0 && !isDispatchPattern && (
            isTransfer ? <details className="rounded-lg border border-border bg-surface px-4 py-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Source shortage records · {formatNumber(records.length)}{latestSourceRecord ? ` · latest record ${formatWhen(latestSourceRecord)}` : ""}</summary>
              <p className="mt-2 text-xs text-foreground-subtle">Rows preserve individual Odoo shortage records. Movement quantities in the comparison above are deduplicated by their stock-move IDs.</p>
              <div className="mt-3 overflow-x-auto">
                <AffectedRecordTable columns={RECORD_COLUMNS_TRANSFER} rows={records} rowKey="id" />
              </div>
            </details> : <details className="rounded-lg border border-border bg-surface px-4 py-3">
                <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">View source records ({formatNumber(records.length)})</summary>
                <p className="mt-2 text-xs text-foreground-subtle">These are the underlying records supplied with this saved finding. The summary and breakdowns above remain the measured result.</p>
                <div className="mt-3 overflow-x-auto">
                  <AffectedRecordTable
                    columns={isExtreme ? RECORD_COLUMNS_EXTREME : isDispatchOverage ? RECORD_COLUMNS_DISPATCH
                      : isStuckSessions ? RECORD_COLUMNS_STUCK_SESSIONS
                      : isCompletedSessions ? RECORD_COLUMNS_COMPLETED_SESSIONS : isDamageWriteoff ? RECORD_COLUMNS_DAMAGE_PRODUCTS
                      : RECORD_COLUMNS_TRANSFER}
                    rows={records}
                    rowKey={isDispatchPattern || isDamageWriteoff ? "sku" : "id"}
                  />
                </div>
            </details>
          )}
      {trendNode && (
        <details className="rounded-lg border border-border bg-surface px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-foreground-strong">Provisional current-period check</summary>
          <div className="mt-3">{trendNode}</div>
        </details>
      )}
      <EvidenceInterpretation latest={latest} notKnown={notKnown} />
      </section>
      <RelatedInventoryFindings caseId={caseId} relationships={relationships} relatedSignals={relatedSignals} />
      </div>
      <InventoryMethodology measurement={measurement} verificationText={verificationText}>
        {isTransfer && <p>Plan is the quantity on the transfer note, Sent is the quantity completed on that note, and Received is the quantity completed on the receipt. Status-history users show who closed a record, not who physically carried goods. Shared accounts do not identify an individual.</p>}
        {isTransfer && <p>Discrepancy rates divide units recorded missing by units received in the same window. Estimated exposure uses current catalog cost; lines without cost are not valued.</p>}
        {isDispatchOverage && <p>This flags outbound lines at least 5x planned and 50 units over plan. It does not establish whether the extra quantity reflects a top-up, picking error, or unit mismatch.</p>}
        {isDispatchPattern && <p>This compares each recurring product’s recorded shortage rate with the rate for the same marked dispatcher’s other products. It uses minimum rate and handled-unit thresholds; it identifies records for review and does not establish fault. If the comparison baseline is unavailable, run a fresh investigation before using the ratio.</p>}
      </InventoryMethodology>
    </div>
  );
}
