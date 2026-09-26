import RankedBarChart from "../charts/RankedBarChart";
import { formatNumber, formatPct } from "../format";

/** Frontend Intelligence Integration, Phase 4: incident investigation
 * panel (store x SKU x hour cancellation spikes -- mcp_server.tools.
 * incident_engine). Gated by the caller on metric_id starting with
 * "incident_" -- each incident's metric_id is synthesized per store/
 * product/hour, never a fixed string, so it can't be matched like
 * stuck_stock_count/revenue/top_seller_stock are.
 *
 * Evidence checks (time-locked pattern, product-specific correlation,
 * recurrence across days) are already rendered generically by CaseDetail's
 * "What the evidence establishes" section -- not repeated here. This panel
 * is the one thing that section can't show: the actual hour-of-day shape
 * of the spike. */
export default function IncidentInvestigationPanel({ latest }) {
  if (!latest) return null;
  const hourBreakdown = (latest.breakdowns ?? []).find((b) => b.dimension === "hour");
  const persistence = latest.persistence;

  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Store</span>
          <p className="mt-1 text-sm text-foreground-strong">{latest.store ?? "—"}</p>
        </div>
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Product</span>
          <p className="mt-1 text-sm text-foreground-strong">{latest.product ?? "—"}</p>
        </div>
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Hour window</span>
          <p className="mt-1 text-sm text-foreground-strong">{latest.hour ?? "—"}</p>
        </div>
      </div>

      <p className="mt-3 text-sm text-foreground">
        {formatPct(latest.confirm?.pct_change)} vs. this store/product's own normal hourly cancellation rate
        {latest.confirm?.baseline != null && ` (${formatNumber(latest.confirm.current)}% now vs. ${formatNumber(latest.confirm.baseline)}% normally)`}.
      </p>

      {persistence && (
        <p className="mt-1 text-xs text-foreground-subtle">
          Appeared on {persistence.days_appeared} of the last {persistence.days_observed} days this store was trading.
        </p>
      )}

      {hourBreakdown?.top_contributors?.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
            Cancellation rate by hour, this store/product
          </span>
          <p className="mt-1 text-xs text-foreground-subtle">
            Hours ranked by how far each deviates from this store/product's own normal rate -- not every hour of the day.
          </p>
          <div className="mt-2">
            <RankedBarChart data={hourBreakdown.top_contributors} nameKey="name" valueKey="current" formatValue={(v) => `${v}%`} />
          </div>
        </div>
      )}
    </div>
  );
}
