import assert from "node:assert/strict";
import { test } from "node:test";
import { caseEvolutionStages } from "./caseEvolution.js";

function stateOf(stages, key) {
  return stages.find((s) => s.key === key).state;
}

function action(overrides = {}) {
  return { status: "not_started", outcome_status: null, outcome_verdict: null, ...overrides };
}

test("a case detected without an action: only detected is done, action is the current stage", () => {
  const stages = caseEvolutionStages({ status: "open" }, [], [{ observed_at: "2026-09-01" }]);
  assert.equal(stateOf(stages, "detected"), "done");
  assert.equal(stateOf(stages, "evidence"), "done");
  assert.equal(stateOf(stages, "action"), "current");
  assert.equal(stateOf(stages, "outcome"), "pending");
});

test("an investigation action recorded (no target) advances action but not outcome", () => {
  const stages = caseEvolutionStages(
    { status: "open" },
    [action({ status: "in_progress", action_kind: "investigation" })],
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "action"), "done");
  assert.equal(stateOf(stages, "outcome"), "current");
});

test("multiple actions with different statuses: action stage is done as soon as any action exists", () => {
  const stages = caseEvolutionStages(
    { status: "open" },
    [action({ status: "completed" }), action({ status: "not_started" })],
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "action"), "done");
});

test("a corrective action completed but outcome pending: outcome is not measured yet", () => {
  const stages = caseEvolutionStages(
    { status: "open" },
    [action({ status: "completed", action_kind: "corrective", outcome_status: "pending" })],
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "action"), "done");
  assert.equal(stateOf(stages, "outcome"), "current");
});

test("an inconclusive outcome does not count as a measured outcome (evidence never arrived, not a real measurement)", () => {
  const stages = caseEvolutionStages(
    { status: "verification_pending" },
    [action({ status: "completed", outcome_status: "inconclusive" })],
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "outcome"), "current");
});

test("a blocked (overdue) outcome does not count as a measured outcome either", () => {
  const stages = caseEvolutionStages(
    { status: "open" },
    [action({ status: "completed", outcome_status: "blocked" })],
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "outcome"), "current");
});

test("a met/not_met outcome_status counts as measured even without an explicit outcome_verdict field", () => {
  const stages = caseEvolutionStages(
    { status: "resolved" },
    [action({ status: "completed", outcome_status: "met" })],
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "outcome"), "done");
});

test("REGRESSION: a case resolved with zero actions (metric recovered on its own) marks the un-reached stages not_applicable, not stuck-pending", () => {
  // ProblemRecoveryPolicy can resolve a case purely from the metric
  // clearing its own anomaly threshold -- no management action is
  // required. Showing "Action recorded" / "Outcome measured" as an
  // incomplete pending stage here would misread as "this case is stuck,"
  // when it is in fact closed and those stages were never expected.
  const stages = caseEvolutionStages({ status: "resolved" }, [], [{ observed_at: "2026-09-01" }]);
  assert.equal(stateOf(stages, "action"), "not_applicable");
  assert.equal(stateOf(stages, "outcome"), "not_applicable");
  // A closed case has no "next" stage to point a manager at.
  assert.ok(stages.every((s) => s.state !== "current"));
});

test("a dismissed case with no action also reads not_applicable, not pending", () => {
  const stages = caseEvolutionStages({ status: "dismissed" }, [], []);
  assert.equal(stateOf(stages, "evidence"), "not_applicable");
  assert.equal(stateOf(stages, "action"), "not_applicable");
  assert.equal(stateOf(stages, "outcome"), "not_applicable");
});

test("a reopened case is treated as active, not closed -- un-reached stages stay pending/current", () => {
  const stages = caseEvolutionStages({ status: "reopened" }, [], [{ observed_at: "2026-09-01" }]);
  assert.equal(stateOf(stages, "action"), "current");
  assert.equal(stateOf(stages, "outcome"), "pending");
});

test("a resolved case that DID have a measured outcome shows the real progression, not not_applicable", () => {
  const stages = caseEvolutionStages(
    { status: "resolved" },
    [action({ status: "completed", outcome_status: "met" })],
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "action"), "done");
  assert.equal(stateOf(stages, "outcome"), "done");
});

test("done never regresses to not_applicable just because the case is closed", () => {
  // A case can close with SOME stages genuinely reached and others not --
  // not_applicable only ever applies to a stage that was never done.
  const stages = caseEvolutionStages(
    { status: "resolved" },
    [action({ status: "in_progress" })], // action recorded, but no outcome
    [{ observed_at: "2026-09-01" }],
  );
  assert.equal(stateOf(stages, "action"), "done");
  assert.equal(stateOf(stages, "outcome"), "not_applicable");
});
