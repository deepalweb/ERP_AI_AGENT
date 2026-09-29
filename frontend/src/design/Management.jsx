import { Link } from "react-router-dom";
import { ArrowRight, AlertCircle, Inbox, ClipboardCheck, Activity } from "lucide-react";
import Badge from "./Badge";
import Button from "./Button";
import { actionStateMeta } from "./actionState";
import { buildCaseOutcomeView } from "./caseOutcomeView";
import { caseStatusMeta } from "./caseStatus";
import { DOMAIN_META } from "./domains";
import { toneForPriority } from "./Badge";
import {
  OUTCOME_STATUS_META,
  OUTCOME_VERDICT_META,
  outcomeReasonText,
} from "./outcomeStatus";
import { formatByUnit, formatDate, metricIdFromFindingId, prettifyMetricId } from "../format";

export function PageHeading({
  eyebrow = "Management workspace",
  title,
  description,
  children,
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {children}
    </div>
  );
}

export function QueryState({ query, empty = "Nothing to show yet." }) {
  if (query.isLoading)
    return (
      <div role="status" className="panel p-6 text-sm text-foreground-subtle">
        Loading…
      </div>
    );
  if (query.isError || query.data?.status === "error")
    return (
      <div
        role="alert"
        className="panel flex flex-wrap items-center gap-3 p-5 text-sm text-critical"
      >
        <AlertCircle size={18} />
        {query.data?.detail || "Could not load this section."}
        <Button onClick={() => query.refetch()}>Try again</Button>
      </div>
    );
  if (query.data?.items?.length === 0)
    return (
      <div className="empty-state">
        <Inbox size={24} />
        <p>{empty}</p>
      </div>
    );
  return null;
}

export function Pagination({ data, offset, onChange }) {
  if (!data || data.status !== "ok" || !data.total) return null;
  const limit = data.limit || 20;
  return (
    <div className="pagination">
      <span>
        {offset + 1}–{Math.min(offset + limit, data.total)} of {data.total}
      </span>
      <div className="flex gap-2">
        <Button
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button
          disabled={offset + limit >= data.total}
          onClick={() => onChange(offset + limit)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

export function WorkItem({ action, compact = false }) {
  const outcome =
    OUTCOME_VERDICT_META[action.outcome_verdict] ||
    OUTCOME_STATUS_META[action.outcome_status];
  const state = actionStateMeta(action);
  return (
    <article className={`work-item ${compact ? "compact" : ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={state.tone}>{state.label}</Badge>
        {action.overdue && <Badge tone="critical">Overdue</Badge>}
        {outcome && <Badge tone={outcome.tone}>{outcome.label}</Badge>}
        {action.case?.priority && (
          <Badge tone={toneForPriority(action.case.priority)}>{prettifyMetricId(action.case.priority)}</Badge>
        )}
      </div>
      {action.case && (
        // Frontend Intelligence Integration: "Review session" alone never
        // said why -- the case's own domain + title is the real business
        // reason a manager needs before opening anything.
        <p className="text-xs text-foreground-subtle">
          {DOMAIN_META[action.case.domain]?.shortLabel || prettifyMetricId(action.case.domain)}
          {action.case.title && <> · Origin: {action.case.title}</>}
        </p>
      )}
      <h3>
        {action.action_taken ||
          action.intended_result ||
          (() => {
            // A generic "Recorded action" heading, repeated identically on
            // every unlinked card, told a manager nothing -- the metric is
            // always derivable from the finding_id itself (see
            // metricIdFromFindingId), even when there's no linked case to
            // read a title off of.
            const metricId = action.case?.metric_id ?? metricIdFromFindingId(action.finding_id);
            return metricId ? `${prettifyMetricId(metricId)} follow-up` : "Recorded action";
          })()}
      </h3>
      {action.intended_result && (
        <p className="text-sm text-foreground">
          Objective: {action.intended_result}
        </p>
      )}
      {!compact && action.target_metric_id && (
        <p className="mt-2 text-sm text-foreground">
          Target: {prettifyMetricId(action.target_metric_id)}{" "}
          {action.target_comparison}{" "}
          {formatByUnit(action.target_value, action.target_unit)}
          {action.target_baseline_relative ? " (change from baseline)" : ""}{" "}
          ·{" "}
          {prettifyMetricId(
            action.target_measurement_period || "period not set",
          )}
        </p>
      )}
      {!compact && action.outcome_value != null && (
        <p className="mt-1 text-sm text-foreground-subtle">
          Observed: {formatByUnit(action.outcome_value, action.target_unit)}
          {action.outcome_period && ` · ${action.outcome_period}`}
        </p>
      )}
      {action.outcome_verdict === "met" && (
        <p className="mt-2 text-xs text-foreground-subtle">
          Target achieved is an observation, not proof this action caused it.
        </p>
      )}
      {!action.outcome_verdict && outcomeReasonText(action) && (
        <p className="mt-1 text-xs text-foreground-subtle">{outcomeReasonText(action)}</p>
      )}
      {action.outcome_confidence && (
        <p className="mt-1 text-xs text-foreground-subtle">
          Attribution: {prettifyMetricId(action.outcome_confidence)}
        </p>
      )}
      <div className="work-item-footer">
        <span>
          {action.owner || "Unassigned"} <span aria-hidden="true">·</span>{" "}
          {action.deadline
            ? `Due ${formatDate(action.deadline)}`
            : "No deadline"}
        </span>
        {action.case ? (
          <Link
            to={`/investigate/${encodeURIComponent(action.case_id)}?action=${encodeURIComponent(action.finding_id)}`}
          >
            View investigation <ArrowRight size={14} />
          </Link>
        ) : (
          // No case is linked to this action (direct case_id and the
          // observation-based lookup both came up empty -- see main.py's
          // /management/actions route). Last resort: this finding's own
          // metric_id, parsed from its id, filtered in Investigate -- a
          // manager never lands on a dead end, even without an exact case.
          (() => {
            const metricId = metricIdFromFindingId(action.finding_id);
            return metricId ? (
              <Link to={`/investigate?metric=${encodeURIComponent(metricId)}`}>
                Find related cases <ArrowRight size={14} />
              </Link>
            ) : (
              <span>No linked case found</span>
            );
          })()
        )}
      </div>
      {!compact && (
        <p className="mt-2 break-all text-[11px] text-foreground-subtle">
          Finding: {action.finding_id}
          {action.case &&
            ` · Case: ${caseStatusMeta(action.case.status).label}`}
        </p>
      )}
    </article>
  );
}

/** Replaces the old linear "01 Action completed -> 02 Target achieved ->
 * 03 Case resolved" explainer strip. That stepper told a manager "first
 * this, then this, therefore this" -- a causal pipeline the backend
 * deliberately doesn't enforce (a case can resolve with zero actions ever
 * recorded; a completed action never implies its target was hit). Two
 * independent verticals converging on one case is the honest picture. */
export function ParallelTracks() {
  return (
    <div className="track-diagram">
      <div className="track-col">
        <div className="track-col-head">
          <ClipboardCheck size={15} className="text-accent" />
          <span className="eyebrow">Management action</span>
        </div>
        <p className="track-step-list">Recorded &rarr; in progress &rarr; completed</p>
      </div>
      <div className="track-col">
        <div className="track-col-head">
          <Activity size={15} className="text-accent" />
          <span className="eyebrow">Business condition</span>
        </div>
        <p className="track-step-list">Measured &rarr; improved / unchanged / worsened</p>
      </div>
      <div className="track-convergence">
        Both tracks belong to the same case, but neither proves the other --
        case resolution is decided from the business condition alone.
      </div>
    </div>
  );
}

/** Outcomes' case-primary card -- one card per case, from the shared
 * caseOutcomeView read model. 3 blocks, matching the locked design
 * principle exactly: the business result and the management action are
 * shown as two separate, non-causal facts, with the non-causality
 * statement placed right next to them, not buried in prose elsewhere. */
export function CaseOutcomeCard({ item }) {
  const view = buildCaseOutcomeView(item);
  const { businessResult: result, managementAction: action, interpretation } = view;
  return (
    <article className="outcome-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="eyebrow">{prettifyMetricId(item.domain)} · {item.horizon}</span>
        <Link to={`/investigate/${encodeURIComponent(item.case_id)}`} className="text-xs font-semibold text-accent">
          Open case <ArrowRight size={12} className="inline" />
        </Link>
      </div>
      <h3>{item.latest?.title || prettifyMetricId(item.metric_id)}</h3>

      <div className="outcome-block">
        <span className="outcome-block-label">Business result</span>
        <Badge tone={result.tone} className="px-2.5 py-1 text-sm">{result.label}</Badge>
        {result.before != null && result.after != null ? (
          <p className="text-xs text-foreground-subtle">
            {result.before} &rarr; {result.after}
          </p>
        ) : (
          // A snapshot/control finding (e.g. negative-stock, cost-coverage)
          // has a current reading but no baseline concept at all -- "n/a ->
          // 7" read as a formatting bug, not an honest "no prior value."
          result.after != null && <p className="text-xs text-foreground-subtle">Current: {result.after}</p>
        )}
        {result.resolvedAt && <p className="text-xs text-foreground-subtle">Resolved {result.resolvedAt}</p>}
      </div>

      <div className="outcome-block">
        <span className="outcome-block-label">Latest management action</span>
        {action ? (
          <>
            <Badge tone={action.stateTone}>{action.stateLabel}</Badge>
            <p className="text-xs text-foreground-subtle">
              {action.owner}{action.completedAt && ` · Completed ${action.completedAt}`}
            </p>
            {action.hasEarlierOutstanding && (
              <p className="mt-1 text-xs text-warning">An earlier action on this case is still outstanding.</p>
            )}
          </>
        ) : (
          <p className="text-xs text-foreground-subtle">No action was recorded for this case.</p>
        )}
      </div>

      <div className="outcome-block outcome-interpretation">
        <span className="outcome-block-label">Interpretation</span>
        {interpretation.map((line, i) => (
          <p key={line} className={i === interpretation.length - 1 ? "outcome-attribution" : "text-xs text-foreground-subtle"}>
            {line}
          </p>
        ))}
      </div>
    </article>
  );
}
