import TrendChart from "../charts/TrendChart";
import Badge from "./Badge";
import { formatByUnit, formatConfirmPeriod, formatNumber, moveToneClass } from "../format";

function change(value, unit) {
  if (value == null) return "Not recorded";
  return `${value > 0 ? "+" : ""}${unit === "percent" ? `${Number(value).toFixed(2)} pp` : formatByUnit(value, unit)}`;
}

// Same labels/tones as CurrentPeriodInvestigations.jsx's own CATEGORY map --
// this is the same category value, just surfaced a second place.
const CURRENT_PERIOD_CATEGORY = {
  critical: ["Needs attention", "critical"], opportunity: ["Positive movement", "good"],
  normal: ["Within thresholds", "neutral"],
};

/** Read only the saved investigation: comparison windows and units are not
 * interchangeable, particularly for rates and channel composition.
 * currentPeriodItem (optional) is the matching row from the live
 * current-period check (design/CurrentPeriodInvestigations.jsx) -- passed
 * through so the excluded-month caption below can point at its own early
 * read instead of leaving the reader to separately notice that panel
 * elsewhere on the page already has a verdict for the very month this
 * chart just excluded. */
export default function InvestigationResults({ latest, showTrend = false, currentPeriodItem = null }) {
  if (!latest) return null;
  const confirm = latest.confirm ?? {};
  const unit = confirm.unit ?? latest.metric?.unit;
  const trend = latest.trend ?? [];
  const complete = trend.filter((point) => !point.is_partial);
  const partial = trend.filter((point) => point.is_partial);
  const breakdowns = (latest.breakdowns ?? []).filter((b) => b.top_contributors?.length);
  const stages = latest.how?.stages ?? [];
  const hasComparison = confirm.baseline != null && confirm.current != null;
  if (!hasComparison && !breakdowns.length && !stages.length && !trend.length) return null;

  return (
    <div className="space-y-5">
      {hasComparison && (
        <div>
          <p className="text-xs text-foreground-subtle">Saved comparison · {formatConfirmPeriod(confirm.period)}</p>
          <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[["Baseline", formatByUnit(confirm.baseline, unit)], ["Observed", formatByUnit(confirm.current, unit)],
              ["Difference", change(confirm.current - confirm.baseline, unit)]].map(([label, value]) => (
              <div key={label} className="border-l-2 border-border pl-3">
                <dt className="text-xs text-foreground-subtle">{label}</dt>
                <dd className={`mt-1 font-display text-lg font-semibold tabular-nums ${label === "Difference" ? moveToneClass(confirm.current - confirm.baseline, latest.metric?.direction) : "text-foreground-strong"}`}>{value}</dd>
              </div>
            ))}
          </dl>
          {unit === "percent" && <p className="mt-2 text-xs text-foreground-subtle">pp means percentage points: the absolute difference between two rates.</p>}
        </div>
      )}
      {latest.metric?.notes && <p className="text-sm text-foreground-subtle">{latest.metric.notes}</p>}
      {showTrend && complete.length > 1 && (
        <div>
          <h3 className="text-sm font-semibold text-foreground-strong">Measured trend</h3>
          <p className="mt-1 text-xs text-foreground-subtle">Complete measurement periods from this investigation. Partial periods are excluded.</p>
          <TrendChart data={complete} formatX={formatConfirmPeriod} formatValue={(v) => formatByUnit(v, unit)} stroke="var(--accent)" gridStroke="var(--border)" axisStroke="var(--text-subtle)" />
        </div>
      )}
      {partial.length > 0 && (
        <div className="text-xs text-foreground-subtle">
          <p>Still in progress, excluded from the trend comparison: {partial.map((p) => `${formatConfirmPeriod(p.month)} (${formatByUnit(p.value, unit)})`).join("; ")}.</p>
          {currentPeriodItem?.status === "measured" && CURRENT_PERIOD_CATEGORY[currentPeriodItem.category] && (
            <p className="mt-1 flex flex-wrap items-center gap-1.5">
              Early read on the excluded month:
              <Badge tone={CURRENT_PERIOD_CATEGORY[currentPeriodItem.category][1]}>{CURRENT_PERIOD_CATEGORY[currentPeriodItem.category][0]}</Badge>
              — see the "Current period investigations" panel on this page for detail. This is provisional, not a completed-month verdict.
            </p>
          )}
        </div>
      )}
      {breakdowns.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-foreground-strong">Affected segments</h3>
          <p className="mt-1 text-xs text-foreground-subtle">Ranked segments returned by the investigation. These comparisons use the preceding period, which can differ from the headline baseline. Concentration does not establish a cause.</p>
          <div className="mt-3 space-y-2">
            {breakdowns.map((b, i) => {
              const composition = b.mode === "composition";
              const rowUnit = b.mode === "rate" ? "percent" : unit;
              const deltaUnit = composition ? "percent" : rowUnit;
              const baselinePeriod = b.baseline_month ? formatConfirmPeriod(b.baseline_month) : b.baseline_period_label;
              return (
                <details key={`${b.dimension}-${i}`} open={i === 0} className="rounded-lg border border-border p-3">
                  <summary className="cursor-pointer text-sm font-medium text-foreground-strong">{b.dimension === "store" ? "Celeste outlets" : b.label || b.dimension} · {b.top_contributors.length} {b.top_contributors.length === 1 ? "segment" : "segments"}</summary>
                  <p className="my-2 text-xs text-foreground-subtle">{baselinePeriod || "Comparison period not recorded"} → {formatConfirmPeriod(confirm.period)}{composition ? " · Change is in share of total, not raw volume." : ""}</p>
                  <p className="mb-2 text-xs text-foreground-subtle sm:hidden">Scroll the table sideways to see all values.</p>
                  <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`${b.dimension} comparison table`}>
                    <table className="w-full min-w-[460px] text-left text-xs">
                      <caption className="sr-only">{b.label || b.dimension}: previous and current values</caption>
                      <thead className="text-foreground-subtle"><tr>{["Segment", composition ? "Previous share" : "Previous", composition ? "Current share" : "Current", "Change", ...(b.top_contributors.some((r) => r.current_volume != null) ? ["Sample size"] : [])].map((label) => <th key={label} scope="col" className="border-b border-border px-2 py-2 font-medium">{label}</th>)}</tr></thead>
                      <tbody>{b.top_contributors.map((row, index) => (
                        <tr key={`${row.name}-${index}`} className="border-b border-border last:border-0">
                          <th scope="row" className="max-w-64 px-2 py-3 font-medium text-foreground">{row.name}</th>
                          <td className="px-2 py-3 tabular-nums">{formatByUnit(composition ? row.baseline_share_pct : row.baseline, composition ? "percent" : rowUnit)}</td>
                          <td className="px-2 py-3 tabular-nums">{formatByUnit(composition ? row.current_share_pct : row.current, composition ? "percent" : rowUnit)}</td>
                          <td className="px-2 py-3 tabular-nums">{change(row.delta, deltaUnit)}</td>
                          {b.top_contributors.some((r) => r.current_volume != null) && <td className="px-2 py-3 tabular-nums">{row.current_volume == null ? "Not recorded" : formatNumber(row.current_volume)}</td>}
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                </details>
              );
            })}
          </div>
        </div>
      )}
      {stages.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-foreground-strong">Where fulfillment time accumulates</h3>
          <p className="mt-1 text-xs text-foreground-subtle">Recorded stage timings at flagged outlets compared with other outlets in this period. This does not measure arrival at the customer.</p>
          <div className="mt-3 space-y-2">{stages.map((stage) => (
            <div key={stage.name} className="flex flex-wrap justify-between gap-2 border-b border-border pb-2 text-sm">
              <span>{stage.name}</span><span className="tabular-nums">Flagged: {stage.flagged_minutes ?? "n/a"} min · Other: {stage.baseline_minutes ?? "n/a"} min</span>
            </div>
          ))}</div>
        </div>
      )}
      {!breakdowns.length && hasComparison && <p className="text-sm text-foreground-subtle">No segment breakdown was saved with this result. The total change alone does not show which outlets, products or customers contributed.</p>}
    </div>
  );
}
