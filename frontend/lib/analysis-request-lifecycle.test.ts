import assert from "node:assert/strict";
import test from "node:test";
import { cancelPendingAnalysisForDifferentTicker, cancelRequestsOnHome, claimAutomaticAnalysisAttempt, isAnalysisTickerStillSelected, releaseAutomaticAnalysisAttempt } from "./analysis-request-lifecycle.ts";

test("automatic analysis is attempted once per selected ticker until selection changes", () => {
  const attemptedTicker = { current: null as string | null };

  assert.equal(claimAutomaticAnalysisAttempt(attemptedTicker, "aapl"), true);
  assert.equal(claimAutomaticAnalysisAttempt(attemptedTicker, "AAPL"), false);
  assert.equal(attemptedTicker.current, "AAPL");

  // A loading-state rerender or a failed request must not silently retry.
  assert.equal(claimAutomaticAnalysisAttempt(attemptedTicker, "AAPL"), false);
  assert.equal(claimAutomaticAnalysisAttempt(attemptedTicker, "MSFT"), true);
  assert.equal(attemptedTicker.current, "MSFT");

  releaseAutomaticAnalysisAttempt(attemptedTicker, "AAPL");
  assert.equal(attemptedTicker.current, "MSFT", "a stale request must not clear the new ticker's attempt");
  releaseAutomaticAnalysisAttempt(attemptedTicker, "MSFT");
  assert.equal(claimAutomaticAnalysisAttempt(attemptedTicker, "AAPL"), true);
});

test("a deferred analysis starts only while its ticker remains in the current URL", () => {
  assert.equal(isAnalysisTickerStillSelected("?ticker=AAPL", "aapl"), true);
  assert.equal(isAnalysisTickerStillSelected("?ticker=MSFT", "AAPL"), false);
  assert.equal(isAnalysisTickerStillSelected("", "AAPL"), false);
});

test("leaving the research URL aborts and invalidates pending analysis and scenario requests", () => {
  const analysisRequest = new AbortController();
  const scenarioRequest = new AbortController();
  const analysisController = { current: analysisRequest as AbortController | null };
  const scenarioController = { current: scenarioRequest as AbortController | null };
  const requestId = { current: 4 };
  const scenarioRequestId = { current: 9 };

  const result = cancelRequestsOnHome(analysisController, scenarioController, requestId, scenarioRequestId);

  assert.deepEqual(result, { cancelAnalysis: true, cancelScenario: true });
  assert.equal(analysisRequest.signal.aborted, true);
  assert.equal(scenarioRequest.signal.aborted, true);
  assert.equal(analysisController.current, null);
  assert.equal(scenarioController.current, null);
  assert.equal(requestId.current, 5);
  assert.equal(scenarioRequestId.current, 10);
});

test("home navigation does not invalidate completed requests", () => {
  const requestId = { current: 4 };
  const scenarioRequestId = { current: 9 };

  const result = cancelRequestsOnHome(
    { current: null },
    { current: null },
    requestId,
    scenarioRequestId,
  );

  assert.deepEqual(result, { cancelAnalysis: false, cancelScenario: false });
  assert.equal(requestId.current, 4);
  assert.equal(scenarioRequestId.current, 9);
});

test("selecting a ticker different from the pending analysis aborts and invalidates it", () => {
  const request = new AbortController();
  const controller = { current: request as AbortController | null };
  const requestId = { current: 12 };

  assert.equal(cancelPendingAnalysisForDifferentTicker(controller, requestId, "SLOW", "fast"), true);
  assert.equal(request.signal.aborted, true);
  assert.equal(controller.current, null);
  assert.equal(requestId.current, 13);
});

test("ticker-change cancellation preserves a same-ticker refresh and ignores missing requests", () => {
  const sameTickerRequest = new AbortController();
  const requestId = { current: 3 };
  assert.equal(cancelPendingAnalysisForDifferentTicker(
    { current: sameTickerRequest }, requestId, "AAPL", "AAPL",
  ), false);
  assert.equal(sameTickerRequest.signal.aborted, false);
  assert.equal(requestId.current, 3);

  assert.equal(cancelPendingAnalysisForDifferentTicker({ current: null }, requestId, "MSFT", "AAPL"), false);
  assert.equal(requestId.current, 3);
});
