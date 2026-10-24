import assert from "node:assert/strict";
import test from "node:test";
import { LatestRequestGate } from "./latest-request.ts";

test("a newer request aborts and invalidates an older in-flight request", () => {
  const gate = new LatestRequestGate();
  const first = gate.begin();
  const second = gate.begin();

  assert.equal(first.signal.aborted, true);
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);
});

test("cancel aborts and invalidates the latest request", () => {
  const gate = new LatestRequestGate();
  const request = gate.begin();

  gate.cancel();

  assert.equal(request.signal.aborted, true);
  assert.equal(gate.isCurrent(request), false);
});
