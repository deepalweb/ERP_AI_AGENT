import StatTile from "../charts/StatTile";
import HBarList from "../charts/HBarList";
import { formatCurrency, formatDuration, formatNumber, formatPercent, monthLabel } from "../format";

export default function OperationsSummaryCard({ result }) {
  if (!result || result.error) return null;

  const { headline_month, branches, funnel_wait_times } = result;
  const monthText = headline_month ? monthLabel(headline_month) : "last month";

  return (
    <div className="tool-card operations-summary-card">
      <div className="tool-card-title">Operations &mdash; {monthText}</div>

      <div className="kpi-row">
        <StatTile
          label="Revenue"
          value={formatCurrency(result.headline_revenue)}
          delta={result.revenue_growth_pct}
        />
        <StatTile label="Orders" value={formatNumber(result.headline_orders)} delta={result.orders_growth_pct} />
        <StatTile
          label="Cancellation rate"
          value={formatPercent(result.headline_cancellation_rate_pct)}
          delta={result.cancellation_rate_growth_pct}
          deltaGoodWhenPositive={false}
        />
        <StatTile
          label="Delayed rate"
          value={formatPercent(result.headline_delayed_rate_pct)}
          delta={result.delayed_rate_growth_pct}
          deltaGoodWhenPositive={false}
        />
      </div>

      {branches && branches.length > 0 && (
        <div className="chart-card">
          <div className="chart-card-title">Branch scorecard ({monthText})</div>
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
              {branches.map((b) => (
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
        </div>
      )}

      {funnel_wait_times && funnel_wait_times.length > 0 && (
        <div className="chart-card">
          <div className="chart-card-title">Fulfillment funnel &mdash; where time goes</div>
          <HBarList
            data={funnel_wait_times}
            valueKey="minutes"
            formatValue={formatDuration}
            ariaLabel="Median time spent in each fulfillment stage"
          />
        </div>
      )}
    </div>
  );
}
