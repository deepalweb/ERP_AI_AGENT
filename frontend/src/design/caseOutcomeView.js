import { actionStateMeta } from "./actionState";
import { RESOLUTION_VERDICT_META } from "./outcomeStatus";
import { caseStatusMeta } from "./caseStatus";
import { formatByUnit, formatDate } from "../format";

/** The shared read-model layer for Outcomes (case-primary) and Actions
 * (action-primary) -- turns a raw /cases item (case row + its most
 * recently updated linked action, already joined server-side by
 * management.list_actions/case_engine.list_cases -- see main.py's /cases
 * route) into the 3-block presentation shape both pages render, so
 * neither page re-derives its own wording for the same underlying state.
 *
 * Organizing principle (locked by design review): action execution and
 * business recovery are parallel tracks that may relate to the same case,
 * but neither proves the other. This never synthesizes a combined
 * verdict -- it only labels each track's own real state and states
 * plainly, in the `interpretation` lines, that no causal claim is made. */

function businessResult(item) {
  const isResolved = item.status === "resolved" && item.resolution_verdict;
  const meta = isResolved
    ? RESOLUTION_VERDICT_META[item.resolution_verdict]
    : caseStatusMeta(item.status);
  const confirm = item.latest?.confirm;
  return {
    label: meta.label,
    tone: meta.tone,
    resolved: isResolved,
    before: confirm?.baseline != null ? formatByUnit(confirm.baseline, confirm.unit) : null,
    after: confirm?.current != null ? formatByUnit(confirm.current, confirm.unit) : null,
    resolvedAt: item.closed_at ? formatDate(item.closed_at.slice(0, 10)) : null,
  };
}

function managementAction(item) {
  const action = item.action;
  if (!action) return null;
  const state = actionStateMeta(action);
  // item.action is only the most recently updated action on this case (see
  // main.py's /cases route -- a case can carry several, one per finding it
  // spans). Ordinarily that's also the only outstanding one, so showing it
  // alone reads fine -- but if this row is completed/not-pursued while
  // has_outstanding_action is still true, an OLDER action on the same case
  // is still open and this card isn't showing it. Surfaced explicitly
  // rather than letting "Completed" silently stand in for "case-managed."
  const staleCompleted =
    Boolean(item.has_outstanding_action) && (action.status === "completed" || action.relevant === false);
  return {
    stateLabel: state.label,
    stateTone: state.tone,
    owner: action.owner || "Unassigned",
    completedAt: action.completed_at ? formatDate(action.completed_at.slice(0, 10)) : null,
    hasEarlierOutstanding: staleCompleted,
  };
}

function interpretationLines(result, action) {
  const lines = [`Business condition: ${result.label}.`];
  if (action) {
    lines.push(`Management work: ${action.stateLabel}.`);
    lines.push("Attribution: not established -- this system does not claim the action caused the result.");
  } else {
    lines.push("Management work: No action recorded for this case.");
    lines.push("Attribution: not applicable -- there is no recorded action to attribute a cause to.");
  }
  return lines;
}

export function buildCaseOutcomeView(item) {
  const result = businessResult(item);
  const action = managementAction(item);
  return {
    businessResult: result,
    managementAction: action,
    interpretation: interpretationLines(result, action),
  };
}
