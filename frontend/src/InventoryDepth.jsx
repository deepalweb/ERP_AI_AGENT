import StatTile from "./charts/StatTile";
import { formatCurrency, formatNumber } from "./format";

const SEGMENT_STATUS = {
  potential_dead_stock: "critical",
  overstock: "warning",
  slow_moving: "warning",
};

function DeadStockTable({ items }) {
  if (!items || items.length === 0) {
    return <div className="chart-empty">No dead stock right now.</div>;
  }

  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Product</th>
          <th>Units</th>
          <th>Last sale</th>
          <th>Est. value</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.name}>
            <td>{item.name}</td>
            <td className="anomalies-table-change">{formatNumber(item.units)}</td>
            <td className="anomalies-table-change">
              {item.days_since_last_sale != null ? `${item.days_since_last_sale}d ago` : "Never sold"}
            </td>
            <td className="anomalies-table-change">
              {item.estimated_value !== null ? formatCurrency(item.estimated_value) : "n/a"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SlowMovingTable({ items }) {
  if (!items || items.length === 0) {
    return <div className="chart-empty">Nothing slow-moving right now.</div>;
  }

  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Product</th>
          <th>Units</th>
          <th>Last sale</th>
          <th>Est. value</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.name}>
            <td>{item.name}</td>
            <td className="anomalies-table-change">{formatNumber(item.units)}</td>
            <td className="anomalies-table-change">{item.days_since_last_sale}d ago</td>
            <td className="anomalies-table-change">
              {item.estimated_value !== null ? formatCurrency(item.estimated_value) : "n/a"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OverstockTable({ items }) {
  if (!items || items.length === 0) {
    return <div className="chart-empty">Nothing overstocked right now.</div>;
  }

  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Product</th>
          <th>Days of stock</th>
          <th>Est. value</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.name}>
            <td>{item.name}</td>
            <td className="anomalies-table-change">{item.days_of_stock}d</td>
            <td className="anomalies-table-change">
              {item.estimated_value !== null ? formatCurrency(item.estimated_value) : "n/a"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function InventoryDepth({ data }) {
  if (!data) return null;

  const { segments, potential_dead_stock_items, slow_moving_items, overstock_items } = data;
  const segmentById = Object.fromEntries((segments || []).map((s) => [s.id, s]));

  return (
    <div className="customer-intel-section">
      <div className="overview-full-feed-title customer-intel-title">Inventory Depth</div>

      <div className="kpi-row inventory-depth-segments">
        {["potential_dead_stock", "slow_moving", "overstock", "healthy", "uncertain", "never_sold"].map((id) => {
          const seg = segmentById[id];
          if (!seg) return null;
          return (
            <StatTile
              key={id}
              label={seg.label}
              value={formatNumber(seg.count)}
              status={SEGMENT_STATUS[id]}
              note={`${formatCurrency(seg.value)} value · ${formatNumber(seg.units)} units`}
            />
          );
        })}
      </div>

      <div className="customer-intel-row">
        <div className="chart-card customer-intel-at-risk">
          <div className="chart-card-title">Potential dead stock &mdash; top by value</div>
          <DeadStockTable items={potential_dead_stock_items} />
        </div>
        <div className="chart-card customer-intel-at-risk">
          <div className="chart-card-title">Slow-moving &mdash; top by value</div>
          <SlowMovingTable items={slow_moving_items} />
        </div>
        <div className="chart-card customer-intel-at-risk">
          <div className="chart-card-title">Overstocked &mdash; top by value</div>
          <OverstockTable items={overstock_items} />
        </div>
      </div>
    </div>
  );
}
