import { useQuery } from "@tanstack/react-query";
import { fetchMetricInvestigation } from "../api";
import RankedBarChart from "../charts/RankedBarChart";
import TrendChart from "../charts/TrendChart";
import { formatCurrency, formatNumber, formatPct, monthLabel } from "../format";

function findByPeriod(trend, period) {
  return (trend ?? []).find((m) => m.month === period) ?? null;
}

/** Business-specific investigation panel for a revenue case (Celeste
 * Investigate Phase 3's flagship revenue workflow). Gated by the caller on
 * c.metric_id === "revenue" -- mirrors InventoryInvestigationPanel's
 * pattern (a per-metric-type panel, not a generic breakdown renderer).
 *
 * Reads structured fields already carried on the case (confirm/z_score/
 * breakdowns/trend -- see investigation.build_investigation and case_engine
 * .get_case_detail's trend exposure) plus two supplementary, already-
 * verified-consistent live fetches (order_volume, average_order_value --
 * same SALE_ORDER_LIVE_STATES/CELESTE_STORE_IDS scope as revenue itself,
 * confirmed during the Phase 3 correctness audit) via the SAME
 * fetchMetricInvestigation() Home.jsx's Suppliers panel already uses. No
 * new backend endpoint, no new financial definition, no SQL in this file. */
export default function RevenueInvestigationPanel({ latest, casePeriod }) {
  const orderVolumeQuery = useQuery({
    queryKey: ["metricInvestigation", "order_volume"],
    queryFn: () => fetchMetricInvestigation("order_volume"),
  });
  const aovQuery = useQuery({
    queryKey: ["metricInvestigation", "average_order_value"],
    queryFn: () => fetchMetricInvestigation("average_order_value"),
  });

  const trend = latest?.trend ?? [];
  const completeMonths = trend.filter((m) => !m.is_partial);
  const partialMonth = trend.find((m) => m.is_partial);
  // Same rule detect_anomaly() itself uses for its baseline_mean: every
  // complete month before the latest one -- derived here for transparency
  // only (which months were averaged), never to recompute current/
  // baseline/pct_change themselves, which stay exactly what the backend
  // already returned.
  const baselineMonths = completeMonths.slice(0, -1);

  const storeBreakdown = (latest?.breakdowns ?? []).find((b) => b.dimension === "store");
  const productBreakdown = (latest?.breakdowns ?? []).find((b) => b.dimension === "product");
  const customerBreakdown = (latest?.breakdowns ?? []).find((b) => b.dimension === "customer");

  const ovPoint = findByPeriod(orderVolumeQuery.data?.trend, casePeriod);
  const aovPoint = findByPeriod(aovQuery.data?.trend, casePeriod);
  const ovAnomaly = orderVolumeQuery.data?.anomaly?.latest_month === casePeriod ? orderVolumeQuery.data.anomaly : null;
  const aovAnomaly = aovQuery.data?.anomaly?.latest_month === casePeriod ? aovQuery.data.anomaly : null;
  const aovBarelyMoved = aovAnomaly?.pct_change_vs_baseline != null && Math.abs(aovAnomaly.pct_change_vs_baseline) < 15;

  return (
    <div>
      <p className="text-xs text-foreground-subtle">
        {baselineMonths.length > 0 ? (
          <>
            Baseline: mean of {baselineMonths.length} complete month{baselineMonths.length === 1 ? "" : "s"} (
            {monthLabel(baselineMonths[0]?.month)} to {monthLabel(baselineMonths[baselineMonths.length - 1]?.month)}).
          </>
        ) : (
          latest?.confirm?.baseline != null
            ? "The saved baseline is shown in Comparison and affected segments. Its underlying monthly history is not available here."
            : "No baseline or complete comparison history was saved with this result."
        )}
        {partialMonth && (
          <> {monthLabel(partialMonth.month)} is still in progress ({formatCurrency(partialMonth.value)} so far) and is
            excluded from every comparison on this page.</>
        )}
      </p>

      {completeMonths.length > 1 && (
        <div className="mt-3">
          <TrendChart
            data={completeMonths}
            formatValue={(v) => formatCurrency(v)}
            formatX={monthLabel}
            stroke="var(--accent)"
            gridStroke="var(--border)"
            axisStroke="var(--text-subtle)"
          />
        </div>
      )}

      {(ovPoint || aovPoint) && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
            What changed alongside revenue
          </span>
          <ul className="mt-1.5 space-y-1 text-sm text-foreground">
            {ovPoint && (
              <li>
                Order volume: {formatNumber(ovPoint.value)} orders
                {ovAnomaly?.pct_change_vs_baseline != null && ` (${formatPct(ovAnomaly.pct_change_vs_baseline)} vs its own baseline)`}
              </li>
            )}
            {aovPoint && (
              <li>
                Average order value: {formatCurrency(aovPoint.value)}
                {aovAnomaly?.pct_change_vs_baseline != null && ` (${formatPct(aovAnomaly.pct_change_vs_baseline)} vs its own baseline)`}
              </li>
            )}
          </ul>
          {ovPoint && aovPoint && ovAnomaly && aovAnomaly && (
            <p className="mt-1.5 text-xs text-foreground-subtle">
              {aovBarelyMoved
                // Phase 3 final validation, Check 4: average order value is a
                // ratio (revenue / orders), not a direct price measurement --
                // it can hold steady even while item-level prices, discounting,
                // or basket composition shift. Describing the two co-movements
                // (revenue up, order volume up, AOV flat) is what the evidence
                // actually supports; naming a driver ("volume-driven, not
                // price-driven") would claim a cause this data doesn't establish.
                ? "Revenue and order volume moved together over the same period, while average order value stayed relatively stable -- these are correlated movements, not a confirmed cause."
                : "Both order volume and average order value moved over the same period -- revenue's change reflects a mix of the two, not one alone."}
            </p>
          )}
        </div>
      )}

      {storeBreakdown?.top_contributors?.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
            Outlet contribution ({storeBreakdown.baseline_month ? monthLabel(storeBreakdown.baseline_month) : "n/a"} &rarr;{" "}
            {monthLabel(casePeriod)})
          </span>
          <p className="mt-1 text-xs text-foreground-subtle">
            Change in revenue per outlet between the two most recent months -- a different, smaller comparison than the
            headline's vs-baseline percentage above; see "What the evidence establishes" for how this was checked.
          </p>
          <div className="mt-2">
            <RankedBarChart data={storeBreakdown.top_contributors} valueKey="delta" formatValue={(v) => formatCurrency(v)} />
          </div>
        </div>
      )}

      {(productBreakdown?.top_contributors?.length > 0 || customerBreakdown?.top_contributors?.length > 0) && (
        <div className="mt-4 grid grid-cols-1 gap-4 border-t border-border pt-3 sm:grid-cols-2">
          {productBreakdown?.top_contributors?.length > 0 && (
            <div>
              <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
                Top product movers ({productBreakdown.baseline_month ? monthLabel(productBreakdown.baseline_month) : "n/a"} &rarr;{" "}
                {monthLabel(casePeriod)})
              </span>
              <ul className="mt-1.5 space-y-1 text-sm text-foreground-subtle">
                {productBreakdown.top_contributors.slice(0, 5).map((c) => (
                  <li key={c.name} className="flex items-center justify-between gap-2">
                    <span className="truncate">{c.name}</span>
                    <span className="flex-none tabular-nums text-foreground">{formatCurrency(c.delta)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {customerBreakdown?.top_contributors?.length > 0 && (
            <div>
              <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
                Top customer movers ({customerBreakdown.baseline_month ? monthLabel(customerBreakdown.baseline_month) : "n/a"} &rarr;{" "}
                {monthLabel(casePeriod)})
              </span>
              <ul className="mt-1.5 space-y-1 text-sm text-foreground-subtle">
                {customerBreakdown.top_contributors.slice(0, 5).map((c) => (
                  <li key={c.name} className="flex items-center justify-between gap-2">
                    <span className="truncate">{c.name}</span>
                    <span className="flex-none tabular-nums text-foreground">{formatCurrency(c.delta)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
