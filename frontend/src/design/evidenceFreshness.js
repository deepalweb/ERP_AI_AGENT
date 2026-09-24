/** Detects whether the evidence behind a specific finding_id has changed
 * since a manager first loaded a case, so the UI can warn before an
 * action save gets silently attributed to evidence the manager never
 * actually looked at.
 *
 * backend/app/services/case_engine.py's record_decision_snapshot always
 * captures whatever is CURRENT at the moment an action is saved -- the
 * correct definition of "the evidence behind this decision" (a scan
 * mid-review is real information, not corruption), but only if the
 * manager knows the numbers on screen might already be stale. This module
 * is the check that closes that gap; CaseDetail.jsx calls it right before
 * saving, refetching fresh case detail to compare against what was
 * rendered.
 *
 * Pure, no React/DOM -- testable with node:test, same convention as
 * http.test.js. */

export function latestObservationForFinding(observations, findingId) {
  const matches = (observations || []).filter((o) => o.finding_id === findingId);
  return matches.length > 0 ? matches[matches.length - 1] : null;
}

/** True only when both a viewed and a fresh observation exist for this
 * finding_id AND they're not the same scan run -- a finding with no
 * observation yet (both null) or one that hasn't been rescanned (both
 * carry the same version) is NOT "changed". Compares `.version` (an
 * opaque "<observation id>:<revision>" token from the backend, see
 * case_engine._observation_version), not `.observed_at` -- a wall-clock
 * timestamp isn't a reliable way to tell two observations apart (two
 * writes landing in the same clock tick would look identical), which is
 * exactly the gap this same check is meant to close. */
export function evidenceHasChanged(viewedObservations, freshObservations, findingId) {
  const viewed = latestObservationForFinding(viewedObservations, findingId);
  const fresh = latestObservationForFinding(freshObservations, findingId);
  if (!viewed || !fresh) return false;
  return viewed.version !== fresh.version;
}
