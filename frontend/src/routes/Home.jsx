import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  ArrowDown,
  ArrowUp,
  BarChart3,
  Building2,
  CalendarCheck2,
  Check,
  ChevronRight,
  Clock3,
  Database,
  FileText,
  Layers,
  Package,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  Tag,
  TrendingUp,
  Truck,
  Users,
} from "lucide-react";
import {
  acknowledgeNotifications,
  fetchBrief,
  refreshBrief,
  fetchCases,
  fetchDashboardSummary,
  fetchFindingCase,
  fetchManagementActions,
  fetchManagementSummary,
  fetchMetricInvestigation,
  fetchOperationsSummary,
  fetchOutcomesSummary,
  fetchStockAlerts,
  fetchStoreScope,
} from "../api";
import { DOMAIN_LABEL } from "../design/domains";
import TrendChart from "../charts/TrendChart";
import RadialGauge from "../charts/RadialGauge";
import RankedBarChart from "../charts/RankedBarChart";
import {
  formatCurrency,
  formatNumber,
  formatPct,
  formatPercent,
  monthLabel,
  moveToneClass,
  prettifyMetricId,
} from "../format";
import "./Home.css";

function stamp(value) {
  if (!value) return "Unavailable";
  const date = new Date(
    /[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`,
  );
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}
const count = (value) => (value == null ? "—" : formatNumber(value));
function freshnessMeta(freshness) {
  if (!freshness || freshness.status === "unavailable" || freshness.status == null) {
    return { tone: "unknown", text: "Freshness unavailable" };
  }
  if (freshness.status === "fresh") return { tone: "good", text: "Up to date" };
  const days = freshness.days_stale;
  return {
    tone: "warning",
    text: days != null ? `${days} day${days === 1 ? "" : "s"} behind` : "Behind",
  };
}
const DOMAIN_ICON = {
  sales: BarChart3,
  operations: Settings2,
  inventory: Package,
  customers: Users,
};
const HEALTH_STATUS_COPY = {
  good: "Your business is in a strong position with stable performance across key areas.",
  warning: "Some areas need attention while others remain steady.",
  critical: "Multiple areas need immediate attention right now.",
};
// A "strong position" note next to "4 Critical findings" and a red 30/100
// domain score reads as a contradiction, not reassurance -- reconcile the
// headline with what the page shows right beside it, rather than always
// showing the generic status copy. Only overrides the "good" case (the one
// that can actually read as false reassurance); warning/critical copy
// already acknowledges trouble.
function worstHealthDomain(health) {
  return (
    (health?.domains || []).find((d) => d.status === "critical") ||
    (health?.domains || []).find((d) => d.status === "warning") ||
    null
  );
}
function healthNoteFor(health, criticalCount) {
  if (!health?.status) return null;
  if (health.status === "good" && criticalCount > 0) {
    const worst = worstHealthDomain(health);
    if (worst) {
      return `Overall performance remains healthy, but ${worst.label.toLowerCase()} requires immediate attention.`;
    }
  }
  return HEALTH_STATUS_COPY[health.status] ?? null;
}
// Hero density follow-up: the empty middle zone gets a supporting line
// explaining the headline, built from the same real per-domain driver
// text the domain-health cards/strip already show (health.py's own
// computed explanation for that score) -- not a re-derived or guessed
// number, and specifically NOT a findings count, since health.py's
// domains don't map cleanly onto finding.domain (e.g. "Operations" scores
// off cancellation_rate + delayed_delivery_rate, which carry two
// different finding domains) -- the driver text is the one explanation
// that's guaranteed to actually back the score shown.
function healthSupportingLineFor(health) {
  const worst = worstHealthDomain(health);
  const driver = worst?.drivers?.[0];
  if (!worst || !driver) return null;
  return `${worst.label} score is ${count(worst.score)}/100 — ${driver}`;
}
function level(finding) {
  return finding.category === "opportunity"
    ? "Positive"
    : prettifyMetricId(finding.priority || "Unclassified");
}
function evidence(finding) {
  return (
    finding.root_cause?.checks?.find((check) => check.detail)?.detail ||
    finding.root_cause?.note ||
    "No explanatory evidence supplied. Review the available measurements."
  );
}
function casePath(finding) {
  return `/investigate/${encodeURIComponent(finding.case_id)}${finding.id ? `?action=${encodeURIComponent(finding.id)}` : ""}`;
}
// "Findings by domain" -- a frontend-derived grouping over findings the page
// already fetched (never a new backend endpoint): counts each domain's
// findings by the same 4-tier severity vocabulary (Critical/High/Medium/
// Positive) the rest of this page already uses via level(). Domains with
// zero findings are omitted rather than shown as an empty bar.
const SEVERITY_TIERS = ["Critical", "High", "Medium", "Positive"];
const SEVERITY_BAR_TONE = { Critical: "critical", High: "high", Medium: "medium", Positive: "positive" };
function findingsByDomain(findings) {
  const byDomain = new Map();
  for (const f of findings) {
    if (!f.domain) continue;
    const tier = level(f);
    if (!byDomain.has(f.domain)) byDomain.set(f.domain, { Critical: 0, High: 0, Medium: 0, Positive: 0 });
    const bucket = byDomain.get(f.domain);
    if (tier in bucket) bucket[tier] += 1;
  }
  return [...byDomain.entries()]
    .map(([domain, counts]) => ({
      domain,
      counts,
      total: SEVERITY_TIERS.reduce((sum, t) => sum + counts[t], 0),
    }))
    .filter((d) => d.total > 0)
    .sort((a, b) => b.total - a.total);
}
// Business Controls and Suppliers have no health SCORE (health.py only
// computes Sales/Operations/Inventory/Customers) -- rather than inventing
// one, their "domain health" card shows a real, honest alternative: how
// many of their own findings are currently open, and the worst severity
// among them, both derived from the same already-fetched findings array.
function domainStatusFrom(findings, domainId) {
  const matches = findings.filter((f) => f.domain === domainId);
  if (matches.length === 0) return { count: 0, worst: null };
  const order = ["Critical", "High", "Medium", "Positive"];
  const worst = order.find((tier) => matches.some((f) => level(f) === tier)) ?? null;
  return { count: matches.length, worst };
}
// Phase 5A Task 4: a compact placeholder sized to roughly the final panel's
// own footprint (passed per call site, not a fixed one-size-fits-all block)
// so a slow panel reserves its real space instead of the page jumping once
// data arrives. Visually a shimmering block; "Loading {label}…" stays as
// the accessible text (sr-only), so screen readers get the same
// announcement as before.
function Skeleton({ height = "72px" }) {
  return (
    <div className="hp-skeleton" style={{ height }} aria-hidden="true">
      <div className="hp-skeleton-shimmer" />
    </div>
  );
}
function Message({ query, label, skeletonHeight }) {
  if (query.isPending)
    return skeletonHeight ? (
      <div className="hp-message-loading">
        <span className="sr-only" role="status">Loading {label}…</span>
        <Skeleton height={skeletonHeight} />
      </div>
    ) : (
      <p className="hp-message" role="status">
        Loading {label}…
      </p>
    );
  if (query.isError || query.data?.status === "error")
    return (
      <div className="hp-message" role="alert">
        Could not load {label}.{" "}
        <button onClick={() => query.refetch()}>Try again</button>
      </div>
    );
  return null;
}
function Heading({ number, label, title, icon: Icon, children }) {
  return (
    <div className="hp-section-title">
      <div>
        <span className="hp-kicker">
          {number} / {label}
        </span>
        <h2>
          {Icon && <Icon size={19} className="hp-section-icon" />}
          {title}
        </h2>
      </div>
      {children}
    </div>
  );
}
// Business domain health -- density pass: a compact horizontal status
// strip instead of 6 full cards. Each real scored domain (health.py) shows
// its status dot + score; Suppliers/Controls (no computed score) show an
// honest open-finding count instead of a fabricated 0-100 number. A
// domain's own real driver sentence is still available on hover rather
// than always-visible, since this is a glance-and-move-on strip, not a
// detail view (the detail lives in Investigate).
function DomainHealthStrip({ domains, supplierStatus, controlsStatus }) {
  return (
    <div className="hp-domain-strip">
      {domains.map((item) => {
        const Icon = DOMAIN_ICON[item.id] || BarChart3;
        return (
          <div className="hp-domain-strip-item" key={item.id} title={(item.drivers || [])[0] || "No notable drivers reported."}>
            <span className={`hp-domain-strip-dot hp-domain-bar-${item.status || "good"}`} />
            <Icon size={12} />
            <span className="hp-domain-strip-label">{item.label}</span>
            <strong className="hp-domain-strip-score">{count(item.score)}</strong>
          </div>
        );
      })}
      <div className="hp-domain-strip-item" title="No health score computed for this domain yet.">
        <Truck size={12} />
        <span className="hp-domain-strip-label">Suppliers</span>
        <span className="hp-domain-strip-count">{supplierStatus.count} finding{supplierStatus.count === 1 ? "" : "s"}</span>
      </div>
      <div className="hp-domain-strip-item" title="No health score computed for this domain yet.">
        <ShieldCheck size={12} />
        <span className="hp-domain-strip-label">Controls</span>
        <span className="hp-domain-strip-count">{controlsStatus.count} finding{controlsStatus.count === 1 ? "" : "s"}</span>
      </div>
    </div>
  );
}
// Hero density follow-up: an even lighter version of the strip above,
// for the hero's own middle zone -- plain inline text ("Sales 82 ·
// Operations 65 · ...") rather than bordered chips, so it reads as
// context supporting the headline, not a second copy of the fuller strip
// in section 05 below it.
function MiniDomainStrip({ domains }) {
  return (
    <div className="hp-hero-domain-strip">
      {domains.map((d, i) => (
        <span key={d.id} className="hp-hero-domain-item">
          {i > 0 && <span className="hp-hero-domain-sep">·</span>}
          {d.label} <strong className={`hp-domain-status-${d.status || "good"}`}>{count(d.score)}</strong>
        </span>
      ))}
    </div>
  );
}
// Compact "top issue" card -- same finding data as PriorityCard, denser
// visual treatment (colored left border by severity, one evidence line,
// an explicit as-of stamp) for the paired issues/findings-by-domain row.
function IssueCard({ finding }) {
  const severity = level(finding);
  const asOf = finding.confirm?.period || finding.detected;
  return (
    <article className={`hp-issue-card hp-issue-${severity.toLowerCase()}`}>
      <div className="hp-issue-head">
        <span className={`hp-badge hp-${severity.toLowerCase()}`}>{severity}</span>
        <span className="hp-issue-domain">{DOMAIN_LABEL[finding.domain] || prettifyMetricId(finding.domain)}</span>
      </div>
      <h3 className="hp-issue-title">{finding.title || prettifyMetricId(finding.metric_id)}</h3>
      <p className="hp-issue-evidence">{evidence(finding)}</p>
      <footer className="hp-issue-footer">
        <span className="hp-issue-asof">{asOf ? `As of ${asOf}` : "Period unavailable"}</span>
        {finding.case_id ? (
          <Link to={casePath(finding)}>
            Open investigation <ArrowRight size={13} />
          </Link>
        ) : (
          <span className="hp-muted">No case yet</span>
        )}
      </footer>
    </article>
  );
}
// "Findings by domain" stacked bar -- see findingsByDomain() above for the
// (frontend-derived, real-data) grouping this renders.
function FindingsByDomainBar({ grouped }) {
  if (grouped.length === 0) return <p className="hp-muted">No findings recorded in this briefing.</p>;
  const maxTotal = Math.max(...grouped.map((d) => d.total));
  return (
    <div className="hp-domain-bars">
      {grouped.map((d) => (
        <div className="hp-domain-bar-row" key={d.domain}>
          <span className="hp-domain-bar-label">{DOMAIN_LABEL[d.domain] || prettifyMetricId(d.domain)}</span>
          <div className="hp-domain-bar-track-wrap">
            {/* Bar length is proportionate to this domain's own total
                findings relative to the domain with the most (maxTotal) --
                a plain percentage width, not flex-grow (which only
                compares siblings inside one row, not across rows). */}
            <div className="hp-domain-bar-track" style={{ width: `${Math.max((d.total / maxTotal) * 100, 8)}%` }}>
              {SEVERITY_TIERS.filter((t) => d.counts[t] > 0).map((t) => (
                <div
                  key={t}
                  className={`hp-domain-bar-seg hp-domain-bar-seg-${SEVERITY_BAR_TONE[t]}`}
                  style={{ flexGrow: d.counts[t], flexBasis: 0 }}
                  title={`${t}: ${d.counts[t]}`}
                >
                  {d.counts[t]}
                </div>
              ))}
            </div>
          </div>
          <span className="hp-domain-bar-total">{d.total}</span>
        </div>
      ))}
    </div>
  );
}
function lastComplete(months) {
  const complete = (months || []).filter((m) => !m.is_partial);
  const list = complete.length ? complete : months || [];
  if (!list.length) return [null, null];
  const idx = months.indexOf(list[list.length - 1]);
  return [list[list.length - 1], idx > 0 ? months[idx - 1] : null];
}
function KpiCard({ label, value, delta, deltaUnit = "pct", direction = "higher_is_good", trendData, trendValueKey }) {
  const hasDelta = delta != null;
  const hasTrend = trendData && trendData.length > 1;
  return (
    <div className="hp-kpi-card">
      <span className="hp-kpi-label">{label}</span>
      <strong className="hp-kpi-value">{value}</strong>
      {hasDelta && (
        <span className={`hp-kpi-delta ${moveToneClass(delta, direction)}`}>
          {delta > 0 ? <ArrowUp size={11} /> : delta < 0 ? <ArrowDown size={11} /> : null}
          {deltaUnit === "pp"
            ? `${delta > 0 ? "+" : ""}${delta.toFixed(1)}pp`
            : formatPct(delta)}
        </span>
      )}
      {hasTrend && (
        <div className="hp-kpi-spark">
          <TrendChart data={trendData} xKey="month" valueKey={trendValueKey} compact />
        </div>
      )}
    </div>
  );
}
function MetricSwitch({ options, value, onChange, label }) {
  return (
    <div className="hp-metric-switch" role="group" aria-label={label}>
      {options.map(([key, text]) => (
        <button key={key} aria-pressed={value === key} onClick={() => onChange(key)}>
          {text}
        </button>
      ))}
    </div>
  );
}
// 03 / Key metrics -- 6 KPI cards. Revenue/Orders/AOV/Cancellation come
// from dashboard/summary (unchanged); Margin/Retention are new (real,
// already-registered metrics, just never queried on Home before). Each
// card's sparkline is that metric's own real trend series -- no chart ever
// mixes more than one metric/unit, per the standing rule against combined-
// unit charts.
function KpiSection({ dashboardSummary, grossMargin, customerRetention }) {
  const sales = dashboardSummary.data?.status === "ok" ? dashboardSummary.data.sales : null;
  const [headline, previous] = sales ? lastComplete(sales.months) : [null, null];
  const aov = headline && headline.sale_orders ? headline.revenue / headline.sale_orders : null;
  const prevAov = previous && previous.sale_orders ? previous.revenue / previous.sale_orders : null;
  const aovDelta = aov != null && prevAov ? ((aov - prevAov) / prevAov) * 100 : null;
  const cancellationDelta =
    headline && previous ? headline.cancellation_rate_pct - previous.cancellation_rate_pct : null;
  const aovSeries = sales
    ? sales.months.map((m) => ({ month: m.month, aov: m.sale_orders ? m.revenue / m.sale_orders : 0 }))
    : null;
  const marginTrend = grossMargin.data?.trend;
  const marginAnomaly = grossMargin.data?.anomaly;
  const retentionTrend = customerRetention.data?.trend;
  const retentionAnomaly = customerRetention.data?.anomaly;

  return (
    <section className="hp-key-metrics" aria-label="Key metrics">
      <Heading number="03" label="Key metrics" title="Revenue, orders, margin and retention" icon={BarChart3} />
      <Message query={dashboardSummary} label="key metrics" skeletonHeight="120px" />
      {sales && (
        <div className="hp-kpi-grid hp-kpi-grid-6">
          <KpiCard
            label="Revenue"
            value={formatCurrency(sales.headline_revenue)}
            delta={sales.revenue_growth_pct}
            trendData={sales.months}
            trendValueKey="revenue"
          />
          <KpiCard
            label="Orders"
            value={formatNumber(sales.headline_orders)}
            delta={sales.orders_growth_pct}
            trendData={sales.months}
            trendValueKey="sale_orders"
          />
          <KpiCard
            label="Average order value"
            value={aov != null ? formatCurrency(aov) : "—"}
            delta={aovDelta}
            trendData={aovSeries}
            trendValueKey="aov"
          />
          <KpiCard
            label="Estimated margin"
            value={marginAnomaly?.latest_value != null ? formatPercent(marginAnomaly.latest_value) : "—"}
            delta={marginAnomaly?.pct_change_vs_baseline}
            trendData={marginTrend}
            trendValueKey="value"
          />
          <KpiCard
            label="Cancellation rate"
            value={headline ? formatPercent(headline.cancellation_rate_pct) : "—"}
            delta={cancellationDelta}
            deltaUnit="pp"
            direction="higher_is_bad"
            trendData={sales.months}
            trendValueKey="cancellation_rate_pct"
          />
          <KpiCard
            label="Customer retention"
            value={retentionAnomaly?.latest_value != null ? formatPercent(retentionAnomaly.latest_value) : "—"}
            delta={retentionAnomaly?.pct_change_vs_baseline}
            trendData={retentionTrend}
            trendValueKey="value"
          />
        </div>
      )}
    </section>
  );
}
// Paired with Business domain health (05) -- unchanged single-metric trend
// chart + outlet ranking, just relocated out of the old combined "Sales
// performance" section now that the KPI row is its own section.
// "Celeste Daily Colombo 6" / "Celeste Daily - Attidiya" wrap to 2 lines in
// a ranked bar chart's label column -- same shortening this app already
// does elsewhere (OutletOrderForecastTable), reused here for consistency.
function shortenOutletName(name) {
  return name.replace("Celeste Daily", "").replace("-", "").trim();
}
// Deliberately NOT first-vs-last-of-series math: this business's history
// starts Feb 2026 at near-zero revenue (a ramp-up artifact, not a real
// baseline -- confirmed live: Rs19K in Feb vs Rs200.8M in Aug computes as
// a nonsense "+1,036,866%"). Reuses sales.revenue_growth_pct/
// orders_growth_pct instead -- the same already-computed, already-trusted
// growth rate the KPI cards above show, not a second, fragile calculation
// of the same idea.
function trendTakeaway(sales, trendMetric, choice) {
  if (!sales) return null;
  const growthPct = trendMetric === "revenue" ? sales.revenue_growth_pct : sales.orders_growth_pct;
  const headlineVal = trendMetric === "revenue" ? sales.headline_revenue : sales.headline_orders;
  if (growthPct == null || headlineVal == null || !sales.headline_month) return null;
  return `${choice.label} was ${choice.formatValue(headlineVal)} in ${monthLabel(sales.headline_month)} `
    + `(${growthPct > 0 ? "+" : ""}${growthPct.toFixed(1)}% vs prior month).`;
}
// Skipped for the cancellation view -- "leads by cancellation rate" reads
// backwards (higher is worse there), and this app doesn't invert framing
// per-metric; omitting is more honest than a worded-around claim.
function outletTakeaway(branches, choice, outletMetric, periodLabel) {
  if (!branches || branches.length === 0 || outletMetric === "cancellation") return null;
  const top = [...branches].sort((a, b) => b[choice.valueKey] - a[choice.valueKey]).slice(0, 2).map((b) => b.name);
  if (top.length === 0) return null;
  return `${top.join(" and ")} lead${top.length === 1 ? "s" : ""} by ${choice.label.toLowerCase()}${periodLabel ? ` for ${periodLabel}` : ""}.`;
}
const TREND_OPTIONS = {
  revenue: { valueKey: "revenue", formatValue: formatCurrency, label: "Revenue" },
  orders: { valueKey: "sale_orders", formatValue: formatNumber, label: "Orders" },
};
const OUTLET_OPTIONS = {
  revenue: { valueKey: "revenue", formatValue: formatCurrency, label: "Revenue" },
  orders: { valueKey: "orders", formatValue: formatNumber, label: "Orders" },
  cancellation: { valueKey: "cancellation_rate_pct", formatValue: formatPercent, label: "Cancellation" },
};
// Density pass follow-up: was one component nested inside a single outer
// panel, with its own two inner bordered sub-panels -- a double-border
// "container inside a container" look. Now two independent panels the
// caller renders as sibling cards (design/domains-style .hp-panel-block
// each), no shared wrapper.
function RecentTrendPanel({ dashboardSummary, trendMetric, setTrendMetric }) {
  const sales = dashboardSummary.data?.status === "ok" ? dashboardSummary.data.sales : null;
  const trendChoice = TREND_OPTIONS[trendMetric];
  // Never plot the current, still-in-progress month as a real data point --
  // concretely, September showed a steep "decline" that was purely an
  // artifact of the month being about a third over, not a real trend (same
  // "never treat a partial period as fact" rule this app enforces
  // elsewhere -- see lastComplete()).
  const completeMonths = sales ? sales.months.filter((m) => !m.is_partial) : null;
  const partialMonth = sales ? sales.months.find((m) => m.is_partial) : null;
  const periodLabel = completeMonths?.length > 0
    ? `${monthLabel(completeMonths[0].month)}–${monthLabel(completeMonths[completeMonths.length - 1].month)}`
    : null;
  const takeaway = trendTakeaway(sales, trendMetric, trendChoice);

  return (
    <>
      <div className="hp-panel-head">
        <div>
          <h2 className="hp-panel-title">Recent trend</h2>
          {periodLabel && <p className="hp-panel-period">Monthly {trendChoice.label.toLowerCase()}, {periodLabel}</p>}
        </div>
        <MetricSwitch
          label="Trend metric"
          value={trendMetric}
          onChange={setTrendMetric}
          options={[["revenue", "Revenue"], ["orders", "Orders"]]}
        />
      </div>
      <Message query={dashboardSummary} label="sales trend" skeletonHeight="170px" />
      {completeMonths?.length > 0 && (
        <>
          {takeaway && <p className="hp-panel-takeaway">{takeaway}</p>}
          <TrendChart
            data={completeMonths}
            xKey="month"
            valueKey={trendChoice.valueKey}
            formatValue={trendChoice.formatValue}
            formatX={monthLabel}
            height={170}
          />
          {partialMonth && (
            <p className="hp-outlet-caption">
              {monthLabel(partialMonth.month)} is still in progress and excluded until complete.
            </p>
          )}
        </>
      )}
    </>
  );
}
function OutletPerformancePanel({ operationsSummary, outletMetric, setOutletMetric }) {
  const branches = operationsSummary.data?.status === "ok" ? operationsSummary.data.branches : null;
  const shortBranches = branches ? branches.map((b) => ({ ...b, name: shortenOutletName(b.name) })) : null;
  const outletChoice = OUTLET_OPTIONS[outletMetric];
  const periodLabel = operationsSummary.data?.headline_month ? monthLabel(operationsSummary.data.headline_month) : null;
  const takeaway = outletTakeaway(shortBranches, outletChoice, outletMetric, periodLabel);

  return (
    <>
      <div className="hp-panel-head">
        <div>
          <h2 className="hp-panel-title">Outlet performance</h2>
          {periodLabel && <p className="hp-panel-period">{periodLabel}</p>}
        </div>
        <MetricSwitch
          label="Outlet metric"
          value={outletMetric}
          onChange={setOutletMetric}
          options={[["revenue", "Revenue"], ["orders", "Orders"], ["cancellation", "Cancellation"]]}
        />
      </div>
      <Message query={operationsSummary} label="outlet performance" skeletonHeight="170px" />
      {shortBranches?.length > 0 && (
        <>
          {takeaway && <p className="hp-panel-takeaway">{takeaway}</p>}
          <RankedBarChart data={shortBranches} nameKey="name" valueKey={outletChoice.valueKey} formatValue={outletChoice.formatValue} />
        </>
      )}
      {operationsSummary.isSuccess && (!branches || branches.length === 0) && (
        <p className="hp-muted">No outlet data available.</p>
      )}
    </>
  );
}
// Bottom module: real opportunity-category findings already in the fetched
// findings array (never a separate "opportunities" backend call) -- the
// same category="opportunity" value the rest of the page already reads.
function OpportunitiesModule({ findings, customer }) {
  const opportunities = findings.filter((f) => f.category === "opportunity").slice(0, 2);
  return (
    <div className="hp-bottom-module">
      <div className="hp-bottom-module-head">
        <h3><TrendingUp size={15} /> Opportunities</h3>
        <Link to="#hp-priorities-list">View all <ArrowRight size={12} /></Link>
      </div>
      {customer && (
        <div className="hp-bottom-item">
          <div className="hp-bottom-item-tag"><Users size={11} /> Customer purchase opportunity</div>
          <strong>{count(customer.approaching_count)} customers approaching their purchase window</strong>
          {customer.expected_revenue != null && (
            <span className="hp-muted">Estimated demand {formatCurrency(customer.expected_revenue)}</span>
          )}
          <Link to="/customers/predictions" className="hp-bottom-item-link">Review <ArrowRight size={12} /></Link>
        </div>
      )}
      {opportunities.length === 0 && !customer && <p className="hp-muted">No opportunity findings in this briefing.</p>}
      {opportunities.map((f) => (
        <div className="hp-bottom-item" key={f.id}>
          <div className="hp-bottom-item-tag">
            <Tag size={11} /> {DOMAIN_LABEL[f.domain] || prettifyMetricId(f.domain)}
          </div>
          <strong>{f.title || prettifyMetricId(f.metric_id)}</strong>
          {f.confirm?.pct_change != null && (
            <span className="hp-muted">{formatPct(f.confirm.pct_change)} vs its usual range</span>
          )}
        </div>
      ))}
    </div>
  );
}
// Real counts only (outstanding/blocked actions) -- no fabricated
// overdue/due-this-week/unassigned buckets, since those need a full,
// unfiltered action scan this page doesn't otherwise do.
function ActionsModule({ outstanding, blocked }) {
  return (
    <div className="hp-bottom-module">
      <div className="hp-bottom-module-head">
        <h3><Clock3 size={15} /> Actions requiring attention</h3>
        <Link to="/actions">View all <ArrowRight size={12} /></Link>
      </div>
      <div className="hp-bottom-stat-row">
        <div className={`hp-bottom-stat ${outstanding.data?.total > 0 ? "hp-bottom-stat-warning" : ""}`}>
          <strong>{count(outstanding.data?.total)}</strong>
          <span>Outstanding</span>
        </div>
        {/* Zero blocked is good news, not a critical state -- only color
            it as an alarm when there's actually something to flag. */}
        <div className={`hp-bottom-stat ${blocked.data?.total > 0 ? "hp-bottom-stat-critical" : ""}`}>
          <strong>{count(blocked.data?.total)}</strong>
          <span>Blocked verification</span>
        </div>
      </div>
    </div>
  );
}
function OutcomesModule({ outcomes, outcomesSummary }) {
  const s = outcomesSummary.data?.status === "ok" ? outcomesSummary.data : null;
  return (
    <div className="hp-bottom-module">
      <div className="hp-bottom-module-head">
        <h3><Layers size={15} /> Recent outcomes</h3>
        <Link to="/outcomes">View all <ArrowRight size={12} /></Link>
      </div>
      {s && (
        <div className="hp-bottom-stat-row">
          <div className="hp-bottom-stat hp-bottom-stat-good">
            <strong>{count(s.resolved)}</strong>
            <span>resolved</span>
          </div>
          <div className="hp-bottom-stat">
            <strong>{count(s.pending)}</strong>
            <span>pending</span>
          </div>
          <div className="hp-bottom-stat hp-bottom-stat-good">
            <strong>{count(s.verdicts?.improved)}</strong>
            <span>improved</span>
          </div>
        </div>
      )}
      <Message query={outcomes} label="recently resolved cases" />
      {outcomes.data?.items?.slice(0, 2).map((item) => (
        <p key={item.case_id} className="hp-bottom-item">
          <Link to={`/investigate/${encodeURIComponent(item.case_id)}`}>{item.latest?.title || item.metric_id}</Link>
          <span className="hp-muted"> · {prettifyMetricId(item.resolution_verdict || "verdict unavailable")}</span>
        </p>
      ))}
      {/* Deliberately not "No resolved cases" -- that reads as a
          contradiction right under the real "N resolved" stat above,
          which counts something different (tracked action outcomes, not
          case.status). This is specifically about the recent-cases list. */}
      {outcomes.data?.total === 0 && <p className="hp-muted">No recently closed cases to list.</p>}
    </div>
  );
}
// Bottom module: out-of-stock/low-stock (distinct PRODUCT counts, from
// stock_alerts -- never the product-store combination total, a different,
// larger number) plus negative stock and cost coverage from the two
// controls-domain findings already in the fetched array. All four are
// genuinely distinct counts of different things -- keeping that distinct
// was an explicit correction after an earlier mockup reference conflated
// negative-stock (54) with a stock-out count. No trend arrow: the controls
// findings are point-in-time snapshots with no real baseline to compare
// against (confirm.pct_change is always null for these).
function DataQualityModule({ findings, stockAlerts }) {
  const negativeStock = findings.find((f) => f.metric_id === "negative_stock_control");
  const costCoverage = findings.find((f) => f.metric_id === "cost_coverage_control");
  const stock = stockAlerts.data?.status === "ok" ? stockAlerts.data : null;
  return (
    <div className="hp-bottom-module">
      <div className="hp-bottom-module-head">
        <h3><ShieldCheck size={15} /> Data quality &amp; controls</h3>
        <span className="hp-muted">Snapshot, no trend</span>
      </div>
      {stock && (
        <div className="hp-bottom-stat-row">
          <div className="hp-bottom-stat hp-bottom-stat-critical">
            <strong>{formatNumber(stock.distinct_out_of_stock_products)}</strong>
            <span>out of stock</span>
          </div>
          <div className="hp-bottom-stat hp-bottom-stat-warning">
            <strong>{formatNumber(stock.distinct_low_stock_products)}</strong>
            <span>low stock</span>
          </div>
        </div>
      )}
      {!negativeStock && !costCoverage && <p className="hp-muted">No open data-control findings.</p>}
      {negativeStock && (
        <div className="hp-bottom-item">
          <strong>{formatNumber(negativeStock.confirm?.current)}</strong> products recorded at negative stock <span className="hp-muted">(a data-integrity condition, not a stock-out)</span>
          {negativeStock.case_id && (
            <Link to={casePath(negativeStock)} className="hp-bottom-item-link">Open investigation <ArrowRight size={12} /></Link>
          )}
        </div>
      )}
      {costCoverage && (
        <div className="hp-bottom-item">
          <strong>{formatNumber(costCoverage.confirm?.current)}</strong> products with no recorded cost
          {costCoverage.total_products != null && costCoverage.coverage_pct != null && (
            <span className="hp-muted"> · {costCoverage.coverage_pct}% overall coverage</span>
          )}
          {costCoverage.case_id && (
            <Link to={casePath(costCoverage)} className="hp-bottom-item-link">Open investigation <ArrowRight size={12} /></Link>
          )}
        </div>
      )}
    </div>
  );
}
export default function Home() {
  const [params] = useSearchParams();
  const findingId = params.get("finding");
  const client = useQueryClient();
  const [refreshError, setRefreshError] = useState(null);
  const [requestingRefresh, setRequestingRefresh] = useState(false);
  const brief = useQuery({
    queryKey: ["brief"], queryFn: fetchBrief, retry: false,
    refetchInterval: (query) => query.state.status !== "error" && (query.state.data?.refresh?.status === "running" || query.state.data?.status === "loading") ? 2000 : false,
  });
  async function retryBrief() {
    setRequestingRefresh(true); setRefreshError(null);
    try { await refreshBrief(); await brief.refetch(); }
    catch (error) { setRefreshError(error.message); }
    finally { setRequestingRefresh(false); }
  }
  const outcomes = useQuery({queryKey: ["management", "homeOutcomes"], queryFn: () => fetchCases({status: "resolved", limit: 3})});
  const scope = useQuery({
    queryKey: ["storeScope"],
    queryFn: fetchStoreScope,
  });
  const legacy = useQuery({
    queryKey: ["findingCase", findingId],
    queryFn: () => fetchFindingCase(findingId),
    enabled: Boolean(findingId),
  });
  const generation = brief.data?.generated_at;
  const previousGeneration = useRef(null);
  useEffect(() => {
    if (generation && previousGeneration.current && generation !== previousGeneration.current) {
      client.invalidateQueries({queryKey: ["management"]});
    }
    if (generation) previousGeneration.current = generation;
  }, [generation, client]);
  const outstanding = useQuery({
    queryKey: ["management", "homeOutstanding"],
    queryFn: () =>
      fetchManagementActions({
        status: "outstanding",
        relevant: true,
        limit: 3,
      }),
  });
  const blocked = useQuery({
    queryKey: ["management", "homeBlocked"],
    queryFn: () =>
      fetchManagementActions({
        outcome_status: "blocked",
        relevant: true,
        limit: 2,
      }),
  });
  const dashboardSummary = useQuery({
    queryKey: ["dashboardSummary"],
    queryFn: fetchDashboardSummary,
  });
  const operationsSummary = useQuery({
    queryKey: ["operationsSummary"],
    queryFn: fetchOperationsSummary,
  });
  const [trendMetric, setTrendMetric] = useState("revenue");
  const [outletMetric, setOutletMetric] = useState("revenue");
  const inventoryStockAlerts = useQuery({
    queryKey: ["stockAlerts"],
    queryFn: fetchStockAlerts,
  });
  // Home rebuild (hierarchy reference: Home mockup): margin and retention
  // join the KPI row alongside revenue/orders/AOV/cancellation -- both are
  // real, already-registered metrics with their own investigate() trend,
  // just never queried on Home before.
  const grossMargin = useQuery({
    queryKey: ["metricInvestigation", "gross_margin"],
    queryFn: () => fetchMetricInvestigation("gross_margin"),
  });
  const customerRetention = useQuery({
    queryKey: ["metricInvestigation", "customer_retention_rate"],
    queryFn: () => fetchMetricInvestigation("customer_retention_rate"),
  });
  // Real, already-existing summary endpoints -- open-case count and
  // outstanding-action count for the hero panel, resolved/pending/verdict
  // counts for the Outcomes bottom module. No new backend work.
  const managementSummary = useQuery({
    queryKey: ["management", "summary"],
    queryFn: fetchManagementSummary,
  });
  const outcomesSummary = useQuery({
    queryKey: ["outcomesSummary"],
    queryFn: fetchOutcomesSummary,
  });
  useEffect(() => {
    if (!brief.isSuccess || brief.data?.status !== "ok") return;
    acknowledgeNotifications()
      .then(() =>
        client.invalidateQueries({ queryKey: ["notificationsSummary"] }),
      )
      .catch(() => {});
  }, [brief.isSuccess, generation, client, brief.data?.status]);
  const data =
    brief.data?.status === "ok" ? brief.data : null;
  // Home rebuild: dropped the Long-term/This week toggle -- with the new
  // section layout, it only ever affected 2 of 7 sections (top issues +
  // findings-by-domain), so it silently did nothing for most of the page.
  // Always the long-term view now (monthly trends + current-state
  // signals); the trailing-7-day short_term_findings data is no longer
  // surfaced on Home.
  const findings = data ? [...(data.investigations || []), ...(data.state_findings || [])] : [];
  const presentation = data?.home?.long;
  const domainFindingGroups = findingsByDomain(findings);
  const supplierStatus = domainStatusFrom(findings, "supplier");
  const controlsStatus = domainStatusFrom(findings, "controls");
  const byId = new Map(findings.map(finding => [finding.id, finding]));
  const priorities = presentation ? presentation.priority_ids.map(id => byId.get(id)).filter(Boolean) : [];
  const refresh = brief.isError ? brief.error?.body?.refresh : brief.data?.refresh;
  const health = data?.health;
  const healthNote = healthNoteFor(health, presentation?.severity_counts?.critical);
  const healthSupportingLine = healthSupportingLineFor(health);
  const customer = data?.customer_finding;
  if (legacy.data?.case_id)
    return (
      <Navigate
        replace
        to={`/investigate/${encodeURIComponent(legacy.data.case_id)}?action=${encodeURIComponent(findingId)}`}
      />
    );

  const freshness = freshnessMeta(data?.data_freshness);
  const refreshLabel =
    brief.isError || refresh?.status === "failed"
      ? "Retry briefing"
      : refresh?.status === "running"
        ? "Refresh in progress"
        : "Check briefing again";

  return (
    <div className="home-briefing">
      <header className="hp-orientation">
        <div>
          <span className="hp-kicker">01 / Celeste intelligence</span>
          <h1>
            Business briefing<span>.</span>
          </h1>
          <p>The signals that matter. The decisions that follow.</p>
        </div>
        <div className="hp-context">
          <div className="hp-context-item">
            <Building2 size={16} />
            <div>
              <span>Configured scope</span>
              <strong>
                {scope.data?.stores
                  ? `${scope.data.stores.length} outlets`
                  : "Unavailable"}
              </strong>
              <details>
                <summary>
                  View outlets <ChevronRight size={11} />
                </summary>
                <ul>
                  {scope.data?.stores?.map((store) => (
                    <li key={store}>{store}</li>
                  ))}
                </ul>
                <p>Each finding shows its applicable evidence scope.</p>
              </details>
            </div>
          </div>
          <div className="hp-context-item">
            <CalendarCheck2 size={16} />
            <div>
              <span>Briefing checked</span>
              <strong>
                {data
                  ? stamp(data.generated_at)
                  : brief.isError
                    ? "Check failed"
                    : "Awaiting briefing"}
              </strong>
            </div>
          </div>
          <div className="hp-context-item">
            <Database size={16} />
            <div>
              <span>Order data through</span>
              <strong>
                {data
                  ? stamp(data.data_freshness?.latest_order_at)
                  : "Awaiting source timestamp"}
              </strong>
              <small className={`hp-freshness-dot hp-freshness-${freshness.tone}`}>
                {freshness.text}
              </small>
            </div>
          </div>
        </div>
      </header>
      <div className="hp-horizon">
        <p>Monthly trends + current-state signals · Each finding shows its own period</p>
        <button
          className="hp-refresh-btn"
          disabled={requestingRefresh || refresh?.status === "running"}
          onClick={retryBrief}
        >
          <RefreshCw size={13} className={refresh?.status === "running" ? "hp-spin" : ""} />
          {refreshLabel}
        </button>
      </div>
      {findingId && (
        <>
          <Message query={legacy} label="saved case association" />
          {legacy.isSuccess && !legacy.data?.case_id && (
            <p className="hp-message">
              This finding has no stored case association. <Link to="/investigate">Browse Investigate</Link> for current cases.
            </p>
          )}
        </>
      )}
      {((brief.isPending || brief.data?.status === "loading") ||
        data?.cache_status === "stale" ||
        refresh?.status === "running" ||
        brief.isError ||
        refresh?.status === "failed" ||
        refreshError) && (
        <div className="hp-brief-status" aria-live="polite">
          {(brief.isPending || brief.data?.status === "loading") && (
            <p role="status">
              {brief.isPending
                ? "Loading briefing…"
                : "Preparing the first briefing from reporting data. This can take several minutes; saved follow-up is available below."}
            </p>
          )}
          {data?.cache_status === "stale" && (
            <p>
              Showing a stale cached briefing checked {stamp(data.generated_at)}.
              Review this saved evidence; it may not reflect current conditions.
            </p>
          )}
          {refresh?.status === "running" && (
            <p role="status">
              Checking the briefing in the background. You can continue
              reviewing saved evidence.
            </p>
          )}
          {(brief.isError || refresh?.status === "failed" || refreshError) && (
            <div role="alert">
              <strong>Could not {data ? "refresh" : "load"} briefing.</strong>{" "}
              {refreshError || refresh?.error || brief.error?.message}{" "}
              {data && "The last saved briefing is still shown."}
            </div>
          )}
        </div>
      )}
      <div className="hp-composition">
        {data && (
          <>
            <section className="hp-snapshot" aria-label="Business snapshot">
              {/* Zone 1: score. Kept minimal -- kicker/title/pulse badge,
                  the gauge, and the delta directly beneath the number it's
                  about. The headline and everything explaining the score
                  moved to zone 2, which used to be empty dead space. */}
              <div className="hp-health">
                <div className="hp-health-head">
                  <div>
                    <span className="hp-kicker">02 / Business snapshot</span>
                    <h2 className="hp-health-title">Business Health</h2>
                  </div>
                  <span
                    className={`hp-pulse-badge hp-freshness-${freshness.tone}`}
                    title={`Order data ${freshness.text.toLowerCase()}`}
                  >
                    <Activity size={16} />
                  </span>
                </div>
                <div className="hp-health-gauge">
                  {health?.overall != null ? (
                    <RadialGauge
                      value={health.overall}
                      status={health.status || "good"}
                      size={100}
                      strokeWidth={8}
                      sublabel="/ 100"
                    />
                  ) : (
                    <div className="hp-gauge-unavailable" aria-hidden="true">
                      <span>N/A</span>
                    </div>
                  )}
                </div>
                <span
                  className={`hp-health-delta ${health?.overall_delta > 0 ? "is-up" : health?.overall_delta < 0 ? "is-down" : ""}`}
                >
                  {health?.overall_delta == null ? (
                    "Previous comparison unavailable"
                  ) : (
                    <>
                      {health.overall_delta > 0 ? (
                        <ArrowUp size={12} />
                      ) : health.overall_delta < 0 ? (
                        <ArrowDown size={12} />
                      ) : null}
                      {health.overall_delta > 0 ? "+" : ""}
                      {health.overall_delta} vs previous assessment
                    </>
                  )}
                </span>
              </div>
              {/* Zone 2: executive message. This used to be empty space --
                  now it carries the headline, a real supporting line
                  (backend's own driver text for the worst domain, not a
                  re-derived number), and a compact 4-domain strip so
                  "inventory requires immediate attention" is immediately
                  explainable without opening the details disclosure. */}
              <div className="hp-health-message">
                {healthNote ? (
                  <h1 className="hp-health-headline">{healthNote}</h1>
                ) : (
                  <strong className="hp-health-figure">
                    {count(health?.overall)}
                    <span> / 100</span>
                  </strong>
                )}
                {healthSupportingLine && <p className="hp-health-supporting">{healthSupportingLine}</p>}
                {health?.domains?.length > 0 && <MiniDomainStrip domains={health.domains} />}
              </div>
              {/* Compact hero right panel -- density pass: only the 3
                  things that need to be visible at a glance (data-through
                  date, open critical findings, outstanding actions).
                  Everything else (last briefing timestamp, reporting
                  scope, new-vs-previous-briefing comparison) moves into a
                  single collapsed disclosure instead of its own row each. */}
              <div className="hp-counts hp-counts-compact">
                <div className="hp-hero-chips">
                  <span className="hp-hero-chip">
                    <Database size={12} /> Data through {stamp(data.data_freshness?.latest_order_at)}
                  </span>
                  <span className="hp-count-pill hp-count-critical">
                    {count(presentation?.severity_counts?.critical)} critical
                  </span>
                  <span className="hp-count-pill hp-count-warning">
                    {count(managementSummary.data?.actions?.outstanding)} outstanding actions
                  </span>
                </div>
                <details className="hp-hero-more">
                  <summary>
                    More details <ChevronRight size={12} className="hp-details-chevron" />
                  </summary>
                  <dl className="hp-meta-list">
                    <div>
                      <dt>Last briefing</dt>
                      <dd>{stamp(data.generated_at)}</dd>
                    </div>
                    <div>
                      <dt>Reporting scope</dt>
                      <dd>{scope.data?.stores ? `${scope.data.stores.length} outlets` : "Unavailable"}</dd>
                    </div>
                  </dl>
                  <a href="#hp-priorities-list" className="hp-summary-row">
                    <FileText size={13} />
                    <span>
                      <strong>
                        {count(data.new_critical_findings?.length)} new
                        critical finding
                        {data.new_critical_findings?.length === 1 ? "" : "s"}
                      </strong>
                      vs previous briefing
                    </span>
                    <ChevronRight size={12} />
                  </a>
                </details>
              </div>
            </section>

            {/* 03 / Key metrics -- 6 KPI cards (revenue/orders/AOV/margin/
                cancellation/retention), each with a real sparkline from its
                own trend series. Single-metric per card -- never mixed
                units on one chart. */}
            <KpiSection
              dashboardSummary={dashboardSummary}
              grossMargin={grossMargin}
              customerRetention={customerRetention}
            />

            {/* 04 / What needs attention -- top issues (left) paired with
                findings-by-domain (right), replacing the old full-width
                "Decision priorities" list + severity-tile grid with the
                same real data in a denser, paired layout. */}
            <section className="hp-attention-grid" aria-label="What needs attention">
              <div className="hp-attention-issues">
                <Heading number="04" label="What needs attention" title="Top issues needing attention" icon={AlertTriangle}>
                  <Link to="/investigate">Investigate all {findings.length} findings <ArrowRight size={14} /></Link>
                </Heading>
                {findings.length ? (
                  <div className="hp-issue-grid" id="hp-priorities-list">
                    {priorities.map((finding) => (
                      <IssueCard key={finding.id} finding={finding} />
                    ))}
                  </div>
                ) : (
                  <div className="hp-empty">
                    <Check size={22} />
                    <h3>No findings in this briefing</h3>
                    <p>
                      Existing cases and outstanding actions may still need
                      attention. <Link to="/investigate">Review cases</Link> or
                      check follow-up below.
                    </p>
                  </div>
                )}
              </div>
              <div className="hp-attention-domains hp-panel-block">
                <h2 className="hp-panel-title">Findings by domain</h2>
                <div className="hp-domain-bar-legend">
                  {SEVERITY_TIERS.map((t) => (
                    <span key={t}><i className={`hp-legend-dot hp-legend-${SEVERITY_BAR_TONE[t]}`} />{t}</span>
                  ))}
                </div>
                <FindingsByDomainBar grouped={domainFindingGroups} />
              </div>
            </section>

            {/* 05 / Business domain health (compact strip) + Recent trends.
                Density pass: domain health used to be 6 full cards fighting
                the trend chart for column space -- now a thin strip on top,
                and Recent Trends (one of the clearest analytical visuals
                on the page) gets the full page width instead of being
                squeezed into a 1fr column. */}
            <section className="hp-domainhealth-section" aria-label="Business domain health and trends">
              <div className="hp-panel-block">
                <h2 className="hp-panel-title">Business domain health</h2>
                {health?.domains?.length > 0 ? (
                  <DomainHealthStrip domains={health.domains} supplierStatus={supplierStatus} controlsStatus={controlsStatus} />
                ) : (
                  <p className="hp-muted">Domain scores are unavailable.</p>
                )}
              </div>
              <div className="hp-panel-block">
                <RecentTrendPanel
                  dashboardSummary={dashboardSummary}
                  trendMetric={trendMetric}
                  setTrendMetric={setTrendMetric}
                />
              </div>
              <div className="hp-panel-block">
                <OutletPerformancePanel
                  operationsSummary={operationsSummary}
                  outletMetric={outletMetric}
                  setOutletMetric={setOutletMetric}
                />
              </div>
            </section>
          </>
        )}
        <section className="hp-followup">
          <Heading
            number="06"
            label="Opportunities & follow-up"
            title="Keep the next move in sight"
            icon={Sparkles}
          >
            <span className="hp-muted">Stored work · all periods</span>
          </Heading>
          <div className="hp-bottom-grid">
            <OpportunitiesModule findings={findings} customer={customer} />
            <ActionsModule outstanding={outstanding} blocked={blocked} />
            <OutcomesModule outcomes={outcomes} outcomesSummary={outcomesSummary} />
            <DataQualityModule findings={findings} stockAlerts={inventoryStockAlerts} />
          </div>
        </section>
      </div>
    </div>
  );
}
