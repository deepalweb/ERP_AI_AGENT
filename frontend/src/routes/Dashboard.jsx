import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPerformanceSummary, fetchInventoryDepth } from "../api";
import Section from "../design/Card";
import { formatCurrency, formatNumber, monthLabel } from "../format";

function RevenueBars({ rows }) {
  const max = Math.max(1, ...rows.map((row) => Math.abs(row.revenue)));
  return <figure aria-label="Revenue ranking" className="space-y-4">{rows.map((row, index) => <div key={row.id ?? `${row.name}-${index}`}>
    <div className="mb-1 flex justify-between gap-3 text-xs"><span className="min-w-0 break-words">{row.name}</span><span className="shrink-0 tabular-nums">{formatCurrency(row.revenue)}</span></div>
    <div className="h-2 rounded bg-background" aria-hidden="true"><div className="h-full rounded" style={{ width: `${Math.abs(row.revenue) / max * 100}%`, background: row.revenue < 0 ? "var(--status-critical)" : "var(--accent)" }} /></div>
  </div>)}</figure>;
}

export default function Dashboard() {
  const [month, setMonth] = useState("");
  const [store, setStore] = useState("");
  const performance = useQuery({ queryKey: ["performanceSummary", month, store], queryFn: () => fetchPerformanceSummary(month, store), retry: false });
  const inventory = useQuery({ queryKey: ["inventoryDepth"], queryFn: fetchInventoryDepth, retry: false });
  const data = performance.data?.status === "ok" ? performance.data : null;
  const [months, setMonths] = useState([]);
  const [stores, setStores] = useState([]);
  useEffect(() => { if (data) { setMonths(data.months); setStores(data.stores); } }, [data]);
  const note = `${month ? monthLabel(month) : "All available history"} · ${store ? "Selected outlet" : "All Celeste outlets"}`;
  const maxHour = Math.max(1, ...(data?.hours ?? []).map((r) => r.orders));
  return <div className="px-6 py-6">
    <h1 className="text-xl font-semibold text-foreground-strong">Business Performance</h1>
    <p className="mt-1 text-sm text-foreground/60">Compare outlets, product sales and ordering patterns over the same period.</p>
    <div className="mt-5 flex flex-wrap items-end gap-3 rounded-xl border border-border bg-chart-surface p-4">
      <label className="text-xs text-foreground/70">Sales period<select aria-label="Sales period" value={month} onChange={(e) => setMonth(e.target.value)} className="mt-1 block rounded-lg border border-border bg-background p-2 text-sm text-foreground-strong"><option value="">All available history</option>{months.map((m) => <option key={m} value={m}>{monthLabel(m)}{m === months.at(-1) ? " (may be partial)" : ""}</option>)}</select></label>
      <label className="text-xs text-foreground/70">Outlet<select aria-label="Outlet" value={store} onChange={(e) => setStore(e.target.value)} className="mt-1 block rounded-lg border border-border bg-background p-2 text-sm text-foreground-strong"><option value="">All Celeste outlets</option>{stores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <button type="button" onClick={() => performance.refetch()} disabled={performance.isFetching} className="rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-50">Reload comparisons</button>
    </div>
    {performance.isFetching && <p role="status" className="mt-4 text-sm">Loading comparisons…</p>}
    {!performance.isFetching && !data && <p role="alert" className="mt-4 text-sm text-warning">Could not load performance comparisons. Use Reload comparisons to try again.</p>}
    {data && <>
      <p className="mt-3 text-xs text-foreground/60">Selected orders: {data.start?.replace("T", " ") ?? "No orders"} to {data.end?.replace("T", " ") ?? "—"} · Asia/Colombo. Source through {data.source_through?.replace("T", " ") ?? "unavailable"}.{data.latest_month_included && " Latest source month may be incomplete."}</p>
      <Section title="Outlet comparison" note={note}>
        {data.outlets.length ? <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-xs text-foreground/60">{["Outlet", "Confirmed orders", "Revenue", "Cancellation rate"].map((h) => <th key={h} className="p-2">{h}</th>)}</tr></thead><tbody>{data.outlets.map((r) => <tr key={r.id} className="border-t border-border"><td className="p-2">{r.name}</td><td className="p-2">{formatNumber(r.orders)}</td><td className="p-2">{formatCurrency(r.revenue)}</td><td className="p-2">{r.cancellation_rate_pct == null ? "Unavailable" : `${r.cancellation_rate_pct}%`}</td></tr>)}</tbody></table></div> : <p className="text-sm">No orders in this selection.</p>}
        <p className="mt-3 text-xs text-foreground/60">Revenue uses confirmed order totals. Cancellation rate = cancelled ÷ confirmed and cancelled orders.</p>
      </Section>
      <div className="grid gap-x-4 xl:grid-cols-2">
        <Section title="Revenue by department" note={note}>{data.departments.length ? <RevenueBars rows={data.departments} /> : <p className="text-sm">No department sales in this selection.</p>}</Section>
        <Section title="Top products by revenue" note={note}>{data.products.length ? <RevenueBars rows={data.products} /> : <p className="text-sm">No product sales in this selection.</p>}</Section>
      </div>
      <p className="mt-2 text-xs text-foreground/60">Product and department revenue use order-line subtotals, which can differ from order totals because of taxes. Unlinked means no department is assigned.</p>
      <Section title="Orders by hour" note={note}>
        <figure aria-label="Confirmed orders by local hour"><div className="flex h-40 items-end gap-1">{data.hours.map((r) => <div key={r.hour} className="flex h-full min-w-0 flex-1 flex-col justify-end" title={`${r.hour}:00 · ${r.orders} orders`}><div className="bg-accent" style={{height: `${r.orders / maxHour * 85}%`}} /><span className="h-4 text-[10px] text-foreground/60">{r.hour % 3 === 0 ? r.hour : ""}</span></div>)}</div><figcaption className="mt-3 text-xs text-foreground/60">Confirmed orders by Asia/Colombo hour. Cancelled orders are excluded.</figcaption></figure>
        <details className="mt-3 text-xs"><summary className="cursor-pointer">Hourly values</summary><div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">{data.hours.map((r) => <span key={r.hour}>{r.hour}:00 · {formatNumber(r.orders)}</span>)}</div></details>
      </Section>
    </>}
    <Section title="Inventory value — separate stock snapshot">
      <p className="mb-3 text-xs text-foreground/60">Shared internal inventory; sales period and outlet filters above do not apply. Valuation is estimated. Sales-velocity reference date: {inventory.data?.as_of_date ?? "unavailable"}; this is not a historical stock snapshot date.</p>
      {inventory.isLoading ? <p className="text-sm">Loading inventory…</p> : inventory.data?.status === "ok" ? <div className="grid gap-3 sm:grid-cols-3">{inventory.data.segments.map((r) => <div key={r.id} className="rounded-lg border border-border p-3"><div className="text-lg font-semibold">{r.value == null ? "Unavailable" : formatCurrency(r.value)}</div><div className="text-sm">{r.label}</div><div className="text-xs text-foreground/60">{formatNumber(r.count)} products · {formatNumber(r.units)} units</div></div>)}</div> : <p className="text-sm text-warning">Inventory unavailable.</p>}
    </Section>
  </div>;
}
