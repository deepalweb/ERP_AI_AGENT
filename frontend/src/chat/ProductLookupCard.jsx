import StatTile from "../charts/StatTile";
import HBarList from "../charts/HBarList";
import { formatCurrency, formatNumber, monthLabel } from "../format";

export default function ProductLookupCard({ result }) {
  if (!result || result.error) return null;

  if (result.status === "not_found") {
    return (
      <div className="tool-card product-lookup-card">
        <div className="tool-card-title">Product lookup &mdash; &ldquo;{result.query}&rdquo;</div>
        <div className="chart-empty">No product matched this name or SKU.</div>
      </div>
    );
  }

  if (result.status === "ambiguous") {
    return (
      <div className="tool-card product-lookup-card">
        <div className="tool-card-title">Product lookup &mdash; &ldquo;{result.query}&rdquo;</div>
        <div className="tool-card-notice">Multiple products matched &mdash; which one did you mean?</div>
        <table className="anomalies-table">
          <thead>
            <tr>
              <th>Product</th>
              <th>SKU</th>
            </tr>
          </thead>
          <tbody>
            {result.matches.map((m) => (
              <tr key={m.sku ?? m.name}>
                <td>{m.name}</td>
                <td className="anomalies-table-change">{m.sku ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="tool-card product-lookup-card">
      <div className="tool-card-title">
        {result.product.name} &mdash; {monthLabel(result.period)}
      </div>
      {result.is_partial_period && (
        <div className="tool-card-notice">Partial month &mdash; figures aren&rsquo;t a full month yet.</div>
      )}
      <div className="kpi-row">
        <StatTile label="Revenue" value={formatCurrency(result.sales.revenue)} note={`${formatNumber(result.sales.orders)} orders`} />
        <StatTile label="Units sold" value={formatNumber(result.sales.qty_sold)} />
        <StatTile
          label="Est. gross margin (current-cost basis)"
          value={result.margin.gross_margin_pct === null ? "n/a" : `${result.margin.gross_margin_pct}%`}
          note={
            result.margin.cost_data_coverage_pct === null
              ? "current catalog cost, not a historical cost-of-goods-sold record"
              : `${result.margin.cost_data_coverage_pct}% of revenue had a cost value (coverage only) -- cost itself is current catalog price, not historical`
          }
        />
        <StatTile
          label="Uber removals"
          value={formatNumber(result.uber_removals)}
          status={result.uber_removals > 0 ? "warning" : undefined}
        />
      </div>
      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Channel mix</div>
          <HBarList
            data={result.channel_mix}
            valueKey="revenue"
            detailKey="qty"
            detailLabel="units"
            ariaLabel="Revenue by channel for this product"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Stock by outlet</div>
          <HBarList
            data={result.stock_by_outlet}
            valueKey="qty"
            formatValue={formatNumber}
            ariaLabel="Current stock by outlet for this product"
          />
        </div>
      </div>
    </div>
  );
}
