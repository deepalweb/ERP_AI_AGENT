import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, ChevronDown, Clock, Lightbulb, Zap } from "lucide-react";
import Badge, { toneForPriority } from "./Badge";
import Button from "./Button";
import ConfidenceBadge from "./ConfidenceBadge";
import EvidenceDrawer from "./EvidenceDrawer";
import TrendChart from "../charts/TrendChart";
import DrivenBarChart from "../charts/DrivenBarChart";
import WaterfallChart from "../charts/WaterfallChart";
import { headlineFor } from "./plainLanguage";
import { resolveMeasurementPeriod, validateTargetInput } from "./targetForm";
import { fetchStockAlerts, fetchTopProductStockCoverage } from "../api";
import { formatByUnit, formatDate, formatPct, monthLabel } from "../format";

const VERDICT_TONE = { improved: "good", worsened: "critical", unchanged: "neutral" };
const VERDICT_LABEL = { improved: "Improved", worsened: "Worsened", unchanged: "Unchanged" };

/** Single badge driven by `priority` -- it already fully encodes both
 * category (worth_pursuing is the only opportunity priority) and severity.
 * Still used by Actions Center's cards (routes/Actions.jsx) -- kept as a
 * named export with the same API through the redesign. */
const PRIORITY_LABEL = {
  critical: "Critical",
  high: "High priority",
  medium: "Medium priority",
  worth_pursuing: "Opportunity",
};

export function PriorityBadge({ priority, category }) {
  return <Badge tone={toneForPriority(priority)}>{PRIORITY_LABEL[priority] ?? category}</Badge>;
}

// The finding feed's severity indicator, shared by the header pill here and
// the compact cards in routes/Home.jsx.
export const SEVERITY_TAG_META = {
  critical: { label: "Critical", tone: "critical" },
  high: { label: "High priority", tone: "warning" },
  medium: { label: "Medium priority", tone: "warning" },
  worth_pursuing: { label: "Opportunity", tone: "good" },
};

function AccordionSection({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-border py-2.5">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-center justify-between text-left text-sm font-medium text-foreground-strong">
        {title}
        <ChevronDown className={`h-4 w-4 flex-none text-foreground-subtle transition-transform duration-150 ${open ? "rotate-180" : ""}`} strokeWidth={2} />
      </button>
      {open && <div className="mt-3 max-w-[52ch] text-sm leading-relaxed text-foreground">{children}</div>}
    </div>
  );
}

// Exported: the Investigate case-detail page (routes/CaseDetail.jsx) reuses
// this exact vocabulary rather than re-deciding what "confirmed" vs
// "possible contributor" means -- the whole point of this map is that it's
// the one place that distinction lives.
export const ROOT_CAUSE_VERDICT_TONE = {
  confirmed: "good", possible_contributor: "warning", related_not_independent: "neutral",
  no_evidence_found: "neutral", unknown: "neutral",
};
export const ROOT_CAUSE_VERDICT_LABEL = {
  confirmed: "Confirmed", possible_contributor: "Possible contributor", related_not_independent: "Related, not independent",
  no_evidence_found: "No evidence found", unknown: "Unknown",
};

// The stated business rule behind each check's verdict -- a "Confirmed"
// badge on its own doesn't say why Celeste is sure. These caption the
// actual, already-verified thresholds the backend uses (never invented
// here): evidence_engine.CONCENTRATION_THRESHOLD_PCT (60%) for every
// *_concentration source, evidence_engine.stock_availability_check /
// dark_outlet_check's own logic, and incident_engine.py's
// Z_CONFIRM_THRESHOLD (2) / LIFT_CONFIRM_THRESHOLD (3) / 50%-of-days
// persistence rule. Shown as a hover title, not a permanent caption line.
export function evidenceRuleFor(source) {
  if (source.endsWith("_concentration")) {
    return "Confirmed when one segment (or a few together) explains 60% or more of the total change.";
  }
  switch (source) {
    case "stock_availability":
      return "Flagged when a top contributing product is currently out of stock or low stock.";
    case "dark_outlet":
      return "Flagged when a top contributing store currently has zero orders.";
    case "data_quality":
      return "Based on at least 3 complete months of history; fewer is marked low-confidence.";
    case "time_lock":
      return "Confirmed when this hour's rate is more than 2 standard deviations above the normal hourly rate.";
    case "sku_cause":
      return "Confirmed when orders containing this product cancel more than 3x as often as orders without it.";
    case "persistence":
      return "Confirmed when the pattern appeared on at least half of the days observed.";
    case "cross_metric_corroboration":
      return "Flagged when a genuinely independent metric's own confirmed breakdown points at the same segment, over the same period, in the same direction — never proof of causation on its own.";
    case "related_metric_agreement":
      return "These metrics share the same underlying order data, so agreeing is expected — shown for context, never counted as independent evidence.";
    case "conflicting_signal":
      return "An independent metric confirmed the same segment but moved the opposite direction — doesn't support this finding's explanation.";
    default:
      return null;
  }
}

// "Confirmed driver" / "Possible driver" / "Under review" -- a one-word
// read on root_cause.checks' own verdicts (never a separate confidence
// score invented here), shown as a header badge next to severity.
export function confidenceLabelFor(rootCause) {
  if (!rootCause?.checks || rootCause.checks.length === 0) return rootCause ? "Likely cause identified" : null;
  if (rootCause.checks.some((c) => c.verdict === "confirmed")) return "Confirmed driver";
  if (rootCause.checks.some((c) => c.verdict === "possible_contributor")) return "Possible driver";
  return "Under review";
}

/** "Root cause (why)" -- the left half of the always-visible why/impact
 * pair. Prefers the generalized, per-check evidence report (each check
 * independently labeled confirmed/possible/no-evidence, never a single
 * free-text guess); falls back to the simpler note+matched-products shape
 * a few checks still use; falls back again to an honest "don't know yet"
 * rather than inventing a cause. */
export function RootCauseBox({ rootCause, notes }) {
  return (
    <div className="rounded-lg border border-border bg-surface-sunken p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Root cause (why)</div>
      {rootCause?.checks?.length > 0 ? (
        <ul className="mt-2 space-y-2">
          {rootCause.checks.map((c) => {
            const rule = evidenceRuleFor(c.source);
            return (
              <li key={c.source} className="flex items-start gap-2 text-sm">
                <Badge tone={ROOT_CAUSE_VERDICT_TONE[c.verdict] ?? "neutral"} className={`flex-shrink-0 ${rule ? "cursor-help" : ""}`}>
                  <span title={rule ?? undefined}>{ROOT_CAUSE_VERDICT_LABEL[c.verdict] ?? "Unknown"}</span>
                </Badge>
                <span className="text-foreground">{c.detail}</span>
              </li>
            );
          })}
        </ul>
      ) : rootCause?.note ? (
        <div className="mt-2 text-sm text-foreground">
          {rootCause.note}
          {rootCause.matched_products?.length > 0 && (
            <ul className="mt-1 list-inside list-disc">
              {rootCause.matched_products.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <p className="mt-2 text-sm text-foreground">{notes || "Celeste hasn't confirmed a specific root cause for this yet."}</p>
      )}
    </div>
  );
}

/** "Projected impact" -- the right half of the pair. Real impact_engine.py
 * output only; no fabricated dollar figure invented if the backend hasn't
 * produced one. */
export function ImpactBox({ impact }) {
  return (
    <div className="rounded-lg border border-border bg-surface-sunken p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-critical">Projected impact</div>
      <p className="mt-2 text-sm text-foreground">{impact?.detail || "Celeste hasn't quantified a business impact for this finding yet."}</p>
    </div>
  );
}

// Turns a breakdown's top contributors into an honest waterfall: the
// backend's top_contributors is truncated to the top 5 (see
// mcp_server/tools/intelligence.py's TOP_N) and is NEVER guaranteed to sum
// to the metric's real total move (evidence_engine.py is explicit about
// this). So the total step here is read independently from confirm's own
// current/baseline -- never summed from the visible bars -- and whatever
// the shown contributors don't explain is bucketed into an explicit
// "Everything else" step, so the chart can't silently imply more precision
// than the data actually supports. Only valid for "contribution"-mode
// breakdowns, where delta is a raw unit change in the metric's own unit;
// "composition" mode's delta is a percentage-point share change (not
// additive to a currency/count total) and "rate" mode's delta is a
// segment's own rate change, not its contribution to the total -- both
// keep using the ranked bar chart instead.
export function buildWaterfallSteps(contributors, totalDelta) {
  const shown = contributors.map((c) => ({ name: c.name, value: c.delta, type: "delta" }));
  const sumShown = shown.reduce((sum, c) => sum + c.value, 0);
  const residual = totalDelta - sumShown;
  const epsilon = Math.max(Math.abs(totalDelta) * 0.01, 0.5);
  const steps = [...shown];
  if (Math.abs(residual) > epsilon) steps.push({ name: "Everything else", value: residual, type: "delta" });
  steps.push({ name: "Total", value: totalDelta, type: "total" });
  return steps;
}

/** The evidence charts only (trend + breakdown-by-segment) -- root cause
 * and impact text moved out into their own boxes (see RootCauseBox /
 * ImpactBox) so the chart panel and the why/impact explanation are
 * visually separate, matching the always-visible (not accordion-hidden)
 * treatment the redesign settled on. */
export function EvidenceCharts({ trend, breakdowns, direction, unit, confirm, notes }) {
  const withData = (breakdowns || []).filter((b) => (b.top_contributors || []).length > 0);
  const totalDelta = confirm?.baseline != null ? confirm.current - confirm.baseline : null;
  return (
    <div className="space-y-4">
      {trend && trend.length > 1 && (
        <TrendChart data={trend} formatValue={(v) => formatByUnit(v, unit)} stroke="var(--accent)" gridStroke="var(--border)" axisStroke="var(--text-subtle)" />
      )}
      {withData.length === 0 && <p className="text-sm text-foreground-subtle">{notes || "Celeste doesn't have a reliable way to break this metric down by segment yet."}</p>}
      {withData.map((b) =>
        b.mode === "composition" ? (
          <EvidenceDrawer key={b.dimension} trigger={`Show breakdown by ${b.label.toLowerCase()}`}>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-foreground-subtle">
                  <th className="pb-1 font-medium">{b.label}</th>
                  <th className="pb-1 font-medium">Current share</th>
                  <th className="pb-1 font-medium">Change</th>
                </tr>
              </thead>
              <tbody>
                {b.top_contributors.map((c) => (
                  <tr key={c.name} className="border-t border-border">
                    <td className="py-1 text-foreground-strong">{c.name}</td>
                    <td className="py-1">{c.current_share_pct}%</td>
                    <td className="py-1">
                      {c.delta > 0 ? "+" : ""}
                      {c.delta}pp
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </EvidenceDrawer>
        ) : b.mode === "contribution" && totalDelta != null ? (
          <div key={b.dimension}>
            <div className="mb-1 text-xs font-medium text-foreground-subtle">By {b.label.toLowerCase()}</div>
            <WaterfallChart
              steps={buildWaterfallSteps(b.top_contributors, totalDelta)}
              formatValue={(v) => formatByUnit(v, unit)}
              deltaGoodWhenPositive={direction === "higher_is_good"}
            />
          </div>
        ) : (
          <div key={b.dimension}>
            <div className="mb-1 text-xs font-medium text-foreground-subtle">By {b.label.toLowerCase()}</div>
            <DrivenBarChart data={b.top_contributors} deltaGoodWhenPositive={direction === "higher_is_good"} formatValue={(v) => formatByUnit(v, unit)} />
          </div>
        ),
      )}
    </div>
  );
}

const OUTLET_STATUS = (outOfStock, lowStock) => {
  if (outOfStock > 0) return { label: "Ruptured", tone: "critical" };
  if (lowStock > 0) return { label: "Low stock", tone: "warning" };
  return { label: "Stable", tone: "good" };
};

/** Real outlet- and SKU-level stock context, fetched live from
 * backend/app/services/stock_alerts.py (already-existing endpoints, not
 * previously wired into a finding card). Only rendered for inventory-
 * domain findings -- meaningless context on a revenue or delivery finding.
 * The SKU hotlist prefers products this specific finding's own breakdowns
 * actually name as contributors over the company-wide shortest-covered
 * list, so the panel explains *this* finding rather than showing generic
 * inventory trivia; it falls back to the company-wide list only when
 * nothing overlaps. There are 4 real outlets (store_scope.py) -- shown by
 * their real names, not a round "6 regional depots" figure. */
function InventoryEvidence({ finding }) {
  const stockAlerts = useQuery({ queryKey: ["stockAlerts"], queryFn: fetchStockAlerts, staleTime: 5 * 60 * 1000 });
  const coverage = useQuery({ queryKey: ["topProductStockCoverage"], queryFn: () => fetchTopProductStockCoverage(), staleTime: 5 * 60 * 1000 });

  if (stockAlerts.data?.status !== "ok" || coverage.data?.status !== "ok") return null;

  const outlets = stockAlerts.data.segments_by_outlet ?? [];
  const contributorNames = new Set(
    (finding.breakdowns ?? []).flatMap((b) => (b.top_contributors ?? []).map((c) => c.name?.toLowerCase())).filter(Boolean),
  );
  const shortProducts = (coverage.data.products ?? []).filter((p) => p.short);
  const relevant = shortProducts.filter((p) => contributorNames.has(p.name?.toLowerCase()));
  const hotlist = (relevant.length > 0 ? relevant : shortProducts).slice(0, 6);

  if (outlets.length === 0 && hotlist.length === 0) return null;

  return (
    <div className="mt-4 grid grid-cols-1 gap-4 border-t border-border pt-4 lg:grid-cols-12">
      {outlets.length > 0 && (
        <div className="lg:col-span-8">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Outlet stockout index</span>
            {stockAlerts.data.as_of_date && <span className="text-[11px] text-foreground-subtle">As of {formatDate(stockAlerts.data.as_of_date)}</span>}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {outlets.map((o) => {
              const status = OUTLET_STATUS(o.out_of_stock, o.low_stock);
              const borderClass = status.tone === "critical" ? "border-critical/30" : status.tone === "warning" ? "border-warning/25" : "border-good/20";
              const textClass = status.tone === "critical" ? "text-critical" : status.tone === "warning" ? "text-warning" : "text-good";
              return (
                <div key={o.outlet} className={`rounded-lg border p-2.5 ${borderClass}`}>
                  <div className="truncate text-xs font-medium text-foreground-strong" title={o.outlet}>
                    {o.outlet}
                  </div>
                  <div className="mt-1 text-xs text-foreground-subtle">
                    {o.out_of_stock} out &middot; {o.low_stock} low
                  </div>
                  <div className={`mt-1 text-[11px] font-semibold ${textClass}`}>{status.label}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {hotlist.length > 0 && (
        <div className="lg:col-span-4">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">SKU depletion hotlist</span>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {hotlist.map((p) => (
              <span key={p.sku ?? p.name} className="inline-flex items-center gap-1.5 rounded-md border border-critical/25 bg-surface-sunken px-2 py-1 text-xs text-foreground-strong">
                <span className="h-1.5 w-1.5 flex-none rounded-full bg-critical" aria-hidden="true" />
                {p.name}
                <span className="font-semibold text-critical">{p.days_of_cover}d cov</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ForecastSection({ forecast, unit }) {
  if (!forecast) {
    return <p>Celeste doesn't project this metric yet -- only revenue and order volume have enough of a stable trend to forecast today.</p>;
  }
  return (
    <div>
      <p>
        Based on {monthLabel(forecast.based_on_month)}, if the current trajectory continues, next period is likely to land between{" "}
        <strong className="text-foreground-strong">{formatByUnit(forecast.projected_low, unit)}</strong> and{" "}
        <strong className="text-foreground-strong">{formatByUnit(forecast.projected_high, unit)}</strong>.
      </p>
      <p className="mt-2 text-foreground-subtle">{forecast.note}</p>
    </div>
  );
}

// What finding_actions showed during a prior occurrence's run, alongside
// (never instead of) the verdict -- the verdict is a pure measurement of
// what happened to the number; this says whether anyone was actually
// working on it.
const ACTION_STATUS_TEXT = {
  completed: "the team had logged this as done",
  in_progress: "the team had marked this in progress",
  not_started: "no action was logged",
};

function PriorFindingNote({ priorFinding }) {
  if (!priorFinding) return null;
  return (
    <div className="rounded-lg border border-border bg-surface-sunken p-3 text-sm">
      <span className="font-medium text-foreground-strong">Celeste flagged this before</span>{" "}
      {priorFinding.status === "resolved" ? (
        <>
          {priorFinding.label} -- outcome:{" "}
          <Badge tone={VERDICT_TONE[priorFinding.verdict] ?? "neutral"} variant="dot">
            {VERDICT_LABEL[priorFinding.verdict] ?? priorFinding.verdict}
          </Badge>
          {priorFinding.actionStatus && <span className="text-foreground-subtle"> ({ACTION_STATUS_TEXT[priorFinding.actionStatus] ?? "no action was logged"})</span>}
        </>
      ) : (
        <>{priorFinding.label} -- still monitoring for a result.</>
      )}
    </div>
  );
}

export function RecommendationsList({ recommendations }) {
  if (!recommendations || recommendations.length === 0) return null;
  return (
    <div className="space-y-3">
      {recommendations.map((rec, i) => (
        <div key={i} className="rounded-lg border border-border bg-surface-sunken p-3.5">
          <div className="flex items-start justify-between gap-3">
            <span className="text-sm leading-snug text-foreground-strong">{rec.action}</span>
            <ConfidenceBadge tier="suggested" className="flex-none" />
          </div>
          <div className="mt-1.5 text-xs text-foreground-subtle">
            {rec.owner} &middot; {rec.priority} priority
          </div>
          {rec.expected_benefit && (
            <div className="mt-1.5 text-xs text-foreground-subtle">
              <strong className="font-medium text-foreground">Expected benefit -- </strong>
              {rec.expected_benefit}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export const STATUS_META = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
};

const ACTION_KIND_META = {
  investigation: "Investigation",
  corrective: "Corrective",
};

// The same small, fixed comparison vocabulary the backend validates
// against (case_engine.COMPARISONS) -- presenting it here isn't inventing
// measurement semantics, it's showing the same closed set the server
// already enforces; the server remains the only source of truth for unit
// and cadence support (see metrics prop, populated from GET
// /intelligence/metrics).
const TARGET_COMPARISONS = [
  { value: ">=", label: "at least (>=)" },
  { value: "<=", label: "at most (<=)" },
  { value: ">", label: "more than (>)" },
  { value: "<", label: "less than (<)" },
  { value: "==", label: "exactly (==)" },
];

function unitSuffix(unit) {
  if (unit === "percent") return "%";
  if (unit === "currency") return " Rs";
  return "";
}

/** The Phase 2e Task 2 form: lets a manager declare what a specific action
 * is meant to achieve, in the exact terms the backend can actually verify
 * -- a metric picked from the server's own registry (never typed free-
 * hand), its real unit (never entered), a fixed comparison operator, and
 * a measurement cadence offered only when that metric genuinely supports
 * it (metric.supports_weekly_measurement, from GET /intelligence/metrics
 * -- see mcp_server.tools.intelligence.list_metrics). Saving only ever
 * sends what the manager chose; the server independently validates all of
 * it and is free to reject a combination this form failed to catch
 * (surfaced via `error` from the shared save() path below). */
function ActionTargetForm({ action, metrics, caseId, onSaveTarget, onDesignateObjective }) {
  const hasTarget = Boolean(action.target_metric_id);
  const [open, setOpen] = useState(hasTarget);
  const [intendedResult, setIntendedResult] = useState(action.intended_result ?? "");
  const [metricId, setMetricId] = useState(action.target_metric_id ?? "");
  const [value, setValue] = useState(action.target_value ?? "");
  const [comparison, setComparison] = useState(action.target_comparison ?? ">=");
  const [period, setPeriod] = useState(action.target_measurement_period ?? "monthly");
  const [baselineRelative, setBaselineRelative] = useState(Boolean(action.target_baseline_relative));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [designating, setDesignating] = useState(false);
  const [designateError, setDesignateError] = useState("");

  const selectedMetric = metrics.find((m) => m.id === metricId) ?? null;

  useEffect(() => {
    const resolved = resolveMeasurementPeriod(period, selectedMetric);
    if (resolved !== period) setPeriod(resolved);
  }, [selectedMetric, period]);

  async function save() {
    setError("");
    const validationError = validateTargetInput({ metricId, value });
    if (validationError) {
      setError(validationError);
      return;
    }
    setSaving(true);
    try {
      await onSaveTarget({
        intended_result: intendedResult.trim() || null,
        target_metric_id: metricId,
        target_value: Number(value),
        target_comparison: comparison,
        target_baseline_relative: baselineRelative,
        target_measurement_period: resolveMeasurementPeriod(period, selectedMetric),
      });
    } catch (e) {
      setError(e.message || "Could not save this target. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function designate() {
    setDesignateError("");
    setDesignating(true);
    try {
      await onDesignateObjective();
    } catch (e) {
      setDesignateError(e.message || "Could not set this as the case objective.");
    } finally {
      setDesignating(false);
    }
  }

  if (!open) {
    return (
      <Button size="sm" onClick={() => setOpen(true)}>
        {hasTarget ? "Edit target" : "Define a measurable target"}
      </Button>
    );
  }

  return (
    <div className="mt-2 rounded-lg border border-border bg-surface-sunken p-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
        What is this action meant to achieve?
      </p>
      {error && <p role="alert" className="mt-1 text-sm text-critical">{error}</p>}
      <textarea
        value={intendedResult}
        onChange={(e) => setIntendedResult(e.target.value)}
        placeholder="Intended result (e.g. 'restore weekend revenue above 8M')"
        rows={2}
        className="mt-2 w-full rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
      />
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <select
          value={metricId}
          onChange={(e) => setMetricId(e.target.value)}
          className="col-span-2 rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border sm:col-span-1"
        >
          <option value="">Target metric...</option>
          {metrics.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
        <select
          value={comparison}
          onChange={(e) => setComparison(e.target.value)}
          className="rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
        >
          {TARGET_COMPARISONS.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
        <div className="flex items-center gap-1">
          <input
            type="number"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Value"
            className="w-full rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
          />
          <span className="flex-none text-xs text-foreground-subtle">
            {selectedMetric ? unitSuffix(selectedMetric.unit) || selectedMetric.unit : ""}
          </span>
        </div>
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
        >
          <option value="monthly">Monthly</option>
          <option value="weekly" disabled={selectedMetric ? !selectedMetric.supports_weekly_measurement : false}>
            Weekly{selectedMetric && !selectedMetric.supports_weekly_measurement ? " (not supported for this metric)" : ""}
          </option>
        </select>
      </div>
      <label className="mt-2 flex items-center gap-2 text-xs text-foreground-subtle">
        <input type="checkbox" checked={baselineRelative} onChange={(e) => setBaselineRelative(e.target.checked)} />
        Relative to this metric's current baseline (instead of an absolute value)
      </label>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" disabled={saving} onClick={save}>
          {hasTarget ? "Update target" : "Save target"}
        </Button>
        <Button size="sm" onClick={() => setOpen(false)}>Cancel</Button>
        {hasTarget && caseId && onDesignateObjective && (
          <Button size="sm" disabled={designating} onClick={designate}>
            Make this the case's objective
          </Button>
        )}
      </div>
      {designateError && <p role="alert" className="mt-1 text-sm text-critical">{designateError}</p>}
    </div>
  );
}

/** Manager-facing action tracking -- separate from the automatic "did the
 * metric recover" verdict shown above via `priorFinding`. Always visible in
 * the card's footer. "Take action" is a real, persisted toggle (PATCH
 * /findings/{id}/action, `relevant: true`) styled as the primary CTA the
 * reference mockups use -- not a decorative button -- its label just
 * reflects the saved state once set.
 *
 * `finding` only needs `.id` and `.action` -- reused as-is by
 * routes/CaseDetail.jsx for each of a case's actions, passing a minimal
 * synthetic finding built from finding_actions.get_actions_for_case's
 * shape, rather than a second action-editing UI. `showActionKind` is
 * additive and off by default (Home.jsx's usage is unaffected): a case
 * has more than one action across its observations, so distinguishing
 * "investigation" from "corrective" only matters there.
 *
 * `showTargetForm`/`metrics`/`caseId`/`onDesignateObjective` are all
 * additive and off by default (Phase 2e Task 2) -- only routes/
 * CaseDetail.jsx passes them, so Home.jsx and Actions.jsx are completely
 * unaffected. `metrics` must be the server's own GET /intelligence/
 * metrics list (id/label/unit/supports_weekly_measurement) -- the form
 * never invents a metric, unit, or cadence itself. */
export function ActionTracker({
  finding, onUpdate, trailing, showActionKind = false,
  showTargetForm = false, metrics = [], caseId = null, onDesignateObjective = null,
}) {
  const action = finding.action ?? { relevant: null, status: "not_started", action_taken: null, owner: null, deadline: null, action_kind: null, overdue: false, updated_at: null };
  const [notes, setNotes] = useState(action.action_taken ?? "");
  const [owner, setOwner] = useState(action.owner ?? "");
  const [deadline, setDeadline] = useState(action.deadline ?? "");
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    setNotes(action.action_taken ?? "");
  }, [action.action_taken]);
  useEffect(() => {
    setOwner(action.owner ?? "");
  }, [action.owner]);
  useEffect(() => {
    setDeadline(action.deadline ?? "");
  }, [action.deadline]);

  async function save(patch) {
    setSaveError("");
    try {
      await onUpdate?.(finding.id, {
        relevant: action.relevant, status: action.status, action_taken: action.action_taken,
        owner: action.owner, deadline: action.deadline, action_kind: action.action_kind, ...patch,
      });
    } catch (error) {
      setSaveError(error.message || "Could not save this action. Please try again.");
    }
  }

  return (
    <div className="mt-4 border-t border-border pt-3.5">
      {saveError && <p role="alert" className="mb-2 text-sm text-critical">{saveError}</p>}
      {action.overdue && (
        <div className="mb-2">
          <Badge tone="critical">Overdue</Badge>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" onClick={() => save({ relevant: true })}>
            <Zap className="h-3.5 w-3.5" strokeWidth={2} />
            {action.relevant === true ? "Action noted" : "Take action"}
          </Button>
          <Button size="sm" active={action.relevant === false} onClick={() => save({ relevant: false })}>
            Not for us
          </Button>
        </div>
        {trailing}
      </div>

      {action.relevant === true && showActionKind && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-xs text-foreground-subtle">Kind</span>
          {Object.entries(ACTION_KIND_META).map(([key, label]) => (
            <Button key={key} size="sm" active={action.action_kind === key} onClick={() => save({ action_kind: key })}>
              {label}
            </Button>
          ))}
        </div>
      )}

      {action.relevant === true && (
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(STATUS_META).map(([key, label]) => (
            <Button key={key} size="sm" active={action.status === key} onClick={() => save({ status: key })}>
              {label}
            </Button>
          ))}
        </div>
      )}

      {action.relevant === true && (
        <div className="mt-2 flex gap-2">
          <input
            type="text"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            onBlur={() => save({ owner: owner || null })}
            placeholder="Who's on this? (e.g. a name or team)"
            className="w-full rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
          />
          <input
            type="date"
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
            onBlur={() => save({ deadline: deadline || null })}
            title="Deadline"
            className="flex-shrink-0 rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
          />
        </div>
      )}

      {action.relevant === true && action.status === "completed" && (
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => save({ action_taken: notes })}
          placeholder="What did the team do?"
          rows={2}
          className="mt-2 w-full rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
        />
      )}

      {action.relevant === true && showTargetForm && (
        <ActionTargetForm
          action={action}
          metrics={metrics}
          caseId={caseId}
          onSaveTarget={(patch) => save(patch)}
          onDesignateObjective={onDesignateObjective ? () => onDesignateObjective(finding.id) : null}
        />
      )}

      {action.updated_at && (
        <div className="mt-1.5 text-xs text-foreground-subtle">
          Updated {new Date(action.updated_at.endsWith("Z") ? action.updated_at : `${action.updated_at}Z`).toLocaleDateString()}
          {action.owner && ` · ${action.owner}`}
          {action.deadline && ` · due ${formatDate(action.deadline)}`}
        </div>
      )}
    </div>
  );
}

/** The finding-detail view -- rendered either as the page's one hero card
 * or, expanded in place, from a compact grid tile (see routes/Home.jsx's
 * CompactFindingCard). Never has its own route: this component IS the
 * finding-detail screen, everywhere it appears.
 *
 * Layout, top to bottom: header badges (severity, root-cause confidence,
 * optional rank, real detected date) -> the finding's real `title` as the
 * headline, with the deterministic plain-language sentence
 * (design/plainLanguage.js) underneath -> an evidence panel (the metric's
 * current value + trend/breakdown charts) -> an always-visible two-column
 * root-cause / projected-impact pair -> a collapsible Forecast (genuinely
 * secondary, only revenue/order_volume have one) -> the action-tracking
 * footer, its trailing slot showing the real top recommendation (if any)
 * next to "Ask Celeste about this". Nothing here is accordion-hidden
 * except Forecast -- matches the denser, more upfront reference the
 * redesign settled on. */
export default function SignalCard({ investigation: inv, rank, forecast, priorFinding, onAskCeleste, onUpdateAction }) {
  const topBreakdown = inv.breakdowns?.find((b) => (b.top_contributors || []).length > 0);
  const topDriver = topBreakdown?.top_contributors?.[0];
  const headline = headlineFor(inv);
  const sparklineStroke = inv.category === "critical" ? "var(--status-critical)" : "var(--status-good)";
  const severityMeta = SEVERITY_TAG_META[inv.priority] ?? { label: inv.category, tone: "accent" };
  const confidenceLabel = confidenceLabelFor(inv.root_cause);
  const topRecommendation = inv.recommendations?.[0];

  return (
    <div id={`finding-${inv.id}`} className="font-sans text-foreground">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={severityMeta.tone} variant="dot">
            {severityMeta.label.toUpperCase()}
          </Badge>
          {confidenceLabel && <Badge tone="neutral">{confidenceLabel}</Badge>}
          {rank && <span className="text-xs font-semibold uppercase tracking-wide text-foreground-subtle">Rank #{rank}</span>}
          {inv.horizon === "weekly" && (
            <span className="inline-flex items-center gap-1 text-xs text-foreground-subtle">
              <Clock className="h-3 w-3" strokeWidth={2} />
              {inv.streak && inv.streak.consecutive_days > 1 ? `Day ${inv.streak.consecutive_days} of this issue` : "Last 7 days"}
            </span>
          )}
        </div>
        {inv.detected && <span className="text-xs text-foreground-subtle">Detected {formatDate(inv.detected)}</span>}
      </div>

      {inv.data_quality?.verdict === "low_confidence" && (
        <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-warning">
          <AlertTriangle className="h-3.5 w-3.5 flex-none" strokeWidth={2} />
          {inv.data_quality.detail}
        </p>
      )}

      <h3 className="mt-3 max-w-[42ch] font-display text-xl font-bold leading-snug text-foreground-strong sm:text-2xl">{inv.title}</h3>
      <p className="mt-1.5 max-w-[52ch] text-sm text-foreground">{headline}</p>

      <div className="mt-4 rounded-lg border border-border bg-surface-sunken p-4">
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">{inv.metric?.label || "Metric"}</span>
          <div className="flex flex-wrap items-baseline gap-2.5">
            <span className="text-3xl font-bold tabular-nums text-foreground-strong">{formatByUnit(inv.confirm.current, inv.confirm.unit)}</span>
            <Badge tone={severityMeta.tone}>{severityMeta.label}</Badge>
          </div>
          <span className="text-xs tabular-nums text-foreground-subtle">{formatPct(inv.confirm.pct_change)} vs usual</span>
        </div>

        {topDriver && (
          <p className="mt-2 text-sm text-foreground">
            Mainly driven by <strong className="font-medium text-foreground-strong">{topDriver.name}</strong> ({topBreakdown.label.toLowerCase()}).
          </p>
        )}

        {(inv.trend?.length > 1 || inv.breakdowns?.length > 0) && (
          <div className="mt-4">
            <EvidenceCharts trend={inv.trend} breakdowns={inv.breakdowns} direction={inv.metric?.direction} unit={inv.confirm.unit} confirm={inv.confirm} notes={inv.metric?.notes} />
          </div>
        )}
        {!(inv.trend?.length > 1) && inv.trend && inv.trend.length <= 1 && (
          <span className="mt-3 inline-block h-[22px] w-[90px]">
            <TrendChart data={inv.trend} compact stroke={sparklineStroke} />
          </span>
        )}
        {inv.domain === "inventory" && <InventoryEvidence finding={inv} />}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <RootCauseBox rootCause={inv.root_cause} notes={inv.metric?.notes} />
        <ImpactBox impact={inv.impact} />
      </div>

      <div className="mt-2">
        <AccordionSection title="Forecast">
          <ForecastSection forecast={forecast} unit={inv.confirm.unit} />
        </AccordionSection>
      </div>

      {(priorFinding || inv.recommendations?.length > 1) && (
        <div className="mt-3 space-y-3">
          <PriorFindingNote priorFinding={priorFinding} />
          {inv.recommendations?.length > 1 && <RecommendationsList recommendations={inv.recommendations.slice(1)} />}
        </div>
      )}

      <ActionTracker
        finding={inv}
        onUpdate={onUpdateAction}
        trailing={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            {topRecommendation && (
              <span className="flex items-center gap-1.5 text-foreground-subtle">
                <Lightbulb className="h-3.5 w-3.5 flex-none text-accent" strokeWidth={2} />
                Suggested: {topRecommendation.action}
              </span>
            )}
            <button type="button" onClick={() => onAskCeleste?.(inv.title)} className="flex items-center gap-1 font-medium text-accent hover:underline">
              Ask Celeste about this <ArrowRight className="h-3 w-3" strokeWidth={2} />
            </button>
          </div>
        }
      />
    </div>
  );
}
