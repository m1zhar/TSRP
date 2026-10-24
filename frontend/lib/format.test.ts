import assert from "node:assert/strict";
import test from "node:test";
import { dateLabel, methodologyVersionLabel, money, pct } from "./format.ts";

test("percentage formatting never renders overflow as an infinite percentage", () => {
  assert.equal(pct(1e308), "N/A");
  assert.equal(pct(Number.NaN), "N/A");
  assert.equal(pct(.12), "12.0%");
});

test("money preserves known quote subunits instead of hiding or relabeling prices", () => {
  for (const unit of ["GBp", "GBX", "ZAc", "ILA"]) {
    assert.equal(money(123.45, unit), `123.45 ${unit}`);
  }
});

test("money keeps standard currency formatting and unavailable states", () => {
  assert.equal(money(123.45, "GBP"), new Intl.NumberFormat("en-US", { style: "currency", currency: "GBP", notation: "standard", maximumFractionDigits: 2 }).format(123.45));
  assert.equal(money(null, "USD"), "N/A");
  assert.equal(money(123.45, "GBp "), "N/A");
});

test("compact currency removes trailing zero decimals consistently", () => {
  assert.equal(money(60_000_000_000, "USD", true), "$60B");
  assert.equal(money(1_230, "USD", true), "$1.23K");
});

test("date labels preserve exchange-local calendar dates from the API", () => {
  assert.equal(dateLabel("2026-10-10"), "Oct 10, 2026");
});

test("timestamp labels normalize source offsets to UTC for snapshot comparisons", () => {
  assert.equal(
    dateLabel("2026-10-07T15:30:00+02:00", true),
    dateLabel("2026-10-07T13:30:00Z", true),
  );
});

test("methodology versions use a readable label without exposing internal identifiers", () => {
  assert.equal(methodologyVersionLabel("score-v7"), "Methodology v7");
  assert.equal(methodologyVersionLabel("score-v8"), "Methodology v8");
  assert.equal(methodologyVersionLabel("score-v9"), "Methodology v9");
  assert.equal(methodologyVersionLabel(" score-v3 "), "Methodology v3");
  for (const value of [undefined, "", "score-v0", "score-v3-dev", "internal-name"]) {
    assert.equal(methodologyVersionLabel(value), "TSRP methodology");
  }
});
