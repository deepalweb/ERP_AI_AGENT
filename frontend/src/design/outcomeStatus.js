import { formatDate, prettifyMetricId } from "../format";

/** Outcome vocabulary for a case's declared objective/target -- distinct
 * from CASE_STATUS_META (design/caseStatus.js), which answers "is this
 * case still open." This answers "did the thing we were trying to achieve
 * actually happen," at two levels a manager needs kept separate:
 *
 * - resolution_verdict (business_cases.resolution_verdict): the CASE's own
 *   verdict -- recovered/captured/expired/inconclusive. Set once, when the
 *   case closes.
 * - outcome_status / outcome_verdict (finding_actions columns): the
 *   ACTION's target-measurement state -- pending/blocked/inconclusive
 *   while unresolved, met/not_met once a real observation lands.
 *
 * Every label/tone maps 1:1 to a real backend value -- nothing here
 * recomputes a verdict. Extracted from CaseDetail.jsx (Frontend Experience
 * v2, Stage 1) so Actions/Outcomes can share the exact same copy instead
 * of drifting into a second wording for the same backend states. */

export const RESOLUTION_VERDICT_TEXT = {
  recovered: "Recovered -- the underlying problem cleared its own anomaly threshold and stayed clear.",
  captured: "Captured -- the declared business objective was verified achieved.",
  expired: "Expired -- the opportunity stopped being flagged without a verified objective being achieved.",
  inconclusive: "Inconclusive -- the objective's metric moved as intended, but an overlapping action means the result can't be confidently attributed.",
};

// Short badge form of the same four verdicts, for list views (Actions,
// Outcomes) where the full sentence above is too long for a row.
export const RESOLUTION_VERDICT_META = {
  recovered: { label: "Recovered", tone: "good" },
  captured: { label: "Captured", tone: "good" },
  expired: { label: "Expired", tone: "neutral" },
  inconclusive: { label: "Inconclusive", tone: "warning" },
};

export const OUTCOME_VERDICT_META = {
  met: { label: "Target met", tone: "good" },
  not_met: { label: "Target not met", tone: "critical" },
};

// outcome_status carries the fuller pending/blocked/inconclusive state
// (Phase 2e Task 3) -- "met"/"not_met" reuse OUTCOME_VERDICT_META above;
// this covers the three states that never reach a verdict at all.
// Deliberately worded to never read as "failed": blocked/inconclusive
// both mean "we don't have a clean answer," not "the target wasn't hit."
export const OUTCOME_STATUS_META = {
  pending: { label: "Outcome pending", tone: "neutral" },
  blocked: { label: "Overdue -- needs follow-up", tone: "warning" },
  inconclusive: { label: "Inconclusive -- evidence never arrived", tone: "warning" },
};

// Frontend Intelligence Integration, Phase 11: the badges above are the
// same three words for every action in that state -- this adds the ONE
// real, per-action fact that explains why, from fields already recorded
// on the action (never fabricated: no session counts or reporting-period
// completeness numbers invented here, only what target_measurement_period/
// deadline/outcome_checked_at actually say). Returns null when there's
// truly nothing more specific to add than the badge already says.
export function outcomeReasonText(action) {
  if (action.outcome_status === "pending") {
    return action.target_measurement_period
      ? `Checked against the ${prettifyMetricId(action.target_measurement_period)} reporting period, once it completes.`
      : null;
  }
  if (action.outcome_status === "blocked") {
    return action.deadline
      ? `Past its ${formatDate(action.deadline)} deadline with no recorded observation yet.`
      : null;
  }
  if (action.outcome_status === "inconclusive") {
    return action.outcome_checked_at
      ? `Last checked ${formatDate(action.outcome_checked_at.slice(0, 10))} -- the required evidence never arrived by then.`
      : null;
  }
  return null;
}
