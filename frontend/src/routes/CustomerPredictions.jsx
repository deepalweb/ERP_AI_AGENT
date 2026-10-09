import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  fetchCustomerLifecycle,
  fetchCustomerNextBasket,
  fetchCustomerOrderForecastChart,
  fetchCustomerProfile,
  fetchCustomerSearch,
  fetchCustomerOutletBehavior,
  fetchCustomerPredictions,
  fetchCustomerPredictionsSummary,
  fetchOutletOrderForecast,
} from "../api";
import Section from "../design/Card";
import CustomerGrowthOverview from "../design/CustomerGrowthOverview";
import Pill from "../design/Pill";
import DonutChart from "../charts/DonutChart";
import OrderForecastChart from "../charts/OrderForecastChart";
import { LIFECYCLE_LABEL, TICKET_PRIORITY_LABEL, VALUE_TIER_LABEL } from "../design/customerVocabulary";
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

/** True once `isLoading` has been true for longer than delayMs -- lets a
 * slow section say something more honest than a silently-frozen skeleton
 * (a real UX complaint: this page's summary query can take 20+ seconds and
 * previously gave no sign it was still working). */
function useSlowFlag(isLoading, delayMs = 6000) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!isLoading) {
      setSlow(false);
      return undefined;
    }
    const t = setTimeout(() => setSlow(true), delayMs);
    return () => clearTimeout(t);
  }, [isLoading, delayMs]);
  return slow;
}

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
/** Frontend Intelligence Integration, Phase 2 (second half): the Customer
 * Lookup Design Phase A/B work (masked search, lifecycle category, value
 * tier, recent-vs-prior deterioration) existed only inside Ask Celeste's
 * CustomerLookupCard until now -- this is the same GET /customers/{id}/
 * profile call, rendered on the Customers page itself. No raw phone/
 * email anywhere; no churn score; no blended deterioration verdict, same
 * discipline as the chat card. */
function CustomerProfileSection({ partnerId }) {
  const profile = useQuery({
    queryKey: ["customerProfile", partnerId],
    queryFn: () => fetchCustomerProfile(partnerId),
    enabled: Boolean(partnerId),
  });

  if (profile.isLoading) return <p className="mt-3 text-sm text-foreground/50">Loading profile…</p>;
  if (profile.data?.status !== "ok") return null;

  const c = profile.data.customer;
  const d = c.deterioration;

  return (
    <div className="mt-3 space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <ProfileStat label="Lifecycle" value={LIFECYCLE_LABEL[c.lifecycle_category] ?? "—"} />
        <ProfileStat label="Value tier" value={VALUE_TIER_LABEL[c.value_tier] ?? "—"} />
        <ProfileStat label="Lifetime orders" value={formatNumber(c.order_count)} />
        <ProfileStat label="Lifetime revenue" value={formatCurrency(c.lifetime_revenue)} />
      </div>

      {d && (
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-foreground/50">
            Recent vs. prior activity ({d.window_days}d windows)
          </div>
          <div className="mt-2 grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-border p-3">
              <div className="text-xs text-foreground/50">Recent {d.window_days}d</div>
              <div className="mt-1 text-foreground-strong">{formatNumber(d.recent.orders)} orders</div>
              <div className="text-foreground-strong">{formatCurrency(d.recent.revenue)}</div>
              <div className="text-xs text-foreground/50">
                {d.recent.avg_order_value != null ? `${formatCurrency(d.recent.avg_order_value)} AOV` : "no AOV"}
              </div>
            </div>
            <div className="rounded-lg border border-border p-3">
              <div className="text-xs text-foreground/50">Prior {d.window_days}d</div>
              <div className="mt-1 text-foreground-strong">{formatNumber(d.prior.orders)} orders</div>
              <div className="text-foreground-strong">{formatCurrency(d.prior.revenue)}</div>
              <div className="text-xs text-foreground/50">
                {d.prior.avg_order_value != null ? `${formatCurrency(d.prior.avg_order_value)} AOV` : "no AOV"}
              </div>
            </div>
          </div>
          <p className="mt-2 text-xs text-foreground/50">
            {d.days_since_last_order != null ? `Last order ${d.days_since_last_order}d ago` : "No last-order date available"}
            {d.historical_reorder_interval_days != null && ` — historically orders about every ${d.historical_reorder_interval_days}d`}
            {d.cadence_ratio != null && ` (${d.cadence_ratio}× normal cadence)`}
            . Raw comparison only — not a churn score or a flagged risk.
          </p>
        </div>
      )}

      <CustomerContextSection context={c.context} />

      <p className="text-xs text-foreground/40">Data through {profile.data.as_of ?? "unknown"}</p>
    </div>
  );
}

/** Returns/support tickets/loyalty points for this one customer -- small,
 * individually-real signals (checked live: 89/1,038 returns, 31/79
 * tickets, 505/77,745 loyalty cards are actually tied to a customer),
 * too thin for a population-level rate but genuinely useful here: did
 * THIS lapsing customer also file a complaint or return something. */
function CustomerContextSection({ context }) {
  if (!context) return null;
  const { returns, support_tickets: tickets, loyalty } = context;
  const hasAnything = returns.count > 0 || tickets.count > 0 || loyalty;

  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Returns, support &amp; loyalty</div>
      {!hasAnything ? (
        <p className="mt-1 text-sm text-foreground/50">No returns, support tickets, or loyalty activity on record.</p>
      ) : (
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-border p-3">
            <div className="text-xs text-foreground/50">Returns</div>
            <div className="mt-1 text-foreground-strong">{formatNumber(returns.count)}</div>
            {returns.count > 0 && <div className="text-xs text-foreground/50">{formatCurrency(returns.total_value)} total</div>}
            {returns.recent.map((r) => (
              <div key={r.name} className="mt-1 text-xs text-foreground/60">{r.name} · {formatDate(r.date)} · {formatCurrency(r.amount)}</div>
            ))}
          </div>
          <div className="rounded-lg border border-border p-3">
            <div className="text-xs text-foreground/50">Support tickets</div>
            <div className="mt-1 text-foreground-strong">
              {formatNumber(tickets.count)}
              {tickets.open_count > 0 && <span className="ml-1 text-warning">({tickets.open_count} open)</span>}
            </div>
            {tickets.recent.map((t) => (
              <div key={t.name} className="mt-1 text-xs text-foreground/60">
                {t.name} · {t.stage ?? "Unknown stage"} · {TICKET_PRIORITY_LABEL[t.priority] ?? t.priority}
              </div>
            ))}
          </div>
          <div className="rounded-lg border border-border p-3">
            <div className="text-xs text-foreground/50">Loyalty</div>
            {loyalty ? (
              <>
                <div className="mt-1 text-foreground-strong">{formatNumber(loyalty.points)} points</div>
                <div className="text-xs text-foreground/50">{loyalty.programs.join(", ")}</div>
              </>
            ) : (
              <div className="mt-1 text-sm text-foreground/50">Not a member</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ProfileStat({ label, value }) {
  return (
    <div>
      <div className="text-xs text-foreground/50">{label}</div>
      <div className="text-sm font-medium text-foreground-strong">{value}</div>
    </div>
  );
}

function CustomerLookupSection() {
  // ?customer=<partner_id> -- deep link from Ask Celeste's CustomerLookupCard
  // ("Open customer profile"). partner_id is an internal numeric id, never
  // PII, so it's safe in a URL; read once on mount so a manager clearing
  // the selection below isn't fought by a stale param on the next render.
  const [searchParams] = useSearchParams();
  const [form, setForm] = useState({ name: "", email: "", phone: "" });
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState(null);
  const [selectedId, setSelectedId] = useState(() => {
    const raw = searchParams.get("customer");
    const id = raw ? Number(raw) : NaN;
    return Number.isInteger(id) ? id : null;
  });

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
    <Section title="Find a customer" note="Search by name, phone, or email — masked results only, never a raw phone or email">
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
                    {r.phone_masked ?? "no phone"} &middot; {formatNumber(r.order_count)} orders
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {selectedId != null && (
        <div className="mt-4 rounded-xl border border-border bg-background p-4">
          <div className="flex items-center justify-between">
            {selected ? (
              <div>
                <div className="font-medium text-foreground-strong">{selected.name}</div>
                <div className="text-xs text-foreground/50">
                  {selected.phone_masked ?? "no phone"} &middot; {formatNumber(selected.order_count)} lifetime orders
                </div>
              </div>
            ) : (
              // Arrived via ?customer= with no search performed yet -- the
              // profile section below fetches by id directly, same as a
              // normal search result would, so there's nothing more needed
              // here than a way back to a fresh search.
              <div className="text-xs text-foreground/50">Opened from Ask Celeste</div>
            )}
            {(selected ? results.length > 1 : true) && (
              <button type="button" onClick={() => setSelectedId(null)} className="text-xs text-accent">
                {selected ? "Change" : "Search instead"}
              </button>
            )}
          </div>
          <CustomerProfileSection partnerId={selectedId} />
          <NextBasketSection partnerId={selectedId} enabled={true} />
        </div>
      )}
    </Section>
  );
}

const VALUE_TIER_TONE = { high: "good", mid: "neutral", low: "neutral" };

/** Plain-language "how late/early" -- the same days_overdue/days_until_due
 * fields already backing the status pill and severity, just said in words
 * instead of left for a reader to infer from two date ranges. */
function timingLine(row) {
  if (row.status === "overdue") return `${formatNumber(row.days_overdue)}d overdue`;
  if (row.status === "due") return "Due now";
  if (row.days_until_due != null) return `Due in ${formatNumber(row.days_until_due)}d`;
  return null;
}

function CustomerRow({ row, expanded, onToggle }) {
  const meta = STATUS_META[row.status];
  const severity = row.overdue_severity ? SEVERITY_META[row.overdue_severity] : null;
  const [gapLow, gapHigh] = gapRange(row);
  const timing = timingLine(row);
  return (
    <>
      <tr className="cursor-pointer border-t border-border hover:bg-background" onClick={onToggle}>
        <td className="py-2 pr-2">
          <div className="font-medium text-foreground-strong">{row.name}</div>
          <div
            className="text-xs text-foreground/50"
            title="Confidence is based on how many orders this customer has, not on measured prediction accuracy -- this is a heuristic window, not a calibrated probability."
          >
            {row.order_count} orders &middot; {row.confidence === "high" ? "High confidence" : "Moderate confidence"}
          </div>
        </td>
        <td className="py-2 pr-2 text-foreground/80">{formatDate(row.last_order_date)}</td>
        <td className="py-2 pr-2 text-foreground/80">
          {gapLow}&ndash;{gapHigh}d
        </td>
        <td className="py-2 pr-2 text-foreground/80">
          {formatDate(row.expected_window_start)} &ndash; {formatDate(row.expected_window_end)}
          {timing && <div className="text-xs text-foreground/50">{timing}</div>}
        </td>
        <td className="py-2 pr-2">
          <div className="flex flex-wrap items-center gap-1">
            {row.value_tier && <Pill tone={VALUE_TIER_TONE[row.value_tier]}>{VALUE_TIER_LABEL[row.value_tier]}</Pill>}
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
            <th className="pb-2 text-right font-medium">Repeat rate</th>
            <th className="pb-2 text-right font-medium">2nd-order rate</th>
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
              <td className="py-1.5 text-right">
                {o.repeat_rate_pct == null ? "—" : `${o.repeat_rate_pct}%`}
              </td>
              <td className="py-1.5 text-right">
                {o.second_order_rate_pct == null ? "—" : `${o.second_order_rate_pct}%`}
              </td>
              <td className="py-1.5 text-right">{formatCurrency(o.avg_order_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-foreground/50">
        Repeat rate: share of each outlet&rsquo;s own customers who ordered there more than once. 2nd-order rate:
        of customers acquired at that outlet (their first order ever), the share who placed a second order — anywhere
        — within 30 days. &ldquo;—&rdquo; means too few customers yet to show a rate rather than one that looks
        falsely precise.
      </p>
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

/** Channel & churn -- does churn/lapsing cluster on one delivery platform.
 * The per-category counts/revenue this section used to also show (a flat
 * tile per lifecycle stage) were a straight duplicate of the Lifecycle x
 * customer value heatmap above (CustomerGrowthOverview) -- same 8
 * categories, same counts, just without the value-tier breakdown or the
 * click-through to real customers the heatmap has. Removed rather than
 * kept "for redundancy" (Customer Intelligence review, 2026-10-09): this
 * is the one piece of that old section genuinely NOT shown anywhere else
 * -- churn/lapsing broken out by channel instead of by value tier. See
 * backend/app/services/customer_predictions.py compute_lifecycle_summary()
 * for the exact rule behind each lifecycle stage this still references. */
function ChannelChurnSection({ lifecycle }) {
  if (!lifecycle?.channel_breakdown) return null;
  return (
    <Section title="Channel & churn" note={`${formatNumber(lifecycle.total_customers)} customers · data through ${formatDate(lifecycle.as_of_date)}`}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {lifecycle.channel_breakdown.map((c) => (
          <ChannelTile key={c.id} channel={c} />
        ))}
      </div>
      <p className="mt-2 text-xs text-foreground/50">
        Churn/lapsing rate is a share of each channel&rsquo;s own customers with an established pattern
        (Active/Due/Lapsing/Churned/Win-back) — not of every customer who&rsquo;s ever used that channel, so
        a channel full of brand-new customers doesn&rsquo;t look falsely healthy.
      </p>
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
  const outletForecast = useQuery({
    queryKey: ["outletOrderForecast"],
    queryFn: () => fetchOutletOrderForecast(5),
  });
  const lifecycle = useQuery({ queryKey: ["customerLifecycle"], queryFn: fetchCustomerLifecycle });
  const summarySlow = useSlowFlag(summary.isLoading);

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

  // Previously this whole page waited behind `if (summary.isLoading) return
  // <skeleton>` before ANY section mounted -- every section below already
  // has its own independent loading/error state, but they never got the
  // chance to render while that one (often slow) query was pending. Now the
  // page shell and every summary-independent section render immediately;
  // only the two sections that actually need `summary`'s data (Executive
  // summary, Purchase pattern) wait on it, and do so visibly.
  const s = summary.data?.status === "ok" ? summary.data : null;
  const rows = list.data?.status === "ok" ? list.data.results : [];
  const total = list.data?.status === "ok" ? list.data.total : 0;
  const pattern = s?.pattern_distribution;
  const patternTotal = pattern
    ? (pattern.ten_plus ?? 0) + (pattern.three_to_nine ?? 0) + (pattern.one_to_two ?? 0) + (pattern.excluded ?? 0)
    : 0;

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
        {s?.as_of_date && <span className="text-xs text-foreground/60">Data through {formatDate(s.as_of_date)}</span>}
      </div>

      <CustomerGrowthOverview
        summary={s}
        lifecycle={lifecycle.data?.status === "ok" ? lifecycle.data : null}
        loading={summary.isFetching || lifecycle.isFetching}
        onRetry={() => { summary.refetch(); lifecycle.refetch(); }}
        onViewCustomers={() => {
          changeStatus("due");
          document.getElementById("customer-list")?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
      />

      {/* Find a customer */}
      <CustomerLookupSection />

      {/* Order forecast -- stock coverage/alerts moved to the dedicated
          Inventory page (/inventory), which has the real charts for them;
          this stays here since it's a customer order-volume forecast, not
          an inventory signal. */}
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

      {/* Executive summary */}
      <Section title="Executive summary">
        {summary.isLoading && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-16 animate-pulse rounded-lg border border-border bg-chart-surface" />
              ))}
            </div>
            <p className="mt-2 text-sm text-foreground/50">
              {summarySlow
                ? "This is taking longer than usual. You can keep using the rest of the page while this loads."
                : "Loading…"}
            </p>
          </>
        )}
        {!summary.isLoading && !s && (
          <p className="text-sm text-foreground/60">
            Couldn&rsquo;t load the executive summary.{" "}
            <button type="button" onClick={() => summary.refetch()} className="text-accent underline">
              Try again
            </button>
          </p>
        )}
        {s && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              <ExecutiveTile label="Customers analyzed" value={formatNumber(s.total_customers)} />
              <ExecutiveTile
                label="With a usable pattern"
                value={formatNumber(s.predictable_customers)}
                sub={`${((s.predictable_customers / s.total_customers) * 100).toFixed(0)}% of all customers`}
              />
              <ExecutiveTile label="Approaching / due" value={formatNumber(s.due.count)} tone="warning" />
              <ExecutiveTile label="Overdue" value={formatNumber(s.overdue.count)} tone="critical" />
              <ExecutiveTile
                label="Typical baskets across due customers"
                value={formatCurrency(s.expected_revenue)}
                sub={`${formatCurrency(s.high_confidence_expected_revenue)} from customers with 10+ orders`}
              />
            </div>
            <p className="mt-3 text-xs text-foreground/50">
              &ldquo;Customers analyzed&rdquo; is everyone who has ever ordered. &ldquo;With a usable pattern&rdquo; is
              the smaller group (3+ orders with a consistent gap) Celeste can actually predict for — most of the rest
              have only 1&ndash;2 orders and don&rsquo;t have an individual pattern yet. Typical basket value sums historical average order values across due customers; it is not a sales forecast or incremental revenue.
            </p>
          </>
        )}
      </Section>

      {/* Purchase pattern */}
      <Section title="Purchase pattern" note={s ? `${formatNumber(s.predictable_customers)} predictable` : undefined}>
        {summary.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {!summary.isLoading && !s && <p className="text-sm text-foreground/60">Couldn&rsquo;t load purchase pattern.</p>}
        {s && (
          <>
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
          </>
        )}
      </Section>

      {/* Overdue severity breakdown moved into Customer lifecycle below
          (Lapsing/Churned tiles + the value-mix bars) — having it here too,
          with different thresholds (3 tiers here vs. 2 there), meant the
          same customer could read as "Critical" in one section and
          "Lapsing" in the other. One place for that answer now. */}

      {/* Channel & churn -- the lifecycle-stage tile grid that used to live
          here was a duplicate of the heatmap above it (CustomerGrowthOverview);
          removed, see ChannelChurnSection's own docstring. */}
      {lifecycle.isLoading && (
        <div className="mt-4 h-40 animate-pulse rounded-2xl border border-border bg-chart-surface" />
      )}
      {lifecycle.data?.status === "ok" && <ChannelChurnSection lifecycle={lifecycle.data} />}

      {/* Outlet-wise customer behavior */}
      <Section title="Outlet-wise customer behavior" note="Configured Celeste outlets only">
        {outletBehavior.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {outletBehavior.data?.status === "ok" && <OutletBehaviorTable outlets={outletBehavior.data.outlets} />}
        {outletBehavior.data?.status === "error" && (
          <p className="text-sm text-foreground/50">Couldn&rsquo;t load outlet behavior.</p>
        )}
      </Section>

      {/* Customer table */}
      <div id="customer-list" className="scroll-mt-4">
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
                <th className="pb-2 pr-2 font-medium">Customer</th>
                <th className="pb-2 pr-2 font-medium">Last order</th>
                <th className="pb-2 pr-2 font-medium">Normal gap</th>
                <th className="pb-2 pr-2 font-medium">Expected window</th>
                <th className="pb-2 pr-2 font-medium">Status</th>
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
          {list.isLoading && <p className="py-6 text-center text-sm text-foreground/50">Loading customers&hellip;</p>}
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

      </div>

      {/* Prediction performance — placeholder until the prediction -> actual
          tracking loop has had real time to accumulate evaluated
          predictions. Showing a score today would mean inventing one. */}
      <Section title="Prediction performance">
        <p className="text-sm text-foreground/60">
          Timing accuracy and sales uplift have not been established here. Purchase windows are heuristics;
          validating them requires historical backtesting and measured outcomes from customer actions.
        </p>
      </Section>


    </div>
  );
}
