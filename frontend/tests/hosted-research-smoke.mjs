// Explicit opt-in live check. Run separately from deterministic/offline tests.
import assert from "node:assert/strict";
import { requestJson } from "../lib/api-client.ts";
import { assertAnalysisResponse } from "../lib/analysis-response.ts";

const origin = new URL(process.env.TSRP_CHECK_ORIGIN || "https://thesrp.vercel.app");
assert.equal(origin.protocol, "https:", "Hosted verification requires HTTPS.");
assert.equal(origin.username + origin.password + origin.search + origin.hash, "", "Use a plain site origin without credentials or query parameters.");
assert.equal(origin.pathname, "/", "Use a site origin, not an API path.");
const cases = [
  ["MSFT", "Technology"], ["GOOGL", "Communication Services"],
  ["AMZN", "Consumer Cyclical"], ["PG", "Consumer Defensive"],
  ["CAT", "Industrials"], ["JNJ", "Healthcare"], ["XOM", "Energy"],
  ["LIN", "Basic Materials"], ["NEE", "Utilities"], ["PLD", "Real Estate"],
  ["JPM", "Financial Services"], ["AZN.L", "Healthcare"], ["TM", "Consumer Cyclical"],
];
const fetcher = (path, init = {}) => fetch(new URL(path, origin), {
  ...init, redirect: "error",
  headers: { ...init.headers, Origin: origin.origin, Referer: `${origin.origin}/` },
});
let passed = 0;
for (const [ticker, expectedSector] of cases) {
  const start = performance.now();
  try {
    const analysis = await requestJson(`/api/analyze?ticker=${encodeURIComponent(ticker)}`, {}, 35_000, fetcher);
    assertAnalysisResponse(analysis, ticker);
    const c = analysis.company;
    assert.equal(c.ticker, ticker);
    assert.equal(c.sector, expectedSector);
    assert.equal(c.methodology_version, "score-v10", "Hosted source is not the reviewed financial methodology.");
    if (expectedSector === "Financial Services") {
      assert.equal(c.sector_methodology_supported, false);
      assert.equal(c.score, null);
      assert.equal(c.required_growth, null);
    }
    if (c.score_available) assert.ok(c.score !== null && c.score >= 0 && c.score <= 100);
    if (c.solver_status === "exact") assert.ok(Number.isFinite(c.required_growth));
    assert.ok(c.retrieved_at && c.reporting_currency);
    assert.ok(c.quality_flags.every((flag) => typeof flag.message === "string"));
    passed += 1;
    console.log(JSON.stringify({ ticker, result: "PASS", sector: c.sector, score: c.score, confidence: c.confidence, valuation: c.solver_status, reporting_currency: c.reporting_currency, market_at: c.market_at, seconds: Number(((performance.now() - start) / 1000).toFixed(2)) }));
  } catch (error) {
    // No response bodies, request tokens, environment values or stacks in logs.
    console.error(JSON.stringify({ ticker, result: "FAIL", message: error instanceof Error ? error.message : "Unknown failure" }));
  }
}
console.log(`Hosted analysis checks: ${passed}/${cases.length}. Unavailable valuations are allowed only when the validated response discloses their state; this is not proof of data accuracy or investment usefulness.`);
process.exitCode = passed === cases.length ? 0 : 1;
