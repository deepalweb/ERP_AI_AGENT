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

export async function fetchForwardLook() {
  const response = await fetch(`${API_BASE_URL}/forecast/forward-look`);

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
