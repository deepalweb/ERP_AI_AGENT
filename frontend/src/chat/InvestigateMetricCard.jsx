import { CATEGORY_META } from "../InvestigationCard";
import LineChart from "../charts/LineChart";
import { formatByUnit, monthLabel } from "../format";

function BreakdownTable({ breakdown, unit }) {
  const rows = breakdown.top_contributors || [];
  if (rows.length === 0) {
    return <div className="chart-empty">No segments with enough volume to break this down.</div>;
  }

  const isComposition = breakdown.mode === "composition";

  return (
    <div className="chart-card">
      <div className="chart-card-title">By {breakdown.label}</div>
      <table className="anomalies-table">
        <thead>
          <tr>
            <th>{breakdown.label}</th>
            <th>Current</th>
            <th>Baseline</th>
            <th>Change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td>{r.name}</td>
              <td>
                {isComposition
                  ? `${r.current_share_pct}% share`
                  : formatByUnit(r.current, unit)}
              </td>
              <td>
                {isComposition
                  ? `${r.baseline_share_pct}% share`
                  : formatByUnit(r.baseline, unit)}
              </td>
              <td className="anomalies-table-change">
                {isComposition
                  ? `${r.delta > 0 ? "+" : ""}${r.delta}pp`
                  : `${r.delta > 0 ? "+" : ""}${formatByUnit(r.delta, unit)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function InvestigateMetricCard({ result }) {
  if (!result || result.error) return null;

  const { metric, trend, anomaly, breakdowns } = result;
  const meta =
    CATEGORY_META[anomaly.category] ?? {
      emoji: "ℹ️",
      label: anomaly.category === "normal" ? "Normal range" : anomaly.category,
    };

  return (
    <div className="tool-card investigate-metric-card">
      <div className="tool-card-title">
        {metric.label}
        {anomaly.latest_month && (
          <span className={`anomalies-table-badge anomalies-table-badge-${anomaly.category}`}>
            {meta.emoji} {meta.label} &middot; {monthLabel(anomaly.latest_month)}
          </span>
        )}
      </div>

      <div className="chart-card">
        <div className="chart-card-title">Monthly trend &mdash; dashed = month in progress</div>
        <LineChart
          data={trend}
          valueKey="value"
          formatValue={(v) => formatByUnit(v, metric.unit)}
          ariaLabel={`Monthly ${metric.label}`}
        />
      </div>

      {(breakdowns || [])
        .filter((b) => (b.top_contributors || []).length > 0)
        .map((b) => (
          <BreakdownTable key={b.dimension} breakdown={b} unit={metric.unit} />
        ))}
    </div>
  );
}
