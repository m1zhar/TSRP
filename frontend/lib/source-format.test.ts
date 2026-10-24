import assert from "node:assert/strict";
import test from "node:test";
import { currencyConversionLine, fxSourceLine, sourceComponentsLine } from "./source-format.ts";

test("quote subunit conversion is disclosed as an exact scale, not live FX", () => {
  assert.equal(currencyConversionLine({
    from: "GBp", to: "GBP", rate: 0.01, pairs: [], directions: ["exact quote-subunit scale"], period: "Fixed quote-subunit conversion",
  }, "Fixed quote-subunit conversion"), "Exact unit scale GBp → GBP: 0.01 · fixed quote-subunit ratio");
  assert.equal(currencyConversionLine({
    from: "EUR", to: "USD", rate: 1.08, pairs: ["EURUSD=X"], directions: ["pair quote"], period: "2026-10-12T12:00:00Z",
  }, "EURUSD=X as of 2026-10-12T12:00:00Z"), "FX conversion EUR → USD: 1.08 · EURUSD=X as of 2026-10-12T12:00:00Z");
});

test("currency source describes unavailable conversion instead of claiming none was required", () => {
  assert.equal(fxSourceLine([], "Required currency conversion unavailable"), "A required currency conversion is unavailable");
  assert.equal(fxSourceLine([], "No currency conversion used"), "No currency conversion required");
  assert.match(fxSourceLine([
    { from: "GBp", to: "GBP", rate: 0.01, pairs: [], directions: ["exact quote-subunit scale"], period: "Fixed quote-subunit conversion" },
  ], "fixed quote-unit scale; some required conversions unavailable"), /some requested conversions are unavailable/u);
});

test("SEC component provenance shows unit, period, taxonomy, and amendment filing date", () => {
  assert.equal(sourceComponentsLine([
    { value: 100, period: "2025-12-31", source_unit: "USD", filing_date: "2026-03-01", filing_form: "10-K/A", taxonomy: "us-gaap", tag: "DebtCurrent" },
    { value: 200, period: "2025-12-31", source_unit: "USD", filing_date: "2026-02-15", filing_form: "10-K", taxonomy: "us-gaap", tag: "LongTermDebtNoncurrent" },
  ]), " · Components: us-gaap: Debt Current: 100 USD · 2025-12-31 · 10-K/A filed 2026-03-01; us-gaap: Long Term Debt Noncurrent: 200 USD · 2025-12-31 · 10-K filed 2026-02-15");
});

test("Yahoo total-debt provenance preserves current and long-term reported components", () => {
  assert.equal(sourceComponentsLine([
    { value: 50, period: "2025-12-31", source_unit: "USD", filing_date: null, filing_form: null, taxonomy: "Yahoo Finance statement", tag: "Current Debt" },
    { value: 200, period: "2025-12-31", source_unit: "USD", filing_date: null, filing_form: null, taxonomy: "Yahoo Finance statement", tag: "Long Term Debt And Capital Lease Obligation" },
  ]), " · Components: Yahoo Finance statement: Current Debt: 50 USD · 2025-12-31; Yahoo Finance statement: Long Term Debt And Capital Lease Obligation: 200 USD · 2025-12-31");
});

test("missing source components remain an empty detail instead of invented values", () => {
  assert.equal(sourceComponentsLine([]), "");
  assert.equal(sourceComponentsLine([
    { value: null, period: null, source_unit: null, filing_date: null, filing_form: null, taxonomy: null, tag: null },
  ]), " · Components: Reported component: N/A · period unavailable");
});
