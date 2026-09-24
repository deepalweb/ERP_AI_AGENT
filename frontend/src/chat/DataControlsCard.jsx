import HBarList from "../charts/HBarList";
import { formatCurrency, formatNumber } from "../format";

/** Business Data Controls, Wave 1 Step 1: renders
 * data_controls.get_data_controls()'s raw result. Deliberately doesn't
 * say "loss" or "shrinkage" for negative stock (a data-integrity
 * condition, cause unverified) and states cost-coverage's percentage
 * plainly rather than letting a small count read as a widespread
 * valuation problem. */
export default function DataControlsCard({ result }) {
  if (!result || result.error) return null;

  const negativeStock = result.negative_stock || { count: 0, products: [] };
  const costCoverage = result.cost_coverage || { no_cost_at_all: 0, total_products: 0, uncosted_products: [] };

  return (
    <div className="tool-card data-controls-card">
      <div className="tool-card-title">Business data controls</div>
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Products at negative stock</span>
          <span className="tool-card-value">{formatNumber(negativeStock.count)}</span>
        </div>
        <div>
          <span className="tool-card-label">Estimated value</span>
          <span className="tool-card-value">{formatCurrency(negativeStock.total_value_rs)}</span>
        </div>
        <div>
          <span className="tool-card-label">Catalog cost coverage</span>
          <span className="tool-card-value">
            {costCoverage.coverage_pct != null ? `${costCoverage.coverage_pct}%` : "—"}
          </span>
        </div>
      </div>

      {negativeStock.count > 0 && (
        <div className="chart-card">
          <div className="chart-card-title">Top negative-stock products</div>
          <HBarList
            data={negativeStock.products.slice(0, 8).map((p) => ({ ...p, name: p.name, value_rs: p.value_rs || 0 }))}
            valueKey="value_rs"
            formatValue={formatCurrency}
            ariaLabel="Products with negative recorded stock, by estimated value"
          />
          <div className="tool-card-notice">
            Negative recorded stock means the system shows less than zero on hand — a data-integrity condition,
            not confirmed loss or theft. The cause isn't established here.
          </div>
        </div>
      )}

      {costCoverage.no_cost_at_all > 0 ? (
        <div className="tool-card-notice">
          {costCoverage.no_cost_at_all} of {formatNumber(costCoverage.total_products)} active inventory products
          ({costCoverage.coverage_pct}% overall coverage) have no recorded cost anywhere. This affects margin and
          inventory-value figures only for these specific products — not the rest of the catalog.
        </div>
      ) : (
        <div className="tool-card-notice">Every active inventory product has a usable recorded cost.</div>
      )}

      {negativeStock.count === 0 && (
        <div className="tool-card-notice">No products currently show negative recorded stock.</div>
      )}
    </div>
  );
}
