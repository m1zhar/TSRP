import assert from "node:assert/strict";
import test from "node:test";
import { canConfirmTickerAfterSearchFailure, containsPersonalSearchText, decideCompanySearch } from "./company-search.ts";

test("company search blocks common email and phone or long-number patterns", () => {
  for (const value of [
    "person@example.test",
    "+1 (415) 555-0134",
    "415/555/0134",
    "415‐555‐0134",
    "415‑555‑0134",
    "415–555–0134",
    "user＠example.com",
    "user@example。com",
    "user%40example%2Etest",
    "user%40example%2Etest%FF",
    "user%40example%2Etest%ZZ",
    "user%2540example%252Etest",
    "４１５／５５５／０１３４",
    "%34%31%35%2F%35%35%35%2F%30%31%33%34",
    "415\u200b555\u200b0134",
    "123-45-6789",
    "4111 1111 1111 1111",
    "١٢٣-٤٥-٦٧٨٩",
    "𝟡𝟡𝟡-𝟡𝟡-𝟡𝟡𝟡𝟡",
  ]) assert.equal(containsPersonalSearchText(value), true, value);

  for (const value of ["Apple", "3M", "8x8", "AAPL", "C%2B%2B", "2025 revenue outlook", "2026/10/14"]) {
    assert.equal(containsPersonalSearchText(value), false, value);
  }
});

test("waits for suggestions when a lowercase ticker is submitted before lookup finishes", () => {
  assert.deepEqual(decideCompanySearch("aapl", [], "pending"), { kind: "wait" });
  assert.deepEqual(decideCompanySearch("aapl", [], "loading"), { kind: "wait" });
});

test("selects the exact ticker result when the lookup resolves", () => {
  assert.deepEqual(decideCompanySearch("aapl", [{ ticker: "AAPL" }], "done"), { kind: "select", ticker: "AAPL" });
});

test("offers explicit uppercase ticker fallback when company lookup has no result", () => {
  assert.deepEqual(decideCompanySearch("aapl", [], "failed"), { kind: "confirm-ticker", ticker: "AAPL" });
  assert.deepEqual(decideCompanySearch("aapl", [], "done"), { kind: "confirm-ticker", ticker: "AAPL" });
});

test("uses a company result instead of mistaking a lowercase company name for its uppercase spelling", () => {
  assert.deepEqual(decideCompanySearch("apple", [{ ticker: "AAPL" }], "done"), { kind: "select", ticker: "AAPL" });
});

test("accepts direct uppercase tickers and rejects unresolved company names", () => {
  assert.deepEqual(decideCompanySearch("MSFT", [], "failed"), { kind: "select", ticker: "MSFT" });
  assert.deepEqual(decideCompanySearch("Apple Inc.", [], "failed"), { kind: "invalid" });
  assert.deepEqual(decideCompanySearch("  ", [], "idle"), { kind: "invalid" });
});

test("does not offer a ticker fallback when company search is disabled by deployment configuration", () => {
  assert.equal(canConfirmTickerAfterSearchFailure("provider_disabled"), false);
  assert.equal(canConfirmTickerAfterSearchFailure("rate_limited"), false);
  assert.equal(canConfirmTickerAfterSearchFailure("search_unavailable"), true);
  assert.equal(canConfirmTickerAfterSearchFailure(undefined), true);
});
