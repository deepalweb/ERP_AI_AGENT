import { formatCurrency } from "../format";

function AvailabilityDot({ ok }) {
  return (
    <span className="listing-availability">
      <span className={`signal-tile-dot signal-tile-dot-${ok ? "good" : "critical"} attention-scan-dot`} />
      {ok ? "Listed" : "Not listed"}
    </span>
  );
}

export default function ProductListingCard({ result }) {
  if (!result || result.error) return null;

  if (result.status === "not_found") {
    return (
      <div className="tool-card product-listing-card">
        <div className="tool-card-title">Listing lookup &mdash; &ldquo;{result.query}&rdquo;</div>
        <div className="chart-empty">No product matched this name or SKU.</div>
      </div>
    );
  }

  if (result.status === "ambiguous") {
    return (
      <div className="tool-card product-listing-card">
        <div className="tool-card-title">Listing lookup &mdash; &ldquo;{result.query}&rdquo;</div>
        <div className="tool-card-notice">Multiple products matched &mdash; which one did you mean?</div>
        <table className="anomalies-table">
          <thead>
            <tr>
              <th>Product</th>
              <th>SKU</th>
            </tr>
          </thead>
          <tbody>
            {result.matches.map((m) => (
              <tr key={m.sku ?? m.name}>
                <td>{m.name}</td>
                <td className="anomalies-table-change">{m.sku ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="tool-card product-listing-card">
      <div className="tool-card-title">{result.product.name} &mdash; channel listing</div>
      <table className="anomalies-table">
        <thead>
          <tr>
            <th>Outlet</th>
            <th>Uber</th>
            <th>PickMe</th>
            <th>Price</th>
          </tr>
        </thead>
        <tbody>
          {result.listings.map((l) => (
            <tr key={l.outlet}>
              <td>{l.outlet}</td>
              <td><AvailabilityDot ok={l.available_in_uber} /></td>
              <td><AvailabilityDot ok={l.available_in_pickme} /></td>
              <td className="anomalies-table-change">{l.price === null ? "—" : formatCurrency(l.price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {result.not_listed_at.length > 0 && (
        <div className="tool-card-notice">Not listed at all at: {result.not_listed_at.join(", ")}</div>
      )}
    </div>
  );
}
