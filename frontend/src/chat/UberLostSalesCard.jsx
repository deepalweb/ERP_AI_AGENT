import StatTile from "../charts/StatTile";
import { formatCurrency, formatNumber, monthLabel } from "../format";

function ReasonsTable({ reasons }) {
  if (!reasons || reasons.length === 0) {
    return <div className="chart-empty">No removals recorded.</div>;
  }
  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Reason</th>
          <th>Count</th>
        </tr>
      </thead>
      <tbody>
        {reasons.map((r) => (
          <tr key={r.reason}>
            <td>{r.reason}</td>
            <td className="anomalies-table-change">{formatNumber(r.count)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ProductsTable({ products }) {
  if (!products || products.length === 0) {
    return <div className="chart-empty">No removals recorded.</div>;
  }
  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Product</th>
          <th>Times removed</th>
        </tr>
      </thead>
      <tbody>
        {products.map((p) => (
          <tr key={p.name}>
            <td>{p.name}</td>
            <td className="anomalies-table-change">{formatNumber(p.times_removed)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function UberLostSalesCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card uber-lost-sales-card">
      <div className="tool-card-title">Uber item removals &mdash; {monthLabel(result.period)}</div>
      {result.is_partial_period && (
        <div className="tool-card-notice">
          Partial month &mdash; {result.days_elapsed} of {result.days_in_period} days elapsed.
        </div>
      )}
      <div className="kpi-row">
        <StatTile label="Items removed" value={formatNumber(result.removed_lines)} status="warning" />
        <StatTile label="Removal rate" value={`${result.removed_rate_pct}%`} note={`of ${formatNumber(result.total_lines)} lines`} />
        <StatTile
          label="Estimated lost revenue"
          value={formatCurrency(result.estimated_lost_revenue)}
          note={result.estimate_note}
        />
      </div>
      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Top removal reasons</div>
          <ReasonsTable reasons={result.top_reasons} />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Most-removed products</div>
          <ProductsTable products={result.top_products} />
        </div>
      </div>
    </div>
  );
}
