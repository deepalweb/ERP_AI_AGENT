import { useEffect, useState } from "react";
import ConfidenceBadge from "./ConfidenceBadge";
import EvidenceDrawer from "./EvidenceDrawer";
import TrendChart from "../charts/TrendChart";
import DrivenBarChart from "../charts/DrivenBarChart";
import { deriveLifecycle, LIFECYCLE_META } from "./findingLifecycle";
import { headlineFor } from "./plainLanguage";
import { formatByUnit, formatPct, monthLabel } from "../format";

const VERDICT_META = {
  improved: { emoji: "🟢", label: "Improved" },
  worsened: { emoji: "🔴", label: "Worsened" },
  unchanged: { emoji: "🟡", label: "Unchanged" },
};

/** Single badge driven by `priority` — it already fully encodes both
 * category (worth_pursuing is the only opportunity priority) and severity,
 * so a separate "category" badge next to it used to say "Critical" (the
 * category) right beside "High priority" (the severity) for the same
 * finding — two different fields both called "critical"/"high" read as
 * contradictory. One badge, one number line, no ambiguity. */
const PRIORITY_META = {
  critical: { emoji: "🔴", label: "Critical", badgeClass: "bg-critical/15 text-critical border-critical/30" },
  high: { emoji: "🟠", label: "High priority", badgeClass: "bg-warning/15 text-warning border-warning/30" },
  medium: { emoji: "🟡", label: "Medium priority", badgeClass: "bg-warning/10 text-warning/80 border-warning/20" },
  worth_pursuing: { emoji: "🟢", label: "Opportunity", badgeClass: "bg-good/15 text-good border-good/30" },
};

const LIFECYCLE_TONE_CLASS = {
  neutral: "bg-border/40 text-foreground/70",
  warning: "bg-warning/15 text-warning",
  accent: "bg-accent-soft text-accent",
  good: "bg-good/15 text-good",
  critical: "bg-critical/15 text-critical",
};

function AccordionSection({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t border-border pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between text-left text-sm font-semibold text-foreground-strong"
      >
        {title}
        <span className="text-foreground/50">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  );
}

const ROOT_CAUSE_VERDICT_META = {
  confirmed: { label: "Confirmed", badgeClass: "bg-good/15 text-good border-good/30" },
  possible_contributor: { label: "Possible contributor", badgeClass: "bg-warning/15 text-warning border-warning/30" },
  no_evidence_found: { label: "No evidence found", badgeClass: "bg-border/40 text-foreground/60 border-border" },
  unknown: { label: "Unknown", badgeClass: "bg-border/40 text-foreground/50 border-border" },
};

/** The generalized evidence-based root-cause report (checks: [...]) — each
 * check is independently labeled confirmed/possible/no-evidence/unknown,
 * never a single free-text guess, so a manager can see exactly what was
 * checked and how sure Celeste actually is. Distinct from the older
 * single-shape root cause below (rootCause.note), which
 * _check_stock_root_cause still produces for cancellation_rate — both
 * shapes are supported since only revenue is on the newer framework so far. */
function RootCauseEvidence({ checks }) {
  if (!checks || checks.length === 0) return null;
  return (
    <div className="rounded-lg border border-border bg-background p-3">
      <div className="text-sm font-medium text-foreground-strong">Why this happened</div>
      <ul className="mt-2 space-y-2">
        {checks.map((c) => {
          const meta = ROOT_CAUSE_VERDICT_META[c.verdict] ?? ROOT_CAUSE_VERDICT_META.unknown;
          return (
            <li key={c.source} className="flex items-start gap-2 text-sm">
              <span className={`mt-0.5 flex-shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${meta.badgeClass}`}>
                {meta.label}
              </span>
              <span className="text-foreground/80">{c.detail}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function BreakdownSection({ breakdowns, direction, unit, rootCause, notes }) {
  const withData = (breakdowns || []).filter((b) => (b.top_contributors || []).length > 0);
  if (withData.length === 0 && !rootCause) {
    return (
      <p className="text-sm text-foreground/60">
        {notes || "Celeste doesn't have a reliable way to break this metric down by segment yet."}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {rootCause?.checks ? (
        <RootCauseEvidence checks={rootCause.checks} />
      ) : (
        rootCause && (
          <div className="rounded-lg border border-accent-border bg-accent-soft p-3 text-sm text-foreground-strong">
            <span className="font-medium">Likely root cause:</span> {rootCause.note}
            {rootCause.matched_products?.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-foreground/80">
                {rootCause.matched_products.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            )}
          </div>
        )
      )}
      {withData.map((b) =>
        b.mode === "composition" ? (
          <EvidenceDrawer key={b.dimension} trigger={`Show breakdown by ${b.label.toLowerCase()}`}>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-foreground/60">
                  <th className="pb-1 font-medium">{b.label}</th>
                  <th className="pb-1 font-medium">Current share</th>
                  <th className="pb-1 font-medium">Change</th>
                </tr>
              </thead>
              <tbody>
                {b.top_contributors.map((c) => (
                  <tr key={c.name} className="border-t border-border">
                    <td className="py-1">{c.name}</td>
                    <td className="py-1">{c.current_share_pct}%</td>
                    <td className="py-1">{c.delta > 0 ? "+" : ""}{c.delta}pp</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </EvidenceDrawer>
        ) : (
          <div key={b.dimension}>
            <div className="mb-1 text-xs font-medium text-foreground/70">By {b.label.toLowerCase()}</div>
            <DrivenBarChart
              data={b.top_contributors}
              deltaGoodWhenPositive={direction === "higher_is_good"}
              formatValue={(v) => formatByUnit(v, unit)}
            />
          </div>
        ),
      )}
    </div>
  );
}

function ForecastSection({ forecast, unit }) {
  if (!forecast) {
    return (
      <p className="text-sm text-foreground/60">
        Celeste doesn&rsquo;t project this metric yet — only revenue and order volume have enough of a stable
        trend to forecast today.
      </p>
    );
  }
  return (
    <div>
      <p className="text-sm text-foreground/80">
        Based on {monthLabel(forecast.based_on_month)}, if the current trajectory continues, next period is
        likely to land between <strong>{formatByUnit(forecast.projected_low, unit)}</strong> and{" "}
        <strong>{formatByUnit(forecast.projected_high, unit)}</strong>.
      </p>
      <p className="mt-2 text-xs text-foreground/50">{forecast.note}</p>
    </div>
  );
}

function ActionSection({ recommendations, priorFinding }) {
  return (
    <div className="space-y-4">
      {priorFinding && (
        <div className="rounded-lg border border-border bg-background p-3 text-sm">
          <span className="font-medium text-foreground-strong">Celeste flagged this before</span>{" "}
          {priorFinding.status === "resolved" ? (
            <>
              in {monthLabel(priorFinding.period)} — outcome:{" "}
              <span>
                {VERDICT_META[priorFinding.verdict]?.emoji} {VERDICT_META[priorFinding.verdict]?.label}
              </span>
            </>
          ) : (
            <>in {monthLabel(priorFinding.period)} — still monitoring for a result.</>
          )}
        </div>
      )}
      {(!recommendations || recommendations.length === 0) && (
        <p className="text-sm text-foreground/60">No recommendation generated for this finding.</p>
      )}
      {recommendations?.map((rec, i) => (
        <div key={i} className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-foreground-strong">{rec.action}</span>
            <ConfidenceBadge tier="suggested" />
          </div>
          <div className="mt-1 text-xs text-foreground/60">
            {rec.owner} &middot; {rec.priority} priority
          </div>
          {rec.expected_benefit && (
            <div className="mt-1 text-xs text-foreground/70">
              <span className="font-medium text-foreground-strong">Expected benefit:</span> {rec.expected_benefit}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const STATUS_META = {
  not_started: "Not started",
  in_progress: "In progress",
  completed: "Completed",
};

function toggleClass(active) {
  return `rounded-full border px-3 py-1 text-xs font-medium ${
    active ? "border-accent-border bg-accent-soft text-accent" : "border-border text-foreground/70 hover:text-foreground"
  }`;
}

/** Manager-facing action tracking — separate from the automatic "did the
 * metric recover" verdict shown above via `priorFinding`. That one is
 * purely data-driven; this one records a person's own judgment (is this
 * worth acting on, did the team act, what did they do) via
 * PATCH /findings/{id}/action, merged into every finding fresh on each
 * /brief fetch (see backend/app/main.py — never baked into the daily scan
 * cache, so an update shows up immediately). */
function ActionTracker({ finding, onUpdate }) {
  const action = finding.action ?? { relevant: null, status: "not_started", action_taken: null, owner: null, updated_at: null };
  const [notes, setNotes] = useState(action.action_taken ?? "");
  const [owner, setOwner] = useState(action.owner ?? "");

  useEffect(() => {
    setNotes(action.action_taken ?? "");
  }, [action.action_taken]);

  useEffect(() => {
    setOwner(action.owner ?? "");
  }, [action.owner]);

  function save(patch) {
    onUpdate?.(finding.id, {
      relevant: action.relevant,
      status: action.status,
      action_taken: action.action_taken,
      owner: action.owner,
      ...patch,
    });
  }

  return (
    <div className="mt-4 border-t border-border pt-3">
      <div className="text-xs font-medium text-foreground/60">Track this</div>
      <div className="mt-2 flex gap-2">
        <button type="button" onClick={() => save({ relevant: true })} className={toggleClass(action.relevant === true)}>
          I&rsquo;ll act on this
        </button>
        <button type="button" onClick={() => save({ relevant: false })} className={toggleClass(action.relevant === false)}>
          Not for us
        </button>
      </div>

      {action.relevant === true && (
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(STATUS_META).map(([key, label]) => (
            <button key={key} type="button" onClick={() => save({ status: key })} className={toggleClass(action.status === key)}>
              {label}
            </button>
          ))}
        </div>
      )}

      {action.relevant === true && (
        <input
          type="text"
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          onBlur={() => save({ owner: owner || null })}
          placeholder="Who's on this? (e.g. a name or team)"
          className="mt-2 w-full rounded-lg border border-border bg-background p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
        />
      )}

      {action.relevant === true && action.status === "completed" && (
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => save({ action_taken: notes })}
          placeholder="What did the team do?"
          rows={2}
          className="mt-2 w-full rounded-lg border border-border bg-background p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
        />
      )}

      {action.updated_at && (
        <div className="mt-1 text-xs text-foreground/40">
          Updated {new Date(action.updated_at.endsWith("Z") ? action.updated_at : `${action.updated_at}Z`).toLocaleDateString()}
          {action.owner && ` · ${action.owner}`}
        </div>
      )}
    </div>
  );
}

/** One flagged finding, collapsed to a compact summary by default: badge,
 * title, current value + mini sparkline, top driver, the recommended
 * action, and a lifecycle status pill — click "View finding" to expand
 * into the full story (why/what's-next/what-to-do/track-this). Nothing
 * here ever navigates away — see the plan's "Situation Card" section for
 * why: forcing a click to another page to see evidence recreates the
 * analyst workflow this app exists to remove. Collapsing by default is
 * the fix for the previous version's problem: every finding fully
 * expanded meant scrolling through a lot before reaching the next one. */
export default function SignalCard({ investigation: inv, forecast, priorFinding, tracking, onAskCeleste, onUpdateAction }) {
  const meta =
    PRIORITY_META[inv.priority] ?? {
      emoji: "ℹ️",
      label: inv.category,
      badgeClass: "bg-accent-soft text-accent border-accent-border",
    };
  const topBreakdown = inv.breakdowns?.find((b) => (b.top_contributors || []).length > 0);
  const topDriver = topBreakdown?.top_contributors?.[0];
  const firstRecommendation = inv.recommendations?.[0];
  const lifecycleKey = deriveLifecycle(inv, tracking);
  const lifecycleMeta = LIFECYCLE_META[lifecycleKey];
  const headline = headlineFor(inv);

  // Stays open for a critical finding until its action is actually
  // completed — everything else starts collapsed to a summary.
  const [expanded, setExpanded] = useState(inv.category === "critical" && lifecycleKey !== "completed_unverified" && !lifecycleKey.startsWith("resolved"));

  return (
    <article className="rounded-2xl border border-border bg-chart-surface p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${meta.badgeClass}`}>
          {meta.emoji} {meta.label}
        </span>
        {inv.horizon === "weekly" && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs font-medium text-foreground/60">
            🕐 {inv.streak && inv.streak.consecutive_days > 1 ? `Day ${inv.streak.consecutive_days} of this issue` : "Last 7 days"}
          </span>
        )}
      </div>

      <button type="button" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded} className="mt-3 block w-full text-left">
        <p className="text-lg font-semibold text-foreground-strong">{headline}</p>
        <p className="mt-1 text-sm text-foreground/60">{inv.title}</p>

        <div className="mt-2 flex items-center gap-4">
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-foreground-strong">{formatByUnit(inv.confirm.current, inv.confirm.unit)}</span>
            <span className="text-sm text-foreground/60">{formatPct(inv.confirm.pct_change)} vs usual</span>
          </div>
          {inv.trend && inv.trend.length > 1 && (
            <div className="h-10 w-28 flex-shrink-0">
              <TrendChart data={inv.trend} compact />
            </div>
          )}
        </div>

        {topDriver && (
          <p className="mt-1 text-sm text-foreground/70">
            Mainly driven by <strong>{topDriver.name}</strong> ({topBreakdown.label.toLowerCase()}).
          </p>
        )}

        {firstRecommendation && (
          <p className="mt-2 text-sm text-foreground/80">
            <span className="font-medium text-foreground-strong">Recommended:</span> {firstRecommendation.action}
          </p>
        )}
      </button>

      <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${LIFECYCLE_TONE_CLASS[lifecycleMeta.tone]}`}>
          {lifecycleMeta.label}
        </span>
        <button type="button" onClick={() => setExpanded((v) => !v)} className="text-sm font-medium text-accent hover:underline">
          {expanded ? "Hide details" : "View finding"} &rarr;
        </button>
      </div>

      {expanded && (
        <div className="mt-4 border-t border-border pt-4">
          {inv.trend && inv.trend.length > 1 && (
            <TrendChart
              data={inv.trend}
              formatValue={(v) => formatByUnit(v, inv.confirm.unit)}
              formatX={inv.horizon === "weekly" ? (m) => m : monthLabel}
            />
          )}

          {inv.impact && (
            <p className="mt-2 text-sm text-foreground/70">
              <strong>Impact:</strong> {inv.impact.detail}
            </p>
          )}

          <div className="mt-4 space-y-3">
            <AccordionSection title="Why is this happening?">
              <BreakdownSection
                breakdowns={inv.breakdowns}
                direction={inv.metric?.direction}
                unit={inv.confirm.unit}
                rootCause={inv.root_cause}
                notes={inv.metric?.notes}
              />
            </AccordionSection>

            <AccordionSection title="What happens next?">
              <ForecastSection forecast={forecast} unit={inv.confirm.unit} />
            </AccordionSection>

            <AccordionSection title="What should we do?" defaultOpen={inv.category === "critical"}>
              <ActionSection recommendations={inv.recommendations} priorFinding={priorFinding} />
              <ActionTracker finding={inv} onUpdate={onUpdateAction} />
            </AccordionSection>
          </div>

          <button
            type="button"
            onClick={() => onAskCeleste?.(inv.title)}
            className="mt-4 text-sm font-medium text-accent hover:underline"
          >
            Ask Celeste about this &rarr;
          </button>
        </div>
      )}
    </article>
  );
}
