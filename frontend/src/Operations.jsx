import { useEffect, useState } from "react";
import "./shared.css";
import "./Operations.css";
import { fetchOperationsSummary } from "./api";
import StatTile from "./charts/StatTile";
import LineChart from "./charts/LineChart";
import HBarList from "./charts/HBarList";
import AIInsightPanel from "./AIInsightPanel";
import ActionCenter from "./ActionCenter";
import { formatCurrency, formatDuration, formatNumber, formatPercent, monthLabel } from "./format";

const OPERATIONS_METRIC_IDS = ["cancellation_rate", "delayed_delivery_rate"];

export default function Operations({ investigations: allInvestigations, onOpenInvestigation }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchOperationsSummary()
      .then((opsRes) => {
        if (cancelled) return;
        if (opsRes.status === "error") {
          setError(opsRes.detail || "Could not load operations data.");
          return;
        }
        setData(opsRes);
      })
      .catch(() => {
        if (!cancelled) setError("Could not reach the backend. Is it running on port 8000?");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <div className="page-state">Loading operations&hellip; running real analysis, give it a moment.</div>;
  }

  if (error) {
    return <div className="page-state page-state-error">{error}</div>;
  }

  if (!data) return null;

  const investigations = (allInvestigations || []).filter((i) => OPERATIONS_METRIC_IDS.includes(i.metric_id));
  const topInvestigation = investigations[0] ?? null;

  return (
    <div className="operations">
      <section className="briefing-header">
        <div><div className="section-kicker">Operations briefing</div><h2>Fulfillment health and immediate priorities</h2></div>
        <span className="briefing-date">Data through {data.headline_month ? monthLabel(data.headline_month) : "n/a"}</span>
      </section>
      <p className="operations-subtitle">
        Fulfillment &amp; delivery timing &middot; data through{" "}
        {data.headline_month ? monthLabel(data.headline_month) : "n/a"}
      </p>

      <section className="attention-section">
        <div className="section-heading-row"><div><div className="section-kicker">Needs attention</div><h2>{topInvestigation ? "Most important operations finding" : "Operations are within their usual range"}</h2></div></div>
        <AIInsightPanel investigation={topInvestigation} onOpenInvestigation={onOpenInvestigation} />
      </section>

      <div className="kpi-row">
        <StatTile
          label={`Revenue (${data.headline_month ? monthLabel(data.headline_month) : "last month"})`}
          value={formatCurrency(data.headline_revenue)}
          delta={data.revenue_growth_pct}
          note="Company-wide, same figure as Overview"
        />
        <StatTile
          label={`Orders (${data.headline_month ? monthLabel(data.headline_month) : "last month"})`}
          value={formatNumber(data.headline_orders)}
          delta={data.orders_growth_pct}
        />
        <StatTile
          label="Cancellation rate"
          value={formatPercent(data.headline_cancellation_rate_pct)}
          delta={data.cancellation_rate_growth_pct}
          deltaGoodWhenPositive={false}
          status={data.headline_cancellation_rate_pct > 10 ? "critical" : undefined}
        />
        <StatTile
          label={`Severely delayed rate (${data.headline_month ? monthLabel(data.headline_month) : "last month"})`}
          value={formatPercent(data.headline_delayed_rate_pct)}
          delta={data.delayed_rate_growth_pct}
          deltaGoodWhenPositive={false}
          note="Deliveries done more than 24h after their scheduled time"
        />
        <StatTile
          label="Delayed orders"
          value={formatNumber(data.headline_delayed_count)}
          status={data.headline_delayed_count > 0 ? "warning" : undefined}
          note={`of ${formatNumber(data.headline_total)} total deliveries`}
        />
        <StatTile
          label="Median fulfillment time"
          value={formatDuration(data.median_fulfillment_minutes)}
          note="Typical time from scheduled to done &mdash; most orders ship fast"
        />
      </div>

      <details className="evidence-disclosure">
        <summary>Explore delivery trends and branch evidence</summary>
        <div className="evidence-content"><div className="operations-hero-row">
        <div className="chart-card operations-hero-chart">
          <div className="chart-card-title">Severely delayed delivery rate &mdash; dashed = month in progress</div>
          <LineChart
            data={data.months}
            valueKey="delayed_rate_pct"
            formatValue={formatPercent}
            ariaLabel="Monthly severely delayed delivery rate"
          />
        </div>
      </div>

      <div className="chart-card operations-branches-card">
        <div className="chart-card-title">
          Branch scorecard ({data.headline_month ? monthLabel(data.headline_month) : "last month"})
        </div>
        {data.branches.length === 0 ? (
          <div className="chart-empty">No data</div>
        ) : (
          <table className="anomalies-table">
            <thead>
              <tr>
                <th>Branch</th>
                <th>Revenue</th>
                <th>Orders</th>
                <th>Cancellation rate</th>
                <th>Delayed rate</th>
                <th>Median time</th>
              </tr>
            </thead>
            <tbody>
              {data.branches.map((b) => (
                <tr key={b.name}>
                  <td>{b.name}</td>
                  <td>{formatCurrency(b.revenue)}</td>
                  <td>{formatNumber(b.orders)}</td>
                  <td className="anomalies-table-change">{formatPercent(b.cancellation_rate_pct)}</td>
                  <td className="anomalies-table-change">{formatPercent(b.delayed_rate_pct)}</td>
                  <td>{formatDuration(b.median_minutes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="chart-card">
        <div className="chart-card-title">Fulfillment funnel &mdash; where time goes after packing</div>
        {data.funnel_stages.length === 0 ? (
          <div className="chart-empty">No data</div>
        ) : (
          <>
            <p className="operations-subtitle">
              {formatNumber(data.headline_orders)} orders confirmed in{" "}
              {data.headline_month ? monthLabel(data.headline_month) : "last month"} &middot;{" "}
              {data.funnel_stages[data.funnel_stages.length - 1].pct}% reached dispatch
            </p>
            <HBarList
              data={data.funnel_wait_times}
              valueKey="minutes"
              formatValue={formatDuration}
              ariaLabel="Median time spent in each fulfillment stage"
            />
          </>
        )}
      </div>
        </div>
      </details>

      <ActionCenter investigations={investigations} onOpenInvestigation={onOpenInvestigation} />
    </div>
  );
}
