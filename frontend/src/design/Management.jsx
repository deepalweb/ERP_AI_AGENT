import { Link } from "react-router-dom";
import { ArrowRight, AlertCircle, Inbox } from "lucide-react";
import Badge from "./Badge";
import Button from "./Button";
import { caseStatusMeta } from "./caseStatus";
import {
  OUTCOME_STATUS_META,
  OUTCOME_VERDICT_META,
  RESOLUTION_VERDICT_META,
} from "./outcomeStatus";
import { formatByUnit, formatDate, prettifyMetricId } from "../format";

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

export function CaseSummary({ item }) {
  const meta = caseStatusMeta(item.status);
  const verdict = RESOLUTION_VERDICT_META[item.resolution_verdict];
  return (
    <Link
      className="case-row"
      to={`/investigate/${encodeURIComponent(item.case_id)}`}
    >
      <span
        className={`case-marker ${item.category === "opportunity" ? "positive" : ""}`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="eyebrow">
            {prettifyMetricId(item.domain)} · {item.horizon}
          </span>
          <Badge tone={meta.tone}>{meta.label}</Badge>
          {verdict && <Badge tone={verdict.tone}>{verdict.label}</Badge>}
        </div>
        <h3>{item.latest?.title || prettifyMetricId(item.metric_id)}</h3>
        <p className="text-xs text-foreground-subtle">
          Last observed{" "}
          {item.last_seen_at
            ? formatDate(item.last_seen_at.slice(0, 10))
            : "unknown"}
        </p>
      </div>
      <ArrowRight size={18} className="flex-none text-accent" />
    </Link>
  );
}

export function WorkItem({ action, compact = false }) {
  const outcome =
    OUTCOME_VERDICT_META[action.outcome_verdict] ||
    OUTCOME_STATUS_META[action.outcome_status];
  const statusLabel =
    {
      not_started: "Not started",
      in_progress: "In progress",
      completed: "Action completed",
    }[action.status] || action.status;
  return (
    <article className={`work-item ${compact ? "compact" : ""}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={action.status === "completed" ? "good" : "neutral"}>
          {statusLabel}
        </Badge>
        {action.relevant === false && <Badge>Not relevant</Badge>}
        {action.overdue && <Badge tone="critical">Overdue</Badge>}
        {outcome && <Badge tone={outcome.tone}>{outcome.label}</Badge>}
      </div>
      <h3>
        {action.action_taken ||
          action.intended_result ||
          (action.case?.metric_id
            ? `${prettifyMetricId(action.case.metric_id)} follow-up`
            : "Recorded action")}
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
            View action <ArrowRight size={14} />
          </Link>
        ) : (
          <span>Case link unavailable</span>
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
