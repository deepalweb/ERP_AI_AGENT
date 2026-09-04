import HBarList from "../charts/HBarList";
import { monthLabel } from "../format";

export default function TopProductsCard({ result }) {
  if (!result || result.error || !result.products || result.products.length === 0) return null;

  return (
    <div className="tool-card top-products-card">
      <div className="tool-card-title">
        Top products &mdash; {result.period ? `${monthLabel(result.period)}, by revenue` : "all-time, by revenue"}
      </div>
      <HBarList
        data={result.products}
        valueKey="revenue"
        detailKey="qty"
        detailLabel="units sold"
        ariaLabel="Top products by revenue"
      />
    </div>
  );
}
