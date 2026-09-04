import HBarList from "../charts/HBarList";
import { formatCurrency } from "../format";

export default function InventoryByOutletCard({ result }) {
  if (!result || result.error || result.length === 0) return null;

  return (
    <div className="tool-card inventory-by-outlet-card">
      <div className="tool-card-title">Inventory value by outlet</div>
      <HBarList
        data={result}
        valueKey="estimated_value"
        formatValue={formatCurrency}
        detailKey="units"
        detailLabel="units on hand"
        ariaLabel="Estimated inventory value by outlet"
      />
    </div>
  );
}
