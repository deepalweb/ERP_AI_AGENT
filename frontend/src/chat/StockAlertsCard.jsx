import { formatNumber } from "../format";

function OutletSegmentsTable({ segments }) {
  if (!segments || segments.length === 0) {
    return <div className="chart-empty">No data.</div>;
  }
  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Outlet</th>
          <th>Out of stock</th>
          <th>Low stock</th>
        </tr>
      </thead>
      <tbody>
        {segments.map((s) => (
          <tr key={s.outlet}>
            <td>{s.outlet}</td>
            <td className="anomalies-table-change">{formatNumber(s.out_of_stock)}</td>
            <td className="anomalies-table-change">{formatNumber(s.low_stock)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ItemsTable({ items, extraLabel, extraKey }) {
  if (!items || items.length === 0) {
    return <div className="chart-empty">None.</div>;
  }
  return (
    <table className="anomalies-table">
      <thead>
        <tr>
          <th>Product</th>
          <th>Outlet</th>
          <th>Sold (30d)</th>
          {extraKey && <th>{extraLabel}</th>}
        </tr>
      </thead>
      <tbody>
        {items.map((it, i) => (
          <tr key={`${it.sku}-${it.outlet}-${i}`}>
            <td>{it.name}</td>
            <td>{it.outlet}</td>
            <td className="anomalies-table-change">{formatNumber(it.sold_30d)}</td>
            {extraKey && <td className="anomalies-table-change">{it[extraKey]}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function StockAlertsCard({ result }) {
  if (!result || result.error) return null;

  return (
    <div className="tool-card stock-alerts-card">
      <div className="tool-card-title">Stock alerts &mdash; as of {result.as_of_date}</div>
      <OutletSegmentsTable segments={result.segments_by_outlet} />
      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Top out-of-stock (fast movers)</div>
          <ItemsTable items={result.top_out_of_stock} />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">
            Top low-stock (&lt; {result.low_stock_threshold_days} days cover)
          </div>
          <ItemsTable items={result.top_low_stock} extraLabel="Days left" extraKey="days_of_stock" />
        </div>
      </div>
    </div>
  );
}
