import { formatByUnit } from "../format";

const CATEGORY_META = {
  critical: { label: "Critical", cls: "health-text-critical" },
  opportunity: { label: "Opportunity", cls: "health-text-good" },
};

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
            <th>Top driver</th>
          </tr>
        </thead>
        <tbody>
          {result.flagged.map((f) => {
            const meta = CATEGORY_META[f.category] ?? { label: f.category, cls: "" };
            return (
              <tr key={f.metric_id}>
                <td>{f.label}</td>
                <td className={meta.cls}>{meta.label}</td>
                <td className="anomalies-table-change">{formatByUnit(f.latest_value, f.unit)}</td>
                <td className="anomalies-table-change">
                  {f.pct_change_vs_baseline === null ? "n/a" : `${f.pct_change_vs_baseline > 0 ? "+" : ""}${f.pct_change_vs_baseline}%`}
                </td>
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
