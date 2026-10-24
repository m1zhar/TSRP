import assert from "node:assert/strict";
import test from "node:test";
import { attemptResearchExport, exportFeedbackMessage } from "./export-feedback.ts";

test("successful CSV and JSON exports report only that download was initiated", () => {
  for (const format of ["csv", "json"] as const) {
    let calls = 0;
    const state = attemptResearchExport(format, () => { calls += 1; });
    assert.equal(calls, 1);
    assert.equal(state.status, "started");
    assert.equal(exportFeedbackMessage(state), `${format.toUpperCase()} download started.`);
  }
});

test("export exceptions become a generic retryable message without leaking details", () => {
  const state = attemptResearchExport("csv", () => { throw new Error("private browser path or provider response"); });
  const message = exportFeedbackMessage(state);
  assert.deepEqual(state, { format: "CSV", status: "failed" });
  assert.equal(message, "CSV export could not be prepared. Please try again.");
  assert.doesNotMatch(message, /private browser path|provider response/u);
});
