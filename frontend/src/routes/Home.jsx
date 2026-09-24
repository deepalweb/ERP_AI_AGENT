import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  BarChart3,
  Building2,
  CalendarCheck2,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileText,
  Info,
  Lightbulb,
  Package,
  RefreshCw,
  Settings2,
  Sparkle,
  Sparkles,
  Truck,
  Users,
} from "lucide-react";
import {
  acknowledgeNotifications,
  fetchBrief,
  refreshBrief,
  fetchCases,
  fetchChannelBreakdown,
  fetchDashboardSummary,
  fetchFindingCase,
  fetchForwardLook,
  fetchManagementActions,
  fetchMetricInvestigation,
  fetchOperationsSummary,
  fetchStockAlerts,
  fetchStoreScope,
} from "../api";
import { DOMAIN_LABEL } from "../design/domains";
import {
  RootCauseBox,
  ImpactBox,
  RecommendationsList,
  EvidenceCharts,
} from "../design/SignalCard";
import TrendChart from "../charts/TrendChart";
import RadialGauge from "../charts/RadialGauge";
import RankedBarChart from "../charts/RankedBarChart";
import {
  formatByUnit,
  formatCurrency,
  formatDuration,
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
const SEVERITY_META = {
  Critical: { icon: AlertTriangle, tone: "critical", caption: "Requires immediate attention" },
  High: { icon: AlertCircle, tone: "warning", caption: "Monitor closely" },
  Medium: { icon: Info, tone: "info", caption: "Keep on your radar" },
  Positive: { icon: Sparkles, tone: "good", caption: "Working well" },
};
const HEALTH_STATUS_COPY = {
  good: "Your business is in a strong position with stable performance across key areas.",
  warning: "Some areas need attention while others remain steady.",
  critical: "Multiple areas need immediate attention right now.",
};
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
function DomainCards({ domains }) {
  return (
    <div className="hp-domain-cards">
      {domains.map((item) => {
        const Icon = DOMAIN_ICON[item.id] || BarChart3;
        const hasDelta = item.delta != null;
        const deltaUp = hasDelta && item.delta >= 0;
        return (
          <div className="hp-domain-card" key={item.id}>
            <div className="hp-domain-card-head">
              <Icon size={14} />
              <span>{item.label}</span>
            </div>
            <div className="hp-domain-card-score">
              <strong>{count(item.score)}</strong>
              {hasDelta && (
                <span className={`hp-domain-delta ${deltaUp ? "is-up" : "is-down"}`}>
                  {deltaUp ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
                  {Math.abs(item.delta)}
                </span>
              )}
            </div>
            <div className="hp-domain-card-track">
              <div
                className={`hp-domain-card-fill hp-domain-bar-${item.status || "good"}`}
                style={{ width: `${Math.max(Math.min(item.score ?? 0, 100), 2)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
function Measurement({ finding }) {
  return (
    <>
      <div className="hp-measure">
        <strong>
          {finding.confirm?.current == null
            ? "—"
            : formatByUnit(finding.confirm.current, finding.confirm.unit)}
        </strong>
        <span>
          {finding.confirm?.pct_change == null
            ? "No baseline comparison"
            : `${formatPct(finding.confirm.pct_change)} vs baseline`}
        </span>
        {finding.trend?.length > 1 && (
          <div className="hp-measure-chart">
            <TrendChart data={finding.trend} compact />
          </div>
        )}
      </div>
      <p className="hp-period">
        {finding.confirm?.period || "Observation period unavailable"}
        {finding.horizon ? ` · ${prettifyMetricId(finding.horizon)}` : ""}
      </p>
    </>
  );
}
function Forecast({ finding }) {
  const key = { revenue: "revenue", order_volume: "orders" }[finding.metric_id];
  const query = useQuery({
    queryKey: ["forwardLook"],
    queryFn: fetchForwardLook,
    enabled: Boolean(key),
  });
  if (!key) return null;
  const projection = query.data?.[key];
  return (
    <section>
      <h3>Forecast</h3>
      <Message query={query} label="forecast" />
      {query.isSuccess &&
        (projection ? (
          <>
            <p>
              {formatByUnit(projection.projected_low, finding.confirm?.unit)}–
              {formatByUnit(projection.projected_high, finding.confirm?.unit)}{" "}
              projected for the next period. Based on{" "}
              {projection.based_on_month || "an unspecified period"}.
            </p>
            <p className="hp-muted">{query.data.note}</p>
          </>
        ) : (
          <p>No forecast is available for this metric.</p>
        ))}
    </section>
  );
}
function FindingDetail({ finding, onAskCeleste }) {
  return (
    <div className="hp-detail-content">
      {finding.data_quality?.detail && (
        <p className="hp-muted">Data quality: {finding.data_quality.detail}</p>
      )}
      <Measurement finding={finding} />
      <RootCauseBox
        rootCause={finding.root_cause}
        notes={finding.metric?.notes}
      />
      <ImpactBox impact={finding.impact} />
      <EvidenceCharts
        trend={finding.trend}
        breakdowns={finding.breakdowns}
        direction={finding.metric?.direction}
        unit={finding.confirm?.unit}
        confirm={finding.confirm}
        notes={finding.metric?.notes}
      />
      <Forecast finding={finding} />
      {finding.recommendations?.length > 0 && (
        <RecommendationsList recommendations={finding.recommendations} />
      )}
      {finding.case_id ? (
        <p>
          <Link to={casePath(finding)}>
            Open case for history, action editing, and verification{" "}
            <ArrowUpRight size={14} />
          </Link>
        </p>
      ) : (
        <p className="hp-muted">
          No stored case association. Available evidence is shown here; no case
          link has been inferred.
        </p>
      )}
      <button
        onClick={() =>
          onAskCeleste?.(
            `Help me analyze the metric ${finding.metric?.label || prettifyMetricId(finding.metric_id)}. This is a separate analysis, not a review of saved case evidence.`,
          )
        }
      >
        Ask about this metric <ArrowRight size={14} />
      </button>
      <p className="hp-muted">
        Chat performs a separate analysis; it does not receive the stored case
        evidence.
      </p>
    </div>
  );
}
function PriorityCard({ finding, index, onEvidence }) {
  const severity = level(finding);
  return (
    <article className={`hp-priority hp-${severity.toLowerCase()}`}>
      <div className="hp-card-meta">
        <span className="hp-number">0{index + 1}</span>
        <span className="hp-badge">{severity}</span>
        <span>
          {DOMAIN_LABEL[finding.domain] || prettifyMetricId(finding.domain)}
        </span>
      </div>
      <h3>{finding.title || prettifyMetricId(finding.metric_id)}</h3>
      <Measurement finding={finding} />
      <div className="hp-impact">
        {finding.impact?.detail || "Impact not quantified"}
      </div>
      <p className="hp-evidence">{evidence(finding)}</p>
      <details className="hp-uncertainty">
        <summary>Evidence and uncertainty</summary>
        <p>
          {finding.data_quality?.detail ||
            "No additional data-quality explanation supplied."}
        </p>
        <p>Driver evidence does not by itself establish causation.</p>
        {finding.impact?.confidence && (
          <p>Impact basis: {finding.impact.confidence}</p>
        )}
      </details>
      <footer>
        <p>
          <span>Suggested next step</span>
          {finding.recommendations?.[0]?.action ||
            "Review the available evidence before choosing an action."}
        </p>
        {finding.case_id ? (
          <Link to={casePath(finding)}>
            Open case <ArrowUpRight size={15} />
          </Link>
        ) : (
          <button onClick={() => onEvidence(finding.id)}>
            Review evidence <ArrowRight size={15} />
          </button>
        )}
      </footer>
    </article>
  );
}
function SeverityTile({ label, value }) {
  const meta = SEVERITY_META[label];
  const Icon = meta.icon;
  return (
    <div className={`hp-severity-tile hp-severity-${meta.tone}`}>
      <span className="hp-severity-icon">
        <Icon size={19} />
      </span>
      <strong>{value}</strong>
      <span className="hp-severity-label">{label}</span>
      <span className="hp-severity-caption">{meta.caption}</span>
      <span className="hp-severity-bar" />
    </div>
  );
}
function priorityBannerCopy(counts) {
  if (!counts) return null;
  if (counts.critical > 0)
    return `${counts.critical} critical finding${counts.critical === 1 ? "" : "s"} require attention.`;
  if (counts.high > 0)
    return `${counts.high} high-priority finding${counts.high === 1 ? "" : "s"} need review.`;
  if (counts.medium > 0)
    return `${counts.medium} medium-priority finding${counts.medium === 1 ? "" : "s"} to review.`;
  if (counts.positive > 0)
    return `No urgent findings — ${counts.positive} positive result${counts.positive === 1 ? "" : "s"} to review.`;
  return null;
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
function SalesPerformanceSection({
  dashboardSummary,
  operationsSummary,
  trendMetric,
  setTrendMetric,
  outletMetric,
  setOutletMetric,
}) {
  const sales = dashboardSummary.data?.status === "ok" ? dashboardSummary.data.sales : null;
  const branches = operationsSummary.data?.status === "ok" ? operationsSummary.data.branches : null;
  const [headline, previous] = sales ? lastComplete(sales.months) : [null, null];
  const aov = headline && headline.sale_orders ? headline.revenue / headline.sale_orders : null;
  const prevAov = previous && previous.sale_orders ? previous.revenue / previous.sale_orders : null;
  const aovDelta = aov != null && prevAov ? ((aov - prevAov) / prevAov) * 100 : null;
  const cancellationDelta =
    headline && previous ? headline.cancellation_rate_pct - previous.cancellation_rate_pct : null;
  const aovSeries = sales
    ? sales.months.map((m) => ({ month: m.month, aov: m.sale_orders ? m.revenue / m.sale_orders : 0 }))
    : null;

  const TREND_OPTIONS = {
    revenue: { valueKey: "revenue", formatValue: formatCurrency },
    orders: { valueKey: "sale_orders", formatValue: formatNumber },
  };
  const OUTLET_OPTIONS = {
    revenue: { valueKey: "revenue", formatValue: formatCurrency },
    orders: { valueKey: "orders", formatValue: formatNumber },
    cancellation: { valueKey: "cancellation_rate_pct", formatValue: formatPercent },
  };
  const trendChoice = TREND_OPTIONS[trendMetric];
  const outletChoice = OUTLET_OPTIONS[outletMetric];

  return (
    <section className="hp-sales-performance" aria-label="Sales performance">
      <Heading number="03" label="Sales performance" title="Revenue, orders and cancellation" icon={BarChart3} />
      <Message query={dashboardSummary} label="sales performance" skeletonHeight="320px" />
      {sales && (
        <>
          <div className="hp-kpi-grid">
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
              label="Cancellation rate"
              value={headline ? formatPercent(headline.cancellation_rate_pct) : "—"}
              delta={cancellationDelta}
              deltaUnit="pp"
              direction="higher_is_bad"
              trendData={sales.months}
              trendValueKey="cancellation_rate_pct"
            />
          </div>
          <div className="hp-trend-panel">
            <div className="hp-panel-head">
              <h3>Sales trend</h3>
              <MetricSwitch
                label="Trend metric"
                value={trendMetric}
                onChange={setTrendMetric}
                options={[
                  ["revenue", "Revenue"],
                  ["orders", "Orders"],
                ]}
              />
            </div>
            <TrendChart
              data={sales.months}
              xKey="month"
              valueKey={trendChoice.valueKey}
              formatValue={trendChoice.formatValue}
              formatX={monthLabel}
            />
          </div>
        </>
      )}
      <div className="hp-outlet-panel">
        <div className="hp-panel-head">
          <h3>Outlet performance</h3>
          <MetricSwitch
            label="Outlet metric"
            value={outletMetric}
            onChange={setOutletMetric}
            options={[
              ["revenue", "Revenue"],
              ["orders", "Orders"],
              ["cancellation", "Cancellation"],
            ]}
          />
        </div>
        <Message query={operationsSummary} label="outlet performance" skeletonHeight="220px" />
        {branches && branches.length > 0 && (
          <>
            <RankedBarChart data={branches} nameKey="name" valueKey={outletChoice.valueKey} formatValue={outletChoice.formatValue} />
            {operationsSummary.data?.headline_month && (
              <p className="hp-outlet-caption">
                Same period as the KPIs above ({monthLabel(operationsSummary.data.headline_month)}).
              </p>
            )}
          </>
        )}
        {operationsSummary.isSuccess && (!branches || branches.length === 0) && (
          <p className="hp-muted">No outlet data available.</p>
        )}
      </div>
    </section>
  );
}
function OpsPanel({ icon: Icon, title, to, linkLabel, query, hasContent, children }) {
  return (
    <div className="hp-ops-panel">
      <div className="hp-ops-panel-head">
        <span className="hp-ops-panel-title">
          <Icon size={16} />
          {title}
        </span>
        {to && (
          <Link to={to}>
            {linkLabel} <ArrowRight size={12} />
          </Link>
        )}
      </div>
      {query && <Message query={query} label={title.toLowerCase()} skeletonHeight="70px" />}
      {query?.isSuccess && !hasContent && <p className="hp-muted">No {title.toLowerCase()} data available.</p>}
      {children}
    </div>
  );
}
function OperationalHealthSection({
  inventoryStockAlerts,
  operationsSummary,
  supplierFillRate,
  channelBreakdown,
}) {
  const stock = inventoryStockAlerts.data?.status === "ok" ? inventoryStockAlerts.data : null;
  const ops = operationsSummary.data?.status === "ok" ? operationsSummary.data : null;
  const supplier = supplierFillRate.data?.status === "ok" ? supplierFillRate.data : null;
  const anomaly = supplier?.anomaly;
  const channels = channelBreakdown.data?.status === "ok" ? channelBreakdown.data.channels : null;

  return (
    <section className="hp-operational-health" aria-label="Operational health">
      <Heading number="04" label="Operational health" title="Inventory, fulfilment, suppliers and channels" icon={Settings2} />
      <div className="hp-ops-grid">
        <OpsPanel icon={Package} title="Inventory" to="/dashboard" linkLabel="View inventory" query={inventoryStockAlerts} hasContent={Boolean(stock)}>
          {stock && (
            <>
              <strong className="hp-kpi-value">{formatNumber(stock.distinct_out_of_stock_products)}</strong>
              <span className="hp-kpi-label">products out of stock</span>
              {stock.as_of_date && <p className="hp-outlet-caption">As of {stock.as_of_date}</p>}
            </>
          )}
        </OpsPanel>
        <OpsPanel icon={Settings2} title="Operations" query={operationsSummary} hasContent={Boolean(ops)}>
          {ops && (
            <>
              <strong className="hp-kpi-value">{formatPercent(ops.headline_delayed_rate_pct)}</strong>
              <span className="hp-kpi-label">delayed delivery rate</span>
              {ops.delayed_rate_growth_pct != null && (
                <span className={`hp-kpi-delta ${moveToneClass(ops.delayed_rate_growth_pct, "higher_is_bad")}`}>
                  {ops.delayed_rate_growth_pct > 0 ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
                  {formatPct(ops.delayed_rate_growth_pct)}
                </span>
              )}
              <p className="hp-outlet-caption">
                Median fulfilment time: {formatDuration(ops.median_fulfillment_minutes)}
              </p>
            </>
          )}
        </OpsPanel>
        <OpsPanel icon={Building2} title="Suppliers" query={supplierFillRate} hasContent={Boolean(anomaly)}>
          {anomaly && (
            <>
              <strong className="hp-kpi-value">{formatPercent(anomaly.latest_value)}</strong>
              <span className="hp-kpi-label">on-time fill rate</span>
              {anomaly.pct_change_vs_baseline != null && (
                <span className={`hp-kpi-delta ${moveToneClass(anomaly.pct_change_vs_baseline, "higher_is_good")}`}>
                  {anomaly.pct_change_vs_baseline > 0 ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
                  {formatPct(anomaly.pct_change_vs_baseline)} vs trailing baseline
                </span>
              )}
              <p className="hp-outlet-caption">Business-wide (warehouse-level), not per-outlet.</p>
            </>
          )}
        </OpsPanel>
        <OpsPanel icon={Truck} title="Channels" query={channelBreakdown} hasContent={Boolean(channels && channels.length > 0)}>
          {channels && channels.length > 0 && (
            <>
              <div className="hp-ops-channel-list">
                {channels.map((c) => (
                  <div className="hp-ops-channel-row" key={c.name}>
                    <span>{c.name}</span>
                    <span>{formatPercent(c.revenue_share_pct)}</span>
                  </div>
                ))}
              </div>
              {channelBreakdown.data?.period && (
                <p className="hp-outlet-caption">{monthLabel(channelBreakdown.data.period)}</p>
              )}
            </>
          )}
        </OpsPanel>
      </div>
    </section>
  );
}
function Followup({ query, title, destination }) {
  if (query.isSuccess && query.data?.total === 0) return (
    <article className="hp-follow-empty"><span>No {title.toLowerCase()} recorded.</span>
      <Link to={destination}>View {destination === "/outcomes" ? "Outcomes" : "Actions"} <ArrowRight size={12}/></Link>
    </article>
  );
  return (
    <article className="hp-follow-list">
      <div>
        <Clock3 size={16} />
        <h3>{title}</h3>
        <span>
          {query.isSuccess ? `${count(query.data?.total)} recorded` : ""}
        </span>
      </div>
      <Message query={query} label={title.toLowerCase()} />
      {query.data?.items?.map((action) => (
        <Link
          key={action.finding_id}
          to={
            action.case?.case_id
              ? `/investigate/${encodeURIComponent(action.case.case_id)}?action=${encodeURIComponent(action.finding_id)}`
              : "/actions"
          }
        >
          <span>
            <small>
              {action.overdue ? "Overdue action · " : ""}
              {action.outcome_status === "blocked"
                ? "Verification blocked"
                : prettifyMetricId(action.status)}
            </small>
            <strong>
              {action.description || action.action_taken?.trim() ||
                action.intended_result?.trim() ||
                "No action description saved"}
            </strong>
            <small>Saved finding: {action.finding_id}</small>
            <em>
              {action.owner || "Unassigned"}
              {action.deadline ? ` · Due ${action.deadline}` : ""}
              {!action.case?.case_id ? (action.case_link_status === "missing_case" ? " · Linked case is missing" : " · Historical record — no stored case association") : ""}
            </em>
            {action.deadline_note && <small>{action.deadline_note}</small>}
          </span>
          <ArrowUpRight size={16} />
        </Link>
      ))}
      {query.data?.items?.length === 0 && <p>No matching work recorded.</p>}
      <p>
        Latest records across all periods.{" "}
        <Link to={destination}>
          View all <ArrowRight size={12} />
        </Link>
      </p>
    </article>
  );
}

export default function Home({ onAskCeleste }) {
  const [horizon, setHorizon] = useState("long");
  const [domain, setDomain] = useState("All");
  const [expanded, setExpanded] = useState({});
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
  const supplierFillRate = useQuery({
    queryKey: ["metricInvestigation", "supplier_fill_rate"],
    queryFn: () => fetchMetricInvestigation("supplier_fill_rate"),
  });
  const headlineMonth = dashboardSummary.data?.sales?.headline_month;
  const channelBreakdown = useQuery({
    queryKey: ["channelBreakdown", headlineMonth],
    queryFn: () => fetchChannelBreakdown(headlineMonth),
    enabled: Boolean(headlineMonth),
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
  const findings = data
    ? horizon === "long"
      ? [...(data.investigations || []), ...(data.state_findings || [])]
      : data.short_term_findings || []
    : [];
  const presentation = data?.home?.[horizon];
  const byId = new Map(findings.map(finding => [finding.id, finding]));
  const ranked = presentation ? presentation.ranked_ids.map(id => byId.get(id)).filter(Boolean) : findings;
  const priorities = presentation ? presentation.priority_ids.map(id => byId.get(id)).filter(Boolean) : [];
  const refresh = brief.isError ? brief.error?.body?.refresh : brief.data?.refresh;
  const domains = [
    "All",
    ...new Set(findings.map((finding) => finding.domain).filter(Boolean)),
  ];
  const filtered = ranked.filter(
    (finding) => domain === "All" || finding.domain === domain,
  );
  const health = data?.health;
  const customer = data?.customer_finding;
  function reveal(id) {
    setDomain("All");
    setExpanded((previous) => ({ ...previous, [id]: true }));
    requestAnimationFrame(() =>
      document
        .getElementById(`brief-finding-${id}`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  }
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
        <div role="group" aria-label="Reporting horizon">
          {[
            ["long", "Long-term"],
            ["week", "This week"],
          ].map(([key, label]) => (
            <button
              key={key}
              aria-pressed={horizon === key}
              onClick={() => {
                setHorizon(key);
                setDomain("All");
                setExpanded({});
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <p>
          {horizon === "long"
            ? "Monthly trends + current-state signals · Each finding shows its own period"
            : "Trailing 7-day signals · Each finding shows its observed window"}
        </p>
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
              This finding has no stored case association. Any evidence in the
              current briefing is available below.
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
                <p className="hp-health-subtitle">
                  A consolidated view of your business performance.
                </p>
                <div className="hp-health-gauge">
                  {health?.overall != null ? (
                    <RadialGauge
                      value={health.overall}
                      status={health.status || "good"}
                      size={128}
                      strokeWidth={10}
                      sublabel="/ 100"
                    />
                  ) : (
                    <div className="hp-gauge-unavailable" aria-hidden="true">
                      <span>N/A</span>
                    </div>
                  )}
                  <div className="hp-health-summary">
                    <small>Overall assessment</small>
                    <strong className="hp-health-figure">
                      {count(health?.overall)}
                      <span> / 100</span>
                    </strong>
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
                    {health?.status && HEALTH_STATUS_COPY[health.status] && (
                      <p className="hp-health-note">
                        {HEALTH_STATUS_COPY[health.status]}
                      </p>
                    )}
                  </div>
                </div>
                {health?.domains?.length > 0 ? (
                  <DomainCards domains={health.domains} />
                ) : (
                  <p className="hp-muted">Domain scores are unavailable.</p>
                )}
                <details className="hp-health-details">
                  <summary>
                    <FileText size={16} />
                    <span>
                      Health details
                      <small>
                        View domain scores, calculation method and comparison
                        period
                      </small>
                    </span>
                    <ChevronRight size={15} className="hp-details-chevron" />
                  </summary>
                  <p>
                    Backend composite of sales, operations, inventory and customer metrics, with metric-specific reporting scopes. Scores do not establish
                    case resolution or action impact. Changes compare against the
                    prior stored assessment; its timestamp is not supplied in this
                    briefing.
                  </p>
                  <div>
                    {(health?.domains || []).map((item) => (
                      <p key={item.id}>
                        <strong>{item.label}</strong>
                        <br />
                        <small>
                          {(item.drivers || []).join(" · ") ||
                            "No notable drivers reported."}
                        </small>
                      </p>
                    ))}
                  </div>
                  {!health && <p>Health data is unavailable.</p>}
                </details>
              </div>
              <div className="hp-counts">
                <div className="hp-counts-head">
                  <span className="hp-kicker">
                    Findings in this briefing ·{" "}
                    {horizon === "long" ? "Long-term" : "This week"}
                  </span>
                  <strong className="hp-counts-total">
                    {findings.length} findings in total
                  </strong>
                </div>
                <p className="hp-health-subtitle">
                  Key insights from your data, organized by impact level.
                </p>
                <div className="hp-severity-grid">
                  {["Critical", "High", "Medium", "Positive"].map((severity) => (
                    <SeverityTile
                      key={severity}
                      label={severity}
                      value={count(
                        presentation?.severity_counts?.[severity.toLowerCase()],
                      )}
                    />
                  ))}
                </div>
                <small className="hp-muted">
                  Finding counts are not case counts.
                </small>
                {data && (
                  <div className="hp-summary-rows">
                    <a href="#hp-priorities-list" className="hp-summary-row">
                      <FileText size={15} />
                      <span>
                        <strong>
                          {count(data.new_critical_findings?.length)} new
                          critical finding
                          {data.new_critical_findings?.length === 1 ? "" : "s"}
                        </strong>
                        vs previous briefing
                      </span>
                      <ChevronRight size={14} />
                    </a>
                    <button
                      type="button"
                      className="hp-summary-row"
                      onClick={() => {
                        setHorizon("week");
                        setDomain("All");
                        setExpanded({});
                      }}
                    >
                      <CheckCircle2 size={15} />
                      <span>
                        <strong>
                          {count(data.short_term_resolved?.length)} no longer
                          flagged
                        </strong>
                        from this week
                      </span>
                      <ChevronRight size={14} />
                    </button>
                  </div>
                )}
                <p className="hp-tagline">
                  <Sparkle size={13} /> Turning data into a clearer tomorrow.
                </p>
              </div>
              <svg
                className="hp-snapshot-decor"
                viewBox="0 0 400 200"
                preserveAspectRatio="xMaxYMax slice"
                aria-hidden="true"
              >
                <path
                  d="M0,200 L120,90 L165,130 L230,55 L300,120 L340,85 L400,140 L400,200 Z"
                  fill="currentColor"
                />
              </svg>
            </section>
            <SalesPerformanceSection
              dashboardSummary={dashboardSummary}
              operationsSummary={operationsSummary}
              trendMetric={trendMetric}
              setTrendMetric={setTrendMetric}
              outletMetric={outletMetric}
              setOutletMetric={setOutletMetric}
            />
            <OperationalHealthSection
              inventoryStockAlerts={inventoryStockAlerts}
              operationsSummary={operationsSummary}
              supplierFillRate={supplierFillRate}
              channelBreakdown={channelBreakdown}
            />
            {(() => {
              const banner = priorityBannerCopy(presentation?.severity_counts);
              return banner ? (
                <a href="#hp-priorities-list" className="hp-decision-banner">
                  <span className="hp-decision-icon">
                    <Lightbulb size={21} />
                  </span>
                  <span className="hp-decision-text">
                    <strong>Most important: {banner}</strong>
                    <small>
                      Review the top issues below to understand impact and
                      next steps.
                    </small>
                  </span>
                  <ChevronRight size={18} />
                </a>
              ) : null;
            })()}
            <section className="hp-priorities" id="hp-priorities-list">
              <Heading
                number="05"
                label="Decision priorities"
                title="What needs attention"
                icon={AlertTriangle}
              >
                <a href="#hp-all">
                  Explore all {findings.length} findings{" "}
                  <ArrowRight size={14} />
                </a>
              </Heading>
              {findings.length ? (
                <div className="hp-priority-list">
                  {priorities.map((finding, index) => (
                    <PriorityCard
                      key={finding.id}
                      finding={finding}
                      index={index}
                      onEvidence={reveal}
                    />
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
          <div
            className={`hp-follow-content ${customer ? "has-opportunity" : ""}`}
          >
            {data && !customer && <p className="hp-muted">No customer opportunity is supplied in this snapshot. <Link to="/customers/predictions">Review customer intelligence</Link></p>}
            {customer && (
              <article className="hp-opportunity">
                <span className="hp-kicker">
                  Customer purchase opportunity · Configured Celeste outlets
                </span>
                <h3>
                  {count(customer.approaching_count)} customers approaching
                  their purchase window.
                </h3>
                <p>
                  Estimated customer demand{" "}
                  <strong>
                    {customer.expected_revenue == null
                      ? "Unavailable"
                      : formatCurrency(customer.expected_revenue)}
                  </strong>
                </p>
                <div className="hp-mini-stats">
                  <span>
                    <b>{count(customer.high_confidence_count)}</b> High
                    confidence
                  </span>
                  <span>
                    <b>{count(customer.overdue_count)}</b> Already overdue
                  </span>
                </div>
                <Link to="/customers/predictions">
                  Review customer opportunity <ArrowRight size={14} />
                </Link>
                <p className="hp-muted">
                  As of {customer.as_of_date || "date unavailable"}. Repeat-customer estimate, not booked revenue; unchanged by horizon tabs.
                </p>
              </article>
            )}
            <div className="hp-follow-grid">
              <Followup
                query={outstanding}
                title="Outstanding actions"
                destination="/actions"
              />
              <Followup
                query={blocked}
                title="Blocked verification"
                destination="/outcomes"
              />
            </div>
          </div>
          <div className="hp-case-outcomes">
            <h3>
              <CheckCircle2 size={15} /> Recent verified outcomes
            </h3>
            <Message query={outcomes} label="case outcomes" />
            {outcomes.data?.items?.map(item => <p key={item.case_id}><Link to={`/investigate/${encodeURIComponent(item.case_id)}`}>{item.latest?.title || item.metric_id}</Link> · {prettifyMetricId(item.resolution_verdict || "verdict unavailable")} · Closed {stamp(item.closed_at)}</p>)}
            {outcomes.data?.total === 0 && <span>No resolved cases recorded. </span>}
            <Link to="/outcomes">View Outcomes <ArrowRight size={12}/></Link>
          </div>
          {horizon === "week" &&
            (data?.short_term_resolved || []).slice(0, 3).map((item) => (
              <div
                className="hp-recovery"
                key={`${item.metric_id}-${item.last_flagged_date}`}
              >
                <Check size={16} />
                <p>
                  <strong>{item.metric_label} is no longer flagged.</strong> The
                  alert streak lasted {item.consecutive_days} days; last flagged{" "}
                  {item.last_flagged_date}. This does not establish that its
                  persistent case is resolved.
                </p>
              </div>
            ))}
        </section>
        {data && (
          <section className="hp-all" id="hp-all">
            <Heading
              number="07"
              label="Detailed briefing"
              title="Explore all findings"
              icon={FileText}
            >
              <span className="hp-muted">
                {filtered.length} of {findings.length} findings
              </span>
            </Heading>
            <div
              className="hp-domain-tabs"
              aria-label="Filter findings by domain"
            >
              {domains.map((name) => (
                <button
                  key={name}
                  aria-pressed={domain === name}
                  onClick={() => setDomain(name)}
                >
                  {DOMAIN_LABEL[name] || prettifyMetricId(name)}
                </button>
              ))}
            </div>
            <div className="hp-register">
              {filtered.map((finding) => (
                <details
                  id={`brief-finding-${finding.id}`}
                  key={finding.id}
                  open={Boolean(expanded[finding.id])}
                  onToggle={(event) => {
                    const isOpen = event.currentTarget.open;
                    setExpanded((previous) =>
                      previous[finding.id] === isOpen
                        ? previous
                        : { ...previous, [finding.id]: isOpen },
                    );
                  }}
                >
                  <summary>
                    <span
                      className={`hp-badge hp-${level(finding).toLowerCase()}`}
                    >
                      {level(finding)}
                    </span>
                    <span className="hp-register-title">
                      {finding.title}
                      <small>
                        {DOMAIN_LABEL[finding.domain] ||
                          prettifyMetricId(finding.domain)}{" "}
                        · {finding.confirm?.period || "Period unavailable"}
                      </small>
                    </span>
                    <strong>
                      {finding.confirm?.current == null
                        ? "—"
                        : formatByUnit(
                            finding.confirm.current,
                            finding.confirm.unit,
                          )}
                    </strong>
                    <span className="hp-expand">+</span>
                  </summary>
                  {expanded[finding.id] && (
                    <FindingDetail
                      finding={finding}
                      onAskCeleste={onAskCeleste}
                    />
                  )}
                </details>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
