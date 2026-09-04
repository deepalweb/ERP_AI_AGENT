import { CATEGORY_META } from "../InvestigationCard";
import { monthLabel } from "../format";

export default function AnomaliesTable({ investigations, onOpenInvestigation }) {
  if (!investigations || investigations.length === 0) {
    return <div className="chart-empty">Nothing outside its usual range right now.</div>;
  }

  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Metric</th>
          <th>Period</th>
          <th>Change</th>
          <th>Category</th>
        </tr>
      </thead>
      <tbody>
        {investigations.map((inv) => {
          const meta = CATEGORY_META[inv.category] ?? { emoji: "ℹ️", label: inv.category };
          const pct = inv.confirm.pct_change;
          return (
            <tr key={inv.id} tabIndex={0} onClick={() => onOpenInvestigation?.(inv.id)}>
              <td>{inv.title}</td>
              <td>{monthLabel(inv.confirm.period)}</td>
              <td className="anomalies-table-change">
                {pct === null ? "n/a" : `${pct > 0 ? "+" : ""}${pct}%`}
              </td>
              <td>
                <span className={`anomalies-table-badge anomalies-table-badge-${inv.category}`}>
                  {meta.emoji} {meta.label}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
