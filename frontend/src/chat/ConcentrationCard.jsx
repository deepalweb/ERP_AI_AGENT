import StatTile from "../charts/StatTile";
import HBarList from "../charts/HBarList";
import { monthLabel } from "../format";

export default function ConcentrationCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card concentration-card">
      <div className="tool-card-title">Revenue concentration &mdash; {monthLabel(result.period)}</div>
      {result.is_partial_period && (
        <div className="tool-card-notice">Partial period &mdash; figures aren&rsquo;t a full period yet.</div>
      )}
      <div className="kpi-row">
        <StatTile
          label={`Top ${result.top_n} products' share`}
          value={result.top_products_revenue_share_pct === null ? "n/a" : `${result.top_products_revenue_share_pct}%`}
          note="of total revenue"
        />
        <StatTile
          label={`Top ${result.top_n} customers' share`}
          value={result.top_customers_revenue_share_pct === null ? "n/a" : `${result.top_customers_revenue_share_pct}%`}
          note="of total revenue"
        />
      </div>
      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Top products by revenue</div>
          <HBarList
            data={result.top_products}
            valueKey="revenue"
            detailKey="share_pct"
            detailLabel="% of revenue"
            ariaLabel="Top products by revenue share"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Top customers by revenue</div>
          <HBarList
            data={result.top_customers}
            valueKey="revenue"
            detailKey="share_pct"
            detailLabel="% of revenue"
            ariaLabel="Top customers by revenue share"
          />
        </div>
      </div>
    </div>
  );
}
