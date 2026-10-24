import assert from "node:assert/strict";
import test from "node:test";
import { snapshotChangeStatus, snapshotValueFallback } from "./snapshot-change.ts";

test("snapshot changes identify added and removed values instead of implying a numeric update", () => {
  assert.equal(snapshotChangeStatus({ before: null, after: 0.12, delta: null }), "Added");
  assert.equal(snapshotChangeStatus({ before: 0.12, after: null, delta: null }), "Removed");
  assert.equal(snapshotChangeStatus({ before: 0.12, after: 0.15, delta: 0.03 }), null);
  assert.equal(snapshotChangeStatus({ before: { a: 1 }, after: { a: 2 }, delta: null }), "Updated");
});

test("missing scenario assumptions are labeled as unsaved, not as unavailable source data", () => {
  assert.equal(snapshotValueFallback("user_starting_growth", null), "Not saved");
  assert.equal(snapshotValueFallback("user_discount_rate", undefined), "Not saved");
  assert.equal(snapshotValueFallback("revenue", null), "N/A");
  assert.equal(snapshotValueFallback("user_fcf_margin", 0.15), null);
});
