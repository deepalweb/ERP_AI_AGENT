import StatTile from "../charts/StatTile";
import { formatCurrency, monthLabel } from "../format";

export default function MarginCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card margin-card">
      <div className="tool-card-title">Estimated gross margin &mdash; current-cost basis &mdash; {monthLabel(result.period)}</div>
      <div className="tool-card-notice">
        Cost is today's catalog price applied to historical sales quantities, not a historical cost-of-goods-sold
        record &mdash; this does not establish realized historical gross margin.
      </div>
      {result.is_partial_period && (
        <div className="tool-card-notice">
          Partial month &mdash; {result.days_elapsed} of {result.days_in_period} days elapsed.
        </div>
      )}
      <div className="kpi-row">
        <StatTile label="Revenue" value={formatCurrency(result.revenue)} />
        <StatTile label="Cost of goods sold (current catalog cost)" value={formatCurrency(result.cost_of_goods_sold)} />
        <StatTile label="Estimated gross margin" value={formatCurrency(result.gross_margin_amount)} />
        <StatTile
          label="Estimated margin %"
          value={result.gross_margin_pct === null ? "n/a" : `${result.gross_margin_pct}%`}
          note={`${result.cost_data_coverage_pct}% of revenue had a cost value -- coverage only, not a check on whether that cost was accurate for the period`}
        />
      </div>
    </div>
  );
}
