import HBarList from "../charts/HBarList";
import { formatNumber, monthLabel } from "../format";

function toRows(pairs, valueKey) {
  return (pairs || []).map((p) => ({
    name: `${p.product_a} + ${p.product_b}`,
    [valueKey]: valueKey === "lift" ? p.lift ?? 0 : p.orders_together,
    confidence: Math.max(p.confidence_a_to_b_pct ?? 0, p.confidence_b_to_a_pct ?? 0),
  }));
}

function formatLift(value) {
  return `${Number(value || 0).toFixed(1)}×`;
}

export default function BasketAnalysisCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card basket-card">
      <div className="tool-card-title">Basket analysis &mdash; {monthLabel(result.period)}</div>
      {result.is_partial_period && (
        <div className="tool-card-notice">Partial period &mdash; figures aren&rsquo;t a full period yet.</div>
      )}
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Orders analyzed</span>
          <span className="tool-card-value">{formatNumber(result.total_orders)}</span>
        </div>
        <div>
          <span className="tool-card-label">Pairs meeting volume floor</span>
          <span className="tool-card-value">
            {formatNumber(result.pairs_considered)}
            <span className="basket-card-floor"> (&ge;{result.min_pair_orders} orders)</span>
          </span>
        </div>
      </div>
      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Most common combinations</div>
          <HBarList
            data={toRows(result.top_pairs_by_frequency, "orders_together")}
            valueKey="orders_together"
            formatValue={formatNumber}
            detailKey="confidence"
            detailLabel="% of the smaller item's orders"
            ariaLabel="Most frequently co-purchased product pairs"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Strongest affinity (lift)</div>
          <HBarList
            data={toRows(result.top_pairs_by_affinity, "lift")}
            valueKey="lift"
            formatValue={formatLift}
            detailKey="confidence"
            detailLabel="% of the smaller item's orders"
            ariaLabel="Product pairs most likely to be bought together relative to chance"
          />
        </div>
      </div>
    </div>
  );
}
