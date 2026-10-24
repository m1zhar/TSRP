import assert from "node:assert/strict";
import test from "node:test";
import { formatScoreWeight } from "./score-weight-format.ts";

test("normalized score weights are rendered as percentages", () => {
  assert.equal(formatScoreWeight(7 / 13), "53.8%");
  assert.equal(formatScoreWeight(6 / 13), "46.2%");
});

test("zero and non-finite score weights remain explicit", () => {
  assert.equal(formatScoreWeight(0), "0.0%");
  assert.equal(formatScoreWeight(Number.NaN), "N/A");
  assert.equal(formatScoreWeight(Number.POSITIVE_INFINITY), "N/A");
});
