import { formatNumber } from "../format";

export default function StockReconciliationInvestigationPanel({ latest }) {
  const episodes = latest?.stock_move_missing_episodes ?? [];

  return (
    <div className="space-y-4 text-sm">
      <div>
        <p className="font-semibold text-foreground-strong">{latest?.title ?? "Missing stock deduction"}</p>
        <p className="mt-1 text-foreground-subtle">{latest?.impact?.detail ?? "No detail is available."}</p>
      </div>
      {episodes.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="bg-surface-sunken text-foreground-subtle">
              <tr>
                {["Window", "Orders", "Outlets", "Products involved"].map((heading) => (
                  <th key={heading} className="px-3 py-2 font-medium">{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {episodes.map((ep, index) => (
                <tr key={`${ep.start}-${index}`} className="border-t border-border text-foreground">
                  <td className="px-3 py-2">{ep.start} – {ep.end}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(ep.order_count)}</td>
                  <td className="px-3 py-2">
                    {Object.entries(ep.outlets ?? {}).map(([name, count]) => (
                      <div key={name}>{name}: {formatNumber(count)}</div>
                    ))}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(ep.distinct_products)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-foreground-subtle">
        Each episode is a cluster of confirmed orders, in a short time window, whose physical-product lines
        never received a stock deduction — their outlet's recorded stock is overstated relative to what was
        actually sold. This does not identify what specifically failed (a sync job, a picking batch, or
        another integration step) — only that it happened and when.
      </p>
    </div>
  );
}
