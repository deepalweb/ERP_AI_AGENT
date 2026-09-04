import { CATEGORY_META, contributorValueParts } from "./InvestigationCard";
import { formatByUnit } from "./format";

function ContributorBar({ contributor, mode, maxAbsDelta }) {
  const pct = maxAbsDelta > 0 ? (Math.abs(contributor.delta) / maxAbsDelta) * 100 : 0;
  const { main } = contributorValueParts(contributor, mode);
  return (
    <div className="ai-panel-factor">
      <div className="ai-panel-factor-row">
        <span className="ai-panel-factor-name">{contributor.name}</span>
        <span className="ai-panel-factor-value">{main}</span>
      </div>
      <div className="hbar-track ai-panel-factor-track">
        <div className="hbar-fill" style={{ width: `${Math.max(pct, 2)}%` }} />
      </div>
    </div>
  );
}

export default function AIInsightPanel({ investigation: inv, onOpenInvestigation }) {
  if (!inv) {
    return (
      <div className="ai-panel ai-panel-empty">
        <div className="ai-panel-title">AI Insight</div>
        <p>Nothing outside its usual range right now — revenue, orders, and cancellation rate all look normal.</p>
      </div>
    );
  }

  const meta = CATEGORY_META[inv.category] ?? { emoji: "ℹ️", label: inv.category };
  const topBreakdown = inv.breakdowns?.[0];
  const topFactors = topBreakdown?.top_contributors?.slice(0, 3) ?? [];
  const maxAbsDelta = Math.max(...topFactors.map((c) => Math.abs(c.delta)), 0);

  return (
    <div className={`ai-panel ai-panel-${inv.category}`}>
      <div className="ai-panel-header">
        <span className="ai-panel-badge">
          {meta.emoji} {meta.label}
        </span>
      </div>
      <div className="ai-panel-title">{inv.title}</div>
      <div className="insight-story-grid">
        <div><span>What happened</span><strong>{formatByUnit(inv.confirm.current, inv.confirm.unit)}</strong><small>{inv.confirm.pct_change > 0 ? "+" : ""}{inv.confirm.pct_change}% from usual</small></div>
        <div><span>Likely cause</span><strong>{topFactors[0]?.name || "No single cause identified"}</strong><small>{topBreakdown ? `Leading ${topBreakdown.label.toLowerCase()} contributor` : "More evidence needed"}</small></div>
        <div><span>Business impact</span><strong>{inv.impact ? `${inv.impact.orders.toLocaleString()} orders` : "Under review"}</strong><small>{inv.impact ? `${inv.impact.kind === "lost_orders" ? "Potentially affected" : "Potentially recovered"}` : "No reliable estimate yet"}</small></div>
      </div>
      {inv.recommendations?.[0] && <div className="primary-recommendation"><span>Recommended next step</span><strong>{inv.recommendations[0].action}</strong><small>{inv.recommendations[0].owner}</small></div>}

      {topFactors.length > 0 && (
        <div className="ai-panel-factors">
          <div className="ai-panel-factors-title">Drivers &middot; {topBreakdown.label}</div>
          {topFactors.map((c) => (
            <ContributorBar key={c.name} contributor={c} mode={topBreakdown.mode} maxAbsDelta={maxAbsDelta} />
          ))}
        </div>
      )}

      <button type="button" className="ai-panel-link" onClick={() => onOpenInvestigation?.(inv.id)}>
        View full analysis
      </button>
    </div>
  );
}
