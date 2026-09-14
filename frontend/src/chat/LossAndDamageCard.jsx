import HBarList from "../charts/HBarList";
import { formatNumber, monthLabel } from "../format";

function withName(rows, nameKey) {
  return (rows || []).map((r) => ({ ...r, name: r[nameKey] }));
}

export default function LossAndDamageCard({ result }) {
  if (!result || result.error) return null;

  const totalShortageQty = (result.shortage_by_reason || []).reduce((sum, r) => sum + r.qty, 0);
  const totalDamagedQty = (result.damaged_top_products || []).reduce((sum, r) => sum + r.qty, 0);

  return (
    <div className="tool-card loss-damage-card">
      <div className="tool-card-title">Loss &amp; damage &mdash; {monthLabel(result.period)}</div>
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Shortage lines</span>
          <span className="tool-card-value">
            {formatNumber((result.shortage_by_reason || []).reduce((sum, r) => sum + r.lines, 0))}
          </span>
        </div>
        <div>
          <span className="tool-card-label">Damaged lines</span>
          <span className="tool-card-value">
            {formatNumber((result.damaged_top_products || []).reduce((sum, r) => sum + r.lines, 0))}
          </span>
        </div>
        <div>
          <span className="tool-card-label">Stock returns</span>
          <span className="tool-card-value">{formatNumber(result.returns_orders)}</span>
        </div>
        <div>
          <span className="tool-card-label">Returned qty</span>
          <span className="tool-card-value">{formatNumber(result.returns_qty)}</span>
        </div>
      </div>

      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Shortage by reason</div>
          <HBarList
            data={withName(result.shortage_by_reason, "reason")}
            valueKey="qty"
            formatValue={formatNumber}
            detailKey="lines"
            detailLabel="lines"
            ariaLabel="Picking shortage quantity by reason"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Top shortage products</div>
          <HBarList
            data={withName(result.shortage_top_products, "name")}
            valueKey="qty"
            formatValue={formatNumber}
            detailKey="lines"
            detailLabel="lines"
            ariaLabel="Top products affected by picking shortages"
          />
        </div>
      </div>

      <div className="customer-intel-row">
        <div className="chart-card">
          <div className="chart-card-title">Top damaged products</div>
          <HBarList
            data={withName(result.damaged_top_products, "name")}
            valueKey="qty"
            formatValue={formatNumber}
            detailKey="lines"
            detailLabel="lines"
            ariaLabel="Top products affected by damage"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Damaged orders by outlet</div>
          <HBarList
            data={withName(result.damaged_by_store, "outlet")}
            valueKey="qty"
            formatValue={formatNumber}
            detailKey="damaged_orders"
            detailLabel="damaged orders"
            ariaLabel="Damaged goods quantity by outlet"
          />
        </div>
      </div>

      {totalShortageQty === 0 && totalDamagedQty === 0 && (
        <div className="tool-card-notice">No shortage or damage lines recorded for this period.</div>
      )}
    </div>
  );
}
