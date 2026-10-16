import RankedBarChart from "../charts/RankedBarChart";
import { MetricSummaryCard } from "./CaseVisuals";
import { formatCurrency, formatDate, formatNumber } from "../format";

// formatPercent/formatByUnit both fabricate "0.00%" for null -- wrong here,
// where null means "no priceable lines, rate unknown" (see
// stock_counts.get_completed_stock_count_variance's own _variance_rate
// docstring note), never a healthy 0%.
const formatRate = (pct) => (pct == null ? "Unknown" : `${pct}%`);

/** Frontend Intelligence Integration, Phase 4: completed stock-count
 * variance panel. Gated by the caller on metric_id === "completed_stock_
 * count_variance". Distinct from StockCountInvestigationPanel (stuck/
 * unfinished counts, a phantom estimate) -- this is REAL, physically-
 * verified variance from counts that actually finished.
 *
 * Deliberately doesn't claim "shrinkage" or "loss" anywhere -- gross
 * (absolute, always positive) and net (signed, can be positive or
 * negative) are two different real numbers computed here only as a sum
 * of the per-session values already on the case, never a new valuation.
 * There is no per-session responsible-user or valuation-basis field in
 * this data (unlike the stuck-count panel) -- not shown, not guessed. */
export default function CompletedVarianceInvestigationPanel({ latest }) {
  if (!latest) return null;
  const sessions = latest.sessions ?? [];
  if (sessions.length === 0) return null;

  const netTotal = sessions.reduce((sum, s) => sum + (s.net_variance_value_rs || 0), 0);
  const positiveTotal = sessions.reduce((sum, s) => sum + Math.max(0, s.net_variance_value_rs || 0), 0);
  const negativeTotal = sessions.reduce((sum, s) => sum + Math.min(0, s.net_variance_value_rs || 0), 0);

  const outletBreakdown = (latest.breakdowns ?? []).find((b) => b.dimension === "store");
  const productTypeBreakdown = (latest.breakdowns ?? []).find((b) => b.dimension === "product_type");

  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-5">
        <MetricSummaryCard label="Completed sessions" value={formatNumber(sessions.length)} />
        <MetricSummaryCard label="Gross absolute discrepancy" value={formatCurrency(latest.impact?.financial_impact_rs)} caption="Sum of every line's variance, ignoring direction" />
        <MetricSummaryCard label="Net variance" value={formatCurrency(netTotal)} caption="Overs and unders offsetting each other" />
        <MetricSummaryCard label="Overs vs. unders" value={`${formatCurrency(positiveTotal)} / ${formatCurrency(negativeTotal)}`} />
        <MetricSummaryCard
          label="Variance rate"
          value={formatRate(latest.variance_rate_pct)}
          caption="Gross variance as a share of counted inventory value -- comparable across outlets, unlike raw Rs"
        />
      </div>
      <p className="mt-2 text-xs text-foreground-subtle">
        Gross and net are different real numbers, not a typo -- a store with Rs 50,000 counted over on one line and Rs
        50,000 under on another shows Rs 100,000 gross, Rs 0 net. Neither figure is a shrinkage or loss estimate by
        itself; see "What the evidence establishes" below for whether picking-loss or damage data overlaps. The
        variance rate can read above 100% when discrepancies concentrate on lines counted as zero stock (onhand said
        there should have been stock, the physical count found none) -- those lines contribute nothing to counted
        value but can carry a large variance.
      </p>

      {outletBreakdown?.top_contributors?.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">By outlet (gross)</span>
          <div className="mt-2">
            <RankedBarChart data={outletBreakdown.top_contributors} valueKey="current" formatValue={formatCurrency} />
          </div>
        </div>
      )}

      {productTypeBreakdown?.top_contributors?.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">By product type (gross)</span>
          <div className="mt-2">
            <RankedBarChart data={productTypeBreakdown.top_contributors} valueKey="current" formatValue={formatCurrency} />
          </div>
        </div>
      )}

      <div className="mt-4 overflow-x-auto border-t border-border pt-3">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Sessions ({sessions.length})</span>
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="text-left text-foreground-subtle">
              <th className="pb-2 pr-3 font-medium">Session</th>
              <th className="pb-2 pr-3 font-medium">Outlet</th>
              <th className="pb-2 pr-3 font-medium">Completed</th>
              <th className="pb-2 pr-3 font-medium">Lines</th>
              <th className="pb-2 pr-3 font-medium">Net variance</th>
              <th className="pb-2 pr-3 font-medium">Gross variance</th>
              <th className="pb-2 font-medium">Rate</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id} className="border-t border-border">
                <td className="py-1.5 pr-3 text-foreground-strong">{s.name}</td>
                <td className="py-1.5 pr-3 text-foreground-subtle">{s.outlet}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground-subtle">{s.completed_at ? formatDate(s.completed_at.slice(0, 10)) : "—"}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground">{formatNumber(s.lines)}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground">{formatCurrency(s.net_variance_value_rs)}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground">{formatCurrency(s.gross_abs_variance_value_rs)}</td>
                <td className="py-1.5 tabular-nums text-foreground-subtle">{formatRate(s.variance_rate_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-foreground-subtle">
          No responsible-user or valuation-basis field is recorded for completed counts in this data (unlike unfinished
          counts) -- not shown here rather than guessed.
        </p>
      </div>
    </div>
  );
}
