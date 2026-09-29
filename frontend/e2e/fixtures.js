// Hand-built, realistic fixtures matching the actual backend response
// shapes (backend/app/services/case_engine.py's get_case_detail/
// list_cases, mcp_server/tools/intelligence.py's list_metrics) -- never
// real business data. See mockApi.js for how these are served.

export const CASE_ID = "e2e-case-1";
export const FINDING_ID = "revenue-2026-09";

export function ok(body) {
  return { status: "ok", ...body };
}

export const authStatus = ok({ mode: "local" });
export const storeScope = ok({ stores: [] });

export const emptyBrief = ok({
  cache_status: "fresh",
  refresh: { status: "idle" },
  home: {
    long: { ranked_ids: [], priority_ids: [], severity_counts: { critical: 0, high: 0, medium: 0, positive: 0 } },
    week: { ranked_ids: [], priority_ids: [], severity_counts: { critical: 0, high: 0, medium: 0, positive: 0 } },
  },
  investigations: [],
  state_findings: [],
  short_term_findings: [],
  tracking: [],
  new_critical_findings: [],
  state_tracking: [],
  streak_outcomes: [],
  data_freshness: { status: "fresh" },
  synthesis: null,
  health: null,
  short_term_resolved: [],
  customer_finding: null,
});

export const forwardLook = ok({ points: [] });
export const outcomesSummary = ok({ summary: [] });
export const customerPredictionsSummary = ok({});
export const notificationsSummary = ok({ count: 0, findings: [] });

export const metrics = ok({
  metrics: [
    {
      id: "revenue", label: "Revenue", unit: "currency", direction: "higher_is_good",
      dimensions: [], notes: null, supports_weekly_measurement: false,
    },
    {
      id: "cancellation_rate", label: "Order cancellation rate", unit: "percent", direction: "higher_is_bad",
      dimensions: [], notes: null, supports_weekly_measurement: true,
    },
  ],
});

const baseCase = {
  case_id: CASE_ID,
  metric_id: "revenue",
  domain: "financial",
  horizon: "monthly",
  category: "opportunity",
  status: "open",
  resolution_policy: "opportunity_capture",
  verification_progress: 0,
  last_verified_period: null,
  scope_status: "segment_evidenced",
  scope_segments: JSON.stringify([{ dimension: "store", segment_name: "Ethul Kotte" }]),
  opened_at: "2026-09-01T05:00:00",
  last_seen_at: "2026-09-17T05:00:00",
  closed_at: null,
  dismissed_reason: null,
  priority: "worth_pursuing",
  business_impact_score: 42.5,
  resolution_verdict: null,
  objective_metric_id: null,
  objective_target_value: null,
  objective_comparison: null,
  objective_unit: null,
  objective_baseline_relative: null,
  objective_baseline_value: null,
  objective_source_finding_id: null,
};

const latest = {
  title: "Weekend revenue is running ahead of plan",
  category: "opportunity",
  severity: null,
  priority: "worth_pursuing",
  confirm: { period: "2026-09", current: 12_500_000, baseline: 10_800_000, pct_change: 15.7, unit: "currency" },
  metric: { id: "revenue", label: "Revenue", unit: "currency", direction: "higher_is_good", notes: null },
  persistence: null,
  horizon: "monthly",
  streak: null,
  finding_id: FINDING_ID,
  root_cause: {
    checks: [
      { source: "store_concentration", verdict: "confirmed", detail: "Concentrated in Ethul Kotte (68% of the move)." },
    ],
  },
  impact: { financial_impact_rs: 1_200_000, detail: "Roughly Rs 1.2M above plan this month if the trend holds through month-end." },
  how: null,
  recommendations: [
    { action: "Consider extending the weekend promo into weekdays.", owner: "Marketing", priority: "medium", expected_benefit: "Further lift if the same segment responds." },
  ],
  breakdowns: null,
};

export function caseListFixture() {
  return ok({
    items: [{ ...baseCase, latest: { ...latest, root_cause: undefined, impact: undefined, how: undefined, recommendations: undefined, breakdowns: undefined } }],
    total: 1,
    limit: 20,
    offset: 0,
  });
}

// actionsOverride lets a test simulate the state AFTER a save (so the
// mock server behaves like the real one: refetching after a save shows
// the newly created finding_actions row).
export function caseDetailFixture({ actions = [], caseOverrides = {} } = {}) {
  return ok({
    case: { ...baseCase, ...caseOverrides },
    resolution_policy: { name: "opportunity_capture", verification_window: 2 },
    latest,
    observations: [
      {
        id: 1, case_id: CASE_ID, scan_run_id: "2026-09-17", finding_id: FINDING_ID,
        observed_at: "2026-09-17T05:00:00", revision: 1, version: "1:1", snapshot: "{}",
      },
    ],
    observations_note: "Each observation reflects the last scan of that calendar day, not every scan run that day -- it is not a complete evidence log.",
    performance_history: [
      { observed_at: "2026-09-17T05:00:00", scan_run_id: "2026-09-17", current: 12_500_000, baseline: 10_800_000 },
    ],
    relationships: [],
    history: [
      { id: 1, case_id: CASE_ID, at: "2026-09-01T05:00:00", event_type: "opened", detail: JSON.stringify({ detected_value: 12_500_000 }) },
    ],
    actions,
  });
}
