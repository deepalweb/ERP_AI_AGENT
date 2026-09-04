import { useState } from "react";
import "./shared.css";
import "./Investigation.css";
import { CATEGORY_META, PRIORITY_META, contributorValueParts } from "./InvestigationCard";
import { VERDICT_META } from "./AIImpact";
import HBarList from "./charts/HBarList";
import { formatByUnit, formatCurrency, formatDuration, formatNumber, formatPct, monthLabel } from "./format";

const STAGES = [
  { id: "issue", label: "Issue" },
  { id: "why", label: "Why" },
  { id: "how", label: "How" },
  { id: "resolve", label: "Resolve" },
  { id: "result", label: "Result" },
];

function ContributorRow({ contributor, mode }) {
  const { main, sub } = contributorValueParts(contributor, mode);
  return (
    <li>
      <span className="contributor-name">{contributor.name}</span>
      <span className="contributor-value">
        {main} {sub && <span className="contributor-was">{sub}</span>}
      </span>
    </li>
  );
}

function RecommendationRow({ rec }) {
  const meta = PRIORITY_META[rec.priority];
  return (
    <li>
      <div className="rec-action">{rec.action}</div>
      <div className="rec-meta">
        <span className="rec-owner">{rec.owner}</span>
        {meta && <span className={`rec-priority ${meta.className}`}>{meta.label}</span>}
      </div>
    </li>
  );
}

export default function Investigation({ investigation: inv, tracking, onBack }) {
  const stages = STAGES.filter((s) => s.id !== "how" || inv?.how);
  const [activeStage, setActiveStage] = useState("issue");

  if (!inv) {
    return (
      <div className="investigation-page">
        <button type="button" className="investigation-back" onClick={onBack}>&larr; Back to dashboard</button>
        <p>That investigation isn&rsquo;t available anymore &mdash; it may have resolved since you last checked.</p>
      </div>
    );
  }

  const meta = CATEGORY_META[inv.category] ?? { emoji: "ℹ️", label: inv.category };
  const activeIndex = stages.findIndex((s) => s.id === activeStage);
  const nextStage = stages[activeIndex + 1];
  const trackedRow = (tracking || []).find(
    (t) => t.metric_id === inv.metric_id && t.period === inv.confirm.period,
  );

  return (
    <div className="investigation-page">
      <button type="button" className="investigation-back" onClick={onBack}>&larr; Back to dashboard</button>

      <div className="investigation-header">
        <span className="investigation-header-emoji" aria-hidden="true">{meta.emoji}</span>
        <div>
          <div className="investigation-title">{inv.title}</div>
          <div className="insight-meta">{meta.label} &middot; {monthLabel(inv.confirm.period)}</div>
        </div>
      </div>

      <nav className="stepper-track" aria-label="Investigation stages">
        {stages.map((s, i) => (
          <button
            key={s.id}
            type="button"
            className={
              "stepper-step" +
              (activeStage === s.id ? " stepper-step-active" : "") +
              (activeIndex > i ? " stepper-step-done" : "")
            }
            onClick={() => setActiveStage(s.id)}
          >
            <span className="stepper-step-index">{i + 1}</span>
            <span className="stepper-step-label">{s.label}</span>
          </button>
        ))}
      </nav>

      {activeStage === "issue" && (
        <section className="investigation-stage">
          <div className="section-kicker">Issue</div>
          <h2>Is this real?</h2>
          <div className="issue-stat">
            <div className="issue-stat-main">{formatByUnit(inv.confirm.current, inv.confirm.unit)}</div>
            <div className="issue-stat-sub">
              Usual: <strong>{formatByUnit(inv.confirm.baseline, inv.confirm.unit)}</strong>
              {inv.confirm.pct_change !== null && (
                <>
                  {" "}&middot; Change: <strong>{formatPct(inv.confirm.pct_change)}</strong>
                </>
              )}
            </div>
          </div>
          {inv.impact && (
            <div className={`insight-impact ${inv.impact.kind === "lost_orders" ? "impact-bad" : "impact-good"}`}>
              <strong>{inv.impact.kind === "lost_orders" ? "Estimated impact:" : "Estimated recovery:"}</strong>{" "}
              {formatNumber(inv.impact.orders)}{" "}
              {inv.impact.kind === "lost_orders" ? "orders likely lost" : "orders recovered"} in{" "}
              {monthLabel(inv.impact.month)}, roughly {formatCurrency(inv.impact.estimated_revenue_impact)}{" "}
              {inv.impact.kind === "lost_orders" ? "in lost revenue" : "in recovered revenue"}.
            </div>
          )}
        </section>
      )}

      {activeStage === "why" && (
        <section className="investigation-stage">
          <div className="section-kicker">Why</div>
          <h2>What&rsquo;s driving this?</h2>
          {inv.breakdowns.length === 0 && <p className="investigation-empty">No breakdown available for this metric.</p>}
          {inv.breakdowns.map((b) => {
            const top = b.top_contributors[0];
            const framing =
              b.mode === "composition" && top
                ? `${top.name} accounts for ${top.current_share_pct}% of cases (was ${top.baseline_share_pct}%).`
                : null;
            return (
              <div className="insight-breakdown" key={b.dimension}>
                <div className="insight-breakdown-title">By {b.label.toLowerCase()}</div>
                {framing && <p className="investigation-framing">{framing}</p>}
                <ul className="contributor-list">
                  {b.top_contributors.map((c) => (
                    <ContributorRow key={c.name} contributor={c} mode={b.mode} />
                  ))}
                </ul>
              </div>
            );
          })}
        </section>
      )}

      {activeStage === "how" && inv.how && (
        <section className="investigation-stage">
          <div className="section-kicker">How</div>
          <h2>Where is the time actually going?</h2>
          <p className="investigation-framing">
            Median time in each fulfillment stage at the flagged branches vs. everywhere else.
          </p>
          <HBarList
            data={inv.how.stages}
            valueKey="flagged_minutes"
            formatValue={formatDuration}
            detailKey="baseline_minutes"
            detailLabel="min elsewhere"
            ariaLabel="Median fulfillment stage time at flagged branches vs elsewhere"
          />
        </section>
      )}

      {activeStage === "resolve" && (
        <section className="investigation-stage">
          <div className="section-kicker">Resolve</div>
          <h2>What should we do?</h2>
          {(!inv.recommendations || inv.recommendations.length === 0) && (
            <p className="investigation-empty">No recommendation generated for this finding.</p>
          )}
          {inv.recommendations?.[0] && (
            <div className="investigation-primary-action">
              <span className="investigation-primary-action-label">Recommended next step</span>
              <div className="rec-action">{inv.recommendations[0].action}</div>
              <div className="rec-meta">
                <span className="rec-owner">{inv.recommendations[0].owner}</span>
                {PRIORITY_META[inv.recommendations[0].priority] && (
                  <span className={`rec-priority ${PRIORITY_META[inv.recommendations[0].priority].className}`}>
                    {PRIORITY_META[inv.recommendations[0].priority].label}
                  </span>
                )}
              </div>
            </div>
          )}
          {inv.recommendations?.length > 1 && (
            <div className="insight-recommendations">
              <div className="insight-step-label">Other options</div>
              <ol>
                {inv.recommendations.slice(1).map((r, i) => (
                  <RecommendationRow key={i} rec={r} />
                ))}
              </ol>
            </div>
          )}
        </section>
      )}

      {activeStage === "result" && (
        <section className="investigation-stage">
          <div className="section-kicker">Result</div>
          <h2>Did it work?</h2>
          {!trackedRow && <p className="investigation-empty">Not tracked yet.</p>}
          {trackedRow && trackedRow.status === "resolved" && (
            <div className="investigation-result">
              <div className="investigation-result-values">
                <div>
                  <span>Before</span>
                  <strong>{formatByUnit(trackedRow.baseline_value, trackedRow.unit)}</strong>
                </div>
                <div>
                  <span>Detected</span>
                  <strong>{formatByUnit(trackedRow.detected_value, trackedRow.unit)}</strong>
                </div>
                <div>
                  <span>After ({monthLabel(trackedRow.outcome_period)})</span>
                  <strong>{formatByUnit(trackedRow.outcome_value, trackedRow.unit)}</strong>
                </div>
              </div>
              <span className={`impact-verdict ${VERDICT_META[trackedRow.verdict].className}`}>
                {VERDICT_META[trackedRow.verdict].emoji} {VERDICT_META[trackedRow.verdict].label}{" "}
                ({formatPct(trackedRow.outcome_pct_change)} vs baseline, was {formatPct(trackedRow.detected_pct_change)})
              </span>
            </div>
          )}
          {trackedRow && trackedRow.status === "open" && (
            <div className="impact-row-pending">
              🕒 Still tracking &mdash; check back after {monthLabel(trackedRow.period)} closes.
            </div>
          )}
        </section>
      )}

      {nextStage && (
        <button type="button" className="stepper-next" onClick={() => setActiveStage(nextStage.id)}>
          Continue to {nextStage.label} &rarr;
        </button>
      )}
    </div>
  );
}
