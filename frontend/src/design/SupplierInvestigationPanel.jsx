import RankedBarChart from "../charts/RankedBarChart";
import TrendChart from "../charts/TrendChart";
import { formatByUnit, formatPct, monthLabel } from "../format";

const PERSISTENCE_LABEL = {
  consistent_declining: "Declining", consistent_improving: "Improving",
  volatile: "Volatile, no clear direction", stable: "Stable",
};

/** Frontend Intelligence Integration, Phase 4: supplier investigation
 * panel. Gated by the caller on domain === "supplier" -- unlike Revenue/
 * Inventory/StockCount, this is NOT a special finding shape: supplier_
 * fill_rate/purchase_price_variance/supplier_concentration flow through
 * the exact same generic monthly trend pipeline as revenue/gross_margin
 * (investigation.build_investigation) -- see case_engine's own persisted
 * snapshot for confirm/trend/persistence/breakdowns/metric, all already
 * real, none recomputed here. Only ever shows the "supplier" breakdown
 * dimension -- no outlet/product effects, because these metrics are
 * business-wide, not per-outlet (see metric.notes below) and carry no
 * such breakdown. */
export default function SupplierInvestigationPanel({ latest }) {
  if (!latest) return null;
  const metric = latest.metric;
  const trend = (latest.trend ?? []).filter((m) => !m.is_partial);
  const supplierBreakdown = (latest.breakdowns ?? []).find((b) => b.dimension === "supplier");
  const persistence = latest.persistence;

  return (
    <div>
      {metric?.notes && (
        <p className="text-xs text-foreground-subtle">{metric.notes}</p>
      )}

      {trend.length > 1 && (
        <div className="mt-3">
          <TrendChart
            data={trend}
            formatValue={(v) => formatByUnit(v, metric?.unit)}
            formatX={monthLabel}
            stroke="var(--accent)"
            gridStroke="var(--border)"
            axisStroke="var(--text-subtle)"
          />
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-3 border-t border-border pt-3 sm:grid-cols-3">
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Current vs. baseline</span>
          <p className="mt-1 text-sm text-foreground">
            {formatByUnit(latest.confirm?.current, latest.confirm?.unit)}
            {latest.confirm?.baseline != null && ` vs. ${formatByUnit(latest.confirm.baseline, latest.confirm.unit)} baseline`}
            {latest.confirm?.pct_change != null && ` (${formatPct(latest.confirm.pct_change)})`}
          </p>
        </div>
        {persistence && (
          <div>
            <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Persistence</span>
            <p className="mt-1 text-sm text-foreground">
              {PERSISTENCE_LABEL[persistence.pattern] || persistence.pattern}
              {persistence.consecutive_periods != null && ` -- ${persistence.consecutive_periods} consecutive month${persistence.consecutive_periods === 1 ? "" : "s"}`}
            </p>
            {persistence.period_range && <p className="text-xs text-foreground-subtle">{persistence.period_range}</p>}
          </div>
        )}
      </div>

      {supplierBreakdown?.top_contributors?.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">By supplier</span>
          <div className="mt-2">
            <RankedBarChart
              data={supplierBreakdown.top_contributors}
              valueKey="current"
              formatValue={(v) => formatByUnit(v, metric?.unit)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
