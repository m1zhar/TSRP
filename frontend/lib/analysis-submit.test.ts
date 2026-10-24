import assert from "node:assert/strict";
import test from "node:test";
import { shouldReloadSubmittedTicker } from "./analysis-submit.ts";

test("a same-ticker submit retries even when the previous analysis has no result", () => {
  const currentTicker = "AAPL";
  const submittedTicker = "aapl";
  const previousAnalysis = null;

  assert.equal(previousAnalysis, null);
  assert.equal(shouldReloadSubmittedTicker(currentTicker, submittedTicker), true);
});

test("submitting a different ticker navigates and lets URL state start the request", () => {
  assert.equal(shouldReloadSubmittedTicker("AAPL", "MSFT"), false);
});
