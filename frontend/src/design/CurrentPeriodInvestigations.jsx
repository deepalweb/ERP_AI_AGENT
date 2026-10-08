import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchCurrentPeriod } from "../api";
import { formatByUnit, formatPct } from "../format";
import Badge from "./Badge";
import Button from "./Button";
import CurrentPeriodChart from "../charts/CurrentPeriodChart";

// Preserve the database's UTC cutoff rather than silently changing the date in
// the browser's timezone. No wall-clock 'today' is substituted for source time.
function stamp(value) {
  return value ? `${value.replace("T", " ").replace(/(?:Z|\+00:00)$/, "")} UTC` : "Unavailable";
}

const CATEGORY = {
  critical: ["Needs attention", "critical"], opportunity: ["Positive movement", "good"],
  normal: ["Within thresholds", "neutral"],
};

export default function CurrentPeriodInvestigations({ metricId, metricIds, domain }) {
  const client = useQueryClient();
  const [refreshError, setRefreshError] = useState(null);
  const query = useQuery({ queryKey: ["currentPeriod"], queryFn: ({ signal }) => fetchCurrentPeriod({ signal }), staleTime: 300000, retry: false });
  const items = (query.data?.items ?? []).filter(
    (item) =>
      (!metricId || item.metric_id === metricId) &&
      (!metricIds || metricIds.includes(item.metric_id)) &&
      (!domain || item.domain === domain)
  );
  async function refresh() {
    setRefreshError(null);
    try {
      await client.fetchQuery({ queryKey: ["currentPeriod"], queryFn: ({ signal }) => fetchCurrentPeriod({ refresh: true, signal }), staleTime: 0 });
    } catch { setRefreshError("Could not refresh current-period data. Any values below are from the earlier check."); }
  }
  return (
    <section className="panel mt-5 min-w-0 p-4 sm:p-6" aria-labelledby="current-period-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2"><h2 id="current-period-heading" className="font-display text-lg font-semibold text-foreground-strong">Current period investigations</h2><Badge tone="accent">Provisional</Badge></div>
          <p className="mt-2 max-w-3xl text-sm text-foreground-subtle">Includes the latest available month, even while it is in progress. Each metric is compared with the same elapsed duration in earlier months, using its own source cutoff.</p>
        </div>
        <Button size="sm" disabled={query.isFetching} onClick={refresh}>{query.isFetching ? "Checking sources…" : "Check latest data"}</Button>
      </div>
      {query.data?.checked_at && <p className="mt-2 text-xs text-foreground-subtle">Last checked: {stamp(query.data.checked_at)} · Cached for up to 5 minutes. Source dates appear on each result.</p>}
      {query.isPending && <p className="mt-4 text-sm" role="status">Measuring current-period activity across all registered metrics…</p>}
      {(query.isError || refreshError) && <p className="mt-4 text-sm text-critical" role="alert">{refreshError || "The current-period check could not finish. Retry when the reporting database is reachable."}</p>}
      {!query.isPending && !query.isError && items.length === 0 && <p className="mt-4 text-sm text-foreground-subtle">No current-period measurements are available for this selection.</p>}
      <div className="mt-4 grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        {items.map((item) => {
          const [label, tone] = CATEGORY[item.category] ?? [item.status === "unavailable" ? "Source unavailable" : "Comparison unavailable", "warning"];
          const confirm = item.confirm ?? {};
          return (
            <article key={item.metric_id} className="min-w-0 rounded-lg border border-border p-4" aria-label={item.metric.label}>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                  <div className="min-w-0 flex-1"><h3 className="text-sm font-semibold text-foreground-strong">{item.metric.label}</h3><p className="mt-1 text-xs text-foreground-subtle">{item.scope} · Source data through {stamp(item.source_data_through)}</p></div>
                  <div className="flex flex-wrap items-center gap-3"><strong className="tabular-nums">{confirm.current == null ? "Unavailable" : formatByUnit(confirm.current, confirm.unit)}</strong><Badge tone={tone}>{label}</Badge>{item.freshness === "stale" && <Badge tone="warning">Source is stale</Badge>}{item.freshness === "unknown" && <Badge tone="warning">Freshness unknown</Badge>}</div>
                </div>
              <CurrentPeriodChart item={item} />
              {!Number.isFinite(confirm.current) && <p className="mt-3 text-xs text-foreground-subtle">Chart unavailable until this source returns a measurement.</p>}
              <details className="mt-3 border-t border-border pt-3">
                <summary className="cursor-pointer text-xs font-medium text-accent">View dates and comparison</summary>
              <div className="mt-3 space-y-3 border-l-2 border-accent-border pl-3 text-sm">
                {item.reason && <p className="text-foreground-subtle">{item.reason}</p>}
                {item.comparison && <>
                  <p><strong>Period measured:</strong> {stamp(item.comparison.start)} through {stamp(item.source_data_through)}.</p>
                  <p><strong>Comparable baseline:</strong> {confirm.baseline == null ? "Not enough comparable data" : formatByUnit(confirm.baseline, confirm.unit)}{confirm.pct_change != null ? ` · ${formatPct(confirm.pct_change)} relative change` : ""}.</p>
                  {confirm.unit === "percent" && confirm.current != null && confirm.baseline != null && <p>Rate difference: {(confirm.current - confirm.baseline).toFixed(2)} percentage points.</p>}
                  <p className="text-foreground-subtle">{item.comparison.method}</p>
                  {item.comparison.excluded_short_months?.length > 0 && <p className="text-foreground-subtle">Too short for a matching duration: {item.comparison.excluded_short_months.join(", ")}.</p>}
                  <details><summary className="cursor-pointer text-accent">Baseline date windows ({item.comparison.baseline_windows.length})</summary><ul className="mt-2 space-y-1 text-xs text-foreground-subtle">{item.comparison.baseline_windows.map((window) => <li key={window.start}>{stamp(window.start)} to {stamp(window.end_exclusive)} (end exclusive)</li>)}</ul></details>
                </>}
                {item.cohort_note && <p className="text-foreground-subtle">{item.cohort_note}</p>}
                {item.metric.notes && <details><summary className="cursor-pointer text-accent">Metric definition and limitations</summary><p className="mt-2 text-xs text-foreground-subtle">{item.metric.notes}</p></details>}
                <p className="text-xs text-foreground-subtle">An early signal, not a completed-month verdict. It does not automatically resolve a case or establish that an action worked.</p>
              </div>
              </details>
            </article>
          );
        })}
      </div>
    </section>
  );
}
