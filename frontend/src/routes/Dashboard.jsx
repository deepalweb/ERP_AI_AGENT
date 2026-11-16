import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchDeliveryPartnerOrderForecast, fetchForwardLook, fetchOutletHourlyOrderForecast, fetchOutletRevenueForecast, fetchSkuDemandForecast, fetchTimesFmOutletForecast } from "../api";
import Section from "../design/Card";
import OrderForecastChart from "../charts/OrderForecastChart";
import { formatCurrency, formatNumber, monthLabel } from "../format";

function outletForecastSeries(outlet) {
  const rows = outlet.history_tail.dates.map((date, i) => ({ date, actual_orders: outlet.history_tail.values[i] }));
  if (rows.length) rows[rows.length - 1].predicted_orders = rows[rows.length - 1].actual_orders;
  outlet.forecast_dates.forEach((date, i) => rows.push({ date, predicted_orders: Math.round(outlet.point[i]) }));
  return rows;
}

// Naive sum of each outlet's own point/q10/q90 -- an indicative company-wide
// range, not a statistically joint interval (summing independent intervals
// overstates confidence if outlets' errors move together, which weekday
// effects suggest they do). Labeled "indicative" in the UI for that reason.
function totalAcrossOutlets(outlets) {
  const days = outlets[0]?.forecast_dates.length || 0;
  let point = 0, q10 = 0, q90 = 0;
  for (const outlet of outlets) {
    for (let i = 0; i < days; i++) {
      point += outlet.point[i] || 0;
      q10 += outlet.q10[i] || 0;
      q90 += outlet.q90[i] || 0;
    }
  }
  return { point: Math.round(point), q10: Math.round(q10), q90: Math.round(q90) };
}

/** A minimal inline trend line for the last few months behind a single
 * projected number -- "Rs 207.5M" alone doesn't say whether that's a climb,
 * a plateau, or a dip. Plain SVG, not Recharts: a handful of points in an
 * inline strip doesn't need axes, a tooltip, or a container. */
function Sparkline({ values }) {
  if (!values || values.length < 2) return null;
  const w = 72, h = 22, pad = 2;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * (w - 2 * pad) + pad},${h - pad - ((v - min) / range) * (h - 2 * pad)}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="inline-block align-middle" role="img" aria-label="Recent monthly trend">
      <polyline points={points} fill="none" stroke="var(--chart-ink-muted)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function ForwardLookRow({ label, projection, history, formatValue }) {
  if (!projection) return null;
  const { based_on_month, based_on_value, recent_growth_rates_pct, projected_low, projected_high } = projection;
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border py-3 first:border-t-0 first:pt-0">
      <span className="text-sm font-medium text-foreground-strong">{label}</span>
      <Sparkline values={history?.map((row) => row.value)} />
      <span className="text-sm tabular-nums">
        {formatValue(based_on_value)} ({monthLabel(based_on_month)}) &rarr; {formatValue(projected_low)}&ndash;{formatValue(projected_high)} next month
      </span>
      <span className="text-xs text-foreground/60">
        recent growth: {Math.min(...recent_growth_rates_pct)}%&ndash;{Math.max(...recent_growth_rates_pct)}%
      </span>
    </div>
  );
}

/** Shared per-outlet forecast layout for every TimesFM series on this page
 * (orders, revenue, delivery-partner workload) -- only the order-count
 * series has been through the formal validation/holdout evaluation
 * protocol (docs/outlet-forecast-evaluation.md), so `accuracy` is only
 * passed for that one; the others honestly say so instead of borrowing
 * its number. */
function MultiOutletForecastSection({
  title, totalLabel, queryResult, formatValue, actualLabel, predictedLabel,
  horizonLabel = "next 7 days", formatX = (d) => d?.slice(5), showTodayMarker = true,
}) {
  const data = queryResult.data?.status === "ok" ? queryResult.data : null;
  return (
    <Section title={title} note="TimesFM 2.5, zero-shot">
      {queryResult.isLoading && <p role="status" className="text-sm">Loading forecast…</p>}
      {data && <>
        {data.source.is_stale && <p role="alert" className="mb-4 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          Source data is {data.source.source_age_days} day{data.source.source_age_days === 1 ? "" : "s"} old (through {data.source.end_exclusive}) — this is a forecast made from that older data, not a live prediction for today. Treat it as historical, not current.
        </p>}
        {(() => {
          const total = totalAcrossOutlets(data.outlets);
          return (
            <p className="mb-4 text-sm">
              <span className="font-medium text-foreground-strong">All outlets, {horizonLabel}: ~{formatValue(total.point)} {totalLabel}</span>
              <span className="text-foreground/60"> (indicative range {formatValue(total.q10)}&ndash;{formatValue(total.q90)})</span>
            </p>
          );
        })()}
        <div className="grid gap-4 sm:grid-cols-2">
          {data.outlets.map((outlet) => <div key={outlet.store_id}>
            <h3 className="mb-1 text-sm font-medium text-foreground-strong">{outlet.outlet}</h3>
            <OrderForecastChart
              data={outletForecastSeries(outlet)}
              asOfDate={showTodayMarker && !data.source.is_stale ? data.source.end_exclusive : undefined}
              formatOrders={formatValue}
              formatX={formatX}
              actualLabel={actualLabel}
              predictedLabel={predictedLabel}
            />
            {outlet.accuracy && <p className="mt-1 text-xs text-foreground/50">
              Measured error at this outlet: {outlet.accuracy.wape_pct}% WAPE, {outlet.accuracy.bias_pct < 0 ? "tends to underpredict" : "tends to overpredict"} by ~{Math.abs(outlet.accuracy.bias_pct)}%
            </p>}
          </div>)}
        </div>
        <p className="mt-3 text-xs text-foreground/60">
          Model: {data.model.checkpoint} · Generated {data.generated_at?.replace("T", " ").slice(0, 16)} UTC · Dashed line is the forecast, shading not shown is the model's 10th-90th percentile range.
          {data.accuracy && <> Measured on a holdout evaluation ({data.accuracy.evaluated_at}): overall {data.accuracy.overall.wape_pct}% WAPE, {data.accuracy.overall.coverage_80_pct}% of actuals fell inside the 80% band.</>}
          {data.accuracy?.live_track_record && <> Live so far ({data.accuracy.live_track_record.earliest_date} to {data.accuracy.live_track_record.latest_date}, {data.accuracy.live_track_record.graded_predictions} graded predictions): {data.accuracy.live_track_record.wape_pct}% WAPE.</>}
          {!data.accuracy && <> Not yet run through the formal validation/holdout evaluation the order-count forecast has — treat this one as indicative until that's done.</>}
          {" "}Full breakdown: docs/outlet-forecast-evaluation.md.
        </p>
      </>}
      {!queryResult.isLoading && !data && <p className="text-sm text-foreground/60">No forecast has been generated yet.</p>}
    </Section>
  );
}

/** Company-wide (all outlets combined) per-product demand -- a table, not
 * per-outlet charts, since this covers thousands of products at once. Only
 * the genuinely dense, recently active products are forecast at all (see
 * scripts/export_sku_demand.py); a product missing here was deliberately
 * left out, not forecast as zero. */
function SkuDemandSection({ queryResult }) {
  const [search, setSearch] = useState("");
  const data = queryResult.data?.status === "ok" ? queryResult.data : null;
  const sorted = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    const filtered = q
      ? data.products.filter((p) => p.product.toLowerCase().includes(q) || p.sku?.toLowerCase().includes(q))
      : data.products;
    return [...filtered].sort((a, b) => b.next_7_day_total - a.next_7_day_total);
  }, [data, search]);

  return (
    <Section title="Product demand forecast — next 7 days" note={data ? `${data.products.length} products, company-wide` : "TimesFM 2.5, zero-shot"}>
      {queryResult.isLoading && <p role="status" className="text-sm">Loading forecast…</p>}
      {data && <>
        {data.source.is_stale && <p role="alert" className="mb-3 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          Source data is {data.source.source_age_days} day{data.source.source_age_days === 1 ? "" : "s"} old (through {data.source.end_exclusive}) — this is a forecast made from that older data, not a live prediction for today. Treat it as historical, not current.
        </p>}
        <input
          type="text"
          placeholder="Search product or SKU…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="mb-3 w-full max-w-sm rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground-strong placeholder:text-foreground/40"
        />
        <div className="max-h-[32rem] overflow-y-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-background text-xs text-foreground/50">
              <tr>
                <th className="pb-1 pr-2 font-normal">Product</th>
                <th className="pb-1 pr-2 font-normal">SKU</th>
                <th className="pb-1 pr-2 text-right font-normal">Recent avg/day</th>
                <th className="pb-1 pr-2 text-right font-normal">Next 7 days</th>
                <th className="pb-1 text-right font-normal">Indicative range</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => (
                <tr key={p.product_id} className="border-t border-border/70 first:border-0">
                  <td className="py-1.5 pr-2 text-foreground/80">{p.product}</td>
                  <td className="py-1.5 pr-2 font-mono text-xs text-foreground/50">{p.sku ?? "—"}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-foreground/60">{p.recent_avg_daily ?? "—"}</td>
                  <td className="py-1.5 pr-2 text-right tabular-nums font-medium text-foreground-strong">{Math.round(p.next_7_day_total)}</td>
                  <td className="py-1.5 text-right tabular-nums text-foreground/50">
                    {Math.round(p.q10.reduce((a, b) => a + b, 0))}&ndash;{Math.round(p.q90.reduce((a, b) => a + b, 0))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sorted.length === 0 && <p className="py-6 text-center text-sm text-foreground/50">No products match that search.</p>}
        </div>
        <p className="mt-3 text-xs text-foreground/60">
          Model: {data.model.checkpoint} · Generated {data.generated_at?.replace("T", " ").slice(0, 16)} UTC · Company-wide totals across all 4 outlets, not a per-outlet breakdown. Not yet run through the formal validation/holdout evaluation the order-count forecast has — treat as indicative. Products with thin or stale sales history are left out entirely, never shown with an invented forecast.
        </p>
      </>}
      {!queryResult.isLoading && !data && <p className="text-sm text-foreground/60">No SKU demand forecast has been generated yet.</p>}
    </Section>
  );
}

export default function Dashboard() {
  const forwardLook = useQuery({ queryKey: ["forwardLook"], queryFn: fetchForwardLook, retry: false });
  const forwardLookData = forwardLook.data?.status === "ok" ? forwardLook.data : null;
  const orderForecast = useQuery({ queryKey: ["timesFmOutletForecast"], queryFn: fetchTimesFmOutletForecast, retry: false });
  const revenueForecast = useQuery({ queryKey: ["outletRevenueForecast"], queryFn: fetchOutletRevenueForecast, retry: false });
  const deliveryForecast = useQuery({ queryKey: ["deliveryPartnerOrderForecast"], queryFn: fetchDeliveryPartnerOrderForecast, retry: false });
  const hourlyForecast = useQuery({ queryKey: ["outletHourlyOrderForecast"], queryFn: fetchOutletHourlyOrderForecast, retry: false });
  const skuDemandForecast = useQuery({ queryKey: ["skuDemandForecast"], queryFn: fetchSkuDemandForecast, retry: false });

  return <div className="px-6 py-6">
    <h1 className="text-xl font-semibold text-foreground-strong">Business Performance</h1>
    <p className="mt-1 text-sm text-foreground/60">Forward-looking views only: a simple trend projection, and per-outlet machine-learning forecasts.</p>

    <Section title="Forward Look — if the recent pace holds">
      {forwardLook.isLoading && <p role="status" className="text-sm">Loading…</p>}
      {!forwardLook.isLoading && !forwardLookData && <p role="alert" className="text-sm text-warning">Could not load the forward look.</p>}
      {forwardLookData && (forwardLookData.revenue || forwardLookData.orders) ? <>
        <ForwardLookRow label="Revenue" projection={forwardLookData.revenue} history={forwardLookData.revenue_history} formatValue={formatCurrency} />
        <ForwardLookRow label="Orders" projection={forwardLookData.orders} history={forwardLookData.orders_history} formatValue={formatNumber} />
        <p className="mt-3 text-xs text-warning">⚠ {forwardLookData.note}</p>
      </> : forwardLookData && <p className="text-sm text-foreground/60">Not enough stable month-over-month history yet for a projection.</p>}
    </Section>

    <MultiOutletForecastSection
      title="Order forecast — next 7 days per outlet"
      totalLabel="orders"
      queryResult={orderForecast}
      formatValue={formatNumber}
      actualLabel="Current order count"
      predictedLabel="Predicted order count"
    />

    <MultiOutletForecastSection
      title="Revenue forecast — next 7 days per outlet"
      totalLabel="revenue"
      queryResult={revenueForecast}
      formatValue={formatCurrency}
      actualLabel="Current revenue"
      predictedLabel="Predicted revenue"
    />

    <MultiOutletForecastSection
      title="Delivery-partner workload forecast — next 7 days per outlet"
      totalLabel="deliveries (Uber + PickMe)"
      queryResult={deliveryForecast}
      formatValue={formatNumber}
      actualLabel="Current delivery-partner orders"
      predictedLabel="Predicted delivery-partner orders"
    />

    <MultiOutletForecastSection
      title="Hourly order forecast — next 24 hours per outlet"
      totalLabel="orders"
      horizonLabel="next 24 hours"
      queryResult={hourlyForecast}
      formatValue={formatNumber}
      actualLabel="Current order count"
      predictedLabel="Predicted order count"
      formatX={(d) => d?.slice(11, 16)}
      showTodayMarker={false}
    />

    <SkuDemandSection queryResult={skuDemandForecast} />
  </div>;
}
