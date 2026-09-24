// Phase 2e Task 2's client-side half of "do not let the frontend invent
// measurement rules or silently choose targets": these are the two small,
// pure decisions the action-target form (SignalCard.ActionTargetForm)
// makes before ever sending a save -- kept here, not inline in the
// component, purely so they're covered by a plain Node test without
// needing a DOM/component-testing harness (see targetForm.test.js and the
// same reasoning behind design/evidenceFreshness.js). The server remains
// the actual source of truth; these only give the manager a clear,
// immediate error instead of a round trip for the obvious cases, and
// never widen what the form offers beyond what the server has confirmed
// (metric, unit, weekly support) via GET /intelligence/metrics.

/** Client-side pre-check before a target save -- mirrors, but does not
 * replace, finding_actions.set_action's own validation server-side. */
export function validateTargetInput({ metricId, value }) {
  if (!metricId) return "Choose a target metric.";
  if (value === "" || value == null || Number.isNaN(Number(value))) return "Enter a numeric target value.";
  return null;
}

/** Never offers "weekly" for a metric the server hasn't confirmed
 * supports it (metric.supports_weekly_measurement, from GET
 * /intelligence/metrics) -- silently falls back to "monthly" rather than
 * letting a stale selection (e.g. after switching metrics) submit a
 * cadence the backend would reject anyway. */
export function resolveMeasurementPeriod(period, metric) {
  if (period === "weekly" && !metric?.supports_weekly_measurement) return "monthly";
  return period;
}
