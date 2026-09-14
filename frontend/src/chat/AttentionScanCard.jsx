import { formatByUnit } from "../format";

const CATEGORY_META = {
  critical: { label: "Critical", cls: "health-text-critical", dot: "critical" },
  opportunity: { label: "Opportunity", cls: "health-text-good", dot: "good" },
};

function persistenceLabel(p) {
  if (!p || p.pattern === "insufficient_data" || p.pattern === "flat") return "—";
  if (p.pattern === "recovering") return "Recovering";
  if (p.pattern === "single_period_move") return "Isolated move";
  const noun = p.direction === "declining" ? "decline" : "improvement";
  return `${p.consecutive_periods}-month ${noun}`;
}

export default function AttentionScanCard({ result }) {
  if (!result || result.error) return null;

  if (!result.flagged || result.flagged.length === 0) {
    return (
      <div className="tool-card attention-scan-card">
        <div className="tool-card-title">Attention scan</div>
        <div className="chart-empty">
          Nothing crossed the anomaly threshold across {result.metrics_checked} metrics this month.
        </div>
      </div>
    );
  }

  return (
    <div className="tool-card attention-scan-card">
      <div className="tool-card-title">
        Attention scan &mdash; {result.flagged.length} of {result.metrics_checked} metrics flagged
      </div>
      <table className="anomalies-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Status</th>
            <th>Latest value</th>
            <th>vs baseline</th>
            <th>Pattern</th>
            <th>Top driver</th>
          </tr>
        </thead>
        <tbody>
          {result.flagged.map((f) => {
            const meta = CATEGORY_META[f.category] ?? { label: f.category, cls: "", dot: null };
            return (
              <tr key={f.metric_id}>
                <td>{f.label}</td>
                <td className={meta.cls}>
                  {meta.dot && <span className={`signal-tile-dot signal-tile-dot-${meta.dot} attention-scan-dot`} />}
                  {meta.label}
                </td>
                <td className="anomalies-table-change">{formatByUnit(f.latest_value, f.unit)}</td>
                <td className="anomalies-table-change">
                  {f.pct_change_vs_baseline === null ? "n/a" : `${f.pct_change_vs_baseline > 0 ? "+" : ""}${f.pct_change_vs_baseline}%`}
                </td>
                <td>{persistenceLabel(f.persistence)}</td>
                <td>
                  {f.top_driver ? `${f.top_driver.name} (${f.top_driver.dimension})` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
