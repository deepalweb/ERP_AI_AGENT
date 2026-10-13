import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchCustomerLifecycleSegment, fetchCustomerLifecycleTransitions, fetchSecondOrderConversion } from "../api";
import Card from "./Card";
import CohortConversionChart from "../charts/CohortConversionChart";
import { formatCurrency, formatDate, formatNumber } from "../format";

const SECOND_ORDER_WINDOWS = [30, 60, 90];
function formatMonthLabel(monthKey) {
  const [year, month] = monthKey.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

const TIERS = [
  { id: "high", label: "High value", color: "var(--accent)" },
  { id: "mid", label: "Mid value", color: "var(--series-2)" },
  { id: "low", label: "Low value", color: "var(--foreground)" },
];
const count = (value) => Number.isFinite(value) ? formatNumber(value) : "Unavailable";
const share = (value, total) => Number.isFinite(value) && total > 0 ? value / total * 100 : 0;

export default function CustomerGrowthOverview({ summary, lifecycle, loading, onRetry, onViewCustomers }) {
  const [selectedStage, setSelectedStage] = useState("lapsing");
  // null = no cell picked yet (row-level summary only, the original
  // behavior). Set together with selectedStage when a specific tier CELL
  // is clicked, so the segment query below has both axes of the cell.
  const [selectedTier, setSelectedTier] = useState(null);
  const [segmentOffset, setSegmentOffset] = useState(0);
  const categories = lifecycle?.categories ?? [];
  const cross = lifecycle?.value_by_category ?? [];
  const tiers = lifecycle?.value_tiers ?? [];
  const selected = categories.find((row) => row.id === selectedStage);
  const selectedMix = cross.find((row) => row.id === selectedStage);
  const lapsing = cross.find((row) => row.id === "lapsing");
  const SEGMENT_PAGE_SIZE = 10;
  // One fetch per cell click, not per page: the backend's cost is the
  // classification pass itself (8-10s), not the row count, so offset is
  // NOT in the query key -- re-querying per page would re-pay that cost
  // for no reason. segmentOffset instead slices the already-fetched,
  // server-capped (top 200 by revenue) list client-side below.
  const segmentQuery = useQuery({
    queryKey: ["customerLifecycleSegment", selectedStage, selectedTier],
    queryFn: () => fetchCustomerLifecycleSegment(selectedStage, selectedTier),
    enabled: Boolean(selectedStage && selectedTier),
  });
  const segmentPage = (segmentQuery.data?.customers ?? []).slice(segmentOffset, segmentOffset + SEGMENT_PAGE_SIZE);
  const segmentShown = segmentQuery.data?.customers?.length ?? 0;
  const secondOrder = useQuery({ queryKey: ["secondOrderConversion"], queryFn: fetchSecondOrderConversion });
  const transitions = useQuery({ queryKey: ["customerLifecycleTransitions"], queryFn: fetchCustomerLifecycleTransitions });
  function selectCell(stageId, tierId) {
    setSelectedStage(stageId);
    setSelectedTier((current) => (current === tierId && selectedStage === stageId ? null : tierId));
    setSegmentOffset(0);
  }
  const maxCell = Math.max(1, ...cross.flatMap((row) => TIERS.map((tier) => row[tier.id] ?? 0)));
  const revenueTotal = tiers.reduce((sum, row) => sum + (row.revenue ?? 0), 0);
  const customerTotal = tiers.reduce((sum, row) => sum + (row.count ?? 0), 0);
  const timing = summary?.timing_distribution ?? [];
  const maxTiming = Math.max(1, ...timing.map((row) => row.count ?? 0));

  return (
    <section aria-label="Customer growth opportunities" className="mt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-accent">Where to focus</p>
          <h2 className="mt-1 text-xl font-semibold text-foreground-strong">Build the next purchase</h2>
          <p className="mt-1 text-sm text-foreground/60">Retention audiences and purchase patterns from your available history.</p>
        </div>
        <button type="button" onClick={onRetry} disabled={loading} className="rounded-lg border border-border px-3 py-2 text-xs text-foreground-strong hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">Reload saved insights</button>
      </div>
      <p className="mt-2 text-xs text-foreground/60">
        Timing data through {summary?.as_of_date ? formatDate(summary.as_of_date) : "unavailable"} · Lifecycle through {lifecycle?.as_of_date ? formatDate(lifecycle.as_of_date) : "unavailable"}.
        {" "}Saved scan results; reloading does not run a new analysis.
      </p>
      {loading && <p role="status" className="mt-3 text-sm text-foreground/60">Loading customer opportunities…</p>}
      {!loading && (!summary || !lifecycle) && <p role="status" className="mt-3 text-sm text-warning">Some customer insights are unavailable. Reload to try again; missing values are not zero.</p>}

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {[
          {
            label: "High-value customers lapsing", value: lapsing?.high,
            // Revenue represented, not "at risk" -- a historical fact about
            // this segment's past spending, never a prediction of future loss.
            revenue: lapsing?.revenue_by_tier?.high,
            note: "Review recent spending and service history before choosing a retention action.", color: "var(--status-warning)",
          },
          { label: "Customers with only 1–2 orders", value: summary?.pattern_distribution?.one_to_two, note: "An audience for second-purchase analysis, not a measured conversion failure.", color: "var(--accent)" },
          { label: "Customers in reorder window", value: summary?.due?.count, note: "A timing signal based on past purchases. These are not guaranteed orders.", color: "var(--status-good)" },
        ].map((item) => (
          <article key={item.label} className="rounded-xl border border-border bg-chart-surface p-4" style={{ borderTop: `3px solid ${item.color}` }}>
            <h3 className="text-sm text-foreground/70">{item.label}</h3>
            <div className="my-2 text-3xl font-semibold tabular-nums text-foreground-strong">{count(item.value)}</div>
            {Number.isFinite(item.revenue) && (
              <p className="text-xs font-medium text-foreground-strong">{formatCurrency(item.revenue)} revenue represented</p>
            )}
            <p className="mt-1 text-xs leading-relaxed text-foreground/60">{item.note}</p>
          </article>
        ))}
      </div>

      <div className="grid gap-x-4 xl:grid-cols-2">
        <Card title="Lifecycle × customer value">
          <p className="mb-3 text-xs text-foreground/60">Darker cells contain more customers. Click a cell to list those customers.</p>
          {cross.length ? <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Customer lifecycle by value">
                <thead><tr><th className="p-2 text-left font-medium">Stage</th>{TIERS.map((tier) => <th key={tier.id} className="p-2 text-right text-xs font-medium">{tier.label}</th>)}</tr></thead>
                <tbody>{cross.map((row) => <tr key={row.id}>
                  <th className="p-1 text-left font-normal"><button type="button" aria-pressed={selectedStage === row.id} onClick={() => { setSelectedStage(row.id); setSelectedTier(null); setSegmentOffset(0); }} className={`rounded px-2 py-2 text-left text-xs focus-visible:outline-2 focus-visible:outline-accent ${selectedStage === row.id ? "bg-accent-soft text-accent" : "hover:bg-accent-soft"}`}>{row.label}</button></th>
                  {TIERS.map((tier) => {
                    const isPicked = selectedStage === row.id && selectedTier === tier.id;
                    return (
                      <td key={tier.id} className="border-2 border-chart-surface p-0.5 text-right tabular-nums text-foreground-strong">
                        <button
                          type="button"
                          aria-pressed={isPicked}
                          onClick={() => selectCell(row.id, tier.id)}
                          className={`w-full rounded px-2 py-1.5 focus-visible:outline-2 focus-visible:outline-accent ${isPicked ? "ring-2 ring-accent" : ""}`}
                          style={{ background: Number.isFinite(row[tier.id]) ? `color-mix(in srgb, var(--accent) ${8 + row[tier.id] / maxCell * 42}%, var(--chart-surface))` : undefined }}
                        >
                          {count(row[tier.id])}
                        </button>
                      </td>
                    );
                  })}
                </tr>)}</tbody>
              </table>
            </div>
            <div aria-live="polite" className="mt-3 rounded-lg border border-border p-3 text-xs leading-relaxed">
              {selected ? <><strong>{selected.label}: {count(selected.count)} customers.</strong> {count(selectedMix?.high)} high value. Historical revenue: {Number.isFinite(selected.revenue) ? formatCurrency(selected.revenue) : "Unavailable"}.</> : "Select a stage above."}
              <p className="mt-1 text-foreground/60">Value tiers use lifetime revenue. Lapsing and churned are heuristic stages, not confirmed customer loss.</p>
              <p className="mt-1 text-foreground/60">
                New: first order in the last 30 days. Active/Due/Lapsing/Churned come from each customer&rsquo;s own
                expected order window. Win-back: just ordered again after an unusually long gap versus their own
                normal pattern. Dormant: 1&ndash;2 orders, not recent, not enough history to score against a
                personal window.
              </p>
            </div>

            {selectedTier && (
              <div className="mt-3 rounded-lg border border-border p-3">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-semibold text-foreground-strong">
                    {TIERS.find((t) => t.id === selectedTier)?.label} · {categories.find((c) => c.id === selectedStage)?.label}
                  </h4>
                  {segmentQuery.data?.status === "ok" && (
                    <span className="text-xs text-foreground/60">{formatNumber(segmentQuery.data.total)} customer{segmentQuery.data.total === 1 ? "" : "s"}</span>
                  )}
                </div>
                {segmentQuery.isLoading && <p className="mt-2 text-xs text-foreground/60">Loading…</p>}
                {segmentQuery.data?.status === "error" && <p className="mt-2 text-xs text-warning">Couldn&rsquo;t load this segment.</p>}
                {segmentQuery.data?.status === "ok" && (
                  <>
                    <ul className="mt-2 divide-y divide-border text-xs">
                      {segmentPage.map((c) => (
                        <li key={c.partner_id} className="flex items-center justify-between gap-2 py-1.5">
                          <span className="text-foreground-strong">{c.name}</span>
                          <span className="text-foreground/60">{c.phone_masked ?? "no phone"} · {formatNumber(c.order_count)} orders</span>
                          <span className="tabular-nums text-foreground-strong">{formatCurrency(c.revenue)}</span>
                        </li>
                      ))}
                      {segmentPage.length === 0 && <li className="py-1.5 text-foreground/60">No customers in this segment.</li>}
                    </ul>
                    {segmentShown > SEGMENT_PAGE_SIZE && (
                      <div className="mt-2 flex items-center justify-between text-xs text-foreground/60">
                        <span>{segmentOffset + 1}–{Math.min(segmentOffset + SEGMENT_PAGE_SIZE, segmentShown)} of {formatNumber(segmentShown)}</span>
                        <div className="flex gap-2">
                          <button type="button" disabled={segmentOffset === 0} onClick={() => setSegmentOffset(Math.max(0, segmentOffset - SEGMENT_PAGE_SIZE))} className="rounded border border-border px-2 py-1 disabled:opacity-40">Previous</button>
                          <button type="button" disabled={segmentOffset + SEGMENT_PAGE_SIZE >= segmentShown} onClick={() => setSegmentOffset(segmentOffset + SEGMENT_PAGE_SIZE)} className="rounded border border-border px-2 py-1 disabled:opacity-40">Next</button>
                        </div>
                      </div>
                    )}
                    {/* Honest about the cap: "2,275 customers" up top is the
                        real total: this list is the top SEGMENT_LIST_CAP by
                        revenue, not all of them, whenever the two differ. */}
                    {segmentQuery.data.total > segmentShown && (
                      <p className="mt-2 text-foreground/50">Showing the top {formatNumber(segmentShown)} by revenue, of {formatNumber(segmentQuery.data.total)} total.</p>
                    )}
                  </>
                )}
              </div>
            )}
          </> : <p className="py-6 text-sm text-foreground/60">Lifecycle value breakdown unavailable.</p>}
        </Card>

        <Card title="Who contributes the revenue?">
          <p className="text-xs text-foreground/60">Share of customers compared with share of historical revenue, within the same value-tier population.</p>
          <figure aria-label="Customer share versus revenue share" className="mt-5 space-y-6">
            {tiers.length ? TIERS.map((tier) => {
              const row = tiers.find((value) => value.id === tier.id);
              if (!row) return null;
              return <div key={tier.id}>
                <div className="mb-2 flex justify-between gap-2 text-sm"><strong>{tier.label}</strong><span className="text-xs text-foreground/60">{count(row.count)} customers</span></div>
                {[{ label: "Customers", value: row.count, total: customerTotal, color: "var(--foreground)" }, { label: "Revenue", value: row.revenue, total: revenueTotal, color: tier.color }].map((bar) => <div key={bar.label} className="mt-2 grid grid-cols-[70px_1fr_48px] items-center gap-2 text-xs">
                  <span>{bar.label}</span><div className="h-3 overflow-hidden rounded-sm bg-background" aria-hidden="true"><div className="h-full rounded-sm" style={{ width: `${share(bar.value, bar.total)}%`, background: bar.color, opacity: bar.label === "Customers" ? 0.4 : 0.85 }} /></div><span className="text-right tabular-nums">{Number.isFinite(bar.value) && bar.total > 0 ? `${share(bar.value, bar.total).toFixed(1)}%` : "—"}</span>
                </div>)}
                <p className="mt-2 text-xs text-foreground/50">{Number.isFinite(row.revenue) ? formatCurrency(row.revenue) : "Unavailable"} historical revenue</p>
              </div>;
            }) : <p className="text-sm text-foreground/60">Revenue mix unavailable.</p>}
            <figcaption className="text-xs leading-relaxed text-foreground/60">Use this concentration to guide retention effort. Historical revenue is not profit or future customer value.</figcaption>
          </figure>
        </Card>
      </div>

      <Card title="Customer movement">
        {transitions.isLoading && <p className="text-sm text-foreground/60">Loading…</p>}
        {transitions.data?.status === "error" && <p className="text-sm text-warning">Customer movement unavailable.</p>}
        {transitions.data?.status === "insufficient_history" && (
          <p className="text-sm text-foreground/60">
            Needs at least 2 days of customer lifecycle history to show real movement between stages
            ({transitions.data.snapshots_available} recorded so far). This isn&rsquo;t a bug &mdash; it fills in
            once a new day of order data lands.
          </p>
        )}
        {transitions.data?.status === "ok" && (() => {
          const byFrom = Object.fromEntries(transitions.data.transitions.map((t) => [t.from, t.to]));
          const KEY_MOVES = [
            { from: "active", to: "due", label: "Active → Due" },
            { from: "due", to: "lapsing", label: "Due → Lapsing" },
            { from: "lapsing", to: "churned", label: "Lapsing → Churned" },
            { from: "churned", to: "win_back", label: "Churned → Win-back" },
            { from: "win_back", to: "active", label: "Win-back → Active" },
          ];
          return (
            <>
              <p className="text-xs text-foreground/60">
                Who actually moved between {formatDate(transitions.data.prev_date)} and {formatDate(transitions.data.curr_date)} &mdash;
                not just a change in how many are in each stage.
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-5">
                {KEY_MOVES.map((move) => (
                  <div key={move.label} className="rounded-lg border border-border p-3 text-center">
                    <div className="text-2xl font-semibold tabular-nums text-foreground-strong">
                      {formatNumber(byFrom[move.from]?.[move.to] ?? 0)}
                    </div>
                    <div className="mt-1 text-xs text-foreground/60">{move.label}</div>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-xs text-foreground/50">
                {formatNumber(transitions.data.new_customers)} new customers acquired in between, not counted as a transition from any prior stage.
              </p>
            </>
          );
        })()}
      </Card>

      <Card title="Are new customers coming back for a second order?">
        {secondOrder.isLoading && <p className="text-sm text-foreground/60">Loading…</p>}
        {secondOrder.data?.status === "error" && <p className="text-sm text-warning">Second-order conversion unavailable.</p>}
        {secondOrder.data?.status === "ok" && <>
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-3xl font-semibold tabular-nums text-foreground-strong">
              {secondOrder.data.headline.rate_pct != null ? `${secondOrder.data.headline.rate_pct}%` : "—"}
            </span>
            <span className="text-sm text-foreground/60">
              of {formatNumber(secondOrder.data.headline.eligible)} customers placed a second order within {secondOrder.data.headline.window_days} days of their first
            </span>
          </div>
          <p className="mt-1 text-xs text-foreground/50">
            Retrospective — based on each customer&rsquo;s own order history, not affected by how current today&rsquo;s data is. As of {formatDate(secondOrder.data.as_of_date)}.
          </p>

          {secondOrder.data.cohorts.length > 0 && (
            <div className="mt-4">
              <CohortConversionChart cohorts={secondOrder.data.cohorts} formatX={formatMonthLabel} />
            </div>
          )}

          {secondOrder.data.cohorts.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-xs" aria-label="Second-order conversion by acquisition month">
                <thead>
                  <tr className="text-left text-foreground/60">
                    <th className="p-1.5 font-medium">Acquisition month</th>
                    <th className="p-1.5 text-right font-medium">Customers</th>
                    {SECOND_ORDER_WINDOWS.map((w) => <th key={w} className="p-1.5 text-right font-medium">{w}d repeat</th>)}
                  </tr>
                </thead>
                <tbody>
                  {secondOrder.data.cohorts.map((row) => (
                    <tr key={row.month} className="border-t border-border">
                      <td className="p-1.5 text-foreground-strong">{formatMonthLabel(row.month)}</td>
                      <td className="p-1.5 text-right tabular-nums">{formatNumber(row.customers)}</td>
                      {SECOND_ORDER_WINDOWS.map((w) => {
                        const cell = row.windows[String(w)];
                        return (
                          <td key={w} className="p-1.5 text-right tabular-nums">
                            {cell?.rate_pct != null ? `${cell.rate_pct}%` : "—"}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs leading-relaxed text-foreground/50">
                A dash means not enough time has passed yet for that window, or too few customers to show a reliable rate — not zero.
              </p>
            </div>
          )}
        </>}
      </Card>

      <Card title="When are repeat customers approaching a purchase?">
        <figure aria-label="Customer reorder timing" className="space-y-3">
          {timing.length ? timing.map((row) => <div key={row.id} className="grid grid-cols-[95px_1fr_65px] items-center gap-3 text-xs sm:grid-cols-[130px_1fr_90px]">
            <span>{row.id === "due_today" ? "Due / within 1 day" : row.label}</span>
            <div className="h-5 overflow-hidden rounded-sm bg-background" aria-hidden="true"><div className="h-full bg-accent" style={{ width: `${(row.count ?? 0) / maxTiming * 100}%`, opacity: row.id === "due_today" ? 0.9 : 0.45 }} /></div>
            <span className="text-right tabular-nums">{count(row.count)}</span>
          </div>) : <p className="text-sm text-foreground/60">Timing distribution unavailable.</p>}
          <figcaption className="text-xs leading-relaxed text-foreground/60">Relative to the timing data cutoff, not today’s calendar date. Includes due and approaching customers; excludes overdue customers. Windows are estimates from purchase gaps, not conversion probabilities.</figcaption>
        </figure>
        <button type="button" onClick={onViewCustomers} className="mt-4 rounded-lg border border-border px-3 py-2 text-xs font-medium text-accent hover:bg-accent-soft focus-visible:outline-2 focus-visible:outline-accent">Review customers in reorder window</button>
      </Card>
    </section>
  );
}
