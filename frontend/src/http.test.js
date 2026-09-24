import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { API_BASE_URL, apiFetch, readResponse, signIn, signOut } from "./http.js";

const originalFetch = globalThis.fetch;
afterEach(() => {
  signOut();
  globalThis.fetch = originalFetch;
});

test("default API requests use a relative deployment path", () => {
  assert.equal(API_BASE_URL, "/api");
});

test("a rejected action returned as HTTP 200 still reports failure", async () => {
  const response = Response.json({ status: "error", detail: "Could not save this action." });
  await assert.rejects(readResponse(response), /Could not save this action/);
});

test("non-JSON proxy failures report the HTTP status", async () => {
  await assert.rejects(readResponse(new Response("Bad gateway", { status: 502 })), /502/);
});

test("login sends credentials, subsequent requests use them, sign-out clears them", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return Response.json({ status: "ok" });
  };
  await signIn("manager", "test-password");
  await apiFetch("/api/store-scope");
  signOut();
  await apiFetch("/api/store-scope");
  assert.equal(requests[0].url, "/api/auth/session");
  assert.equal(requests[0].headers.get("Authorization"), `Basic ${btoa("manager:test-password")}`);
  assert.equal(requests[1].headers.get("Authorization"), requests[0].headers.get("Authorization"));
  assert.equal(requests[2].headers.get("Authorization"), null);
  assert.equal(requests[1].credentials, "omit");
});

test("rejected credentials are not retained", async () => {
  globalThis.fetch = async () => Response.json({ detail: "Sign in" }, { status: 401 });
  await assert.rejects(signIn("manager", "wrong"), /Sign in/);
  let headers;
  globalThis.fetch = async (_url, options) => {
    headers = options.headers;
    return Response.json({ status: "ok" });
  };
  await apiFetch("/api/store-scope");
  assert.equal(headers.get("Authorization"), null);
});

test("an expired login clears credentials before the next request", async () => {
  globalThis.fetch = async () => Response.json({ status: "ok" });
  await signIn("manager", "test-password");
  globalThis.fetch = async () => new Response(null, { status: 401 });
  await apiFetch("/api/store-scope");
  let headers;
  globalThis.fetch = async (_url, options) => {
    headers = options.headers;
    return Response.json({ status: "ok" });
  };
  await apiFetch("/api/store-scope");
  assert.equal(headers.get("Authorization"), null);
});
