import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { CATEGORY_META } from "../InvestigationCard";
import LineChart from "../charts/LineChart";
import DivergingBarList from "../charts/DivergingBarList";
import Button from "../design/Button";
import CardFooter from "./CardFooter";
import { formatByUnit, monthLabel } from "../format";
import { saveMetricInvestigationCase } from "../api";

const PERSISTENCE_META = {
  consistent_declining: (p) => ({ emoji: "📉", label: `${p.consecutive_periods}-month decline` }),
  consistent_improving: (p) => ({ emoji: "📈", label: `${p.consecutive_periods}-month improvement` }),
  recovering: () => ({ emoji: "🔄", label: "Recovering" }),
  single_period_move: () => ({ emoji: "•", label: "Isolated move" }),
};

function PersistenceBadge({ persistence }) {
  const build = persistence && PERSISTENCE_META[persistence.pattern];
  if (!build) return null;
  const meta = build(persistence);
  return (
    <span className="anomalies-table-badge persistence-badge" title={persistence.period_range}>
      {meta.emoji} {meta.label}
    </span>
  );
}

function BreakdownChart({ breakdown, unit, direction }) {
  const rows = breakdown.top_contributors || [];
  if (rows.length === 0) {
    return <div className="chart-empty">No segments with enough volume to break this down.</div>;
  }

  if (breakdown.mode === "composition") {
    return (
      <div className="chart-card">
        <div className="chart-card-title">By {breakdown.label} (share of mix)</div>
        <table className="anomalies-table">
          <thead>
            <tr>
              <th>{breakdown.label}</th>
              <th>Current share</th>
              <th>Baseline share</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td>{r.name}</td>
                <td>{r.current_share_pct}%</td>
                <td>{r.baseline_share_pct}%</td>
                <td className="anomalies-table-change">
                  {r.delta > 0 ? "+" : ""}
                  {r.delta}pp
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="chart-card">
      <div className="chart-card-title">By {breakdown.label} &mdash; change vs. baseline</div>
      <DivergingBarList
        data={rows}
        formatValue={(v) => formatByUnit(v, unit)}
        deltaGoodWhenPositive={direction === "higher_is_good"}
        ariaLabel={`Change in ${breakdown.label} vs baseline`}
      />
    </div>
  );
}

export default function InvestigateMetricCard({ result }) {
  const [saveState, setSaveState] = useState("idle"); // idle | saving | saved | error
  const [savedCaseId, setSavedCaseId] = useState(null);

  if (!result || result.error) return null;

  const { metric, trend, anomaly, persistence, breakdowns } = result;
  const meta =
    CATEGORY_META[anomaly.category] ?? {
      emoji: "ℹ️",
      label: anomaly.category === "normal" ? "Normal range" : anomaly.category,
    };
  // Only metrics build_investigation() would actually turn into a case --
  // same gate the backend route applies (status === "completed" there),
  // mirrored here so the button never appears for a flagged-but-unsaveable
  // "normal" month and doesn't flash once a related case already exists.
  const canSave = !result.related_case_id && !savedCaseId
    && (anomaly.category === "critical" || anomaly.category === "opportunity");

  async function handleSave() {
    setSaveState("saving");
    try {
      const saved = await saveMetricInvestigationCase(metric.id);
      setSavedCaseId(saved.case_id);
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  }

  return (
    <div className="tool-card investigate-metric-card">
      <div className="tool-card-title">
        {metric.label}
        {anomaly.latest_month && (
          <span className={`anomalies-table-badge anomalies-table-badge-${anomaly.category}`}>
            {meta.emoji} {meta.label} &middot; {monthLabel(anomaly.latest_month)}
          </span>
        )}
        <PersistenceBadge persistence={persistence} />
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
          <BreakdownChart key={b.dimension} breakdown={b} unit={metric.unit} direction={metric.direction} />
        ))}

      {savedCaseId ? (
        <div className="tool-card-footer">
          <span className="tool-card-footer-label">Saved as case {savedCaseId}</span>
          <Link to={`/investigate/${encodeURIComponent(savedCaseId)}`} className="tool-card-footer-link">
            Open in Investigate <ArrowRight size={12} />
          </Link>
        </div>
      ) : canSave ? (
        <div className="tool-card-footer">
          <Button size="sm" onClick={handleSave} disabled={saveState === "saving"}>
            {saveState === "saving" ? "Saving…" : "Save as case"}
          </Button>
          {saveState === "error" && (
            <span className="tool-card-footer-label"> Couldn't save this as a case — try again.</span>
          )}
        </div>
      ) : (
        <CardFooter relatedCaseId={result.related_case_id} />
      )}
    </div>
  );
}
