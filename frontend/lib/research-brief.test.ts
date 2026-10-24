import assert from "node:assert/strict";
import test from "node:test";
import { analystAvailabilityReason, buildResearchBrief, metricAvailabilityReason, modelGrowthLabel, plainFinancialReason, SCORE_VALIDATION_NOTE, scoreAvailabilityReason, sectorResearchGuide, valuationAvailabilityReason } from "./research-brief.ts";
import type { Company } from "./types.ts";

const c = {
  sector: "Technology", sector_methodology_supported: true, currency_methodology_supported: true,
  score_available: true, score: 70, score_label: "Reasonable support", confidence: "Medium",
  historical_growth: .12, historical_growth_years: 3, operating_margin: .2, return_on_equity: .18,
  cash: 20, debt: 40, fcf_margin: .1, debt_to_assets: .2, reporting_currency: "USD", rate_currency: "USD",
  is_stale: false, solver_status: "exact", required_growth: .16, enterprise_value: 1000,
  model_fcf_margin: .1, model_fcf_refused: false, discount_rate: .1, consensus_growth: .14,
  analyst_estimate_end_date: "2027-06-30", analyst_estimate_currency: "USD",
  score_trace: {
    evidence_coverage: 1, minimum_evidence_coverage: .7,
    subscores: {
      business_quality: { components: [
        { key: "growth", label: "Revenue growth", value: 80, weight: 1/3 },
        { key: "operating", label: "Operating margin", value: 90, weight: 1/3 },
        { key: "roe", label: "Return on average equity", value: 70, weight: 1/3 },
      ] },
      financial_strength: { components: [
        { key: "cash_debt", label: "Cash relative to debt", value: 60, weight: 1/3 },
        { key: "fcf", label: "Free-cash-flow margin", value: 40, weight: 1/3 },
        { key: "leverage", label: "Debt to assets", value: 75, weight: 1/3 },
      ] },
    },
  },
} as unknown as Company;

test("takeaway cites actual server-ranked factors, observed values and provenance without altering score", () => {
  const original = JSON.stringify(c);
  const result = buildResearchBrief(c);
  assert.match(result.conclusion, /Reasonable support/);
  assert.match(result.conclusion, /does not establish whether the shares are cheap/);
  assert.deepEqual(result.supports.map((factor) => factor.key), ["operating", "growth"]);
  assert.equal(result.supports[0].observed, "20.0%");
  assert.deepEqual(result.supports[0].sourceKeys, ["operating_margin"]);
  assert.equal(result.watch?.key, "fcf");
  assert.equal(JSON.stringify(c), original);
});

test("missing score gives coverage and missing inputs, not a negative rating or substitute zero", () => {
  const missing = structuredClone(c);
  missing.score = null;
  missing.score_available = false;
  missing.score_trace!.evidence_coverage = .5;
  missing.score_trace!.subscores!.financial_strength.components[0].value = null;
  assert.match(scoreAvailabilityReason(missing), /50%.*70%/);
  assert.match(scoreAvailabilityReason(missing), /cash relative to debt/);
  const brief = buildResearchBrief(missing);
  assert.equal(brief.supports.length, 0);
  assert.equal(brief.watch, null);
  assert.match(brief.uncertainty, /not a negative rating/);
});

test("withheld server traces still explain specifically missing observed inputs", () => {
  const reason = scoreAvailabilityReason({ ...c, score: null, score_available: false, score_trace: null, fcf_margin: null, cash: null });
  assert.match(reason, /cash relative to debt, free-cash-flow margin/);
  assert.match(reason, /check their sources and periods/);
});

test("non-finite, missing and zero-weight factors never become evidence", () => {
  const bad = structuredClone(c);
  bad.score_trace!.subscores!.business_quality.components[1].value = Number.NaN;
  bad.score_trace!.subscores!.business_quality.components[0].weight = 0;
  const result = buildResearchBrief(bad);
  assert.deepEqual(result.supports.map((factor) => factor.key), ["leverage", "roe"]);
});

test("tied factors are deterministic and not mislabeled as weaknesses", () => {
  const tied = structuredClone(c);
  for (const group of Object.values(tied.score_trace!.subscores!)) for (const factor of group.components) factor.value = 50;
  const first = buildResearchBrief(tied);
  assert.equal(first.watch, null);
  assert.deepEqual(first, buildResearchBrief(tied));
  assert.equal(new Set(first.supports.map((factor) => factor.key)).size, first.supports.length);
});

test("score uncertainty distinguishes stale quotes, partial evidence and low data confidence", () => {
  assert.match(buildResearchBrief({ ...c, is_stale: true }).uncertainty, /operating score uses separately dated/);
  const partial = { ...c, score_trace: { ...c.score_trace!, evidence_coverage: 5/6 } };
  assert.match(buildResearchBrief(partial).uncertainty, /Remaining factors have more influence/);
  assert.match(buildResearchBrief({ ...c, confidence: "Low" }).uncertainty, /Data confidence is low/);
});

test("unsupported sectors remain facts-only and explain banks rather than promising a retry fix", () => {
  const bank = { ...c, sector: "Financial Services", sector_methodology_supported: false, score_available: false, score: null };
  assert.match(sectorResearchGuide(bank), /deposits, regulatory capital/);
  assert.match(sectorResearchGuide(bank), /does not calculate/);
  assert.equal(buildResearchBrief(bank).supports.length, 0);
  assert.match(valuationAvailabilityReason(bank), /retrying will not add/);
  assert.match(scoreAvailabilityReason(bank), /not a judgment that the company is weak/);
  assert.match(sectorResearchGuide({ ...bank, sector: "Unknown sector" }), /No score or valuation framework is configured/);
});

test("every configured sector has a specific caveat without invented company facts", () => {
  for (const sector of ["Technology", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Industrials", "Healthcare", "Energy", "Basic Materials", "Utilities", "Real Estate"]) {
    assert.ok(sectorResearchGuide({ sector, sector_methodology_supported: true }).length > 100, sector);
  }
  assert.match(sectorResearchGuide({ ...c, sector: "Energy" }), /does not normalize commodity prices/);
  assert.match(sectorResearchGuide({ ...c, sector: "Real Estate" }), /does not calculate those measures/);
});

test("valuation unavailable states preserve currency, negative cash flow and provider limitations", () => {
  assert.match(valuationAvailabilityReason({ ...c, reporting_currency: "JPY", currency_methodology_supported: false }), /JPY.*does not substitute a USD/);
  assert.match(valuationAvailabilityReason({ ...c, is_stale: true }), /Refresh once/);
  assert.match(valuationAvailabilityReason({ ...c, enterprise_value: null }), /currency conversion cannot be filled with assumptions/);
  assert.match(valuationAvailabilityReason({ ...c, model_fcf_margin: null, model_fcf_refused: true }), /Negative cash flow is not replaced/);
  assert.match(valuationAvailabilityReason({ ...c, discount_rate: null }), /financing inputs/);
  assert.match(valuationAvailabilityReason(c), /do not interpret N\/A as zero/);
});

test("model ranges are not disguised as exact results or missing values", () => {
  assert.equal(modelGrowthLabel(c), "16.0%");
  assert.equal(modelGrowthLabel({ ...c, solver_status: "above_range", required_growth: null }), "Above range");
  assert.equal(modelGrowthLabel({ ...c, solver_status: "below_range", required_growth: null }), "Below range");
  assert.equal(modelGrowthLabel({ ...c, solver_status: "unavailable", required_growth: null }), "N/A");
});

test("analyst estimates retain fiscal date/currency and never imply missing means zero", () => {
  assert.match(analystAvailabilityReason(c), /2027-06-30 \(USD\)/);
  assert.match(analystAvailabilityReason({ ...c, analyst_estimate_end_date: null, analyst_estimate_currency: null }), /date unavailable.*currency unavailable/);
  assert.match(analystAvailabilityReason({ ...c, consensus_growth: null }), /do not mean zero growth/);
  assert.match(analystAvailabilityReason({ ...c, consensus_growth: 0 }), /Provider estimate/);
});

test("each missing headline metric explains what remains usable", () => {
  for (const key of ["share_price", "market_cap_reporting", "enterprise_value", "revenue"] as const) assert.ok(metricAvailabilityReason(c, key).length > 100);
  assert.match(metricAvailabilityReason({ ...c, sector_methodology_supported: false }, "enterprise_value"), /sector valuation framework/);
});

test("validation status does not claim historical returns or independent review", () => {
  assert.match(SCORE_VALIDATION_NOTE, /no independent validation or historical return backtest/);
});

test("provider input keys become plain labels without altering dates or currencies", () => {
  assert.equal(plainFinancialReason("Missing 2025-12-31 USD inputs: capex, working_capital_change, opening_debt"), "Missing 2025-12-31 USD inputs: capital spending, working-capital cash movement, debt at the previous year-end");
  assert.equal(plainFinancialReason("Depreciation/amortization is unavailable."), "Depreciation/amortization is unavailable.");
});
