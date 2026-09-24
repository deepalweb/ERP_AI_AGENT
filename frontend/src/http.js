export const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL || "/api").replace(/\/+$/, "");

// Shared-login credentials live only in this tab's memory, never in a
// Vite variable, localStorage, sessionStorage, or a URL.
let authorization = null;

export function signOut() {
  authorization = null;
  if (typeof window !== "undefined") window.dispatchEvent(new Event("celeste:signout"));
}

export async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers);
  headers.set("X-Requested-With", "XMLHttpRequest");
  if (authorization && !headers.has("Authorization")) headers.set("Authorization", authorization);
  const response = await fetch(url, { ...options, headers, credentials: "omit" });
  if (response.status === 401) signOut();
  return response;
}

export async function readResponse(response) {
  const body = await response.json().catch(() => null);
  if (!response.ok || body?.status === "error") {
    const error = new Error(body?.detail || `Request failed with status ${response.status}`);
    // Additive: existing callers only ever read .message, so this changes
    // nothing for them. New callers (e.g. the evidence-freshness conflict
    // on /findings/{id}/action) can inspect the full structured body
    // (error.body.error === "stale_evidence", etc.) without a second
    // round trip to re-fetch what the error response already carried.
    error.body = body;
    error.httpStatus = response.status;
    throw error;
  }
  if (body === null) throw new Error("The server returned an invalid response.");
  return body;
}

export async function fetchAccessStatus() {
  return readResponse(await apiFetch(`${API_BASE_URL}/auth/status`));
}

export async function signIn(username, password) {
  const candidate = `Basic ${btoa(`${username}:${password}`)}`;
  await readResponse(await apiFetch(`${API_BASE_URL}/auth/session`, {
    headers: { Authorization: candidate },
  }));
  authorization = candidate;
}
