import Badge from "./Badge";
import { formatDate, formatNumber, stripSkuSuffix } from "../format";

/** Business-specific investigation panel for an inventory stock-out case
 * (Celeste Investigate Phase 2's flagship workflow -- see state_findings.
 * _top_seller_stock_finding). Reads structured fields already carried
 * through on the case's breakdowns (name/sku/current/negative_stock/
 * days_of_cover) rather than parsing recommendation prose or the "name"
 * string for a SKU -- see plainLanguage.js's metricSnapshotText/
 * negativeStockText for the same "structured only" discipline applied to
 * the surrounding text.
 *
 * Gated by the caller on c.metric_id -- this only renders for the one
 * finding shape it actually understands, matching "other investigation
 * types stay out of scope for now."
 *
 * Outlet-level breakdown is genuinely not available: the SQL behind this
 * metric (backend/app/services/stock_alerts.py TOP_PRODUCTS_STOCK_SQL)
 * sums current_stock company-wide, with no per-store join at all -- unlike
 * get_stock_alerts()'s separate, differently-scoped query, which IS
 * per-outlet but covers a different product population. Cross-referencing
 * the two would be guessing at a relationship neither query actually
 * establishes, so this says plainly that outlet data isn't available
 * rather than fabricating or approximating it.
 *
 * Metric Truthfulness Correction, Task 1: the same query also never
 * subtracts stock_quant.reserved_quantity, so "recorded stock" here can
 * overstate what's actually free to sell (some of it may already be
 * allocated to a pending outgoing transfer) -- stated explicitly below
 * rather than left implicit, and "Out of stock" (an availability claim)
 * is now "Zero recorded stock" (a measurement claim) on the row badge. */
export default function InventoryInvestigationPanel({ latest, asOfDate }) {
  const breakdowns = latest?.breakdowns ?? [];
  const outOfStock = breakdowns[0]?.top_contributors ?? [];
  const otherAtRisk = breakdowns[1]?.top_contributors ?? [];

  if (outOfStock.length === 0) return null;

  return (
    <div>
      <p className="text-xs text-foreground-subtle">
        Source snapshot: stock levels as of {formatDate(asOfDate)}. Recorded on-hand quantity, measured company-wide
        across monitored internal stock locations -- not per outlet, and not reduced for stock already reserved
        against a pending transfer. A zero or negative reading here does not by itself confirm the product is
        unavailable to sell at every outlet.
      </p>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-foreground-subtle">
              <th className="pb-2 pr-3 font-medium">Product</th>
              <th className="pb-2 pr-3 font-medium">SKU</th>
              <th className="pb-2 pr-3 font-medium">Recorded stock</th>
              <th className="pb-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {outOfStock.map((p) => (
              <tr key={p.sku ?? p.name} className="border-t border-border">
                <td className="py-1.5 pr-3 text-foreground-strong">{stripSkuSuffix(p.name)}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground-subtle">{p.sku ?? "n/a"}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground">{formatNumber(p.current)}</td>
                <td className="py-1.5">
                  {p.negative_stock ? (
                    <Badge tone="critical">Negative on-hand -- data issue</Badge>
                  ) : (
                    <Badge tone="critical">Zero recorded stock</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {otherAtRisk.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
            Other at-risk top sellers ({otherAtRisk.length})
          </span>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-foreground-subtle">
                  <th className="pb-2 pr-3 font-medium">Product</th>
                  <th className="pb-2 pr-3 font-medium">SKU</th>
                  <th className="pb-2 font-medium">Days of cover</th>
                </tr>
              </thead>
              <tbody>
                {otherAtRisk.map((p) => (
                  <tr key={p.sku ?? p.name} className="border-t border-border">
                    <td className="py-1.5 pr-3 text-foreground">{stripSkuSuffix(p.name)}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-foreground-subtle">{p.sku ?? "n/a"}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-warning">{p.days_of_cover ?? "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
