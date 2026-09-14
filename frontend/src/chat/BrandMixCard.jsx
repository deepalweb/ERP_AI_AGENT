import HBarList from "../charts/HBarList";
import { formatCurrency, monthLabel } from "../format";

function withName(rows, key) {
  return (rows || []).map((r) => ({ ...r, name: r[key] }));
}

export default function BrandMixCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card brand-mix-card">
      <div className="tool-card-title">Brand &amp; department mix &mdash; {monthLabel(result.period)}</div>
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Total revenue</span>
          <span className="tool-card-value">{formatCurrency(result.total_revenue)}</span>
        </div>
        <div>
          <span className="tool-card-label">Departments</span>
          <span className="tool-card-value">{(result.departments || []).length}</span>
        </div>
      </div>

      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Top brands by revenue</div>
          <HBarList
            data={withName(result.top_brands, "brand")}
            valueKey="revenue"
            detailKey="qty"
            detailLabel="units sold"
            ariaLabel="Top brands by revenue"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Revenue by department</div>
          <HBarList
            data={withName(result.departments, "department")}
            valueKey="revenue"
            detailKey="revenue_share_pct"
            detailLabel="% of revenue"
            ariaLabel="Revenue by department"
          />
        </div>
      </div>
    </div>
  );
}
