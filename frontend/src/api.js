import { API_BASE_URL, apiFetch, readResponse } from "./http";

export async function fetchStoreScope() {
  const response = await apiFetch(`${API_BASE_URL}/store-scope`);

  return readResponse(response);
}

export async function fetchDashboardSummary() {
  const response = await apiFetch(`${API_BASE_URL}/dashboard/summary`);

  return readResponse(response);
}

export async function fetchBusinessHealth() {
  const response = await apiFetch(`${API_BASE_URL}/intelligence/health`);

  return readResponse(response);
}

export async function fetchOperationsSummary() {
  const response = await apiFetch(`${API_BASE_URL}/operations/summary`);

  return readResponse(response);
}

export async function fetchOperationsPickers(month) {
  const query = month ? `?month=${encodeURIComponent(month)}` : "";
  const response = await apiFetch(`${API_BASE_URL}/operations/pickers${query}`);

  return readResponse(response);
}

export async function fetchFreshProductionAverageWeight() {
  const response = await apiFetch(`${API_BASE_URL}/operations/fresh-production/average-weight`);

  return readResponse(response);
}

export async function fetchCustomerIntelligence() {
  const response = await apiFetch(`${API_BASE_URL}/customers/intelligence`);

  return readResponse(response);
}

export async function fetchInventoryDepth() {
  const response = await apiFetch(`${API_BASE_URL}/inventory/depth`);

  return readResponse(response);
}

export async function fetchInventoryTurnover() {
  const response = await apiFetch(`${API_BASE_URL}/inventory/turnover`);

  return readResponse(response);
}

export async function fetchOrdersByHour() {
  const response = await apiFetch(`${API_BASE_URL}/dashboard/orders-by-hour`);

  return readResponse(response);
}

export async function fetchChannelBreakdown(period) {
  const url = period
    ? `${API_BASE_URL}/channels/breakdown?period=${encodeURIComponent(period)}`
    : `${API_BASE_URL}/channels/breakdown`;
  const response = await apiFetch(url);

  return readResponse(response);
}

export async function fetchForwardLook() {
  const response = await apiFetch(`${API_BASE_URL}/forecast/forward-look`);

  return readResponse(response);
}

export async function fetchTimesFmOutletForecast() {
  const response = await apiFetch(`${API_BASE_URL}/forecast/outlet-orders`);

  return readResponse(response);
}

export async function fetchMetricInvestigation(metricId) {
  const response = await apiFetch(`${API_BASE_URL}/intelligence/investigate/${metricId}`);

  return readResponse(response);
}

export async function fetchCurrentPeriodStoreBreakdown(metricId) {
  const response = await apiFetch(`${API_BASE_URL}/intelligence/current-period/${metricId}/store-breakdown`);

  return readResponse(response);
}

export async function fetchCurrentPeriod({ refresh = false, signal } = {}) {
  const timeout = AbortSignal.timeout(60000);
  const response = await apiFetch(`${API_BASE_URL}/intelligence/current-period${refresh ? "?refresh=true" : ""}`, {
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  const body = await readResponse(response);
  if (!Array.isArray(body.items)) throw new Error("Current-period measurements are unavailable.");
  return body;
}

export async function fetchInvestigations() {
  const response = await apiFetch(`${API_BASE_URL}/investigations`);

  return readResponse(response);
}

export async function fetchBrief({ signal } = {}) {
  const timeout = AbortSignal.timeout(15000);
  const response = await apiFetch(`${API_BASE_URL}/brief`, {
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });

  const body = await readResponse(response);
  if (body.status !== "loading" && (body.status !== "ok" ||
      !Array.isArray(body.investigations) || !Array.isArray(body.state_findings) ||
      !Array.isArray(body.short_term_findings) || !body.home?.long || !body.home?.week)) {
    throw new Error("The briefing response is incomplete. Retry or check the backend logs.");
  }
  return body;
}

export async function fetchLossAndDamage() {
  const response = await apiFetch(`${API_BASE_URL}/operations/loss-and-damage`);

  return readResponse(response);
}

export async function fetchLoyalty() {
  const response = await apiFetch(`${API_BASE_URL}/customers/loyalty`);

  return readResponse(response);
}

export async function fetchBrandMix() {
  const response = await apiFetch(`${API_BASE_URL}/products/brand-mix`);

  return readResponse(response);
}

export async function fetchBasketAnalysis() {
  const response = await apiFetch(`${API_BASE_URL}/products/basket-analysis`);

  return readResponse(response);
}

export async function fetchConcentration() {
  const response = await apiFetch(`${API_BASE_URL}/products/concentration`);

  return readResponse(response);
}

export async function fetchAttentionScan() {
  const response = await apiFetch(`${API_BASE_URL}/intelligence/attention`);

  return readResponse(response);
}

export async function fetchNotificationsSummary() {
  const response = await apiFetch(`${API_BASE_URL}/notifications/summary`);

  return readResponse(response);
}

export async function fetchOutcomesSummary() {
  const response = await apiFetch(`${API_BASE_URL}/outcomes/summary`);

  return readResponse(response);
}

export async function acknowledgeNotifications() {
  const response = await apiFetch(`${API_BASE_URL}/notifications/acknowledge`, { method: "POST" });

  return readResponse(response);
}

export async function updateFindingAction(findingId, {
  relevant, status, action_taken, owner, deadline, action_kind,
  intended_result, target_metric_id, target_value, target_comparison, target_baseline_relative,
  target_measurement_period, evidence_version,
}) {
  const response = await apiFetch(`${API_BASE_URL}/findings/${encodeURIComponent(findingId)}/action`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      relevant, status, action_taken, owner, deadline, action_kind,
      intended_result, target_metric_id, target_value, target_comparison, target_baseline_relative,
      target_measurement_period, evidence_version,
    }),
  });

  return readResponse(response);
}

// Celeste Investigate Phase 2, final correction ("Recommendation vs
// Action"): a deliberately separate call from updateFindingAction --
// accepting one of Celeste's recommendations is an auditable planning
// decision, never an operational action, so this hits its own route
// (POST /findings/{id}/recommendation-acceptance -> case_engine.
// accept_recommendation) which never creates or touches a finding_actions
// row. Same evidence-version-conflict/scan-lock error shape as
// updateFindingAction, so the same stale-evidence handling in
// CaseDetail.jsx applies to either.
export async function acceptRecommendation(findingId, { recommendation_action, evidence_version }) {
  const response = await apiFetch(`${API_BASE_URL}/findings/${encodeURIComponent(findingId)}/recommendation-acceptance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recommendation_action, evidence_version }),
  });

  return readResponse(response);
}

export async function fetchMetricDefinitions() {
  const response = await apiFetch(`${API_BASE_URL}/intelligence/metrics`);

  return readResponse(response);
}

export async function setCaseObjective(caseId, { metric_id, target_value, comparison, baseline_relative }) {
  const response = await apiFetch(`${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/objective`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ metric_id, target_value, comparison, baseline_relative }),
  });

  return readResponse(response);
}

export async function designateCaseObjectiveFromAction(caseId, findingId) {
  const response = await apiFetch(`${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/objective/from-action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finding_id: findingId }),
  });

  return readResponse(response);
}

export async function fetchCustomerPredictionsSummary() {
  const response = await apiFetch(`${API_BASE_URL}/customers/predictions/summary`);

  return readResponse(response);
}

export async function fetchCustomerPredictions({ status, sort = "revenue", limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ sort, limit: String(limit), offset: String(offset) });
  if (status) params.set("status", status);
  const response = await apiFetch(`${API_BASE_URL}/customers/predictions?${params.toString()}`);

  return readResponse(response);
}

export async function fetchCustomerOrderForecastChart(actualDays = 5, forecastDays = 5) {
  const response = await apiFetch(
    `${API_BASE_URL}/customers/predictions/order-forecast-chart?actual_days=${actualDays}&forecast_days=${forecastDays}`
  );

  return readResponse(response);
}

export async function fetchStockAlerts() {
  const response = await apiFetch(`${API_BASE_URL}/inventory/stock-alerts`);

  return readResponse(response);
}

export async function fetchTopProductStockCoverage(topN = 15, horizonDays = 10) {
  const response = await apiFetch(
    `${API_BASE_URL}/inventory/top-product-stock-coverage?top_n=${topN}&horizon_days=${horizonDays}`
  );

  return readResponse(response);
}

export async function fetchStockCountSessionDetail(sessionId) {
  const response = await apiFetch(`${API_BASE_URL}/inventory/stuck-stock-counts/${encodeURIComponent(sessionId)}`);

  return readResponse(response);
}

export async function fetchInventoryKpiHistory() {
  const response = await apiFetch(`${API_BASE_URL}/inventory/kpi-history`);

  return readResponse(response);
}

export async function fetchStuckStockCounts() {
  const response = await apiFetch(`${API_BASE_URL}/inventory/stuck-stock-counts`);

  return readResponse(response);
}

export async function fetchCompletedStockCountVariance({ period, startDate, endDate, storeId } = {}) {
  const params = new URLSearchParams();
  if (period) params.set("period", period);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (storeId) params.set("store_id", storeId);
  const qs = params.toString();
  const response = await apiFetch(`${API_BASE_URL}/inventory/completed-stock-count-variance${qs ? `?${qs}` : ""}`);

  return readResponse(response);
}

// Takes the resolved start_date/end_date the investigation already ran
// with (from get_completed_stock_count_variance's own result), not the
// human-readable `period` label -- the API only understands YYYY-MM-DD
// bounds or a period spec like "30d", never "Last 90 days (...)".
export async function saveCompletedStockCountVarianceCase(startDate, endDate) {
  const response = await apiFetch(
    `${API_BASE_URL}/inventory/completed-stock-count-variance/save-as-case`
      + `?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`,
    { method: "POST" },
  );

  return readResponse(response);
}

// Generic counterpart for every other investigate_metric-flagged metric
// (revenue, supplier_fill_rate, purchase_price_variance, supplier_
// concentration, ...) -- no scope params, the backend recomputes the
// current flagged month for metricId itself.
export async function saveMetricInvestigationCase(metricId) {
  const response = await apiFetch(
    `${API_BASE_URL}/intelligence/investigate/${encodeURIComponent(metricId)}/save-as-case`,
    { method: "POST" },
  );

  return readResponse(response);
}

export async function fetchOutletOrderForecast(forecastDays = 5) {
  const response = await apiFetch(
    `${API_BASE_URL}/customers/predictions/outlet-forecast?forecast_days=${forecastDays}`
  );

  return readResponse(response);
}

export async function fetchCustomerSearch({ name, phone, email } = {}) {
  const params = new URLSearchParams();
  if (name) params.set("name", name);
  if (phone) params.set("phone", phone);
  if (email) params.set("email", email);
  const response = await apiFetch(`${API_BASE_URL}/customers/search?${params.toString()}`);

  return readResponse(response);
}

export async function fetchCustomerProfile(partnerId) {
  const response = await apiFetch(`${API_BASE_URL}/customers/${partnerId}/profile`);

  return readResponse(response);
}

export async function fetchCustomerNextBasket(partnerId, topN = 8) {
  const response = await apiFetch(`${API_BASE_URL}/customers/${partnerId}/next-basket?top_n=${topN}`);

  return readResponse(response);
}

export async function fetchCustomerLifecycle() {
  const response = await apiFetch(`${API_BASE_URL}/customers/lifecycle`);

  return readResponse(response);
}

// No limit/offset: the backend's own query is the expensive part (a live
// per-customer classification pass, 8-10s), not the row count, so it's
// fetched once per cell click (capped server-side at SEGMENT_LIST_CAP, top
// by revenue) and paginated client-side -- see list_customers_by_segment's
// own docstring for why re-querying per page would be wasteful.
export async function fetchCustomerLifecycleSegment(category, valueTier) {
  const params = new URLSearchParams({ category });
  if (valueTier) params.set("value_tier", valueTier);
  const response = await apiFetch(`${API_BASE_URL}/customers/lifecycle/segment?${params}`);

  return readResponse(response);
}

export async function fetchCustomerLifecycleSegmentBehavior(category, valueTier) {
  const params = new URLSearchParams({ category });
  if (valueTier) params.set("value_tier", valueTier);
  const response = await apiFetch(`${API_BASE_URL}/customers/lifecycle/segment/behavior?${params}`);

  return readResponse(response);
}

export async function fetchSecondOrderConversion() {
  const response = await apiFetch(`${API_BASE_URL}/customers/second-order-conversion`);

  return readResponse(response);
}

export async function fetchCustomerLifecycleTransitions() {
  const response = await apiFetch(`${API_BASE_URL}/customers/lifecycle/transitions`);

  return readResponse(response);
}

export async function fetchCustomerOutletBehavior() {
  const response = await apiFetch(`${API_BASE_URL}/customers/outlet-behavior`);

  return readResponse(response);
}

export async function fetchCases({ status, metric_id, domain, priority, sort, limit = 20, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set("status", status);
  if (metric_id) params.set("metric_id", metric_id);
  if (domain) params.set("domain", domain);
  if (priority) params.set("priority", priority);
  if (sort) params.set("sort", sort);
  const response = await apiFetch(`${API_BASE_URL}/cases?${params.toString()}`);

  return readResponse(response);
}

export async function refreshBrief() {
  return readResponse(await apiFetch(`${API_BASE_URL}/brief/refresh`, {
    method: "POST", signal: AbortSignal.timeout(15000),
  }));
}

export async function fetchManagementFamilies() {
  return readResponse(await apiFetch(`${API_BASE_URL}/management/families`));
}

export async function fetchTransferAccountability(filters = {}) {
  const params = new URLSearchParams();
  for (const id of filters.outlet ?? []) params.append("outlet", id);
  for (const key of filters.product ?? []) params.append("product", key);
  for (const who of filters.person ?? []) params.append("person", who);
  if (filters.from) params.append("date_from", filters.from);
  if (filters.to) params.append("date_to", filters.to);
  if (filters.recurring) params.append("recurring_only", "true");
  const query = params.toString();
  return readResponse(await apiFetch(`${API_BASE_URL}/management/transfer-accountability${query ? `?${query}` : ""}`));
}

export async function fetchManagementSummary() {
  return readResponse(await apiFetch(`${API_BASE_URL}/management/summary`));
}

export async function fetchManagementActions(filters = {}) {
  const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== undefined && value !== ""));
  return readResponse(await apiFetch(`${API_BASE_URL}/management/actions?${params}`));
}

export async function fetchFindingCase(findingId) {
  return readResponse(await apiFetch(`${API_BASE_URL}/findings/${encodeURIComponent(findingId)}/case`));
}

export async function fetchCaseDetail(caseId) {
  const response = await apiFetch(`${API_BASE_URL}/cases/${encodeURIComponent(caseId)}`);

  return readResponse(response);
}

export async function dismissCase(caseId, reason) {
  const response = await apiFetch(`${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });

  return readResponse(response);
}

export async function reopenCase(caseId, reason) {
  const response = await apiFetch(`${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/reopen`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });

  return readResponse(response);
}

// 90s, not the usual 15s-60s elsewhere in this file -- a chat turn can
// chain up to 6 tool-call round-trips server-side (agent.py's
// MAX_TOOL_ROUNDS), each a real reporting-DB query, before the model
// produces a final reply. `signal` lets the caller add a user-initiated
// Cancel on top of this timeout via AbortSignal.any, same pattern
// fetchBrief/fetchCurrentPeriod already use.
//
// /agent/chat is a Server-Sent Events stream (backend/app/services/agent.py
// stream_agent_turn), not one JSON blob -- each line is `data: {...}\n\n`,
// one event dict per line, no [DONE] sentinel (that's an OpenAI upstream
// convention; this route just closes the HTTP stream when the generator
// ends). An {type:"error"} event means the agent turn itself failed after
// streaming had already started (a mid-turn tool/model exception) -- there's
// no HTTP-status-code channel left to carry that once the stream is open,
// so it's surfaced the same way readResponse() surfaces a JSON error body:
// thrown as an Error with `.body` set, so callers can keep using the same
// `err.body?.detail` pattern regardless of which path failed.
export async function streamChatMessage(messages, { signal, onEvent } = {}) {
  const timeout = AbortSignal.timeout(90000);
  const response = await apiFetch(`${API_BASE_URL}/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (!response.ok) {
    // Only reachable for a failure before the route ever starts streaming
    // (e.g. a 422 on malformed request body) -- a failure during the turn
    // itself arrives as a {type:"error"} event in an otherwise-200 stream.
    return readResponse(response);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const line = rawEvent.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === "error") {
        const error = new Error(event.detail || "The agent failed to process this request.");
        error.body = event;
        throw error;
      }
      onEvent?.(event);
    }
  }
}

export async function fetchPerformanceSummary(month, storeId) {
  const params = new URLSearchParams();
  if (month) params.set("month", month);
  if (storeId) params.set("store_id", storeId);
  return readResponse(await apiFetch(`${API_BASE_URL}/performance/summary?${params}`));
}
