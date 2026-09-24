import assert from "node:assert/strict";
import { test } from "node:test";
import { formatConfirmPeriod } from "./format.js";

// Celeste Investigate Phase 3 (revenue investigation): REGRESSION --
// CaseDetail.jsx called formatDate() unconditionally on confirm.period,
// which only ever worked for a snapshot finding's full "YYYY-MM-DD"
// period. A real monthly revenue case (period "2026-08") rendered "As of
// Invalid Date" in production. formatConfirmPeriod auto-detects the shape
// instead of assuming one.

test("formatConfirmPeriod: a monthly period (YYYY-MM) renders as a month/year label, never Invalid Date", () => {
  const text = formatConfirmPeriod("2026-08");
  assert.doesNotMatch(text, /Invalid Date/);
  assert.match(text, /2026/);
});

test("formatConfirmPeriod: a snapshot period (full YYYY-MM-DD date) renders as a short date", () => {
  const text = formatConfirmPeriod("2026-09-15");
  assert.doesNotMatch(text, /Invalid Date/);
  assert.match(text, /Sep/);
});

test("formatConfirmPeriod: an already-human trailing-window label passes through unchanged", () => {
  assert.equal(formatConfirmPeriod("Sep 03 – Sep 09"), "Sep 03 – Sep 09");
});

test("formatConfirmPeriod: a missing period is reported honestly, not as Invalid Date", () => {
  assert.equal(formatConfirmPeriod(null), "n/a");
  assert.equal(formatConfirmPeriod(undefined), "n/a");
  assert.equal(formatConfirmPeriod(""), "n/a");
});
