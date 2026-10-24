import assert from "node:assert/strict";
import test from "node:test";
import { snapshotChangeStatus, snapshotComparisonReason, snapshotRateDelta, snapshotValueFallback } from "./snapshot-change.ts";

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

test("rate changes use percentage points while score and amount changes remain separate", () => {
  assert.equal(snapshotRateDelta("consensus_growth", .18 - .12), "+6.0 percentage points");
  assert.equal(snapshotRateDelta("user_discount_rate", .08 - .09), "−1.0 percentage points");
  assert.equal(snapshotRateDelta("model_fcf_margin", 0), "0.0 percentage points");
  assert.equal(snapshotRateDelta("consensus_growth", .00001), "Increase <0.1 percentage points");
  assert.equal(snapshotRateDelta("consensus_growth", -.00001), "Decrease <0.1 percentage points");
  assert.equal(snapshotRateDelta("consensus_growth", Number.POSITIVE_INFINITY), "N/A");
  assert.equal(snapshotRateDelta("consensus_growth", 1e308), "N/A");
  assert.equal(snapshotRateDelta("consensus_growth", null), null);
  assert.equal(snapshotRateDelta("score", 6), null);
  assert.equal(snapshotRateDelta("revenue", 6), null);
});

test("reversed snapshot order has an actionable plain-language explanation", () => {
  assert.match(snapshotComparisonReason("snapshot_order_invalid"), /reverse time order.*Refresh/u);
  assert.match(snapshotComparisonReason("snapshot_unverified"), /could not be verified/u);
  assert.match(snapshotComparisonReason(undefined), /missing metadata/u);
  assert.match(snapshotComparisonReason("__proto__"), /missing metadata/u);
});
