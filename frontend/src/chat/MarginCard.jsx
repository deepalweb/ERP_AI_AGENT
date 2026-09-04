import StatTile from "../charts/StatTile";
import { formatCurrency, monthLabel } from "../format";

export default function MarginCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card margin-card">
      <div className="tool-card-title">Gross margin &mdash; {monthLabel(result.period)}</div>
      {result.is_partial_period && (
        <div className="tool-card-notice">
          Partial month &mdash; {result.days_elapsed} of {result.days_in_period} days elapsed.
        </div>
      )}
      <div className="kpi-row">
        <StatTile label="Revenue" value={formatCurrency(result.revenue)} />
        <StatTile label="Cost of goods sold" value={formatCurrency(result.cost_of_goods_sold)} />
        <StatTile label="Gross margin" value={formatCurrency(result.gross_margin_amount)} />
        <StatTile
          label="Margin %"
          value={result.gross_margin_pct === null ? "n/a" : `${result.gross_margin_pct}%`}
          note={`${result.cost_data_coverage_pct}% of revenue has cost data`}
        />
      </div>
    </div>
  );
}
