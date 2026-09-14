import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { acknowledgeNotifications, fetchBrief, fetchForwardLook, updateFindingAction } from "../api";
import SignalCard from "../design/SignalCard";
import { DOMAIN_ORDER, DOMAIN_LABEL } from "../design/domains";
import { deriveLifecycle, LIFECYCLE_META } from "../design/findingLifecycle";
import TrendChart from "../charts/TrendChart";
import { formatByUnit, formatCurrency, formatNumber, formatPct } from "../format";

const FORECAST_BY_METRIC = { revenue: "revenue", order_volume: "orders" };
const CATEGORY_ORDER = { critical: 0, opportunity: 1 };
const CATEGORY_EMOJI = { critical: "🔴", opportunity: "🟢" };

const TABS = [
  { id: "long_term", label: "Long-term" },
  { id: "this_week", label: "This week" },
];

// Real severity buckets — category (critical/opportunity) x priority
// (critical/high/medium/worth_pursuing, see investigation.py's
// _priority_for). There is no "Low" bucket: nothing below
// ANOMALY_PCT_THRESHOLD (15%) ever becomes a finding at all, so a Low tier
// would always read 0 — dropped rather than padded in as fake coverage.
const SEVERITY_BUCKETS = [
  { id: "critical", label: "Critical", emoji: "🔴", match: (f) => f.category === "critical" && f.priority === "critical" },
  { id: "high", label: "High", emoji: "🟠", match: (f) => f.category === "critical" && f.priority === "high" },
  { id: "medium", label: "Medium", emoji: "🟡", match: (f) => f.category === "critical" && f.priority === "medium" },
  { id: "positive", label: "Positive", emoji: "🟢", match: (f) => f.category === "opportunity" },
];

const LIFECYCLE_ORDER = [
  "new",
  "action_required",
  "in_progress",
  "monitoring",
  "resolved_improved",
  "resolved_worsened",
  "resolved_unchanged",
  "completed_unverified",
  "dismissed",
];

// How far along the Found -> Action -> Monitoring -> Result line a
// lifecycle state has progressed, for the journey stepper below.
function journeyStepIndex(lifecycleKey) {
  switch (lifecycleKey) {
    case "action_required":
      return 1;
    case "in_progress":
      return 2;
    case "monitoring":
      return 3;
    case "resolved_improved":
    case "resolved_worsened":
    case "resolved_unchanged":
    case "completed_unverified":
      return 4;
    default:
      return 0;
  }
}

function briefingLabel(generatedAt) {
  if (!generatedAt) return null;
  const generated = new Date(generatedAt.endsWith("Z") ? generatedAt : `${generatedAt}Z`);
  if (Number.isNaN(generated.getTime())) return null;
  const isToday = generated.toDateString() === new Date().toDateString();
  const time = generated.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return isToday ? `Checked at ${time}` : `Last checked ${generated.toLocaleDateString()}`;
}

/** The most recent tracked finding for this metric from a strictly earlier
 * period than today's own flagged period — "Celeste flagged this before".
 * See mcp_server/tools/investigation.py / tracking.py: no persistent
 * situation entity spans periods yet, so this is the honest partial version
 * of continuity available from today's data model. */
function findPriorFinding(tracking, inv) {
  // tracking rows only ever exist for monthly findings ("YYYY-MM" periods) —
  // weekly (trailing-window) findings aren't tracked yet (see brain.py), and
  // their confirm.period is a date-range label, not a month key, so
  // comparing it against a "YYYY-MM" string would be a meaningless string
  // comparison rather than a real "is this earlier" check.
  if (inv.horizon === "weekly") return null;
  const priors = (tracking || []).filter((t) => t.metric_id === inv.metric_id && t.period < inv.confirm.period);
  if (priors.length === 0) return null;
  return priors.reduce((latest, t) => (t.period > latest.period ? t : latest));
}

function DeltaArrow({ value }) {
  if (value == null) return <span className="text-foreground/40">First analysis</span>;
  if (value === 0) return <span className="text-foreground/40">&rarr; 0</span>;
  return (
    <span className={value > 0 ? "text-good" : "text-critical"}>
      {value > 0 ? "↑" : "↓"} {Math.abs(value)}
    </span>
  );
}

/** How's the business, at a glance — folded into the daily scan (brain.py)
 * instead of computed live (that used to cost 35s per page load). Not
 * tab-scoped: this is a whole-business snapshot, same regardless of
 * whether Long-term or This-week findings are showing below. */
function HealthStrip({ health }) {
  if (!health || typeof health.overall !== "number") return null;
  return (
    <div className="mt-4 flex flex-wrap items-center gap-6 rounded-2xl border border-border bg-chart-surface p-4">
      <div>
        <div className="text-3xl font-bold text-foreground-strong">{health.overall}</div>
        <div className="text-xs text-foreground/60">
          Business Health &middot; <DeltaArrow value={health.overall_delta} />
        </div>
      </div>
      <div className="flex flex-1 flex-wrap gap-5">
        {health.domains.map((d) => (
          <div key={d.id} className="min-w-[84px]">
            <div className="text-xs font-medium text-foreground/60">{d.label}</div>
            <div className="text-sm">
              <span className="font-semibold text-foreground-strong">{d.score}</span>{" "}
              <DeltaArrow value={d.delta} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Customer Purchase Opportunity — a summary card for the Customer
 * Purchase Intelligence feature (backend/app/services/customer_predictions.py),
 * not another SignalCard. It's structurally different from the
 * anomaly-triggered findings below: no trend, no baseline, just today's
 * aggregate demand picture, and it's always potentially present rather
 * than triggered by a deviation — so it gets its own simple card instead
 * of being forced into the severity tiles / Business Impact ranking built
 * for the trend-finding shape. The backend (build_home_finding) already
 * decided this is significant enough to show — see its
 * HOME_FINDING_MIN_DUE_COUNT — so this component just renders whatever it
 * receives, or nothing if the backend sent null. Full detail (the
 * customer table, timing buckets, overdue tiers, pattern distribution)
 * lives on /customers/predictions; this card only ever summarizes. */
function CustomerOpportunityCard({ finding }) {
  if (!finding) return null;
  return (
    <div className="mt-4 rounded-2xl border border-accent-border bg-accent-soft p-4">
      <div className="flex items-center gap-2">
        <span className="text-lg">🔵</span>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground-strong">{finding.title}</h2>
      </div>
      <p className="mt-2 text-base font-medium text-foreground-strong">
        {formatNumber(finding.approaching_count)} repeat customers are approaching their expected purchase window.
      </p>

      <div className="mt-3 flex flex-wrap gap-6">
        <div>
          <div className="text-lg font-bold text-foreground-strong">{formatCurrency(finding.expected_revenue)}</div>
          <div className="text-xs text-foreground/60">Expected customer demand</div>
        </div>
        <div>
          <div className="text-lg font-bold text-foreground-strong">{formatNumber(finding.high_confidence_count)}</div>
          <div className="text-xs text-foreground/60">High-confidence predictions</div>
        </div>
        <div>
          <div className="text-lg font-bold text-critical">{formatNumber(finding.overdue_count)}</div>
          <div className="text-xs text-foreground/60">Already overdue</div>
        </div>
      </div>

      <p className="mt-3 text-sm text-foreground/80">
        <span className="font-medium text-foreground-strong">Why it matters — </span>
        These customers normally purchase at predictable intervals and are approaching their expected reorder
        period.
      </p>
      <p className="mt-1 text-sm text-foreground/80">
        <span className="font-medium text-foreground-strong">Recommended action — </span>
        Prioritize high-value customers who are approaching or have passed their expected purchase window.
      </p>

      <Link to="/customers/predictions" className="mt-3 inline-block text-sm font-medium text-accent hover:underline">
        View Customer Intelligence &rarr;
      </Link>
    </div>
  );
}

// Tailwind's scanner needs a static class string per class, not an
// interpolated one (`grid-cols-${n}` would never get generated) — a small
// lookup keeps the column count real (1-4 buckets) without that trap.
const GRID_COLS_CLASS = { 1: "grid-cols-1", 2: "grid-cols-2", 3: "grid-cols-3", 4: "grid-cols-4" };

/** A short-term (trailing-window) finding whose streak just ended — the
 * metric stopped being flagged, which is itself the honest "this got
 * better" signal (see weekly_streaks.py: no waiting for a future period or
 * baseline comparison the way tracking.py's monthly check needs). Only
 * relevant on the This-week tab — a resolved weekly streak has nothing to
 * do with the monthly view. */
function ResolvedStreaks({ resolved }) {
  if (!resolved || resolved.length === 0) return null;
  return (
    <div className="mt-4 space-y-2">
      {resolved.slice(0, 3).map((r) => (
        <div key={`${r.metric_id}-${r.last_flagged_date}`} className="rounded-lg border border-good/30 bg-good/10 p-3 text-sm text-foreground-strong">
          ✅ {r.metric_label} was flagged for {r.consecutive_days} day{r.consecutive_days === 1 ? "" : "s"} running — back to
          normal since {r.last_flagged_date}.
        </div>
      ))}
    </div>
  );
}

/** Severity counts, then a lifecycle-status line — "what did Celeste find"
 * as a state of the business, not just a raw finding count. */
function WhatCelesteFound({ findings, tracking }) {
  if (findings.length === 0) return null;
  const buckets = SEVERITY_BUCKETS.map((b) => ({ ...b, count: findings.filter(b.match).length })).filter((b) => b.count > 0);

  const lifecycleCounts = {};
  for (const f of findings) {
    const key = deriveLifecycle(f, tracking);
    lifecycleCounts[key] = (lifecycleCounts[key] ?? 0) + 1;
  }
  const lifecycleParts = LIFECYCLE_ORDER.filter((k) => lifecycleCounts[k]).map(
    (k) => `${lifecycleCounts[k]} ${LIFECYCLE_META[k].label}`,
  );

  return (
    <div className="mt-4">
      <div className={`grid gap-2 ${GRID_COLS_CLASS[buckets.length] ?? "grid-cols-4"}`}>
        {buckets.map((b) => (
          <div key={b.id} className="rounded-xl border border-border bg-chart-surface p-3 text-center">
            <div className="text-lg font-bold text-foreground-strong">
              {b.emoji} {b.count}
            </div>
            <div className="text-xs text-foreground/60">{b.label}</div>
          </div>
        ))}
      </div>
      {lifecycleParts.length > 0 && <p className="mt-2 text-sm text-foreground/60">{lifecycleParts.join(" · ")}</p>}
    </div>
  );
}

/** One ranked row in the Business Impact section — a mini sparkline for
 * anything with a real trend, or the stat itself for a state finding (no
 * trend/baseline exists for those, so there's nothing honest to chart
 * against). Clicking jumps to and expands the finding's domain group below
 * (no per-finding scroll anchor exists yet, so this is the same
 * granularity the domain chips already jump to). */
function ImpactRow({ finding, rank, onJump }) {
  return (
    <button
      type="button"
      onClick={() => onJump(finding.domain)}
      className="flex w-full items-center gap-3 rounded-xl border border-border bg-chart-surface p-3 text-left hover:border-accent-border"
    >
      <span className="w-5 flex-shrink-0 text-sm font-semibold text-foreground/40">{String(rank).padStart(2, "0")}</span>
      <span className="flex-shrink-0">{CATEGORY_EMOJI[finding.category] ?? "ℹ️"}</span>
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground-strong">{finding.title}</span>
      <span className="h-8 w-24 flex-shrink-0">
        {finding.trend && finding.trend.length > 1 ? (
          <TrendChart data={finding.trend} compact />
        ) : (
          <span className="block text-right text-sm font-semibold text-foreground-strong">
            {formatByUnit(finding.confirm.current, finding.confirm.unit)}
          </span>
        )}
      </span>
      <span className="w-28 flex-shrink-0 text-right text-xs text-foreground/60">
        {finding.impact?.financial_impact_rs
          ? formatCurrency(finding.impact.financial_impact_rs)
          : finding.impact?.count != null
            ? formatNumber(finding.impact.count)
            : formatPct(finding.confirm.pct_change)}
      </span>
    </button>
  );
}

/** Ranked by business_impact_score — a deterministic score (severity x
 * persistence x whether there's a real quantified Rs./count figure behind
 * it x data-quality confidence) computed once on the backend
 * (mcp_server/tools/impact_engine.py), not just severity alone, so a
 * brand-new critical finding with a tiny impact doesn't outrank a
 * smaller-looking problem that's been running for months. */
function BusinessImpact({ findings, onJump, onViewAll }) {
  if (findings.length === 0) return null;
  const ranked = [...findings]
    .sort((a, b) => (b.business_impact_score ?? 0) - (a.business_impact_score ?? 0))
    .slice(0, 5);
  return (
    <div className="mt-6">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/60">Business Impact</h2>
      <div className="mt-3 space-y-2">
        {ranked.map((f, i) => (
          <ImpactRow key={f.id} finding={f} rank={i + 1} onJump={onJump} />
        ))}
      </div>
      {findings.length > ranked.length && (
        <button type="button" onClick={onViewAll} className="mt-2 text-sm font-medium text-accent hover:underline">
          View all findings &rarr;
        </button>
      )}
    </div>
  );
}

function JourneyStepper({ activeIndex }) {
  return (
    <div className="flex items-center gap-1">
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={`h-1.5 w-5 rounded-full ${i <= activeIndex ? "bg-accent" : "bg-border"}`} />
      ))}
    </div>
  );
}

/** "What happened after we acted" — the section the whole redesign is
 * anchored around: don't just list findings, show that each one has a
 * life. Sorted furthest-along first so the findings closest to a real
 * outcome are the ones surfaced here, not buried under brand-new ones. */
function FindingJourney({ findings, tracking }) {
  const withStatus = findings
    .map((f) => ({ f, key: deriveLifecycle(f, tracking) }))
    .filter(({ key }) => key !== "dismissed" && key !== "new");
  if (withStatus.length === 0) return null;
  const sorted = [...withStatus].sort((a, b) => journeyStepIndex(b.key) - journeyStepIndex(a.key)).slice(0, 6);

  return (
    <div className="mt-6">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/60">Finding Journey</h2>
      <p className="mt-1 text-xs text-foreground/50">Found &rarr; Action required &rarr; In progress &rarr; Monitoring &rarr; Result</p>
      <div className="mt-3 space-y-2">
        {sorted.map(({ f, key }) => (
          <div key={f.id} className="flex items-center justify-between gap-3 rounded-lg border border-border p-2.5">
            <span className="min-w-0 flex-1 truncate text-sm text-foreground-strong">{f.title}</span>
            <JourneyStepper activeIndex={journeyStepIndex(key)} />
            <span className="w-36 flex-shrink-0 text-right text-xs text-foreground/60">{LIFECYCLE_META[key].label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Domain chips double as quick-nav — clicking one scrolls to (and expands)
 * that domain's section instead of just being decoration. */
function DomainChips({ counts, onJump }) {
  const entries = DOMAIN_ORDER.filter((id) => counts[id] > 0);
  if (entries.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {entries.map((id) => (
        <button
          key={id}
          type="button"
          onClick={() => onJump(id)}
          className="rounded-full border border-border px-2.5 py-1 text-xs font-medium text-foreground/70 hover:border-accent-border hover:text-accent"
        >
          {DOMAIN_LABEL[id]} {counts[id]}
        </button>
      ))}
    </div>
  );
}

function DomainGroup({ id, label, findings, expanded, onToggle }) {
  const criticalCount = findings.filter((f) => f.category === "critical").length;
  return (
    <div id={`domain-${id}`}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between rounded-lg py-1 text-left"
      >
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/60">
          {label}
          <span className="ml-2 font-normal normal-case text-foreground/40">
            {findings.length} finding{findings.length === 1 ? "" : "s"}
            {criticalCount > 0 && ` · ${criticalCount} critical`}
          </span>
        </h2>
        <span className="text-foreground/50">{expanded ? "−" : "+"}</span>
      </button>
      {expanded && <div className="mt-3 space-y-4">{findings.map((f) => f.card)}</div>}
    </div>
  );
}

/** The front door: "what did Celeste find?" as an overview-first briefing,
 * not a long scroll of fully-expanded technical cards. Structure: a
 * compact Business Health strip, "What Celeste Found" (severity +
 * lifecycle at a glance), a ranked "Business Impact" section (why these
 * specific findings matter most), then the full domain-grouped list of
 * compact, expand-on-click finding cards, and finally "Finding Journey" —
 * showing findings aren't just detected, they have a life (Found -> Action
 * -> Monitoring -> Result). Every number here comes from a real field;
 * see design/findingLifecycle.js for the two places this session added a
 * small, honest, deterministic rule (a medium priority band, a combined
 * lifecycle status) rather than fabricating what a pasted mockup assumed.
 *
 * Long-term vs this-week are separate tabs, not interleaved — they run on
 * the same nightly cadence (see brain.py), but a monthly trend and a
 * trailing-7-day window answer different questions, so mixing them in one
 * list made it hard to tell "this is a slow structural problem" from "this
 * just started this week" at a glance. Business Health isn't tab-scoped —
 * it's a whole-business snapshot from the same daily scan, not a
 * monthly-vs-weekly question. */
export default function Home({ onAskCeleste }) {
  const [activeTab, setActiveTab] = useState("long_term");
  const [expandedOverrides, setExpandedOverrides] = useState({});
  const queryClient = useQueryClient();

  const brief = useQuery({ queryKey: ["brief"], queryFn: fetchBrief });

  // Opening the Business Briefing IS seeing today's findings — no separate
  // "mark as read" control. Clears the Header badge by invalidating the
  // same query it reads (see notifications.py for what counts as "new").
  useEffect(() => {
    acknowledgeNotifications().then(() => {
      queryClient.invalidateQueries({ queryKey: ["notificationsSummary"] });
    });
    // Only on mount -- re-acknowledging on every unrelated re-render would
    // still be harmless (idempotent), but pointless extra network traffic.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Saves a finding's manager-set action state, then refetches /brief so the
  // merged result (see backend/app/main.py) reflects it immediately — /brief
  // reads a cached scan plus one small indexed table, so this refetch is
  // fast; no need for optimistic cache surgery.
  async function handleUpdateAction(findingId, patch) {
    await updateFindingAction(findingId, patch);
    queryClient.invalidateQueries({ queryKey: ["brief"] });
  }
  const forwardLook = useQuery({ queryKey: ["forwardLook"], queryFn: fetchForwardLook });

  const trendFindings = brief.data?.status === "ok" ? brief.data.investigations : null;
  const stateFindings = brief.data?.status === "ok" ? brief.data.state_findings ?? [] : [];
  const shortTermFindings = brief.data?.status === "ok" ? brief.data.short_term_findings ?? [] : [];
  const tracking = brief.data?.status === "ok" ? brief.data.tracking : null;
  const health = brief.data?.status === "ok" ? brief.data.health : null;
  const shortTermResolved = brief.data?.status === "ok" ? brief.data.short_term_resolved ?? [] : [];
  const customerFinding = brief.data?.status === "ok" ? brief.data.customer_finding ?? null : null;

  const longTermFindings = trendFindings ? [...trendFindings, ...stateFindings] : null;
  const tabFindings = activeTab === "long_term" ? longTermFindings : shortTermFindings;
  const findingCount = tabFindings?.length ?? 0;

  const domainCounts = {};
  for (const inv of tabFindings ?? []) {
    domainCounts[inv.domain] = (domainCounts[inv.domain] ?? 0) + 1;
  }

  const groups = DOMAIN_ORDER
    .map((id) => {
      const findings = (tabFindings ?? [])
        .filter((inv) => inv.domain === id)
        .sort((a, b) => (CATEGORY_ORDER[a.category] ?? 2) - (CATEGORY_ORDER[b.category] ?? 2))
        .map((inv) => {
          const forecastKey = FORECAST_BY_METRIC[inv.metric_id];
          const forecastValue = forecastKey && forwardLook.data?.status === "ok" ? forwardLook.data[forecastKey] : null;
          const forecast = forecastValue ? { ...forecastValue, note: forwardLook.data.note } : null;
          return {
            category: inv.category,
            card: (
              <SignalCard
                key={inv.id}
                investigation={inv}
                forecast={forecast}
                priorFinding={findPriorFinding(tracking, inv)}
                tracking={tracking}
                onAskCeleste={(title) => onAskCeleste?.(`Tell me more about: ${title}`)}
                onUpdateAction={handleUpdateAction}
              />
            ),
          };
        });
      const defaultExpanded = findings.some((f) => f.category === "critical");
      const overrideKey = `${activeTab}:${id}`;
      const expanded = overrideKey in expandedOverrides ? expandedOverrides[overrideKey] : defaultExpanded;
      return { id, label: DOMAIN_LABEL[id], findings, expanded, overrideKey };
    })
    .filter((g) => g.findings.length > 0);

  const jumpToDomain = (id) => {
    const overrideKey = `${activeTab}:${id}`;
    setExpandedOverrides((prev) => ({ ...prev, [overrideKey]: true }));
    requestAnimationFrame(() => {
      document.getElementById(`domain-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const jumpToFindings = () => {
    requestAnimationFrame(() => {
      document.getElementById("findings-list")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const headline = brief.isLoading
    ? "Checking your business…"
    : brief.isError || brief.data?.status === "error"
      ? "I couldn't load today's findings."
      : findingCount === 0
        ? activeTab === "this_week"
          ? "Nothing new in the last 7 days."
          : "Nothing needs your attention right now."
        : `I found ${findingCount} thing${findingCount === 1 ? "" : "s"} you should know.`;

  return (
    <div className="px-6 py-6">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-foreground/40">Celeste &middot; Business Briefing</p>

        <div className="mt-2 flex items-center gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-full px-3 py-1.5 text-sm font-medium ${
                activeTab === tab.id ? "bg-accent-soft text-accent" : "text-foreground/70 hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="mt-3 flex items-baseline justify-between">
          <h1 className="text-xl font-semibold text-foreground-strong">{headline}</h1>
          <span className="text-xs text-foreground/60">{briefingLabel(brief.data?.generated_at)}</span>
        </div>

        <HealthStrip health={health} />

        <CustomerOpportunityCard finding={customerFinding} />

        {activeTab === "this_week" && <ResolvedStreaks resolved={shortTermResolved} />}

        {brief.isLoading && (
          <div className="mt-6 space-y-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-40 animate-pulse rounded-2xl border border-border bg-chart-surface" />
            ))}
          </div>
        )}

        {!brief.isLoading && tabFindings && (
          <>
            <WhatCelesteFound findings={tabFindings} tracking={tracking} />
            <BusinessImpact findings={tabFindings} onJump={jumpToDomain} onViewAll={jumpToFindings} />
          </>
        )}

        <div id="findings-list">
          <DomainChips counts={domainCounts} onJump={jumpToDomain} />
        </div>

        {!brief.isLoading && groups.length > 0 && (
          <div className="mt-6 space-y-8">
            {groups.map((group) => (
              <DomainGroup
                key={group.id}
                id={group.id}
                label={group.label}
                findings={group.findings}
                expanded={group.expanded}
                onToggle={() =>
                  setExpandedOverrides((prev) => ({ ...prev, [group.overrideKey]: !group.expanded }))
                }
              />
            ))}
          </div>
        )}

        {!brief.isLoading && tabFindings && tabFindings.length === 0 && (
          <div className="mt-6 rounded-2xl border border-border bg-chart-surface p-6 text-sm text-foreground/70">
            {activeTab === "this_week"
              ? "Cancellation rate and delivery delays are inside their usual range for the last 7 days."
              : "Revenue, orders, and every other tracked metric are inside their usual range."}
          </div>
        )}

        {!brief.isLoading && tabFindings && tabFindings.length > 0 && (
          <FindingJourney findings={tabFindings} tracking={tracking} />
        )}
      </div>
    </div>
  );
}
