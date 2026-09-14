import HBarList from "../charts/HBarList";
import { formatCurrency, formatNumber, monthLabel } from "../format";

function withName(rows) {
  return (rows || []).map((r) => ({ ...r, name: r.customer }));
}

function withPromoName(rows) {
  const seen = new Map();
  return (rows || []).map((r) => {
    const count = seen.get(r.name) || 0;
    seen.set(r.name, count + 1);
    const name = count === 0 ? r.name : `${r.name} (${r.start_date})`;
    return { ...r, name, discount: r.total_discount_value };
  });
}

export default function LoyaltyPromotionsCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card loyalty-card">
      <div className="tool-card-title">Loyalty &amp; promotions &mdash; {monthLabel(result.period)}</div>
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Points issued</span>
          <span className="tool-card-value">{formatNumber(result.points_issued)}</span>
        </div>
        <div>
          <span className="tool-card-label">Points used</span>
          <span className="tool-card-value">{formatNumber(result.points_used)}</span>
        </div>
        <div>
          <span className="tool-card-label">Active cards</span>
          <span className="tool-card-value">{formatNumber(result.active_cards)}</span>
        </div>
        <div>
          <span className="tool-card-label">Active promotions</span>
          <span className="tool-card-value">{formatNumber((result.active_promotions || []).length)}</span>
        </div>
      </div>

      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Top customers by points issued</div>
          <HBarList
            data={withName(result.top_customers_by_points)}
            valueKey="issued"
            formatValue={formatNumber}
            detailKey="used"
            detailLabel="points used"
            ariaLabel="Top customers by loyalty points issued"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Promotions by discount value</div>
          <HBarList
            data={withPromoName(result.active_promotions)}
            valueKey="discount"
            formatValue={formatCurrency}
            detailKey="avg_discount_pct"
            detailLabel="% avg discount"
            ariaLabel="Store promotions ranked by total discount value"
          />
        </div>
      </div>
    </div>
  );
}
