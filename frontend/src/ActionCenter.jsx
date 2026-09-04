const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };

export default function ActionCenter({ investigations, onOpenInvestigation }) {
  const actions = (investigations || [])
    .flatMap((investigation) =>
      (investigation.recommendations || []).map((recommendation) => ({
        ...recommendation,
        investigationId: investigation.id,
        category: investigation.category,
      })),
    )
    .sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3))
    .slice(0, 3);

  if (actions.length === 0) return null;

  return (
    <section className="action-center" aria-labelledby="action-center-title">
      <div className="section-heading-row">
        <div>
          <div className="section-kicker">Action center</div>
          <h2 id="action-center-title">What to do next</h2>
        </div>
        <span className="section-count">{actions.length} priorities</span>
      </div>
      <div className="action-list">
        {actions.map((action, index) => (
          <div className="action-row" key={`${action.investigationId}-${index}`}>
            <span className={`action-priority-dot action-priority-${action.priority}`} aria-hidden="true" />
            <div className="action-copy">
              <strong>{action.action}</strong>
              <span>{action.owner || "Owner to assign"}</span>
            </div>
            <span className="action-status">Recommended</span>
            <button
              type="button"
              className="text-button"
              onClick={() => onOpenInvestigation?.(action.investigationId)}
            >
              Review
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
