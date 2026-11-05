import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchCases, fetchFreshProductionAverageWeight, fetchOperationsPickers, fetchOperationsSummary } from "../api";
import Card from "../design/Card";
import { formatCurrency, formatDuration, formatNumber, formatPercent } from "../format";

const OPERATION_METRICS = new Set([
  "cancellation_rate", "stockout_cancellation_rate", "pickme_cancellation_rate",
  "uber_failure_rate", "delayed_delivery_rate", "dispatch_wait_minutes",
]);

function isOperationsCase(item) {
  const metric = item?.metric_id || item?.latest?.metric_id || "";
  return OPERATION_METRICS.has(metric)
    || metric.startsWith("operation_flow_")
    || metric.startsWith("transfer_discrepancy_")
    || metric.startsWith("extreme_event_")
    || metric.startsWith("dispatch_");
}

function caseSummary(latest, item) {
  const detail = latest?.impact_summary?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  const evidence = latest?.evidence_summary;
  if (typeof evidence === "string" && evidence.trim()) return evidence;
  if (evidence && typeof evidence === "object") {
    const parts = [evidence.established, evidence.unknown].filter((part) => typeof part === "string" && part.trim());
    if (parts.length) return parts.join(" ");
  }
  return typeof latest?.title === "string" ? latest.title : (item.metric_id || "Review the saved evidence.");
}

function insightArea(item) {
  const metric = item?.latest?.metric_id || item?.metric_id || "";
  if (metric.startsWith("operation_flow_")) return "flow";
  if (metric === "dispatch_pattern" || metric.startsWith("extreme_event_")
      || metric.startsWith("transfer_discrepancy_") || metric.startsWith("dispatch_")) return "exceptions";
  if (OPERATION_METRICS.has(metric)) return "baseline";
  return "other";
}

function formatMeasured(value, unit) {
  if (value == null) return "—";
  if (unit === "percent") return `${Number(value).toFixed(2)}%`;
  if (unit === "minutes") return formatDuration(value);
  return formatNumber(value);
}

function CaseRow({ item, visual = false }) {
  const latest = item.latest || {};
  const impactDetail = latest.impact_summary?.detail;
  const impact = latest.impact_summary || {};
  const evidence = latest.evidence_summary;
  const confirmation = latest.confirm || {};
  const metric = latest.metric_id || item.metric_id || "";
  const contributors = [latest.top_contributor, latest.secondary_contributor]
    .filter((contributor) => contributor?.name);
  const mainValue = impact.units_affected ?? impact.count ?? confirmation.current;
  const mainLabel = impact.units_affected != null ? "units affected"
    : impact.kind === "orders_recovered" ? "orders recovered"
      : metric === "dispatch_pattern" ? "pairs flagged"
        : metric.startsWith("extreme_event_") ? "extreme lines"
          : metric.startsWith("transfer_discrepancy_") ? "receipt lines"
            : metric.startsWith("dispatch_") ? "outbound lines"
              : impact.count != null ? "records" : confirmation.unit || "measured";
  const formattedMainValue = impact.units_affected != null || impact.count != null
    ? formatNumber(mainValue)
    : formatMeasured(mainValue, confirmation.unit);
  return (
    <article className="overflow-hidden rounded-xl border border-border bg-surface-sunken">
      <div className={visual ? "grid gap-4 p-4 sm:grid-cols-[9rem_1fr]" : "p-4"}>
        {visual && <div className="flex flex-col justify-center border-b border-border pb-3 sm:border-b-0 sm:border-r sm:pb-0 sm:pr-4">
          <span className="text-3xl font-semibold tabular-nums text-foreground-strong">{mainValue == null ? "—" : formattedMainValue}</span>
          <span className="mt-1 text-xs uppercase tracking-wide text-foreground/55">{mainLabel}</span>
          {impact.outlet_count != null && <span className="mt-2 text-xs text-foreground/65">{formatNumber(impact.outlet_count)} outlets</span>}
          {impact.financial_impact_rs != null && <span className="mt-1 text-xs font-medium text-foreground/75">{formatCurrency(impact.financial_impact_rs)} estimated</span>}
        </div>}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Link to={`/investigate/${item.case_id}`} className="font-semibold text-foreground-strong hover:text-accent hover:underline">{latest.title || item.title || item.metric_id}</Link>
            <span className="rounded-full border border-border px-2 py-0.5 text-xs text-foreground/70">{latest.priority || item.priority || "Review"}</span>
          </div>
          {typeof impactDetail === "string" && <p className="mt-2 line-clamp-2 text-sm text-foreground/70">{impactDetail}</p>}
          {confirmation.current != null && confirmation.baseline != null && <p className="mt-2 text-sm text-foreground/70">
            {formatMeasured(confirmation.current, confirmation.unit)} current vs {formatMeasured(confirmation.baseline, confirmation.unit)} usual
            {Number.isFinite(Number(confirmation.pct_change)) ? ` · ${Number(confirmation.pct_change) > 0 ? "+" : ""}${Number(confirmation.pct_change).toFixed(1)}%` : ""}
          </p>}
          {contributors.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{contributors.map((contributor) => <span key={`${contributor.dimension}-${contributor.name}`} className="rounded-full bg-background px-2 py-1 text-xs text-foreground/65">{contributor.name}</span>)}</div>}
          {confirmation.period && <p className="mt-2 text-xs text-foreground/50">{confirmation.period}</p>}
        </div>
      </div>
      {(evidence?.established || evidence?.unknown || (!impactDetail && !evidence?.established && !evidence?.unknown)) && <details className="border-t border-border px-4 py-2.5">
        <summary className="cursor-pointer text-xs font-medium text-foreground/65">Evidence and limitations</summary>
        <div className="space-y-2 pb-1 pt-3 text-sm leading-5 text-foreground/70">
          {evidence?.established && <p><span className="font-medium text-foreground-strong">Evidence: </span>{evidence.established}</p>}
          {evidence?.unknown && <p><span className="font-medium">Not established: </span>{evidence.unknown}</p>}
          {!impactDetail && !evidence?.established && !evidence?.unknown && <p>{caseSummary(latest, item)}</p>}
        </div>
      </details>}
    </article>
  );
}

function Metric({ label, value, hint }) {
  return <div className="rounded-xl border border-border bg-surface-sunken p-4">
    <div className="text-xs font-medium uppercase tracking-wide text-foreground/55">{label}</div>
    <div className="mt-1 text-2xl font-semibold text-foreground-strong">{value}</div>
    {hint && <div className="mt-1 text-xs text-foreground/55">{hint}</div>}
  </div>;
}

function Bar({ label, value, max, valueLabel }) {
  const width = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return <div className="grid grid-cols-[minmax(8rem,12rem)_1fr_auto] items-center gap-3 py-2">
    <span className="text-sm text-foreground/75">{label}</span>
    <div className="h-3 overflow-hidden rounded-full bg-background" role="img" aria-label={`${label}: ${valueLabel}`}>
      <div className="h-full rounded-full bg-accent" style={{ width: `${width}%` }} />
    </div>
    <span className="min-w-20 text-right text-sm font-semibold text-foreground-strong">{valueLabel}</span>
  </div>;
}

function CaseGroup({ title, note, items, emptyText }) {
  return <Card title={title} note={note}>
    {items.length ? <div className="grid gap-3 xl:grid-cols-2">{items.map((item) => <CaseRow key={item.case_id} item={item} visual />)}</div>
      : <p className="text-sm text-foreground/60">{emptyText}</p>}
  </Card>;
}

function BaselineChart({ items }) {
  const chartItems = items.filter((item) => {
    const c = item.latest?.confirm || {};
    return c.current != null && c.baseline != null && Number(c.baseline) > 0;
  });
  if (!chartItems.length) return null;
  const ratios = chartItems.map((item) => Number(item.latest.confirm.current) / Number(item.latest.confirm.baseline));
  const scaleMax = Math.max(2, Math.min(4, Math.ceil(Math.max(...ratios))));
  const baselinePct = 100 / scaleMax;
  return <Card title="Movement against each finding’s usual level" note="Index chart · baseline = 1×">
    <div className="mb-3 flex items-center justify-between text-[11px] text-foreground/50"><span>0×</span><span>1× usual</span><span>{scaleMax}× usual</span></div>
    <div className="space-y-4">
      {chartItems.map((item) => {
        const latest = item.latest;
        const confirm = latest.confirm;
        const ratio = Number(confirm.current) / Number(confirm.baseline);
        const width = Math.min(100, Math.max(0, ratio / scaleMax * 100));
        const favorable = latest.category === "opportunity";
        const barColor = favorable ? "bg-good" : "bg-critical";
        return <div key={item.case_id} className="grid gap-2 sm:grid-cols-[minmax(12rem,18rem)_1fr_6rem] sm:items-center">
          <div className="min-w-0"><Link to={`/investigate/${item.case_id}`} className="block truncate text-sm font-medium text-foreground-strong hover:text-accent hover:underline">{latest.title}</Link><span className="text-xs text-foreground/50">{formatMeasured(confirm.current, confirm.unit)} now · {formatMeasured(confirm.baseline, confirm.unit)} usual</span></div>
          <div className="relative h-4 rounded-full bg-background" role="img" aria-label={`${latest.title}: ${ratio.toFixed(2)} times its usual level`}>
            <span className="absolute inset-y-0 z-10 border-l border-dashed border-foreground/65" style={{ left: `${baselinePct}%` }} />
            <span className={`absolute inset-y-0 left-0 rounded-full ${barColor}`} style={{ width: `${width}%` }} />
          </div>
          <span className={`text-right text-sm font-semibold tabular-nums ${favorable ? "text-good" : "text-critical"}`}>{ratio.toFixed(2)}× usual</span>
        </div>;
      })}
    </div>
    <p className="mt-3 text-xs text-foreground/50">Bar length compares each metric with its own baseline. Color shows the detector’s classification, not a universal good/bad direction.</p>
  </Card>;
}

function AreaLink({ href, title, description, count }) {
  return <a href={href} className="rounded-xl border border-border bg-surface-sunken p-4 transition hover:border-accent-border">
    <div className="flex items-center justify-between gap-2"><h3 className="font-semibold text-foreground-strong">{title}</h3><span className="rounded-full bg-background px-2 py-0.5 text-xs text-foreground/70">{count}</span></div>
    <p className="mt-2 text-sm text-foreground/60">{description}</p>
  </a>;
}

export default function Operations() {
  const summary = useQuery({ queryKey: ["operationsSummary"], queryFn: fetchOperationsSummary });
  // null = let the server default to the latest available data's month,
  // not wall-clock "today" (the reporting mirror can lag the real
  // calendar by days -- confirmed live, 2026-10-31).
  const [pickerMonth, setPickerMonth] = useState(null);
  const pickers = useQuery({
    queryKey: ["operationsPickers", pickerMonth],
    queryFn: () => fetchOperationsPickers(pickerMonth),
  });
  const freshWeight = useQuery({ queryKey: ["freshProductionAverageWeight"], queryFn: fetchFreshProductionAverageWeight });
  const cases = useQuery({
    queryKey: ["operationsCases"],
    queryFn: async () => {
      const [delivery, orders, inventory] = await Promise.all([
        fetchCases({ status: "active", domain: "delivery", limit: 100 }),
        fetchCases({ status: "active", domain: "order_customer", limit: 100 }),
        fetchCases({ status: "active", domain: "inventory", limit: 100 }),
      ]);
      const all = [...(delivery.items || []), ...(orders.items || []), ...(inventory.items || [])];
      return [...new Map(all.filter(isOperationsCase).map((item) => [item.case_id, item])).values()];
    },
  });

  const data = summary.data;
  const rows = cases.data || [];
  const adverse = rows.filter((item) => (item.latest?.category || item.category) !== "opportunity");
  const improvements = rows.filter((item) => (item.latest?.category || item.category) === "opportunity");
  const baselineFindings = adverse.filter((item) => insightArea(item) === "baseline");
  const allBaselineFindings = rows.filter((item) => insightArea(item) === "baseline");
  const exceptionFindings = adverse.filter((item) => insightArea(item) === "exceptions");
  const flowFindings = adverse.filter((item) => insightArea(item) === "flow");
  const month = data?.detail_window_label || "latest available period";
  const stages = data?.funnel_stages || [];
  const times = data?.funnel_wait_times || [];
  const maxStage = Math.max(1, ...stages.map((stage) => stage.count || 0));
  const maxTime = Math.max(1, ...times.map((stage) => stage.minutes || 0));

  return <div className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6">
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Celeste business analytics</p>
        <h1 className="mt-1 text-2xl font-semibold text-foreground-strong">Operational insights</h1>
        <p className="mt-1 text-sm text-foreground/60">Unusual changes, recurring exceptions, process bottlenecks, and follow-up outcomes.</p>
      </div>
      <span className="rounded-full border border-border px-3 py-1 text-sm text-foreground/70">Summary: {month}</span>
    </header>

    {!cases.isLoading && !cases.isError && <section aria-label="Insight counts" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="Active issues" value={formatNumber(adverse.length)} hint="Require review" />
      <Metric label="Positive signals" value={formatNumber(improvements.length)} hint="Improvements to sustain" />
      <Metric label="Repeat / extreme" value={formatNumber(exceptionFindings.length)} hint="Pattern and outlier findings" />
      <Metric label="Process / data" value={formatNumber(flowFindings.length)} hint="Flow and recording checks" />
    </section>}

    {!cases.isLoading && !cases.isError && <BaselineChart items={allBaselineFindings} />}

    <Card title="Analytics areas" note="Detector findings, not just report totals">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <AreaLink href="#baseline-insights" title="Changes from normal" description="Unexpected movement against a measured baseline." count={baselineFindings.length} />
        <AreaLink href="#exception-insights" title="Recurring & extreme exceptions" description="Repeated patterns and individual outliers." count={exceptionFindings.length} />
        <AreaLink href="#flow-insights" title="Process bottlenecks & data quality" description="Slow transitions and incomplete process records." count={flowFindings.length} />
        <AreaLink href="#follow-up" title="Management follow-up" description="Track whether recorded actions meet their outcome targets." count={<span aria-label="Open outcomes">↗</span>} />
      </div>
    </Card>

    {cases.isError && <Card title="Operational findings unavailable"><p className="text-sm text-foreground/70">{cases.error?.message || "Cases could not be loaded."}</p><button className="mt-3 rounded-lg border border-border px-3 py-2 text-sm" onClick={() => cases.refetch()}>Retry</button></Card>}
    {cases.isLoading && <Card title="Operational findings"><p className="text-sm text-foreground/60">Loading active findings…</p></Card>}
    {!cases.isLoading && !cases.isError && <>
      <div id="baseline-insights"><CaseGroup title="Changes from normal" note="Measured movement against each metric’s baseline" items={baselineFindings} emptyText="No active adverse baseline findings are recorded." /></div>
      <div id="exception-insights"><CaseGroup title="Recurring patterns & extreme exceptions" note="Repeated or unusually large operational records" items={exceptionFindings} emptyText="No active recurring or extreme operational findings are recorded." /></div>
      <div id="flow-insights"><CaseGroup title="Process bottlenecks & data quality" note="Fulfilment stage delays and record completeness" items={flowFindings} emptyText="No active flow bottleneck or stage data-quality cases are recorded." /></div>
      <CaseGroup title="Positive changes" note="Improvements Celeste detected; review for sustainability" items={improvements} emptyText="No active operational improvements are recorded." />
    </>}

    <Card title="Picker order volume & pace" note={pickers.data?.window_label ? `Per picker · ${pickers.data.window_label}` : undefined}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-foreground/60">Order count is a direct record. Avg sec/item is an estimate, not a measurement — this data has no per-item scan time, so it's each picker's total picking time divided by their total items picked, pooled across all their orders that month (never shown for a single order, which is too noisy to trust). It does not account for shift length, hours worked, order size, or difficulty, and is not a performance judgment.</p>
        <label className="flex shrink-0 items-center gap-2 text-xs text-foreground/60">
          Month
          <input
            type="month"
            className="rounded-lg border border-border bg-background px-2 py-1 text-sm text-foreground-strong"
            value={pickerMonth ?? pickers.data?.month ?? ""}
            onChange={(e) => setPickerMonth(e.target.value || null)}
          />
        </label>
      </div>
      {pickers.isLoading && <p className="text-sm text-foreground/60">Loading picker data…</p>}
      {pickers.isError && <p className="text-sm text-critical">Could not load picker data.</p>}
      {!pickers.isLoading && !pickers.isError && !pickers.data?.pickers_by_outlet?.length && (
        <p className="text-sm text-foreground/60">No picker data recorded for this month.</p>
      )}
      {pickers.data?.pickers_by_outlet?.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {pickers.data.pickers_by_outlet.map((group) => <div key={group.outlet}>
            <h3 className="mb-2 text-sm font-semibold text-foreground-strong">{group.outlet}</h3>
            <table className="w-full text-left text-sm">
              <thead><tr className="text-xs text-foreground/50"><th className="pb-1 font-normal">Picker</th><th className="pb-1 pr-2 text-right font-normal">Orders</th><th className="pb-1 pr-2 text-right font-normal">Avg sec/item</th><th className="pb-1 text-right font-normal">Performance</th></tr></thead>
              <tbody>{group.pickers.map((picker) => <tr key={picker.name} className="border-t border-border/70 first:border-0">
                <td className="py-1.5 pr-2 text-foreground/80">{picker.name}</td>
                <td className="py-1.5 pr-2 text-right font-medium tabular-nums text-foreground-strong">{formatNumber(picker.orders)}</td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-foreground/70">{picker.avg_seconds_per_item == null ? "—" : picker.avg_seconds_per_item.toFixed(1)}</td>
                <td className="py-1.5 text-right tabular-nums text-foreground-strong">
                  {picker.combined_score_pct == null ? "—" : `${picker.combined_score_pct.toFixed(0)}%`}
                  {picker.combined_score_basis === "volume_only" && <span className="ml-1 text-foreground/40">(volume only)</span>}
                </td>
              </tr>)}</tbody>
            </table>
          </div>)}
        </div>
      )}
      {pickers.data?.pickers_by_outlet?.length > 0 && (
        <p className="mt-3 text-xs text-foreground/50">
          Performance is relative, not absolute: each picker's orders and pace are scored against the best picker in
          their own outlet that month (100% = that outlet's top performer, not a company target). It does not
          account for shift length, hours worked, order difficulty, or order size — a lower score is not proof of
          lower effort. "(volume only)" means this picker doesn't yet have enough orders for a reliable pace score,
          so their number reflects order volume alone.
        </p>
      )}
    </Card>

    <Card title="Fresh production — pack weight compliance" note={freshWeight.data?.window_days ? `Last ${freshWeight.data.window_days} days` : undefined}>
      <p className="mb-3 text-sm text-foreground/60">
        From the production team's scale/barcode scan log — the real weight scanned into each pack. "Band"
        is the promised range parsed from the product's own name (e.g. "200G–250G") — there's no stored target
        field anywhere, so this is an estimate, not an authoritative spec. A product whose name states no range
        (just "250G", or no weight at all) shows "—" rather than a guessed band.
      </p>
      {freshWeight.isLoading && <p className="text-sm text-foreground/60">Loading…</p>}
      {freshWeight.isError && <p className="text-sm text-critical">Could not load average weight data.</p>}
      {freshWeight.data?.products?.length > 0 && (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-background text-xs text-foreground/50">
              <tr>
                <th className="pb-1 pr-2 font-normal">Product</th>
                <th className="pb-1 pr-2 text-right font-normal">Scans</th>
                <th className="pb-1 pr-2 text-right font-normal">Band (g)</th>
                <th className="pb-1 pr-2 text-right font-normal">Avg (g)</th>
                <th className="pb-1 pr-2 text-right font-normal">Correct</th>
                <th className="pb-1 pr-2 text-right font-normal">Below</th>
                <th className="pb-1 text-right font-normal">Above</th>
              </tr>
            </thead>
            <tbody>
              {freshWeight.data.products.map((p) => (
                <tr key={p.sku ?? p.product} className="border-t border-border/70 first:border-0">
                  <td className="py-1.5 pr-2 text-foreground/80">{p.product}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-foreground-strong">{formatNumber(p.scan_count)}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-foreground/60">
                    {p.band_g ? `${p.band_g[0]}–${p.band_g[1]}` : "—"}
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular-nums font-medium text-foreground-strong">{p.avg_weight_g}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-good">{p.correct_pct == null ? "—" : `${p.correct_pct}%`}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-critical">{p.below_pct == null ? "—" : `${p.below_pct}%`}</td>
                  <td className="py-1.5 text-right tabular-nums text-warning">{p.above_pct == null ? "—" : `${p.above_pct}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {freshWeight.data?.products?.length === 0 && (
        <p className="text-sm text-foreground/60">No scanned weight data recorded in this window.</p>
      )}
      {freshWeight.data?.products?.length > 0 && (
        <p className="mt-3 text-xs text-foreground/50">
          "Below" and "above" are both real deviations from the promised range, not just "below is bad" — packing
          consistently above the stated range is giving away product at cost, not a quality win.
        </p>
      )}
    </Card>

    <Card title="Cross-area connections" note="Inventory · orders · fulfilment">
      <p className="text-sm leading-6 text-foreground/70">Cross-area matching is not active yet. We’ll only link findings when source records align by product, outlet, and time window; sharing an outlet alone is not enough to infer a cause.</p>
    </Card>

    <Card title="Management follow-up" note="Outcome measurement">
      <p className="text-sm text-foreground/70">Recorded actions and their measured outcomes are available in the Outcomes workspace.</p>
      <Link id="follow-up" to="/outcomes" className="mt-3 inline-flex rounded-lg border border-accent-border bg-accent-soft px-3 py-2 text-sm font-medium text-accent hover:underline">Review action outcomes</Link>
    </Card>

    {summary.isLoading && <Card title="Supporting Odoo-style metrics"><p className="text-sm text-foreground/60">Loading the operations reference summary…</p></Card>}
    {summary.isError && <Card title="Supporting metrics unavailable"><p className="text-sm text-foreground/70">{summary.error?.message || "The operations summary could not be loaded."}</p><button className="mt-3 rounded-lg border border-border px-3 py-2 text-sm" onClick={() => summary.refetch()}>Retry</button></Card>}

    {data && <details className="mt-4 rounded-2xl border border-border bg-chart-surface p-4">
      <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Supporting Odoo-style metrics · {month}</summary>
      <div className="mt-3">
      <section aria-label="Operations snapshot" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Orders" value={formatNumber(data.detail_orders)} hint={month} />
        <Metric label="Cancellation rate" value={formatPercent(data.detail_cancellation_rate_pct)} hint="All recorded orders in the summary period" />
        <Metric label="Delayed delivery rate" value={formatPercent(data.detail_delayed_rate_pct)} hint={`${formatNumber(data.detail_delayed_count)} delayed deliveries`} />
        <Metric label="Median fulfilment time" value={formatDuration(data.median_fulfillment_minutes)} hint="Order to fulfilment; median" />
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Recorded order flow" note={month}>
          <p className="mb-2 text-sm text-foreground/60">Orders with each stage timestamp. Counts are not a step-by-step conversion funnel because stages may be missing in source records.</p>
          {stages.length ? stages.map((stage) => <Bar key={stage.name} label={stage.name} value={stage.count} max={maxStage} valueLabel={`${formatNumber(stage.count)} · ${stage.pct}%`} />)
            : <p className="text-sm text-foreground/60">No stage counts are available for this period.</p>}
        </Card>
        <Card title="Time within fulfilment stages" note="Median minutes">
          <p className="mb-2 text-sm text-foreground/60">Median elapsed time recorded for orders with the relevant timestamps.</p>
          {times.length ? times.map((stage) => <Bar key={stage.name} label={stage.name} value={stage.minutes} max={maxTime} valueLabel={formatDuration(stage.minutes)} />)
            : <p className="text-sm text-foreground/60">No stage timing data is available for this period.</p>}
        </Card>
      </div>

      <Card title="Outlet operations comparison" note={month}>
        {data.branches?.length ? <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm">
          <thead><tr className="border-b border-border text-xs text-foreground/55">{["Outlet", "Orders", "Cancellation", "Delayed delivery", "Median fulfilment"].map((label) => <th key={label} className="px-3 py-3 font-medium">{label}</th>)}</tr></thead>
          <tbody>{data.branches.map((branch) => <tr key={branch.name} className="border-b border-border/70 last:border-0">
            <td className="px-3 py-3 font-medium text-foreground-strong">{branch.name}</td><td className="px-3 py-3">{formatNumber(branch.orders)}</td><td className="px-3 py-3">{formatPercent(branch.cancellation_rate_pct)}</td><td className="px-3 py-3">{formatPercent(branch.delayed_rate_pct)}</td><td className="px-3 py-3">{formatDuration(branch.median_minutes)}</td>
          </tr>)}</tbody>
        </table></div> : <p className="text-sm text-foreground/60">No outlet breakdown is available for this period.</p>}
      </Card>
      </div>
    </details>}

    <details className="mt-4 rounded-xl border border-border bg-chart-surface px-4 py-3">
      <summary className="cursor-pointer text-sm font-medium text-foreground/70">Data limits</summary>
      <p className="mt-3 text-sm leading-6 text-foreground/65">Current operation records do not identify picker/packer headcount, worker assignments, or attendance, so Celeste can flag stage delays but cannot attribute them to staffing. Stage measures depend on recorded timestamps, and summary data may lag the current day.</p>
    </details>

    <div className="mt-4 text-sm text-foreground/60">Need the complete case timeline and source evidence? <Link to="/investigate" className="font-medium text-accent hover:underline">Open Investigate</Link></div>
  </div>;
}
