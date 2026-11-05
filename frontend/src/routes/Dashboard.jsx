import { useQuery } from "@tanstack/react-query";
import { fetchForwardLook, fetchTimesFmOutletForecast } from "../api";
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

export default function Dashboard() {
  const forwardLook = useQuery({ queryKey: ["forwardLook"], queryFn: fetchForwardLook, retry: false });
  const forwardLookData = forwardLook.data?.status === "ok" ? forwardLook.data : null;
  const forecast = useQuery({ queryKey: ["timesFmOutletForecast"], queryFn: fetchTimesFmOutletForecast, retry: false });
  const forecastData = forecast.data?.status === "ok" ? forecast.data : null;

  return <div className="px-6 py-6">
    <h1 className="text-xl font-semibold text-foreground-strong">Business Performance</h1>
    <p className="mt-1 text-sm text-foreground/60">Forward-looking views only: a simple trend projection, and a per-outlet machine-learning order forecast.</p>

    <Section title="Forward Look — if the recent pace holds">
      {forwardLook.isLoading && <p role="status" className="text-sm">Loading…</p>}
      {!forwardLook.isLoading && !forwardLookData && <p role="alert" className="text-sm text-warning">Could not load the forward look.</p>}
      {forwardLookData && (forwardLookData.revenue || forwardLookData.orders) ? <>
        <ForwardLookRow label="Revenue" projection={forwardLookData.revenue} history={forwardLookData.revenue_history} formatValue={formatCurrency} />
        <ForwardLookRow label="Orders" projection={forwardLookData.orders} history={forwardLookData.orders_history} formatValue={formatNumber} />
        <p className="mt-3 text-xs text-warning">⚠ {forwardLookData.note}</p>
      </> : forwardLookData && <p className="text-sm text-foreground/60">Not enough stable month-over-month history yet for a projection.</p>}
    </Section>

    <Section title="Order forecast — next 7 days per outlet" note="TimesFM 2.5, zero-shot">
      {forecast.isLoading && <p role="status" className="text-sm">Loading forecast…</p>}
      {forecastData && <>
        {forecastData.source.is_stale && <p role="alert" className="mb-4 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          Source data is {forecastData.source.source_age_days} day{forecastData.source.source_age_days === 1 ? "" : "s"} old (through {forecastData.source.end_exclusive}) — this is a forecast made from that older data, not a live prediction for today. Treat it as historical, not current.
        </p>}
        {(() => {
          const total = totalAcrossOutlets(forecastData.outlets);
          return (
            <p className="mb-4 text-sm">
              <span className="font-medium text-foreground-strong">All outlets, next 7 days: ~{formatNumber(total.point)} orders</span>
              <span className="text-foreground/60"> (indicative range {formatNumber(total.q10)}&ndash;{formatNumber(total.q90)})</span>
            </p>
          );
        })()}
        <div className="grid gap-4 sm:grid-cols-2">
          {forecastData.outlets.map((outlet) => <div key={outlet.store_id}>
            <h3 className="mb-1 text-sm font-medium text-foreground-strong">{outlet.outlet}</h3>
            <OrderForecastChart
              data={outletForecastSeries(outlet)}
              asOfDate={forecastData.source.is_stale ? undefined : forecastData.source.end_exclusive}
              formatOrders={formatNumber}
              formatX={(d) => d?.slice(5)}
            />
            {outlet.accuracy && <p className="mt-1 text-xs text-foreground/50">
              Measured error at this outlet: {outlet.accuracy.wape_pct}% WAPE, {outlet.accuracy.bias_pct < 0 ? "tends to underpredict" : "tends to overpredict"} by ~{Math.abs(outlet.accuracy.bias_pct)}%
            </p>}
          </div>)}
        </div>
        <p className="mt-3 text-xs text-foreground/60">
          Model: {forecastData.model.checkpoint} · Generated {forecastData.generated_at?.replace("T", " ").slice(0, 16)} UTC · Dashed line is the forecast, shading not shown is the model's 10th-90th percentile range.
          {forecastData.accuracy && <> Measured on a holdout evaluation ({forecastData.accuracy.evaluated_at}): overall {forecastData.accuracy.overall.wape_pct}% WAPE, {forecastData.accuracy.overall.coverage_80_pct}% of actuals fell inside the 80% band.</>}
          {forecastData.accuracy?.live_track_record && <> Live so far ({forecastData.accuracy.live_track_record.earliest_date} to {forecastData.accuracy.live_track_record.latest_date}, {forecastData.accuracy.live_track_record.graded_predictions} graded predictions): {forecastData.accuracy.live_track_record.wape_pct}% WAPE.</>}
          {" "}Full breakdown: docs/outlet-forecast-evaluation.md.
        </p>
      </>}
      {!forecast.isLoading && !forecastData && <p className="text-sm text-foreground/60">No outlet order forecast has been generated yet.</p>}
    </Section>
  </div>;
}
