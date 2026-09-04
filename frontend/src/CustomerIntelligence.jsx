import StatTile from "./charts/StatTile";
import LineChart from "./charts/LineChart";
import { formatCurrency, formatNumber, formatPercent } from "./format";

const SEGMENT_STATUS = {
  at_risk: "warning",
  churned: "critical",
};

function AtRiskTable({ customers }) {
  if (!customers || customers.length === 0) {
    return <div className="chart-empty">No at-risk customers right now.</div>;
  }

  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Customer</th>
          <th>Revenue</th>
          <th>Days idle</th>
        </tr>
      </thead>
      <tbody>
        {customers.map((c) => (
          <tr key={c.name}>
            <td>{c.name}</td>
            <td className="anomalies-table-change">{formatCurrency(c.revenue)}</td>
            <td className="anomalies-table-change">{c.days_since_last_order}d</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function CustomerIntelligence({ data }) {
  if (!data) return null;

  const { repeat_purchase_rate_pct, segments, at_risk_customers, retention_trend } = data;
  const segmentById = Object.fromEntries((segments || []).map((s) => [s.id, s]));

  return (
    <div className="customer-intel-section">
      <div className="overview-full-feed-title customer-intel-title">
        Customer Intelligence
        <span className="ai-impact-summary">{repeat_purchase_rate_pct}% repeat purchase rate</span>
      </div>

      <div className="kpi-row">
        {["active", "at_risk", "churned", "one_time"].map((id) => {
          const seg = segmentById[id];
          if (!seg) return null;
          return (
            <StatTile
              key={id}
              label={seg.label}
              value={formatNumber(seg.count)}
              status={SEGMENT_STATUS[id]}
              note={`${formatCurrency(seg.revenue)} revenue`}
            />
          );
        })}
      </div>

      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Monthly retention &mdash; dashed = month in progress</div>
          <LineChart data={retention_trend} valueKey="retention_pct" formatValue={formatPercent} ariaLabel="Monthly customer retention rate" />
        </div>
        <div className="chart-card customer-intel-at-risk">
          <div className="chart-card-title">At-risk customers &mdash; by revenue</div>
          <AtRiskTable customers={at_risk_customers} />
        </div>
      </div>
    </div>
  );
}
