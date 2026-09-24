import assert from "node:assert/strict";
import { test } from "node:test";
import { evidenceHasChanged, latestObservationForFinding } from "./evidenceFreshness.js";

// version mirrors the backend's opaque "<observation id>:<revision>"
// token (case_engine._observation_version) -- observed_at is kept
// alongside purely for display-oriented assertions, never compared for
// staleness (see evidenceHasChanged).
function obs(findingId, version, observedAt = version) {
  return { finding_id: findingId, version, observed_at: observedAt };
}

test("latestObservationForFinding picks the last matching row, not just any match", () => {
  const observations = [obs("revenue-2026-09", "1:1"), obs("revenue-2026-09", "1:2")];
  assert.equal(latestObservationForFinding(observations, "revenue-2026-09").version, "1:2");
});

test("latestObservationForFinding returns null when nothing matches", () => {
  assert.equal(latestObservationForFinding([obs("revenue-2026-09", "1:1")], "order_volume-2026-09"), null);
});

test("no change detected when the same scan run backs both views", () => {
  const same = [obs("revenue-2026-09", "1:1")];
  assert.equal(evidenceHasChanged(same, same, "revenue-2026-09"), false);
});

test("a real rescan between viewing and saving IS detected as changed", () => {
  const viewed = [obs("revenue-2026-09", "1:1")];
  const fresh = [obs("revenue-2026-09", "1:2")]; // same row, rescanned -- revision bumped
  assert.equal(evidenceHasChanged(viewed, fresh, "revenue-2026-09"), true);
});

test("two writes landing in the same wall-clock instant still count as changed (the bug this replaced observed_at for)", () => {
  // Same observed_at string on both sides -- a real risk on coarse
  // clock resolutions -- but the version token still differs because
  // revision, not wall-clock time, is what's compared.
  const viewed = [obs("revenue-2026-09", "1:1", "2026-09-01T09:00:00.000")];
  const fresh = [obs("revenue-2026-09", "1:2", "2026-09-01T09:00:00.000")];
  assert.equal(evidenceHasChanged(viewed, fresh, "revenue-2026-09"), true);
});

test("a different day's row for the same monthly finding is detected as changed even if revision coincidentally matches", () => {
  // A fresh day's row starts back at revision 1 -- a DIFFERENT observation
  // id, so the composite token still differs even though the bare
  // revision number alone would coincidentally repeat.
  const viewed = [obs("revenue-2026-09", "5:1")];
  const fresh = [obs("revenue-2026-09", "6:1")];
  assert.equal(evidenceHasChanged(viewed, fresh, "revenue-2026-09"), true);
});

test("a finding with no observation on either side is not reported as changed", () => {
  assert.equal(evidenceHasChanged([], [], "revenue-2026-09"), false);
});

test("a finding that only just appeared (no viewed observation yet) is not reported as changed", () => {
  // Nothing to compare against yet -- there's no "prior view" to have gone stale.
  const fresh = [obs("revenue-2026-09", "1:1")];
  assert.equal(evidenceHasChanged([], fresh, "revenue-2026-09"), false);
});

test("other findings' observations in the same case do not trigger a false positive", () => {
  const viewed = [obs("revenue-2026-09", "1:1"), obs("order_volume-2026-09", "2:1")];
  const fresh = [obs("revenue-2026-09", "1:1"), obs("order_volume-2026-09", "2:2")];
  assert.equal(evidenceHasChanged(viewed, fresh, "revenue-2026-09"), false);
});
