import assert from "node:assert/strict";
import test from "node:test";
import { pricePointsInRange } from "./price-chart-range.ts";

const point = (date: string, close = 100) => ({ date, close, volume: null });

test("does not pull an older quote into a selected range containing one observation", () => {
  const history = [point("2026-06-01"), point("2026-10-13", 120)];
  assert.deepEqual(pricePointsInRange(history, 31), [point("2026-10-13", 120)]);
});

test("includes observations on the cutoff and latest dates only", () => {
  const history = [point("2026-09-11"), point("2026-09-12"), point("2026-10-13", 120)];
  assert.deepEqual(pricePointsInRange(history, 31), history.slice(1));
});

test("empty history and invalid range lengths return no points", () => {
  const history = [point("2026-10-13")];
  assert.deepEqual(pricePointsInRange([], 31), []);
  assert.deepEqual(pricePointsInRange(history, 0), []);
  assert.deepEqual(pricePointsInRange(history, Number.NaN), []);
  assert.deepEqual(pricePointsInRange(history, 1.5), []);
});
