import assert from "node:assert/strict";
import test from "node:test";
import { ApiResponseError, parseRetryAfterSeconds, requestJson } from "./api-client.ts";

const tokenResponse = () => new Response(JSON.stringify({ data: { access_token: "test-access-token", expires_in: 60 } }), { status: 200 });

function authenticatedFetch(apiResponse: Response, onApiRequest?: (init: RequestInit | undefined) => void): typeof fetch {
  return async (input, init) => {
    if (input === "/api/backend-token") return tokenResponse();
    onApiRequest?.(init);
    return apiResponse;
  };
}

test("a stalled API request aborts at its deadline with a recoverable message", async () => {
  let receivedSignal: AbortSignal | undefined;
  const hangingFetch: typeof fetch = (_input, init) => new Promise((_resolve, reject) => {
    receivedSignal = init?.signal ?? undefined;
    receivedSignal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  });

  await assert.rejects(requestJson("/api/analyze?ticker=AAPL", {}, 10, hangingFetch), /took too long/u);
  assert.equal(receivedSignal?.aborted, true);
});

test("caller cancellation remains an abort rather than being reported as a timeout", async () => {
  const caller = new AbortController();
  const hangingFetch: typeof fetch = (_input, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  });
  const request = requestJson("/api/search", { signal: caller.signal }, 500, hangingFetch);
  caller.abort();
  await assert.rejects(request, (error: unknown) => error instanceof DOMException && error.name === "AbortError");
});

test("API helper preserves JSON content type and returns the data envelope", async () => {
  let requestInit: RequestInit | undefined;
  let observedDate: string | null = null;
  const successfulFetch = authenticatedFetch(new Response(JSON.stringify({ data: [{ ticker: "AAPL" }] }), {
    status: 200,
    headers: { "Content-Type": "application/json", "X-Request-ID": "a".repeat(32), "Date": "Sat, 10 Oct 2026 07:00:00 GMT" },
  }), (init) => {
    requestInit = init;
  });

  const result = await requestJson<{ ticker: string }[]>("/api/search", { method: "POST", body: "{}" }, 100, successfulFetch, (response) => {
    observedDate = response.headers.get("Date");
  });
  assert.deepEqual(result, [{ ticker: "AAPL" }]);
  assert.equal(observedDate, "Sat, 10 Oct 2026 07:00:00 GMT");
  assert.equal((requestInit?.headers as Record<string, string>)?.["Content-Type"], "application/json");
  assert.equal((requestInit?.headers as Record<string, string>)?.Authorization, "Bearer test-access-token");
  assert.ok(requestInit?.signal instanceof AbortSignal);
});

test("malformed success envelopes fail safely instead of exposing a type error", async () => {
  const nullEnvelope = authenticatedFetch(new Response("null", { status: 200 }));
  await assert.rejects(requestJson("/api/analyze?ticker=AAPL", {}, 100, nullEnvelope), /unexpected response/u);
});

test("server error messages retain the safe request reference ID", async () => {
  const failedFetch = authenticatedFetch(new Response(JSON.stringify({ error: { message: "Try again shortly." } }), {
    status: 503,
    headers: { "Content-Type": "application/json", "X-Request-ID": "b".repeat(32) },
  }));
  await assert.rejects(requestJson("/api/analyze?ticker=AAPL", {}, 100, failedFetch), /Try again shortly\. \(Reference ID: b{32}\)/u);
});

test("typed API failures retain a safe error code for non-retryable states", async () => {
  const failedFetch = authenticatedFetch(new Response(JSON.stringify({
    error: { code: "provider_disabled", message: "Market-data access is not enabled for this deployment." },
  }), { status: 503 }));

  await assert.rejects(
    requestJson("/api/analyze?ticker=AAPL", {}, 100, failedFetch),
    (error: unknown) => error instanceof Error && error.message.includes("Market-data access is not enabled")
      && "code" in error && error.code === "provider_disabled",
  );
});

test("plain-text 429 responses become typed rate-limit errors with a bounded wait and request reference", async () => {
  const limited = authenticatedFetch(new Response("Too Many Requests", {
    status: 429,
    headers: { "Retry-After": "30", "X-Request-ID": "d".repeat(32) },
  }));
  await assert.rejects(requestJson("/api/analyze?ticker=AAPL", {}, 100, limited), (error: unknown) =>
    error instanceof Error && error.message.includes("Wait about 30 seconds")
      && "code" in error && error.code === "rate_limited"
      && "status" in error && error.status === 429
      && "retryAfterSeconds" in error && error.retryAfterSeconds === 30
      && error.message.includes(`Reference ID: ${"d".repeat(32)}`));
});

test("Retry-After accepts seconds or an HTTP date and rejects unsafe or invalid waits", () => {
  const now = Date.parse("Sat, 10 Oct 2026 07:00:00 GMT");
  assert.equal(parseRetryAfterSeconds("15", null, now), 15);
  assert.equal(parseRetryAfterSeconds("Sat, 10 Oct 2026 07:00:12 GMT", "Sat, 10 Oct 2026 07:00:00 GMT", now), 12);
  assert.equal(parseRetryAfterSeconds("Saturday, 10-Oct-26 07:00:12 GMT", "Sat, 10 Oct 2026 07:00:00 GMT", now), 12);
  assert.equal(parseRetryAfterSeconds("Sat Oct 10 07:00:12 2026", "Sat, 10 Oct 2026 07:00:00 GMT", now), 12);
  assert.equal(parseRetryAfterSeconds("0", null, now), undefined);
  assert.equal(parseRetryAfterSeconds("86401", null, now), undefined);
  assert.equal(parseRetryAfterSeconds("not-a-date", null, now), undefined);
  assert.equal(parseRetryAfterSeconds("1e2", null, now), undefined);
  assert.equal(parseRetryAfterSeconds("1.5", null, now), undefined);
  assert.equal(parseRetryAfterSeconds("+5", null, now), undefined);
  assert.equal(parseRetryAfterSeconds("Mon, 01 Jan 2026 07:00:12 GMT", "Sat, 10 Oct 2026 07:00:00 GMT", now), undefined);
});

test("a rate-limited token endpoint preserves its safe retry guidance", async () => {
  const limitedToken: typeof fetch = async () => new Response("temporarily limited", {
    status: 429,
    headers: { "Retry-After": "20" },
  });
  await assert.rejects(requestJson("/api/analyze?ticker=AAPL", {}, 100, limitedToken), (error: unknown) =>
    error instanceof Error && error.message.includes("Wait about 20 seconds")
      && "code" in error && error.code === "rate_limited");
});

test("a server-side request-body timeout is shown as a safe recoverable error", async () => {
  const timedOutFetch = authenticatedFetch(new Response(JSON.stringify({
    error: { code: "request_timeout", message: "The request body did not arrive in time. Please try again." },
  }), {
    status: 408,
    headers: { "Content-Type": "application/json", "X-Request-ID": "c".repeat(32) },
  }));

  await assert.rejects(
    requestJson("/api/scenario", { method: "POST", body: "{}" }, 100, timedOutFetch),
    /request body did not arrive in time\. Please try again\. \(Reference ID: c{32}\)/u,
  );
});

test("missing or malformed access token fails before the backend request", async () => {
  let backendCalled = false;
  const noToken: typeof fetch = async (input) => {
    if (input === "/api/backend-token") return new Response(JSON.stringify({ error: { message: "Research access could not be verified." } }), { status: 503 });
    backendCalled = true;
    return new Response("{}", { status: 200 });
  };
  await assert.rejects(requestJson("/api/analyze?ticker=AAPL", {}, 100, noToken), /Research access could not be verified/u);
  assert.equal(backendCalled, false);
});

test("token configuration failures retain their typed code and never call the provider API", async () => {
  let calls = 0;
  const failed: typeof fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ error: { code: "auth_not_configured", message: "Secure API access is not configured for this deployment." } }), { status: 503 });
  };
  await assert.rejects(requestJson("/api/analyze?ticker=AAPL", {}, 100, failed), (error: unknown) => error instanceof ApiResponseError && error.code === "auth_not_configured" && error.status === 503);
  assert.equal(calls, 1);
});
