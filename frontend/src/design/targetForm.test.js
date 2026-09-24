import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveMeasurementPeriod, validateTargetInput } from "./targetForm.js";

test("validateTargetInput requires a metric to be chosen", () => {
  assert.equal(validateTargetInput({ metricId: "", value: "5" }), "Choose a target metric.");
});

test("validateTargetInput requires a numeric value", () => {
  assert.equal(validateTargetInput({ metricId: "revenue", value: "" }), "Enter a numeric target value.");
  assert.equal(validateTargetInput({ metricId: "revenue", value: "not-a-number" }), "Enter a numeric target value.");
});

test("validateTargetInput accepts a metric and a numeric value, including zero and negatives", () => {
  assert.equal(validateTargetInput({ metricId: "revenue", value: "8000000" }), null);
  assert.equal(validateTargetInput({ metricId: "revenue", value: "0" }), null);
  assert.equal(validateTargetInput({ metricId: "revenue", value: "-3" }), null);
});

test("resolveMeasurementPeriod keeps weekly only when the server-confirmed metric supports it", () => {
  const supportsWeekly = { id: "cancellation_rate", supports_weekly_measurement: true };
  const monthlyOnly = { id: "revenue", supports_weekly_measurement: false };

  assert.equal(resolveMeasurementPeriod("weekly", supportsWeekly), "weekly");
  assert.equal(resolveMeasurementPeriod("weekly", monthlyOnly), "monthly");
});

test("resolveMeasurementPeriod never invents weekly support when no metric is selected yet", () => {
  assert.equal(resolveMeasurementPeriod("weekly", null), "monthly");
  assert.equal(resolveMeasurementPeriod("monthly", null), "monthly");
});
