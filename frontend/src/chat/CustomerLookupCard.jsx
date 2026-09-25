import { formatCurrency, formatNumber } from "../format";

const LIFECYCLE_LABEL = {
  new: "New", active: "Active", due: "Due to reorder", lapsing: "Lapsing",
  churned: "Churned", dormant: "Dormant", win_back: "Win-back", excluded: "Unscored",
};

const NEXT_BASKET_LABEL = { overdue: "Overdue", due: "Due", on_track: "On track" };
const VALUE_TIER_LABEL = { high: "High value", mid: "Mid value", low: "Low value" };

/** Customer Lookup Design, Phase A: renders customer_lookup.get_customer_lookup()'s
 * raw result. Never renders a raw phone/email -- the backend never returns them to
 * this tool by design, so there is deliberately no field here that could show one. */
export default function CustomerLookupCard({ result }) {
  if (!result || result.error) return null;

  if (result.status === "not_found") {
    return (
      <div className="tool-card customer-lookup-card">
        <div className="tool-card-title">Customer lookup</div>
        <div className="chart-empty">No customer matched that search.</div>
      </div>
    );
  }

  if (result.status === "multiple") {
    return (
      <div className="tool-card customer-lookup-card">
        <div className="tool-card-title">Multiple customers matched</div>
        <table className="anomalies-table">
          <thead>
            <tr><th>Name</th><th>Phone</th><th>Orders</th></tr>
          </thead>
          <tbody>
            {result.matches.map((m) => (
              <tr key={m.partner_id}>
                <td>{m.name}</td>
                <td className="anomalies-table-change">{m.phone_masked || "—"}</td>
                <td>{formatNumber(m.order_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="tool-card-notice">Ask which one, or search again with a phone number or fuller name.</div>
      </div>
    );
  }

  const c = result.customer;
  const nextBasket = c.next_basket;
  const overdue = (nextBasket?.products || []).filter((p) => p.status !== "on_track").slice(0, 5);

  return (
    <div className="tool-card customer-lookup-card">
      <div className="tool-card-title">{c.name}</div>
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Lifecycle</span>
          <span className="tool-card-value">{LIFECYCLE_LABEL[c.lifecycle_category] || "—"}</span>
        </div>
        <div>
          <span className="tool-card-label">Value tier</span>
          <span className="tool-card-value">{VALUE_TIER_LABEL[c.value_tier] || "—"}</span>
        </div>
        <div>
          <span className="tool-card-label">Orders</span>
          <span className="tool-card-value">{formatNumber(c.order_count)}</span>
        </div>
        <div>
          <span className="tool-card-label">Lifetime revenue</span>
          <span className="tool-card-value">{formatCurrency(c.lifetime_revenue)}</span>
        </div>
        <div>
          <span className="tool-card-label">Last order</span>
          <span className="tool-card-value">{c.last_order_date || "—"}</span>
        </div>
        <div>
          <span className="tool-card-label">Phone on file</span>
          <span className="tool-card-value">{c.phone_masked || "—"}</span>
        </div>
      </div>

      {overdue.length > 0 && (
        <div className="chart-card">
          <div className="chart-card-title">Due for reorder</div>
          <table className="anomalies-table">
            <thead>
              <tr><th>Product</th><th>Status</th><th>Last bought</th></tr>
            </thead>
            <tbody>
              {overdue.map((p) => (
                <tr key={p.product_id}>
                  <td>{p.name}</td>
                  <td>{NEXT_BASKET_LABEL[p.status]}</td>
                  <td className="anomalies-table-change">{p.days_since_last}d ago, usually every {p.avg_gap_days}d</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="tool-card-notice">
        Data through {result.as_of || "unknown"}. Contact detail is intentionally not shown here — only a masked
        phone number, never a raw email or phone.
      </div>
    </div>
  );
}
