/** Case-level evolution stepper — distinct from design/findingLifecycle.js,
 * which derives a single *finding's* action-tracker state from the older
 * tracking.py/state_tracking.py/weekly_streaks.py systems that CaseDetail.jsx
 * doesn't use. This derives a 4-stage stepper purely from the Case Store
 * fields CaseDetail.jsx already fetches (case, actions, observations) --
 * no new backend field, no invented state.
 *
 * Deliberately NOT a claim that these 4 stages are an irreversible pipeline
 * every case must pass through in order, and "done" on a stage is never a
 * claim about case resolution -- ProblemRecoveryPolicy can resolve a case
 * from the metric alone, with zero actions ever recorded (case_engine.py),
 * so a case can reach status="resolved" having skipped "Action recorded"
 * and "Outcome measured" entirely. Each stage is one of three states:
 * done (happened), current (the next thing to look at, only meaningful
 * while the case is still open), or not_applicable (the case already
 * closed without this stage ever happening -- distinct from "pending",
 * which would wrongly imply it's still expected). */

const OUTCOME_MEASURED_STATUSES = new Set(["met", "not_met"]);
const CLOSED_STATUSES = new Set(["resolved", "dismissed"]);

export const CASE_EVOLUTION_STAGES = [
  { key: "detected", label: "Finding detected" },
  { key: "evidence", label: "Evidence recorded" },
  { key: "action", label: "Action recorded" },
  { key: "outcome", label: "Outcome measured" },
];

export function caseEvolutionStages(c, actions, observations) {
  const hasEvidence = (observations?.length ?? 0) > 0;
  const hasAction = (actions?.length ?? 0) > 0;
  const hasOutcome = (actions || []).some(
    (a) => a.outcome_verdict || OUTCOME_MEASURED_STATUSES.has(a.outcome_status),
  );
  const done = { detected: true, evidence: hasEvidence, action: hasAction, outcome: hasOutcome };
  const isClosed = CLOSED_STATUSES.has(c?.status);

  // "Current" = the first not-yet-done stage, in order -- the stage a
  // manager should look at next. Only meaningful for an active case; a
  // closed case has no "next" stage to point at, done or not.
  const currentKey = isClosed ? null : CASE_EVOLUTION_STAGES.find((s) => !done[s.key])?.key ?? null;

  return CASE_EVOLUTION_STAGES.map((s) => {
    if (done[s.key]) return { ...s, state: "done" };
    if (isClosed) return { ...s, state: "not_applicable" };
    return { ...s, state: s.key === currentKey ? "current" : "pending" };
  });
}
