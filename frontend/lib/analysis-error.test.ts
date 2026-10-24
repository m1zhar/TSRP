import assert from "node:assert/strict";
import test from "node:test";
import { analysisErrorCanRetry, analysisErrorForTicker } from "./analysis-error.ts";

test("an analysis error is visible only for the ticker that produced it", () => {
  const error = { ticker: "MSFT", message: "Provider unavailable." };

  assert.equal(analysisErrorForTicker(error, "MSFT"), "Provider unavailable.");
  assert.equal(analysisErrorForTicker(error, "AAPL"), "");
  assert.equal(analysisErrorForTicker(null, "MSFT"), "");
});

test("a disabled provider does not offer a retry that cannot succeed", () => {
  assert.equal(analysisErrorCanRetry("provider_disabled"), false);
  assert.equal(analysisErrorCanRetry("rate_limited"), false);
  assert.equal(analysisErrorCanRetry("provider_unavailable"), true);
  assert.equal(analysisErrorCanRetry(undefined), true);
});

test("configuration and invalid-input errors do not offer futile retries", () => {
  for (const code of ["auth_not_configured", "origin_not_configured", "origin_not_allowed", "unsupported_asset", "invalid_ticker", "unsupported_sector", "unsupported_currency"]) assert.equal(analysisErrorCanRetry(code), false, code);
  assert.equal(analysisErrorCanRetry("request_timeout"), true);
  assert.equal(analysisErrorCanRetry("authentication_required"), true);
});
