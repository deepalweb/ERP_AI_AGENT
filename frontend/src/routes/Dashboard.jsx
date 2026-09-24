import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  fetchBrandMix,
  fetchBusinessHealth,
  fetchCustomerIntelligence,
  fetchDashboardSummary,
  fetchInventoryDepth,
  fetchOperationsSummary,
  fetchOrdersByHour,
} from "../api";
import Section from "../design/Section";
import Pill, { pillTone } from "../design/Pill";
import RankedBarChart from "../charts/RankedBarChart";
import DonutChart from "../charts/DonutChart";
import { formatCurrency, formatNumber, monthLabel } from "../format";

const CANCEL_RATE_THRESHOLDS = { warning: 5, critical: 10 };

function DomainBar({ domain }) {
  const color = domain.status === "critical" ? "bg-critical" : domain.status === "warning" ? "bg-warning" : "bg-good";
  return (
    <div className="py-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-foreground-strong">{domain.label}</span>
        <span className="text-foreground/70">{domain.score}/100</span>
      </div>
      <div className="mt-1 h-2 rounded-full bg-border">
        <div className={`h-2 rounded-full ${color}`} style={{ width: `${domain.score}%` }} />
      </div>
      {domain.drivers?.[0] && <div className="mt-1 text-xs text-foreground/60">{domain.drivers[0]}</div>}
    </div>
  );
}

function StoreTable({ branches }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Store</th>
            <th className="pb-2 font-medium">Orders</th>
            <th className="pb-2 font-medium">Revenue</th>
            <th className="pb-2 font-medium">Cancel rate</th>
            <th className="pb-2 font-medium">Fulfillment delay</th>
          </tr>
        </thead>
        <tbody>
          {branches.map((b) => (
            <tr key={b.name} className="border-t border-border">
              <td className="py-1.5">{b.name}</td>
              <td className="py-1.5">{formatNumber(b.orders)}</td>
              <td className="py-1.5">{formatCurrency(b.revenue)}</td>
              <td className="py-1.5">
                <Pill tone={pillTone(b.cancellation_rate_pct, CANCEL_RATE_THRESHOLDS)}>{b.cancellation_rate_pct}%</Pill>
              </td>
              <td className="py-1.5">{b.delayed_rate_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SegmentTiles({ segments }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {segments.map((s) => (
        <div key={s.id} className="rounded-lg border border-border p-3">
          <div className="text-lg font-bold text-foreground-strong">{formatNumber(s.count)}</div>
          <div className="text-xs font-medium text-foreground/60">{s.label}</div>
          <div className="text-xs text-foreground/50">{formatCurrency(s.revenue)} lifetime</div>
        </div>
      ))}
    </div>
  );
}

function HourBar({ hours, maxOrders }) {
  return (
    <div className="flex h-40 items-end gap-1">
      {hours.map((h) => (
        <div key={h.hour} className="flex flex-1 flex-col items-center gap-1" title={`${h.hour}:00 — ${formatNumber(h.orders)} orders`}>
          <div
            className="w-full rounded-t bg-[var(--series-1)]"
            style={{ height: `${maxOrders ? (h.orders / maxOrders) * 100 : 0}%`, minHeight: 2 }}
          />
          {h.hour % 3 === 0 && <span className="text-[10px] text-foreground/50">{h.hour}</span>}
        </div>
      ))}
    </div>
  );
}

/** The reference dashboard — comparative/reference views a manager can't get
 * from a glance at the header or from Home's findings feed: business health,
 * store-vs-store comparison, inventory value, customer segments, products &
 * categories, and hourly order distribution. Every figure here is real —
 * checked against the live backend before wiring.
 *
 * Deliberately narrower than it used to be. This page originally carried
 * every raw KPI tile (revenue/orders/AOV/margin, fulfillment %, stock
 * counts, channel share), plus cancellation-reasons and a profitability
 * waterfall — trimmed because each of those either just restates a single
 * number the header already shows, duplicates a Celeste finding's own
 * breakdown when something's actually flagged, or (profitability) was
 * already caveated in its own caption as a partial/incomplete P&L. What's
 * left is what has no other place to be seen: cross-store comparison,
 * category/segment breakdowns, and distributions — reference data, not a
 * second copy of what Home already surfaces.
 *
 * This used to be the "/" front page; it moved to "/dashboard" (secondary
 * nav item) when Home became the findings briefing. */
export default function Dashboard() {
  const dashboard = useQuery({ queryKey: ["dashboardSummary"], queryFn: fetchDashboardSummary });
  const health = useQuery({ queryKey: ["businessHealth"], queryFn: fetchBusinessHealth });
  const operations = useQuery({ queryKey: ["operationsSummary"], queryFn: fetchOperationsSummary });
  const inventoryDepth = useQuery({ queryKey: ["inventoryDepth"], queryFn: fetchInventoryDepth });
  const customers = useQuery({ queryKey: ["customerIntelligence"], queryFn: fetchCustomerIntelligence });
  const brandMix = useQuery({ queryKey: ["brandMix"], queryFn: fetchBrandMix });
  const ordersByHour = useQuery({ queryKey: ["ordersByHour"], queryFn: fetchOrdersByHour });

  if (dashboard.isLoading) {
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

  if (dashboard.data?.status !== "ok") {
    return <div className="px-6 py-6 text-sm text-foreground/60">Couldn&rsquo;t load the business dashboard.</div>;
  }

  const { sales, top_products } = dashboard.data;

  // /intelligence/health's own payload has a `status` field (the health
  // verdict) that overwrites the route wrapper's `"status": "ok"` — so
  // `overall` being a number is the real success signal here.
  const healthOk = typeof health.data?.overall === "number";

  const maxHourOrders = ordersByHour.data?.status === "ok"
    ? Math.max(...ordersByHour.data.hours.map((h) => h.orders))
    : 0;

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-foreground-strong">Business dashboard</h1>
        <span className="text-xs text-foreground/60">Data through {monthLabel(sales.headline_month)}</span>
      </div>

      {/* Customer Purchase Intelligence dropped out of the primary nav (see
          Header.jsx) once the nav was trimmed to Home/Actions/Explore/Ask —
          this keeps it one click away instead of removing access to it. */}
      <Link
        to="/customers/predictions"
        className="mt-3 flex items-center justify-between rounded-xl border border-accent-border bg-accent-soft px-4 py-3 text-sm font-medium text-accent hover:bg-accent-soft/80"
      >
        Customer Purchase Intelligence — when customers are likely to order again
        <span aria-hidden="true">&rarr;</span>
      </Link>

      {/* Business health */}
      {healthOk && (
        <Section title="Business health" note={`${health.data.overall}/100 — Sales · Operations · Inventory · Customers, equally weighted`}>
          <div className="grid gap-x-8 sm:grid-cols-2">
            {health.data.domains.map((d) => (
              <DomainBar key={d.id} domain={d} />
            ))}
          </div>
          {health.data.excluded?.length > 0 && (
            <p className="mt-2 text-xs text-foreground/50">
              Not scored: {health.data.excluded.map((e) => e.label).join(", ")} — {health.data.excluded[0].reason}
            </p>
          )}
        </Section>
      )}

      {/* Store performance */}
      {operations.data?.status === "ok" && (
        <Section title="Store performance">
          <StoreTable branches={operations.data.branches} />
        </Section>
      )}

      {/* Inventory value */}
      {inventoryDepth.data?.status === "ok" && (
        <Section title="Inventory value">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {inventoryDepth.data.segments.map((s) => (
              <div key={s.id} className="rounded-lg border border-border p-3">
                <div className="text-lg font-bold text-foreground-strong">{formatCurrency(s.value)}</div>
                <div className="text-xs font-medium text-foreground/60">{s.label}</div>
                <div className="text-xs text-foreground/50">{formatNumber(s.count)} products · {formatNumber(s.units)} units</div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Customers */}
      {customers.data?.status === "ok" && (
        <Section title="Customers" note={`As of ${customers.data.as_of_date}`}>
          <SegmentTiles segments={customers.data.segments} />
          {customers.data.at_risk_customers.length > 0 && (
            <div className="mt-3 text-xs text-foreground/60">
              Top at-risk by revenue: {customers.data.at_risk_customers.slice(0, 3).map((c) => c.name).join(", ")}
            </div>
          )}
        </Section>
      )}

      {/* Products & categories */}
      {brandMix.data?.status === "ok" && (
        <Section
          title="Products & categories"
          note={`${brandMix.data.departments.find((d) => d.department === "Unlinked")?.revenue_share_pct ?? 0}% unlinked to a department`}
        >
          <div className="grid gap-4 xl:grid-cols-2">
            <div>
              <div className="mb-1 text-xs font-medium text-foreground/70">Revenue by department</div>
              <DonutChart data={brandMix.data.departments} nameKey="department" valueKey="revenue" formatValue={formatCurrency} />
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-foreground/70">Top products by revenue</div>
              <RankedBarChart data={top_products} nameKey="name" valueKey="revenue" formatValue={formatCurrency} />
            </div>
          </div>
        </Section>
      )}

      {/* Orders by hour */}
      {ordersByHour.data?.status === "ok" && (
        <Section title="Orders by hour" note="All-time distribution — this business's history is only a few months">
          <HourBar hours={ordersByHour.data.hours} maxOrders={maxHourOrders} />
        </Section>
      )}
    </div>
  );
}
