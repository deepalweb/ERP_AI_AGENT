/** Action-state vocabulary (semantic Layer A) -- answers "where is this
 * piece of management work," nothing about whether it worked. Deliberately
 * only the 4 states a real finding_actions row can be in
 * (not_started/in_progress/completed, plus relevant=false) -- a
 * recommendation that hasn't yet become a tracked action ("Proposed" /
 * "Accepted") is a different concept (recommendation acceptance, already
 * handled in CaseDetail.jsx via case_history's recommendation_approved
 * events) and deliberately stays out of this layer, per explicit design
 * review: recommendation acceptance and action creation are separate
 * events, so a not_started action row must never be labeled "Accepted."
 *
 * Distinct from CASE_STATUS_META (design/caseStatus.js, Layer D -- is the
 * business problem still open) and OUTCOME_STATUS_META (design/
 * outcomeStatus.js, Layer C -- did the declared target get hit). Extracted
 * from Management.jsx's WorkItem, which previously inlined this as an ad
 * hoc object literal instead of a shared, named vocabulary. */

export const ACTION_STATE_META = {
  not_started: { label: "Not started", tone: "neutral" },
  in_progress: { label: "In progress", tone: "accent" },
  completed: { label: "Completed", tone: "good" },
};

// relevant=false overrides status entirely -- a dismissed action is "Not
// pursued" regardless of whatever status it was in when dismissed.
export function actionStateMeta(action) {
  if (action.relevant === false) return { label: "Not pursued", tone: "neutral" };
  return ACTION_STATE_META[action.status] ?? { label: action.status ?? "Unknown", tone: "neutral" };
}
