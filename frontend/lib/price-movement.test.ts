import assert from "node:assert/strict";
import test from "node:test";
import { priceMovementDescription } from "./price-movement.ts";

test("price movement separates gains, losses and unchanged observations", () => {
  assert.equal(priceMovementDescription(100, 110, 2), "Increased 10.0% over this displayed period");
  assert.equal(priceMovementDescription(100, 90, 2), "Decreased 10.0% over this displayed period");
  assert.equal(priceMovementDescription(100, 100, 2), "Unchanged (0.0%) over this displayed period");
});

test("small real changes are not displayed as a zero gain or loss", () => {
  assert.equal(priceMovementDescription(100, 100.001, 2), "Increased less than 0.1% over this displayed period");
  assert.equal(priceMovementDescription(100, 99.999, 2), "Decreased less than 0.1% over this displayed period");
});

test("single, absent, invalid or zero-baseline observations have no fabricated return", () => {
  for (const [first, last, count] of [[100, 100, 1], [undefined, 100, 0], [0, 100, 2], [-10, 100, 2], [100, Infinity, 2], [NaN, 100, 2]] as const) assert.equal(priceMovementDescription(first, last, count), "Change unavailable");
  assert.match(priceMovementDescription(1e-300, 1e300, 2), /exceeds the supported numeric range/);
});
