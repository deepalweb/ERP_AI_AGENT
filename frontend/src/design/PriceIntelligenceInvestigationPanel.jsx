import { formatNumber } from "../format";

export default function PriceIntelligenceInvestigationPanel({ latest }) {
  const rows = latest?.po_price_deviation_rows ?? [];

  return (
    <div className="space-y-4 text-sm">
      <div>
        <p className="font-semibold text-foreground-strong">{latest?.title ?? "Purchase price deviation"}</p>
        <p className="mt-1 text-foreground-subtle">{latest?.impact?.detail ?? "No detail is available."}</p>
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="bg-surface-sunken text-foreground-subtle">
              <tr>
                {["Product", "Supplier", "Previous price", "Latest price", "Change", "Baseline purchases", "GRN-updated", "Date"].map((heading) => (
                  <th key={heading} className="px-3 py-2 font-medium">{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.product_id}-${index}`} className="border-t border-border text-foreground">
                  <td className="px-3 py-2">{row.product_name}</td>
                  <td className="px-3 py-2">{row.supplier_name ?? "—"}</td>
                  <td className="px-3 py-2 tabular-nums">Rs {formatNumber(row.previous_price)}</td>
                  <td className="px-3 py-2 tabular-nums">Rs {formatNumber(row.latest_price)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-medium ${row.pct_change > 0 ? "text-critical" : "text-good"}`}>
                    {row.pct_change > 0 ? "+" : ""}{row.pct_change}%
                  </td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(row.baseline_purchases)}</td>
                  <td className="px-3 py-2">{row.grn_price_updated ? "Yes" : "No"}</td>
                  <td className="px-3 py-2">{row.latest_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-foreground-subtle">
        Each product's deviation is measured against its own purchase history, not a flat threshold — a
        naturally volatile product (like fresh produce) needs a much larger move to qualify than a
        normally-stable one. This does not establish whether a change is a genuine supplier price move, a
        pack-size/UOM change, or a data-entry correction.
      </p>
    </div>
  );
}
