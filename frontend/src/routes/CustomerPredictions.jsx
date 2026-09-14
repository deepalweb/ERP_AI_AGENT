import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchCustomerLifecycle,
  fetchCustomerNextBasket,
  fetchCustomerOrderForecastChart,
  fetchCustomerSearch,
  fetchCustomerOutletBehavior,
  fetchCustomerPredictions,
  fetchCustomerPredictionsSummary,
  fetchOutletOrderForecast,
  fetchStockAlerts,
  fetchTopProductStockCoverage,
} from "../api";
import Section from "../design/Section";
import Pill from "../design/Pill";
import DonutChart from "../charts/DonutChart";
import OrderForecastChart from "../charts/OrderForecastChart";
import { formatCurrency, formatDate, formatNumber } from "../format";

const STATUS_TABS = [
  { id: null, label: "All" },
  { id: "overdue", label: "Overdue" },
  { id: "due", label: "Due soon" },
  { id: "on_track", label: "On track" },
];

const STATUS_META = {
  overdue: { tone: "critical", label: "Overdue" },
  due: { tone: "warning", label: "Due soon" },
  on_track: { tone: "good", label: "On track" },
};

const SEVERITY_META = {
  critical: { tone: "critical", label: "Critical" },
  moderate: { tone: "warning", label: "Moderate" },
  recent: { tone: "good", label: "Recent" },
};

const SORT_OPTIONS = [
  { id: "revenue", label: "Highest lifetime revenue" },
  { id: "days_overdue", label: "Most overdue" },
  { id: "soonest", label: "Soonest expected order" },
  { id: "confidence", label: "Highest confidence" },
  { id: "order_count", label: "Most frequent customer" },
  { id: "days_since_last_order", label: "Days since last order" },
  { id: "name", label: "Name" },
];

const PAGE_SIZE = 25;

function ExecutiveTile({ label, value, sub, tone }) {
  const toneClass = tone === "critical" ? "text-critical" : tone === "warning" ? "text-warning" : tone === "good" ? "text-good" : "text-foreground-strong";
  return (
    <div className="rounded-lg border border-border p-3">
      <div className={`text-lg font-bold ${toneClass}`}>{value}</div>
      <div className="text-xs font-medium text-foreground/60">{label}</div>
      {sub && <div className="text-xs text-foreground/50">{sub}</div>}
    </div>
  );
}

/** Purchase-pattern donut slices, in a fixed order with colors chosen for
 * meaning rather than whatever DonutChart's default palette would assign
 * by index — blue/orange read as a confidence gradient (high -> moderate),
 * gray reads as "not evaluated" rather than good or bad, and red flags
 * Excluded as the one bucket that's an actual data-quality call, not a
 * confidence tier. */
const PATTERN_ROWS = [
  { id: "ten_plus", label: "10+ orders (high confidence)", color: "var(--series-1)" },
  { id: "three_to_nine", label: "3–9 orders (moderate confidence)", color: "var(--series-2)" },
  { id: "one_to_two", label: "1–2 orders (not enough history yet)", color: "var(--border)" },
  { id: "excluded", label: "Excluded", color: "var(--status-critical)" },
];

/** Range of days a customer's own gap history suggests, floored at 1 —
 * same math as the backend's window (avg +/- stddev), shown as a plain
 * "6-8 days" range instead of a "7.2 (+/-1.4)" stats-notation pair. */
function gapRange(row) {
  const stddev = row.stddev_gap_days ?? row.avg_gap_days * 0.35;
  const low = Math.max(1, Math.round(row.avg_gap_days - stddev));
  const high = Math.round(row.avg_gap_days + stddev);
  return [low, high];
}

/** Plain-English line for a customer's own cadence — same posture as
 * plainLanguage.js: built from real per-customer fields (avg_gap_days,
 * status, days_overdue), never invented text. */
function cadenceLine(row) {
  const [low, high] = gapRange(row);
  if (row.status === "overdue") {
    return `Usually orders every ${low}–${high} days — it's been ${row.days_since_last_order} days, ${row.days_overdue} past their usual window.`;
  }
  if (row.status === "due") {
    return `Usually orders every ${low}–${high} days — expected to order any day now.`;
  }
  return `Usually orders every ${low}–${high} days — not due yet.`;
}

function whyBullets(row) {
  const [low, high] = gapRange(row);
  const bullets = [
    `${row.order_count} completed orders`,
    `Typical purchase interval: ${low}–${high} days`,
    row.confidence === "high"
      ? "Enough order history for a reliable individual pattern"
      : "Moderate order history — the expected window is wider to reflect that",
  ];
  if (row.status === "overdue") {
    bullets.push(`No order in ${row.days_since_last_order} days — beyond their usual window`);
  }
  return bullets;
}

/** Which of this customer's own products are due for reorder — same
 * window math as the whole-order prediction above, applied per product.
 * Lazy-loaded only when the row is expanded, since it's a per-customer
 * live query, not part of the daily cache. An empty list is a real,
 * disclosed result (this customer has no single product bought often
 * enough to predict from), not a loading or error state. */
function NextBasketSection({ partnerId, enabled }) {
  const nextBasket = useQuery({
    queryKey: ["customerNextBasket", partnerId],
    queryFn: () => fetchCustomerNextBasket(partnerId, 8),
    enabled,
  });

  if (!enabled || nextBasket.isLoading) {
    return (
      <div className="mt-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Likely next order</div>
        <p className="mt-1 text-foreground/50">Loading&hellip;</p>
      </div>
    );
  }
  if (nextBasket.data?.status !== "ok") {
    return null;
  }
  const { products } = nextBasket.data;
  return (
    <div className="mt-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Likely next order</div>
      {products.length === 0 ? (
        <p className="mt-1 text-foreground/50">
          No single product bought often enough by this customer to predict which items they&rsquo;ll reorder.
        </p>
      ) : (
        <ul className="mt-1 space-y-1">
          {products.map((p) => {
            const meta = STATUS_META[p.status];
            return (
              <li key={p.product_id} className="flex items-center justify-between gap-2">
                <span className="text-foreground-strong">{p.name}</span>
                <span className="flex items-center gap-2 text-xs text-foreground/50">
                  {`bought ${p.times_bought}x, every ~${p.avg_gap_days}d`}
                  <Pill tone={meta.tone}>{meta.label}</Pill>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** Look a customer up by name/phone/email and jump straight to their
 * predicted next order — a faster path than paging through the Customers
 * table below when staff already know who they're checking.
 *
 * Email is included since it was asked for, but rarely finds anything —
 * checked against the live DB before building this: only 17 of 84,896
 * customers have one on file. Phone is the reliable field, matched on the
 * last 9 digits (not an exact string) since the same number appears in
 * several formats in real data ("+94 77 300 0003", "0773000003",
 * "773000003"). Name search can return several people (names aren't
 * unique here), so more than one match shows a picker instead of
 * guessing which person was meant. */
function CustomerLookupSection() {
  const [form, setForm] = useState({ name: "", email: "", phone: "" });
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  const canSearch = form.name.trim() || form.email.trim() || form.phone.trim();

  async function handleGenerate() {
    if (!canSearch || searching) return;
    setSearching(true);
    setSelectedId(null);
    try {
      const data = await fetchCustomerSearch(form);
      const list = data.status === "ok" ? data.results : [];
      setResults(list);
      if (list.length === 1) setSelectedId(list[0].partner_id);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  const selected = results?.find((r) => r.partner_id === selectedId) ?? null;

  return (
    <Section title="Find a customer" note="Look up one customer and generate their predicted next order">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <input
          type="text"
          placeholder="Name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          onKeyDown={(e) => e.key === "Enter" && handleGenerate()}
          className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground-strong placeholder:text-foreground/40"
        />
        <input
          type="email"
          placeholder="Email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          onKeyDown={(e) => e.key === "Enter" && handleGenerate()}
          className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground-strong placeholder:text-foreground/40"
        />
        <input
          type="tel"
          placeholder="Mobile number"
          value={form.phone}
          onChange={(e) => setForm({ ...form, phone: e.target.value })}
          onKeyDown={(e) => e.key === "Enter" && handleGenerate()}
          className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground-strong placeholder:text-foreground/40"
        />
      </div>

      <button
        type="button"
        onClick={handleGenerate}
        disabled={!canSearch || searching}
        className="mt-3 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
      >
        {searching ? "Searching…" : "Generate"}
      </button>

      {results !== null && results.length === 0 && (
        <p className="mt-3 text-sm text-foreground/60">
          No customer found matching that.
          {form.email.trim() && !form.name.trim() && !form.phone.trim() && (
            <> Almost no customers have an email on file for this business &mdash; try their name or mobile
            number instead.</>
          )}
        </p>
      )}

      {results !== null && results.length > 1 && (
        <div className="mt-3">
          <p className="text-sm text-foreground/60">{results.length} customers match &mdash; pick one:</p>
          <ul className="mt-2 space-y-1">
            {results.map((r) => (
              <li key={r.partner_id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(r.partner_id)}
                  className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${
                    selectedId === r.partner_id ? "border-accent-border bg-accent-soft" : "border-border hover:bg-background"
                  }`}
                >
                  <span className="font-medium text-foreground-strong">{r.name}</span>
                  <span className="ml-2 text-foreground/50">
                    {r.phone ?? "no phone"} &middot; {formatNumber(r.order_count)} orders
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {selected && (
        <div className="mt-4 rounded-xl border border-border bg-background p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="font-medium text-foreground-strong">{selected.name}</div>
              <div className="text-xs text-foreground/50">
                {selected.phone ?? "no phone"} &middot; {formatNumber(selected.order_count)} lifetime orders
              </div>
            </div>
            {results.length > 1 && (
              <button type="button" onClick={() => setSelectedId(null)} className="text-xs text-accent">
                Change
              </button>
            )}
          </div>
          <NextBasketSection partnerId={selected.partner_id} enabled={true} />
        </div>
      )}
    </Section>
  );
}

function CustomerRow({ row, expanded, onToggle }) {
  const meta = STATUS_META[row.status];
  const severity = row.overdue_severity ? SEVERITY_META[row.overdue_severity] : null;
  const [gapLow, gapHigh] = gapRange(row);
  return (
    <>
      <tr className="cursor-pointer border-t border-border hover:bg-background" onClick={onToggle}>
        <td className="py-2 pr-2">
          <div className="font-medium text-foreground-strong">{row.name}</div>
          <div className="text-xs text-foreground/50">
            {row.order_count} orders &middot; {row.confidence === "high" ? "High confidence" : "Moderate confidence"}
          </div>
        </td>
        <td className="py-2 pr-2 text-foreground/80">{formatDate(row.last_order_date)}</td>
        <td className="py-2 pr-2 text-foreground/80">
          {gapLow}&ndash;{gapHigh}d
        </td>
        <td className="py-2 pr-2 text-foreground/80">
          {formatDate(row.expected_window_start)} &ndash; {formatDate(row.expected_window_end)}
        </td>
        <td className="py-2 pr-2">
          <div className="flex flex-wrap items-center gap-1">
            <Pill tone={meta.tone}>{meta.label}</Pill>
            {severity && <Pill tone={severity.tone}>{severity.label}</Pill>}
          </div>
        </td>
        <td className="py-2 pr-2 text-right text-foreground-strong">{formatCurrency(row.revenue)}</td>
      </tr>
      {expanded && (
        <tr className="border-t border-border bg-background">
          <td colSpan={6} className="px-2 py-3 text-sm text-foreground/80">
            <p>{cadenceLine(row)}</p>
            <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div>
                <div className="text-xs text-foreground/50">Normal purchase gap</div>
                <div className="font-medium text-foreground-strong">
                  {gapLow}&ndash;{gapHigh} days
                </div>
              </div>
              <div>
                <div className="text-xs text-foreground/50">Typical order value</div>
                <div className="font-medium text-foreground-strong">{formatCurrency(row.avg_order_value)}</div>
              </div>
              <div>
                <div className="text-xs text-foreground/50">Lifetime orders</div>
                <div className="font-medium text-foreground-strong">{formatNumber(row.order_count)}</div>
              </div>
              <div>
                <div className="text-xs text-foreground/50">Lifetime revenue</div>
                <div className="font-medium text-foreground-strong">{formatCurrency(row.revenue)}</div>
              </div>
            </div>
            <div className="mt-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-foreground/50">
                Why Celeste thinks this
              </div>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-foreground/70">
                {whyBullets(row).map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </div>
            <NextBasketSection partnerId={row.partner_id} enabled={expanded} />
          </td>
        </tr>
      )}
    </>
  );
}

const FORECAST_LEGEND = [
  { key: "actual_orders", label: "Current order count", color: "var(--series-1)", dash: false },
  { key: "predicted_orders", label: "Predicted order count", color: "var(--series-1)", dash: true },
];

function ForecastLegendSwatch({ color, dash }) {
  return (
    <svg width="18" height="8" className="flex-shrink-0">
      <line
        x1="0" y1="4" x2="18" y2="4"
        stroke={color}
        strokeWidth="2"
        strokeDasharray={dash ? "4 3" : undefined}
      />
    </svg>
  );
}

/** How customers behave differently at each of Celeste's 5 real outlets —
 * scoped to just those outlets (see backend/app/services/customer_predictions.py's
 * OUTLET_BEHAVIOR_SQL comment), unlike the rest of this page's numbers,
 * which aren't outlet-scoped at all yet. Computed live, not part of the
 * daily prediction cache — a small, fast aggregate over 5 stores. */
function OutletBehaviorTable({ outlets }) {
  if (!outlets || outlets.length === 0) {
    return <p className="text-sm text-foreground/50">No outlet data available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Outlet</th>
            <th className="pb-2 text-right font-medium">Orders</th>
            <th className="pb-2 text-right font-medium">Cancelled</th>
            <th className="pb-2 text-right font-medium">Cancel rate</th>
            <th className="pb-2 text-right font-medium">Unique customers</th>
            <th className="pb-2 text-right font-medium">Orders / customer</th>
            <th className="pb-2 text-right font-medium">Avg order value</th>
          </tr>
        </thead>
        <tbody>
          {outlets.map((o) => (
            <tr key={o.outlet} className="border-t border-border">
              <td className="py-1.5 font-medium text-foreground-strong">{o.outlet}</td>
              <td className="py-1.5 text-right">{formatNumber(o.orders)}</td>
              <td className="py-1.5 text-right">{formatNumber(o.cancelled)}</td>
              <td className="py-1.5 text-right">
                <Pill tone={o.cancellation_rate_pct >= 10 ? "critical" : o.cancellation_rate_pct >= 5 ? "warning" : "good"}>
                  {o.cancellation_rate_pct}%
                </Pill>
              </td>
              <td className="py-1.5 text-right">{formatNumber(o.unique_customers)}</td>
              <td className="py-1.5 text-right">{o.orders_per_customer}</td>
              <td className="py-1.5 text-right">{formatCurrency(o.avg_order_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The company-wide order forecast above, split across outlets by each
 * outlet's own real trailing-30-day share of order volume — not
 * per-customer attribution (see backend/app/services/customer_predictions.py
 * get_outlet_order_forecast() for why). Outlets with no recent orders
 * (Ja-Ela) don't appear, same reasoning as the stock-alerts table below. */
function OutletOrderForecastTable({ data }) {
  if (!data || !data.outlets || data.outlets.length === 0) {
    return <p className="text-sm text-foreground/50">No outlet forecast data available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Date</th>
            {data.outlets.map((o) => (
              <th key={o.name} className="pb-2 text-right font-medium">
                {o.name.replace("Celeste Daily", "").replace("-", "").trim()}
              </th>
            ))}
            <th className="pb-2 text-right font-medium">Total</th>
          </tr>
        </thead>
        <tbody>
          {data.days.map((d, i) => (
            <tr key={d.date} className="border-t border-border">
              <td className="py-1.5 font-medium text-foreground-strong">
                {i === 0 ? "Tomorrow" : formatDate(d.date)}
              </td>
              {data.outlets.map((o) => (
                <td key={o.name} className="py-1.5 text-right">
                  {formatNumber(d.by_outlet[o.name] ?? 0)}
                </td>
              ))}
              <td className="py-1.5 text-right font-semibold text-foreground-strong">
                {formatNumber(d.total_expected_orders)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Out-of-stock / low-stock counts per outlet, plus the specific items
 * behind them — the outlet-wise counterpart to Top products above. Jaela
 * (one of the 5 configured outlets) won't appear here if it has zero
 * orders in the trailing 30 days — that's a real "no trading activity"
 * state, not a missing-data bug. */
function StockAlertsByOutletTable({ data }) {
  if (!data || !data.segments_by_outlet || data.segments_by_outlet.length === 0) {
    return <p className="text-sm text-foreground/50">No stock alert data available.</p>;
  }
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-foreground/60">
              <th className="pb-2 font-medium">Outlet</th>
              <th className="pb-2 text-right font-medium">Out of stock</th>
              <th className="pb-2 text-right font-medium">Low stock</th>
            </tr>
          </thead>
          <tbody>
            {data.segments_by_outlet.map((o) => (
              <tr key={o.outlet} className="border-t border-border">
                <td className="py-1.5 font-medium text-foreground-strong">{o.outlet}</td>
                <td className="py-1.5 text-right">
                  {o.out_of_stock > 0 ? <Pill tone="critical">{o.out_of_stock}</Pill> : formatNumber(0)}
                </td>
                <td className="py-1.5 text-right">
                  {o.low_stock > 0 ? <Pill tone="warning">{o.low_stock}</Pill> : formatNumber(0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.top_out_of_stock && data.top_out_of_stock.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-foreground/50">
            Top out-of-stock, by recent sales
          </div>
          <ul className="mt-1 space-y-1">
            {data.top_out_of_stock.slice(0, 5).map((item, i) => (
              <li key={`${item.sku}-${item.outlet}-${i}`} className="flex justify-between text-foreground/80">
                <span>
                  {item.name} <span className="text-foreground/50">&middot; {item.outlet}</span>
                </span>
                <span className="text-foreground/50">{formatNumber(item.sold_30d)} sold/30d</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Stock coverage for the top-selling products, checked against each
 * product's own sales rate — not a company-wide stock total, which pools
 * every SKU together and can't say whether any specific product is
 * actually about to run out (a healthy overall number can hide a fast
 * mover that's nearly empty). "Short" reuses the same 3-day low-stock
 * threshold used elsewhere in the app, not the 10-day forecast window —
 * checked against the live data first: the top sellers here are almost
 * all fresh produce/dairy, restocked nearly daily, so requiring 10 days
 * of on-hand stock would flag every single one as short when thin stock
 * is normal for a quick-commerce grocer. Negative stock is called out
 * separately — it's a data-integrity issue (sold past what was recorded
 * on hand), not an ordinary low-stock signal. */
function TopProductStockCoverageTable({ data }) {
  if (!data || !data.products || data.products.length === 0) {
    return <p className="text-sm text-foreground/50">No stock coverage data available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Product</th>
            <th className="pb-2 text-right font-medium">Sold (30d)</th>
            <th className="pb-2 text-right font-medium">Current stock</th>
            <th className="pb-2 text-right font-medium">Days of cover</th>
            <th className="pb-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {data.products.map((p) => (
            <tr key={p.sku ?? p.name} className="border-t border-border">
              <td className="py-1.5">
                <div className="font-medium text-foreground-strong">{p.name}</div>
                {p.sku && <div className="text-xs text-foreground/50">SKU {p.sku}</div>}
              </td>
              <td className="py-1.5 text-right">{formatNumber(p.qty_sold_30d)}</td>
              <td className="py-1.5 text-right">{formatNumber(p.current_stock)}</td>
              <td className="py-1.5 text-right">{p.days_of_cover ?? "n/a"}</td>
              <td className="py-1.5">
                {p.negative_stock ? (
                  <Pill tone="critical">Data issue</Pill>
                ) : p.short ? (
                  <Pill tone="warning">{`Under ${data.low_stock_threshold_days}d cover`}</Pill>
                ) : (
                  <Pill tone="good">OK</Pill>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const LIFECYCLE_TONE = {
  new: "accent",
  active: "good",
  due: "warning",
  lapsing: "serious",
  churned: "critical",
  win_back: "accent",
  dormant: "neutral",
  excluded: "neutral",
};

const TILE_TONE_CLASS = {
  accent: "border-accent-border bg-accent-soft",
  good: "border-good/30 bg-good/10",
  warning: "border-warning/30 bg-warning/10",
  serious: "border-serious/30 bg-serious/10",
  critical: "border-critical/30 bg-critical/10",
  neutral: "border-border bg-background",
};

/** One lifecycle-category tile — icon, count, label, and the lifetime
 * revenue behind it, colored by how urgent/positive that stage is. Same
 * "icons + real numbers, no raw statistics" posture as the rest of this
 * page. */
// Whether a rising count is good or bad news, per lifecycle category — used
// to color the day-over-day delta. Categories left out (New/Due/Dormant/
// Excluded) don't have an obvious "up is good/bad" reading (more New
// customers is good but reflects acquisition, not lifecycle health; more
// Due is neutral, just more people approaching their own window), so their
// delta shows neutral, not a guessed color.
const LIFECYCLE_DELTA_SENSE = { active: "up_good", win_back: "up_good", lapsing: "up_bad", churned: "up_bad" };

/** Day-over-day change, colored by whether "up" is good or bad news for
 * this specific number — reusing the same up/down-arrow convention as
 * Home's Business Health strip. null means no prior day to compare against
 * yet (first time this ran, or a brand-new category), shown plainly as
 * "new" rather than a fabricated 0. */
function DeltaBadge({ value, sense = "neutral" }) {
  if (value == null) return <span className="text-foreground/30">new</span>;
  if (value === 0) return <span className="text-foreground/40">&rarr; 0</span>;
  const isBad = sense === "up_bad" ? value > 0 : sense === "up_good" ? value < 0 : false;
  const isGood = sense === "up_bad" ? value < 0 : sense === "up_good" ? value > 0 : false;
  const toneClass = isBad ? "text-critical" : isGood ? "text-good" : "text-foreground/50";
  return (
    <span className={toneClass}>
      {value > 0 ? "↑" : "↓"} {Math.abs(value)}
    </span>
  );
}

function LifecycleTile({ category }) {
  const tone = LIFECYCLE_TONE[category.id] ?? "neutral";
  return (
    <div className={`rounded-xl border p-3 ${TILE_TONE_CLASS[tone]}`}>
      <div className="flex items-center gap-2">
        <span className="text-xl">{category.icon}</span>
        <span className="text-lg font-bold text-foreground-strong">{formatNumber(category.count)}</span>
        <span className="text-xs">
          <DeltaBadge value={category.delta} sense={LIFECYCLE_DELTA_SENSE[category.id]} />
        </span>
      </div>
      <div className="mt-1 text-xs font-medium text-foreground-strong">{category.label}</div>
      <div className="text-xs text-foreground/50">{formatCurrency(category.revenue)} lifetime</div>
    </div>
  );
}

const SMALL_SAMPLE_THRESHOLD = 30;

/** One channel tile — does churn cluster on one delivery platform? Churn/
 * lapsing rate is a share of that channel's own "scoreable" customers
 * (Active/Due/Lapsing/Churned/Win-back — the ones with a real churn
 * signal), not of everyone tagged with that channel, so a channel full of
 * brand-new customers can't look falsely healthy. Direct has too few
 * customers for its rate to mean anything (checked live: ~12 scoreable
 * customers vs. tens of thousands for Uber/PickMe) — flagged rather than
 * shown as a real percentage. */
function ChannelTile({ channel }) {
  const smallSample = channel.scoreable_customers < SMALL_SAMPLE_THRESHOLD;
  return (
    <div className="rounded-xl border border-border p-3">
      <div className="flex items-center gap-2">
        <span className="text-xl">{channel.icon}</span>
        <span className="text-lg font-bold text-foreground-strong">{formatNumber(channel.total_customers)}</span>
      </div>
      <div className="mt-1 text-xs font-medium text-foreground-strong">{channel.label}</div>
      {smallSample ? (
        <div className="text-xs text-foreground/40">Too few customers to compare reliably</div>
      ) : (
        <div className="text-xs text-foreground/50">
          {channel.churn_rate_pct}% churned (<DeltaBadge value={channel.churn_rate_delta} sense="up_bad" />)
          &middot; {channel.lapsing_rate_pct}% lapsing (<DeltaBadge value={channel.lapsing_rate_delta} sense="up_bad" />)
        </div>
      )}
    </div>
  );
}

/** Customer Lifecycle — every customer sorted into one of 8 stages (New /
 * Active / Due / Lapsing / Churned / Win-back / Dormant / Excluded) plus
 * an independent High/Mid/Low value tier. See
 * backend/app/services/customer_predictions.py compute_lifecycle_summary()
 * for the exact rule behind each stage — checked against the live data
 * before picking the Lapsing-vs-Churned cutoff (2x a customer's own
 * window width) and adding the Dormant bucket (without it, ~45% of the
 * customer base — everyone with 1-2 old orders — wouldn't fit anywhere in
 * the 6 stages originally proposed). */
function CustomerLifecycleSection({ lifecycle }) {
  if (!lifecycle) return null;
  return (
    <Section title="Customer lifecycle" note={`${formatNumber(lifecycle.total_customers)} customers`}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {lifecycle.categories.map((c) => (
          <LifecycleTile key={c.id} category={c} />
        ))}
      </div>

      <p className="mt-4 text-xs text-foreground/50">
        New: first order in the last 30 days. Active/Due/Lapsing/Churned come from each customer&rsquo;s own
        expected order window, same as the rest of this page. Win-back: just ordered again after an unusually
        long gap versus their own normal pattern. Dormant: 1&ndash;2 orders, not recent, not enough history to
        score against a personal window.
      </p>

      {lifecycle.channel_breakdown && (
        <div className="mt-6">
          <div className="text-xs font-semibold uppercase tracking-wide text-foreground/50">
            Channel &amp; churn
          </div>
          <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {lifecycle.channel_breakdown.map((c) => (
              <ChannelTile key={c.id} channel={c} />
            ))}
          </div>
          <p className="mt-2 text-xs text-foreground/50">
            Churn/lapsing rate is a share of each channel&rsquo;s own customers with an established pattern
            (Active/Due/Lapsing/Churned/Win-back) — not of every customer who&rsquo;s ever used that channel, so
            a channel full of brand-new customers doesn&rsquo;t look falsely healthy.
          </p>
        </div>
      )}
    </Section>
  );
}

/** Customer Purchase Intelligence — Phase 1: per-customer expected order
 * window and overdue detection, built from each customer's own order-gap
 * history (see backend/app/services/customer_predictions.py).
 *
 * Deliberately doesn't show raw statistics (mean/stddev/z-score) anywhere
 * on this page — a manager needs "when," "how sure," and "why," not the
 * arithmetic behind it. Every number here is either a plain count/revenue
 * figure or a day range in ordinary language.
 *
 * Not built yet, on purpose: predicted next-basket contents, an order-value
 * range, and a prediction-vs-actual accuracy loop (that last one needs
 * real elapsed time to accumulate evaluated predictions before it can show
 * anything honest — see the placeholder note at the bottom of this page).
 */
export default function CustomerPredictions() {
  const [status, setStatus] = useState(null);
  const [sort, setSort] = useState("revenue");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState(null);

  const summary = useQuery({ queryKey: ["customerPredictionsSummary"], queryFn: fetchCustomerPredictionsSummary });
  const list = useQuery({
    queryKey: ["customerPredictions", status, sort, offset],
    queryFn: () => fetchCustomerPredictions({ status, sort, limit: PAGE_SIZE, offset }),
  });
  const outletBehavior = useQuery({ queryKey: ["customerOutletBehavior"], queryFn: fetchCustomerOutletBehavior });
  const forecastChart = useQuery({
    queryKey: ["customerOrderForecastChart"],
    queryFn: () => fetchCustomerOrderForecastChart(5, 5),
  });
  const stockCoverage = useQuery({
    queryKey: ["topProductStockCoverage"],
    queryFn: () => fetchTopProductStockCoverage(15, 10),
  });
  const stockAlerts = useQuery({ queryKey: ["stockAlerts"], queryFn: fetchStockAlerts });
  const outletForecast = useQuery({
    queryKey: ["outletOrderForecast"],
    queryFn: () => fetchOutletOrderForecast(5),
  });
  const lifecycle = useQuery({ queryKey: ["customerLifecycle"], queryFn: fetchCustomerLifecycle });

  function changeStatus(next) {
    setStatus(next);
    setOffset(0);
    setExpandedId(null);
  }

  function changeSort(next) {
    setSort(next);
    setOffset(0);
    setExpandedId(null);
  }

  if (summary.isLoading) {
    return (
      <div className="px-6 py-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl border border-border bg-chart-surface" />
          ))}
        </div>
      </div>
    );
  }

  if (summary.data?.status !== "ok") {
    return <div className="px-6 py-6 text-sm text-foreground/60">Couldn&rsquo;t load customer predictions.</div>;
  }

  const s = summary.data;
  const rows = list.data?.status === "ok" ? list.data.results : [];
  const total = list.data?.status === "ok" ? list.data.total : 0;
  const pattern = s.pattern_distribution;
  const patternTotal = (pattern.ten_plus ?? 0) + (pattern.three_to_nine ?? 0) + (pattern.one_to_two ?? 0) + (pattern.excluded ?? 0);

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground-strong">Customer purchase intelligence</h1>
          <p className="mt-1 text-sm text-foreground/60">
            When each repeat customer is likely to order again, based on their own ordering history — not a
            business-wide average.
          </p>
        </div>
        {s.as_of_date && <span className="text-xs text-foreground/60">Data through {formatDate(s.as_of_date)}</span>}
      </div>

      {/* Find a customer */}
      <CustomerLookupSection />

      {/* Order forecast */}
      <Section title="Order forecast" note="5 days actual, 5 days predicted">
        {forecastChart.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {forecastChart.data?.status === "ok" && (
          <>
            <OrderForecastChart
              data={forecastChart.data.points}
              asOfDate={forecastChart.data.as_of_date}
              formatOrders={(v) => formatNumber(v)}
              formatX={(v) => formatDate(v)}
            />
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
              {FORECAST_LEGEND.map((l) => (
                <div key={l.key} className="flex items-center gap-2 text-xs text-foreground/70">
                  <ForecastLegendSwatch color={l.color} dash={l.dash} />
                  {l.label}
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-foreground/50">
              Solid = actual. Dashed = predicted. Order counts are from repeat customers with an established
              pattern only, so the predicted line reads lower than your total daily order count — see Purchase
              pattern below. Stock coverage isn&rsquo;t shown here: a single company-wide stock total can&rsquo;t
              answer &ldquo;will we run out,&rdquo; since it pools every product together — see Top products &mdash;
              stock coverage below instead.
            </p>
          </>
        )}
        {forecastChart.data?.status === "error" && (
          <p className="text-sm text-foreground/50">Couldn&rsquo;t load the order forecast.</p>
        )}
      </Section>

      {/* Outlet order forecast */}
      <Section title="Order forecast by outlet" note="Split by each outlet's own recent share of volume">
        {outletForecast.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {outletForecast.data?.status === "ok" && <OutletOrderForecastTable data={outletForecast.data} />}
        {outletForecast.data?.status === "error" && (
          <p className="text-sm text-foreground/50">Couldn&rsquo;t load the outlet forecast.</p>
        )}
        <p className="mt-3 text-xs text-foreground/50">
          Each outlet gets its real share of order volume over the trailing 30 days applied to the company-wide
          forecast above &mdash; not a per-customer prediction of which outlet they&rsquo;ll use. Assumes each
          outlet keeps its current share, so it won&rsquo;t catch one outlet growing faster than another.
        </p>
      </Section>

      {/* Top products — stock coverage */}
      <Section title="Top products — stock coverage" note="Top 15 by units sold, trailing 30 days">
        {stockCoverage.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {stockCoverage.data?.status === "ok" && <TopProductStockCoverageTable data={stockCoverage.data} />}
        {stockCoverage.data?.status === "error" && (
          <p className="text-sm text-foreground/50">Couldn&rsquo;t load stock coverage.</p>
        )}
        <p className="mt-3 text-xs text-foreground/50">
          Days of cover = current stock &divide; average daily sales (trailing 30 days). Flagged when under{" "}
          {stockCoverage.data?.low_stock_threshold_days ?? 3} days &mdash; the same threshold used for low-stock
          alerts elsewhere, not a longer requirement, since most of these are fresh produce and dairy restocked
          close to daily. &ldquo;Data issue&rdquo; means recorded stock is negative &mdash; sold past what&rsquo;s
          on hand, worth checking separately from an ordinary low-stock case.
        </p>
      </Section>

      {/* Stock alerts by outlet */}
      <Section title="Stock alerts by outlet" note="Actively-selling products only">
        {stockAlerts.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {stockAlerts.data?.status === "ok" && <StockAlertsByOutletTable data={stockAlerts.data} />}
        {stockAlerts.data?.status === "error" && (
          <p className="text-sm text-foreground/50">Couldn&rsquo;t load stock alerts.</p>
        )}
      </Section>

      {/* Executive summary */}
      <Section title="Executive summary">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <ExecutiveTile label="Customers analyzed" value={formatNumber(s.total_customers)} />
          <ExecutiveTile
            label="With a usable pattern"
            value={formatNumber(s.predictable_customers)}
            sub={`${((s.predictable_customers / s.total_customers) * 100).toFixed(0)}% of all customers`}
          />
          <ExecutiveTile label="Approaching / due" value={formatNumber(s.due.count)} tone="warning" />
          <ExecutiveTile label="Overdue" value={formatNumber(s.overdue.count)} tone="critical" />
          <ExecutiveTile label="Expected orders" value={formatNumber(s.expected_orders)} />
          <ExecutiveTile
            label="Expected revenue"
            value={formatCurrency(s.expected_revenue)}
            sub={`${formatCurrency(s.high_confidence_expected_revenue)} high-confidence`}
          />
        </div>
        <p className="mt-3 text-xs text-foreground/50">
          &ldquo;Customers analyzed&rdquo; is everyone who has ever ordered. &ldquo;With a usable pattern&rdquo; is
          the smaller group (3+ orders with a consistent gap) Celeste can actually predict for — most of the rest
          have only 1&ndash;2 orders and don&rsquo;t have an individual pattern yet. Expected revenue is the
          typical size of each customer&rsquo;s next order, not a business-wide forecast.
        </p>
      </Section>

      {/* Purchase pattern */}
      <Section title="Purchase pattern" note={`${formatNumber(s.predictable_customers)} predictable`}>
        <div className="flex flex-wrap items-center gap-8">
          <div className="w-56 flex-shrink-0">
            <DonutChart
              data={PATTERN_ROWS.map((r) => ({ name: r.label, value: pattern[r.id] }))}
              colors={PATTERN_ROWS.map((r) => r.color)}
              formatValue={(v) => formatNumber(v)}
              showLegend={false}
            />
          </div>
          <div className="flex flex-col gap-3">
            {PATTERN_ROWS.map((r) => (
              <div key={r.id} className="flex items-center gap-2 text-sm">
                <span className="h-2.5 w-2.5 flex-shrink-0 rounded-full" style={{ backgroundColor: r.color }} />
                <span className="font-medium text-foreground-strong">{r.label}</span>
                <span className="text-foreground/50">
                  {formatNumber(pattern[r.id])} ({patternTotal ? Math.round((pattern[r.id] / patternTotal) * 100) : 0}%)
                </span>
              </div>
            ))}
          </div>
        </div>
        <p className="mt-3 text-xs text-foreground/50">
          Excluded: orders far more often than a real shopper would — likely an internal or staff account, not
          an individual customer.
        </p>
      </Section>

      {/* Overdue severity breakdown moved into Customer lifecycle below
          (Lapsing/Churned tiles + the value-mix bars) — having it here too,
          with different thresholds (3 tiers here vs. 2 there), meant the
          same customer could read as "Critical" in one section and
          "Lapsing" in the other. One place for that answer now. */}

      {/* Customer lifecycle */}
      {lifecycle.isLoading && (
        <div className="mt-4 h-40 animate-pulse rounded-2xl border border-border bg-chart-surface" />
      )}
      {lifecycle.data?.status === "ok" && <CustomerLifecycleSection lifecycle={lifecycle.data} />}

      {/* Outlet-wise customer behavior */}
      <Section title="Outlet-wise customer behavior" note="Celeste's 5 retail outlets only">
        {outletBehavior.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {outletBehavior.data?.status === "ok" && <OutletBehaviorTable outlets={outletBehavior.data.outlets} />}
        {outletBehavior.data?.status === "error" && (
          <p className="text-sm text-foreground/50">Couldn&rsquo;t load outlet behavior.</p>
        )}
      </Section>

      {/* Customer table */}
      <Section title="Customers">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.label}
                type="button"
                onClick={() => changeStatus(tab.id)}
                className={`rounded-full px-3 py-1 text-xs font-medium ${
                  status === tab.id ? "bg-accent-soft text-accent" : "text-foreground/60 hover:text-foreground"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <select
            value={sort}
            onChange={(e) => changeSort(e.target.value)}
            className="rounded-lg border border-border bg-background px-2 py-1 text-xs text-foreground-strong"
          >
            {SORT_OPTIONS.map((opt) => (
              <option key={opt.id} value={opt.id}>
                Sort: {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-foreground/60">
                <th className="pb-2 font-medium">Customer</th>
                <th className="pb-2 font-medium">Last order</th>
                <th className="pb-2 font-medium">Normal gap</th>
                <th className="pb-2 font-medium">Expected window</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 text-right font-medium">Lifetime revenue</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <CustomerRow
                  key={row.partner_id}
                  row={row}
                  expanded={expandedId === row.partner_id}
                  onToggle={() => setExpandedId(expandedId === row.partner_id ? null : row.partner_id)}
                />
              ))}
            </tbody>
          </table>
          {rows.length === 0 && !list.isLoading && (
            <p className="py-6 text-center text-sm text-foreground/50">No customers in this view.</p>
          )}
        </div>

        {total > PAGE_SIZE && (
          <div className="mt-3 flex items-center justify-between text-xs text-foreground/60">
            <span>
              {formatNumber(offset + 1)}&ndash;{formatNumber(Math.min(offset + PAGE_SIZE, total))} of {formatNumber(total)}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded-full border border-border px-3 py-1 font-medium disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded-full border border-border px-3 py-1 font-medium disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </Section>

      {/* Prediction performance — placeholder until the prediction -> actual
          tracking loop has had real time to accumulate evaluated
          predictions. Showing a score today would mean inventing one. */}
      <Section title="Prediction performance">
        <p className="text-sm text-foreground/60">
          Building history. Celeste will start comparing these predictions against actual orders as new data
          arrives, and this section will show timing and value accuracy once enough predictions have been
          evaluated.
        </p>
      </Section>

      <p className="mt-4 text-xs text-foreground/40">
        Not shown yet: predicted next-basket contents and an order-value range — planned for a later update.
      </p>
    </div>
  );
}
