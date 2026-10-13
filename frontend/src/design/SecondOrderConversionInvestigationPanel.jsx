import TrendChart from "../charts/TrendChart";
import { formatByUnit, formatPct, monthLabel } from "../format";

const PERSISTENCE_LABEL = {
  consistent_declining: "Declining", consistent_improving: "Improving",
  volatile: "Volatile, no clear direction", stable: "Stable",
};

/** Customer module review, 2026-10-13: second-order-conversion-decline
 * investigation panel. Gated by the caller on metric_id starting with
 * "second_order_conversion_decline" -- mirrors SupplierInvestigationPanel's
 * structure exactly (same generic metric/trend/confirm/persistence shape,
 * see customer_predictions.build_second_order_decline_finding), just with
 * no breakdown dimension (this finding has none) and the cohort-specific
 * root_cause/impact text shown instead. */
export default function SecondOrderConversionInvestigationPanel({ latest }) {
  if (!latest) return null;
  const metric = latest.metric;
  const trend = (latest.trend ?? []).filter((m) => !m.is_partial);
  const persistence = latest.persistence;

  return (
    <div>
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
          <p className="mt-1 text-xs text-foreground-subtle">Baseline is the average across prior mature acquisition cohorts.</p>
        </div>
        {persistence && (
          <div>
            <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Persistence</span>
            <p className="mt-1 text-sm text-foreground">
              {PERSISTENCE_LABEL[persistence.pattern] || persistence.pattern}
              {persistence.consecutive_periods != null && ` -- ${persistence.consecutive_periods} consecutive cohort${persistence.consecutive_periods === 1 ? "" : "s"}`}
            </p>
            {persistence.period_range && <p className="text-xs text-foreground-subtle">{persistence.period_range}</p>}
          </div>
        )}
      </div>

      {latest.root_cause?.summary && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">What we know</span>
          <p className="mt-1 text-sm text-foreground">{latest.root_cause.summary}</p>
        </div>
      )}
    </div>
  );
}
