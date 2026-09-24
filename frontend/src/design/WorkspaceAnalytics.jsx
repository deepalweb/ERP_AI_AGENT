import { Link } from "react-router-dom";
import { formatNumber, prettifyMetricId, formatDate } from "../format";
import Badge from "./Badge";
import { caseStatusMeta } from "./caseStatus";

function Distribution({ title, description, rows }) {
  const maximum = Math.max(1, ...rows.map((row) => row.value));
  return (
    <section className="analytics-panel">
      <div className="analytics-panel-heading">
        <h2>{title}</h2>
        <span>Current snapshot</span>
      </div>
      <p>{description}</p>
      <div
        className="distribution-chart"
        role="img"
        aria-label={`${title}: ${rows.map((r) => `${r.label} ${r.value}`).join(", ")}`}
      >
        {rows.map((row, index) => (
          <div className="distribution-column" key={row.label}>
            <strong>{formatNumber(row.value)}</strong>
            <div className="distribution-track">
              <div
                className={`distribution-bar series-${index}`}
                style={{ height: `${(row.value / maximum) * 100}%` }}
              />
            </div>
            <span>{row.label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export function WorkspaceAnalytics({ data }) {
  if (!data) return null;
  return (
    <div className="analytics-grid">
      <Distribution
        title="Case lifecycle"
        description="All recorded cases by their current status"
        rows={[
          { label: "Open", value: data.cases.open || 0 },
          { label: "Reopened", value: data.cases.reopened || 0 },
          {
            label: "Verification pending",
            value: data.cases.verification_pending || 0,
          },
          { label: "Resolved", value: data.cases.resolved || 0 },
          { label: "Dismissed", value: data.cases.dismissed || 0 },
        ]}
      />
      <Distribution
        title="Action workload"
        description="Relevant actions · current workflow counts"
        rows={[
          { label: "Outstanding", value: data.actions.outstanding || 0 },
          { label: "Completed", value: data.actions.completed || 0 },
        ]}
      />
      <section className="analytics-panel verification-panel">
        <div className="analytics-panel-heading">
          <h2>Verification queue</h2>
          <span>Relevant actions</span>
        </div>
        <p>Measurement status, independent of completion</p>
        <Link to="/outcomes">
          <span className="verification-dot amber" />
          <span>Blocked measurements</span>
          <strong>{formatNumber(data.actions.blocked || 0)}</strong>
        </Link>
        <Link to="/outcomes">
          <span className="verification-dot violet" />
          <span>Pending measurements</span>
          <strong>{formatNumber(data.actions.pending || 0)}</strong>
        </Link>
        <div className="analytics-note">
          Completing an action does not establish that its target was achieved.
        </div>
      </section>
    </div>
  );
}

export function CaseTable({ items }) {
  if (!items?.length) return null;
  return (
    <div className="case-table-scroll">
      <table className="case-table">
        <caption className="sr-only">Latest open and reopened cases</caption>
        <thead>
          <tr>
            <th scope="col">Case / metric</th>
            <th scope="col">Domain</th>
            <th scope="col">Horizon</th>
            <th scope="col">Status</th>
            <th scope="col">Last observed</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const status = caseStatusMeta(item.status);
            return (
              <tr key={item.case_id}>
                <td>
                  <Link to={`/investigate/${encodeURIComponent(item.case_id)}`}>
                    {item.latest?.title || prettifyMetricId(item.metric_id)}
                  </Link>
                  <small>{prettifyMetricId(item.metric_id)}</small>
                </td>
                <td>{prettifyMetricId(item.domain)}</td>
                <td>{item.horizon || "—"}</td>
                <td>
                  <Badge tone={status.tone}>{status.label}</Badge>
                </td>
                <td>
                  {item.last_seen_at
                    ? formatDate(item.last_seen_at.slice(0, 10))
                    : "Unknown"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
