import { useState } from "react";
import RankedBarChart from "../charts/RankedBarChart";
import { formatCurrency, formatNumber } from "../format";
import { AffectedRecordTable, PrimaryMetricStrip } from "./CaseVisuals";
import TransferFlowCharts from "./TransferFlowCharts";
import { inventoryAnswerText, inventoryExposureLine, inventoryNextCheckText } from "./plainLanguage";
import { filterTraceRows, isFilterActive, summarizeTraceRows } from "./transferFilters";

const shortOutlet = (name) => (name ?? "").replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim() || name;
const shortProduct = (name) => (name ?? "").split(/\s+-\s+(?:Celeste\b|SKU\b)/)[0];
const plainUnknown = (text) => {
  const stripped = (text ?? "").replace(/^Not established:\s*/i, "");
  return stripped.charAt(0).toUpperCase() + stripped.slice(1);
};

const TRANSFER_KINDS = new Set(["transfer_discrepancy"]);
const MONEY_KINDS = new Set(["stuck_stock_count_value_at_risk", "completed_stock_count_variance", "inventory_value_at_risk"]);

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
  { key: "received_on", label: "Received on", format: formatWhen },
  { key: "receipt_completed_by", label: "Received by (marked done)", format: personLabel },
  { key: "product", label: "Product", format: (v) => shortProduct(v) },
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "expected_qty", label: "Expected on receipt", format: (v) => formatNumber(v) },
  { key: "received_qty", label: "Received", format: (v) => formatNumber(v) },
  { key: "planning_gap_qty", label: "Planning gap", format: (v) => (v == null ? "n/a" : formatNumber(v)) },
  { key: "missing_qty", label: "Missing (transit)", format: (v) => formatNumber(v) },
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

const RECORD_COLUMNS_PATTERN = [
  { key: "dispatcher", label: "Dispatcher", format: personLabel },
  { key: "product", label: "Product", format: (v) => shortProduct(v) },
  { key: "rate_pct", label: "Rate on this product", format: (v) => `${v}%` },
  { key: "own_rate_pct", label: "Their own overall rate", format: (v) => `${v}%` },
  { key: "ratio", label: "Times their own rate", format: (v) => (v == null ? "n/a" : `${v}x`) },
  { key: "expected_units", label: "Handled", format: (v) => formatNumber(v) },
  { key: "missing_units", label: "Missing", format: (v) => formatNumber(v) },
  { key: "short_lines", label: "Short lines", format: (v) => formatNumber(v) },
  { key: "outlets", label: "Outlets", format: (v) => formatNumber(v) },
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
    <section className="rounded-xl border border-border bg-surface p-5">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">{title}</h2>
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
  if (TRANSFER_KINDS.has(kind) || (latest?.extreme_rows ?? []).length) return inventoryAnswerText(latest);
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
  if (kind === "dispatch_over_plan") {
    const top = topOf(latest.breakdowns?.find((b) => b.dimension === "product"));
    return `${formatNumber(latest.impact.count)} outbound line(s) were sent well above their planned quantity`
      + (top ? `, led by ${shortProduct(top.name)} (${formatNumber(top.current)} units over plan)` : "")
      + ". Check the dispatch before treating the extra stock as real.";
  }
  if (kind === "dispatch_pattern") {
    const top = topOf(latest.breakdowns?.find((b) => b.dimension === "person"));
    return `${formatNumber(latest.impact.count)} dispatcher/product pattern(s) show a miss rate well above that `
      + "person's own overall rate"
      + (top ? `, led by ${personLabel(top.name)} (${formatNumber(top.current)} missing units)` : "")
      + ". See the company-wide panel below to check the lines behind each pattern.";
  }
  return latest?.title ?? "No summary available for this case yet.";
}

function valueLineFor(latest) {
  const kind = latest?.impact?.kind;
  if (TRANSFER_KINDS.has(kind) || (latest?.extreme_rows ?? []).length) return inventoryExposureLine(latest);
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
    return `Estimated variance in completed counts: ${formatCurrency(latest.impact.financial_impact_rs)} at the recorded estimate · not confirmed loss.`;
  }
  if (latest?.impact?.financial_impact_rs != null) {
    return `Estimated value: ${formatCurrency(latest.impact.financial_impact_rs)} · not confirmed loss.`;
  }
  return null;
}

/** Opportunity cases describe a recovery, not a loss. They get their own short
 * view so a revenue estimate never reads as inventory exposure. */
function OpportunityView({ latest, followUp, trendNode }) {
  return (
    <div className="mt-4 space-y-5">
      <Block title="What happened">
        <p className="text-base text-foreground-strong">{latest?.title}</p>
        {latest?.impact?.detail && <p className="mt-2 text-sm text-foreground-subtle">{latest.impact.detail}</p>}
      </Block>
      <Block title="How to read this">
        <p className="text-sm text-foreground">
          This is an improvement signal, not a loss. Any rupee figure is a revenue estimate from recovered orders, so it is
          not counted as inventory exposure anywhere on this page.
        </p>
      </Block>
      {trendNode && (
        <details className="rounded-xl border border-border bg-surface p-4">
          <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Current-period check (provisional)</summary>
          <div className="mt-3">{trendNode}</div>
        </details>
      )}
      <section className="rounded-xl border border-accent-border bg-accent-soft p-5" aria-label="Recommended next check">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-accent">Recommended next check</h2>
        <p className="mt-2 text-base text-foreground-strong">Confirm the improvement continues for the next few weeks before changing stock policy.</p>
        <div className="mt-4">{followUp}</div>
      </section>
    </div>
  );
}

function FlowBlock({ impact }) {
  const stage = (label, value) => (
    <div className="rounded-lg border border-border p-3">
      <p className="text-xs font-medium uppercase tracking-wider text-foreground-subtle">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-foreground-strong">{formatNumber(value)} <span className="text-sm font-normal text-foreground-subtle">units</span></p>
    </div>
  );
  return (
    <Block title="From plan to receipt">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {stage("Planned (transfer note)", impact.plan_units)}
        {stage("Sent (marked done)", impact.sent_units)}
        {stage("Received (marked done)", impact.line_received_units)}
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
        <p><span className="font-semibold text-foreground-strong">{formatNumber(impact.planning_gap_units)} units</span> <span className="text-foreground-subtle">planning gap: planned but not sent</span></p>
        <p><span className="font-semibold text-foreground-strong">{formatNumber(impact.transit_gap_units)} units</span> <span className="text-foreground-subtle">transit and receiving gap: sent but not received (missing)</span></p>
        <p><span className="font-semibold text-foreground-strong">{formatNumber(impact.total_gap_units)} units</span> <span className="text-foreground-subtle">total gap: planned but not received</span></p>
      </div>
      <p className="mt-3 text-xs text-foreground-subtle">These are the quantities recorded in Odoo at each step, not physical counts.</p>
    </Block>
  );
}

function TransferProductTable({ rows }) {
  const byProduct = new Map();
  for (const r of rows) {
    const key = shortProduct(r.product);
    const g = byProduct.get(key) ?? { product: key, plan: 0, sent: 0, received: 0, missing: 0, hasPlan: false };
    if (r.plan_qty != null) { g.plan += r.plan_qty; g.hasPlan = true; }
    if (r.sent_qty != null) g.sent += r.sent_qty;
    g.received += r.received_qty;
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
            <th className="pb-2 pr-3 text-right font-medium">Missing</th>
            <th className="pb-2 text-right font-medium">Share of missing</th>
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
export default function InventoryCaseView({ latest, primaryMetrics, nextDecision, followUp, trendNode, measurement, verificationText, accountabilityNode, transferView }) {
  const [showAllProducts, setShowAllProducts] = useState(false);
  if (latest?.category === "opportunity") return <OpportunityView latest={latest} followUp={followUp} trendNode={trendNode} />;

  const kind = latest?.impact?.kind;
  const isTransfer = TRANSFER_KINDS.has(kind) || (latest?.extreme_rows ?? []).length > 0;
  const isExtreme = (latest?.extreme_rows ?? []).length > 0;
  const isDispatchOverage = kind === "dispatch_over_plan";
  const isDispatchPattern = kind === "dispatch_pattern";
  const transferFiltersOn = isTransfer && isFilterActive(transferView?.filters);
  const traceRows = isTransfer ? filterTraceRows(latest?.trace_rows ?? [], transferView?.filters, transferView?.options) : [];
  const transferSummary = transferFiltersOn ? summarizeTraceRows(traceRows) : null;
  const records = isExtreme ? latest.extreme_rows : isDispatchOverage ? (latest.dispatch_rows ?? []) : isDispatchPattern ? (latest.pattern_rows ?? []) : traceRows;
  const valueLine = valueLineFor(latest);
  const isMoney = MONEY_KINDS.has(kind);
  const storeBreakdown = latest?.breakdowns?.find((b) => b.dimension === "store" || b.dimension === "outlet");
  const outletData = (storeBreakdown?.top_contributors ?? []).map((c) => ({ ...c, name: shortOutlet(c.name) }));
  const outletRates = transferFiltersOn ? transferSummary.outletRows : latest?.outlet_rows ?? [];
  const topRate = [...(latest?.outlet_rows ?? [])].filter((o) => o.unit_rate != null).sort((a, b) => b.unit_rate - a.unit_rate)[0];
  const whatBreakdowns = (latest?.breakdowns ?? []).filter((b) => b.dimension !== "store" && b.dimension !== "outlet");
  const stuckValuation = kind === "stuck_stock_count_value_at_risk" ? (latest.impact?.valuation_coverage ?? {}) : null;
  const notKnown = [
    ...(stuckValuation?.no_cost_data_lines ? [`${formatNumber(stuckValuation.no_cost_data_lines)} unscanned lines have no cost and are not valued, so the total is a lower bound.`] : []),
    ...(stuckValuation?.catalog_estimate_lines ? [`${formatNumber(stuckValuation.catalog_estimate_lines)} lines are valued from a catalog estimate rather than their own cost.`] : []),
    ...(latest?.root_cause?.checks ?? []).filter((c) => c.verdict === "unknown" || c.verdict === "possible_contributor").map((c) => plainUnknown(c.detail)),
    ...(latest?.excluded_rows?.length
      ? [`${formatNumber(latest.impact?.excluded_lines ?? 0)} ${(latest.impact?.excluded_lines ?? 0) === 1 ? "line" : "lines"} on cancelled or not-yet-received receipts (${formatNumber(latest.impact?.excluded_units ?? 0)} units) are excluded from every figure on this page.`]
      : []),
  ];

  return (
    <div className="mt-4 space-y-5">
      <section className="rounded-xl border border-border bg-surface p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">What happened</h2>
        <p className="mt-3 text-base text-foreground-strong">{answerFor(latest)}</p>
        {valueLine ? <p className="mt-2 text-sm text-foreground-subtle">{valueLine}</p> : (
          <p className="mt-2 text-sm text-foreground-subtle">No rupee value is recorded for this finding; the counts above are the measure.</p>
        )}
      </section>

      {(isTransfer || isDispatchPattern) && transferView?.filterBar && (
        <section aria-label="Filter this result" className="rounded-xl border border-border bg-surface p-4">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Filter this result</h2>
          {transferView.filterBar}
          <p className="mt-3 text-xs text-foreground-subtle">
            {!isTransfer
              ? "Filters apply to the company-wide panel below."
              : transferFiltersOn
              ? `Showing ${formatNumber(traceRows.length)} of ${formatNumber(latest?.trace_rows?.length ?? 0)} lines in this finding, and the company-wide panel below. The headline figures above cover the whole finding.`
              : "Filters apply to the lines in this finding and to the company-wide panel below."}
          </p>
          {isTransfer && transferFiltersOn && traceRows.length === 0 && <p className="mt-3 text-sm text-foreground">No lines in this finding match these filters.</p>}
        </section>
      )}

      {primaryMetrics.length > 0 && <PrimaryMetricStrip cards={primaryMetrics} />}

      {isTransfer && (transferFiltersOn ? transferSummary.impact.plan_units != null : latest?.impact?.plan_units != null) && <FlowBlock impact={transferFiltersOn ? transferSummary.impact : latest.impact} />}

      <Block title="Where it is happening">
        {(isTransfer ? outletRates.length > 0 : outletData.length > 0) ? (
          <>
            {isTransfer ? <TransferFlowCharts outlets={outletRates} /> : (
            <RankedBarChart
              data={outletData}
              nameKey="name"
              valueKey="current"
              formatValue={(v) => (isMoney ? formatCurrency(v) : isTransfer ? `${formatNumber(v)} missing units` : formatNumber(v))}
            />
            )}
            {topRate && (
              <p className="mt-3 text-sm text-foreground">
                {shortOutlet(topRate.outlet)} has the highest missing rate: {topRate.unit_rate}% of the units it received.
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-foreground-subtle">This finding is company-wide; no outlet breakdown is recorded.</p>
        )}
      </Block>

      <Block title="What is driving it">
        {isTransfer ? <TransferProductTable rows={traceRows} /> : whatBreakdowns.length > 0 ? whatBreakdowns.map((breakdown) => {
          const rows = breakdown.top_contributors ?? [];
          const shown = showAllProducts ? rows : rows.slice(0, 5);
          const total = rows.reduce((sum, r) => sum + Math.abs(r.current ?? 0), 0);
          const isCover = rows.some((r) => r.days_of_cover != null);
          const valueOf = (r) => (isCover ? r.days_of_cover : r.current);
          const valueHeader = isMoney ? "Value" : isCover ? "Days of cover" : isTransfer ? "Missing units" : isDispatchOverage ? "Units over plan" : isDispatchPattern ? "Missing units" : "Units in stock";
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

      <section className="rounded-xl border border-accent-border bg-accent-soft p-5" aria-label="Recommended next check">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-accent">Recommended next check</h2>
        <p className="mt-2 text-base text-foreground-strong">{latest?.recommendations?.[0]?.action || inventoryNextCheckText(latest) || nextDecision || "Review the evidence and decide on next steps."}</p>
        <div className="mt-4">{followUp}</div>
      </section>

      {accountabilityNode && <Block title="Who and what is driving the loss, company-wide">{accountabilityNode}</Block>}

      <Block title="What is not known yet">
        {notKnown.length > 0 ? (
          <ul className="list-disc space-y-2 pl-5 text-sm text-foreground">
            {notKnown.map((text) => <li key={text}>{text}</li>)}
          </ul>
        ) : (
          <p className="text-sm text-foreground-subtle">Nothing outstanding is recorded for this case.</p>
        )}
      </Block>

      {records.length > 0 && (
        <details className="rounded-xl border border-border bg-surface p-4">
          <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">View {isExtreme ? "receiving records" : isDispatchOverage ? "dispatch records, line by line" : isDispatchPattern ? "dispatcher/product pairs" : "transfer trace, line by line"} ({formatNumber(records.length)})</summary>
          <div className="mt-3">
            <div className="overflow-x-auto">
              <AffectedRecordTable columns={isExtreme ? RECORD_COLUMNS_EXTREME : isDispatchOverage ? RECORD_COLUMNS_DISPATCH : isDispatchPattern ? RECORD_COLUMNS_PATTERN : RECORD_COLUMNS_TRANSFER} rows={records} rowKey={isDispatchPattern ? "sku" : "id"} />
            </div>
          </div>
        </details>
      )}

      {trendNode && (
        <details className="rounded-xl border border-border bg-surface p-4">
          <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Current-period check (provisional)</summary>
          <div className="mt-3">{trendNode}</div>
        </details>
      )}
      <details className="rounded-xl border border-border bg-surface p-4">
        <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Data source and calculation details</summary>
        <div className="mt-3 space-y-2 text-sm text-foreground-subtle">
          <p>{verificationText}</p>
          {measurement && (
            <>
              <p>Data up to date: {measurement.freshness ?? "unknown"} · Data coverage: {measurement.coverage ?? "unknown"}</p>
              <p>Source check: {measurement.detector_id} · Period: {measurement.period || "unavailable"}</p>
            </>
          )}
          {isTransfer && <p>Plan is the quantity on the transfer note, Sent is the quantity completed on that note, and Received is the quantity completed on the receipt. Planned by is the note creator. Sent by and Received by are the people who moved each Odoo record to Done, taken from its status history. They show who closed the record, not who physically carried the goods. Accounts marked shared are device or team logins, so the individual cannot be identified from this data.</p>}
          {isTransfer && <p>Discrepancy rates divide the units recorded missing by the units received over the same window, counting only received receipts. Estimated exposure uses the current catalog cost; lines without a cost are not valued.</p>}
          {isDispatchOverage && <p>This flags an outbound line sent at least 5x its planned quantity and at least 50 units over plan -- the top ~0.3% of all outbound lines by this measure over the last 6 months, not routine rounding. It does not establish whether the extra quantity is a real top-up, a picking error, or a unit mismatch on the transfer note.</p>}
          {isDispatchPattern && <p>This flags a dispatcher's miss rate on one specific recurring product when it is at least 5% and at least 5x that dispatcher's own overall rate across everything else they handle, with at least 50 units handled on the pair. Raw concentration (one person sending most of a product) is not used: dispatchers are tied to fixed routes, so that is usually just who is on the route, not a behavior signal.</p>}
        </div>
      </details>
    </div>
  );
}
