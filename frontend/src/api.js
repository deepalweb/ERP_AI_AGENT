const API_BASE_URL = "http://localhost:8000";

export async function fetchStoreScope() {
  const response = await fetch(`${API_BASE_URL}/store-scope`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchDashboardSummary() {
  const response = await fetch(`${API_BASE_URL}/dashboard/summary`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchBusinessHealth() {
  const response = await fetch(`${API_BASE_URL}/intelligence/health`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchOperationsSummary() {
  const response = await fetch(`${API_BASE_URL}/operations/summary`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerIntelligence() {
  const response = await fetch(`${API_BASE_URL}/customers/intelligence`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchInventoryDepth() {
  const response = await fetch(`${API_BASE_URL}/inventory/depth`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchOrdersByHour() {
  const response = await fetch(`${API_BASE_URL}/dashboard/orders-by-hour`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchChannelBreakdown(period) {
  const url = period
    ? `${API_BASE_URL}/channels/breakdown?period=${encodeURIComponent(period)}`
    : `${API_BASE_URL}/channels/breakdown`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchForwardLook() {
  const response = await fetch(`${API_BASE_URL}/forecast/forward-look`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchMetricInvestigation(metricId) {
  const response = await fetch(`${API_BASE_URL}/intelligence/investigate/${metricId}`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchInvestigations() {
  const response = await fetch(`${API_BASE_URL}/investigations`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchBrief() {
  const response = await fetch(`${API_BASE_URL}/brief`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchLossAndDamage() {
  const response = await fetch(`${API_BASE_URL}/operations/loss-and-damage`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchLoyalty() {
  const response = await fetch(`${API_BASE_URL}/customers/loyalty`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchBrandMix() {
  const response = await fetch(`${API_BASE_URL}/products/brand-mix`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchBasketAnalysis() {
  const response = await fetch(`${API_BASE_URL}/products/basket-analysis`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchConcentration() {
  const response = await fetch(`${API_BASE_URL}/products/concentration`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchAttentionScan() {
  const response = await fetch(`${API_BASE_URL}/intelligence/attention`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchNotificationsSummary() {
  const response = await fetch(`${API_BASE_URL}/notifications/summary`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function acknowledgeNotifications() {
  const response = await fetch(`${API_BASE_URL}/notifications/acknowledge`, { method: "POST" });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function updateFindingAction(findingId, { relevant, status, action_taken, owner }) {
  const response = await fetch(`${API_BASE_URL}/findings/${encodeURIComponent(findingId)}/action`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ relevant, status, action_taken, owner }),
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerPredictionsSummary() {
  const response = await fetch(`${API_BASE_URL}/customers/predictions/summary`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerPredictions({ status, sort = "revenue", limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ sort, limit: String(limit), offset: String(offset) });
  if (status) params.set("status", status);
  const response = await fetch(`${API_BASE_URL}/customers/predictions?${params.toString()}`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerOrderForecastChart(actualDays = 5, forecastDays = 5) {
  const response = await fetch(
    `${API_BASE_URL}/customers/predictions/order-forecast-chart?actual_days=${actualDays}&forecast_days=${forecastDays}`
  );

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchStockAlerts() {
  const response = await fetch(`${API_BASE_URL}/inventory/stock-alerts`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchTopProductStockCoverage(topN = 15, horizonDays = 10) {
  const response = await fetch(
    `${API_BASE_URL}/inventory/top-product-stock-coverage?top_n=${topN}&horizon_days=${horizonDays}`
  );

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchOutletOrderForecast(forecastDays = 5) {
  const response = await fetch(
    `${API_BASE_URL}/customers/predictions/outlet-forecast?forecast_days=${forecastDays}`
  );

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerSearch({ name, phone, email } = {}) {
  const params = new URLSearchParams();
  if (name) params.set("name", name);
  if (phone) params.set("phone", phone);
  if (email) params.set("email", email);
  const response = await fetch(`${API_BASE_URL}/customers/search?${params.toString()}`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerNextBasket(partnerId, topN = 8) {
  const response = await fetch(`${API_BASE_URL}/customers/${partnerId}/next-basket?top_n=${topN}`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerLifecycle() {
  const response = await fetch(`${API_BASE_URL}/customers/lifecycle`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function fetchCustomerOutletBehavior() {
  const response = await fetch(`${API_BASE_URL}/customers/outlet-behavior`);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

export async function sendChatMessage(messages) {
  const response = await fetch(`${API_BASE_URL}/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}
