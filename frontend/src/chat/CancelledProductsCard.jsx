import HBarList from "../charts/HBarList";
import { formatNumber } from "../format";

export default function CancelledProductsCard({ result }) {
  if (!result || result.error || result.length === 0) return null;

  return (
    <div className="tool-card cancelled-products-card">
      <div className="tool-card-title">Most commonly cancelled items &mdash; all-time</div>
      <HBarList
        data={result}
        valueKey="times_cancelled"
        formatValue={formatNumber}
        ariaLabel="Products most frequently appearing on cancelled orders"
      />
    </div>
  );
}
