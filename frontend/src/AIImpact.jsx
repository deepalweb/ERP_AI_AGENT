import { formatByUnit, formatPct, monthLabel } from "./format";

export const VERDICT_META = {
  improved: { emoji: "✅", label: "Improved", className: "impact-verdict-good" },
  worsened: { emoji: "⚠️", label: "Worsened", className: "impact-verdict-critical" },
  unchanged: { emoji: "➖", label: "Unchanged", className: "impact-verdict-neutral" },
};

function TrackedRow({ row }) {
  const verdict = row.status === "resolved" ? VERDICT_META[row.verdict] : null;

  return (
    <li className="impact-row">
      <div className="impact-row-main">
        <span className="impact-row-metric">{row.metric_label}</span>
        <span className="impact-row-period">{monthLabel(row.period)}</span>
      </div>

      {row.status === "resolved" ? (
        <div className="impact-row-detail">
          <span>
            {formatByUnit(row.baseline_value, row.unit)} &rarr; {formatByUnit(row.detected_value, row.unit)}{" "}
            &rarr; <strong>{formatByUnit(row.outcome_value, row.unit)}</strong>
          </span>
          <span className={`impact-verdict ${verdict.className}`}>
            {verdict.emoji} {verdict.label} ({formatPct(row.outcome_pct_change)} vs baseline, was {formatPct(row.detected_pct_change)})
          </span>
        </div>
      ) : (
        <div className="impact-row-detail impact-row-pending">
          🕒 Tracking &mdash; check back after {monthLabel(row.period)} closes
        </div>
      )}
    </li>
  );
}

export default function AIImpact({ tracking }) {
  if (!tracking || tracking.length === 0) return null;

  const resolvedCount = tracking.filter((r) => r.status === "resolved").length;
  const improvedCount = tracking.filter((r) => r.verdict === "improved").length;

  return (
    <div className="chart-card ai-impact-card">
      <div className="chart-card-title">
        AI Impact &mdash; did it work?
        {resolvedCount > 0 && (
          <span className="ai-impact-summary">
            {improvedCount}/{resolvedCount} resolved findings improved
          </span>
        )}
      </div>
      <ul className="impact-list">
        {tracking.map((row) => (
          <TrackedRow key={row.id} row={row} />
        ))}
      </ul>
    </div>
  );
}
