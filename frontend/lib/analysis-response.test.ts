import assert from "node:assert/strict";
import test from "node:test";
import { assertAnalysisResponse } from "./analysis-response.ts";

function validResponse() {
  const requiredStrings = [
    "ticker", "company", "exchange", "quote_type", "sector", "industry",
    "reporting_currency", "trading_currency", "rate_currency", "solver_status",
    "score_label", "analysis_schema_version", "confidence", "retrieved_at",
    "freshness", "methodology_version",
  ];
  const nullableStrings = [
    "revenue_period", "revenue_source", "fcf_period", "fcf_source", "model_fcf_note",
    "analyst_estimate_period", "revenue_growth_source", "market_at",
  ];
  const finiteNumbers = ["historical_growth_years"];
  const nullableNumbers = [
    "discount_rate", "terminal_growth", "risk_free", "equity_risk_premium", "sector_spread",
    "price", "previous_close", "market_cap", "market_cap_reporting", "enterprise_value",
    "revenue", "free_cash_flow", "fcf_margin", "model_fcf_margin", "historical_growth",
    "historical_growth_3y", "consensus_growth", "target_mean_price", "analyst_count",
    "required_growth", "solver_low", "solver_high", "business_quality", "financial_strength",
    "market_expectations", "score", "gross_margin", "operating_margin", "profit_margin",
    "return_on_equity", "cash", "debt", "assets", "equity", "debt_to_assets", "ev_sales",
    "ev_ebitda", "pe",
  ];
  const booleans = ["sector_methodology_supported", "model_fcf_refused", "growth_clamped", "score_available", "is_stale"];
  const company = Object.fromEntries([
    ...requiredStrings.map((field) => [field, field === "ticker" ? "AAPL" : "Example"]),
    ...nullableStrings.map((field) => [field, null]),
    ...finiteNumbers.map((field) => [field, 0.1]),
    ...nullableNumbers.map((field) => [field, null]),
    ...booleans.map((field) => [field, false]),
  ]);
  Object.assign(company, {
    analysis_schema_version: "analysis-v4",
    snapshot_proof: "signed-snapshot-proof",
    score_trace: null,
    sources: {
      share_price: { source: "Yahoo Finance", period: "2026-10-09", currency: "USD", kind: "market data" },
      fx: {
        source: "Yahoo Finance daily FX history", period: "2026-10-09T00:00:00+00:00",
        currency: "EUR", kind: "timestamped conversion rates",
        conversions: [{
          from: "EUR", to: "CAD", rate: 1.375,
          pairs: ["EURUSD=X", "CADUSD=X"],
          directions: ["pair quote", "pair quote"],
          leg_periods: ["2026-10-08T00:00:00+00:00", "2026-10-09T00:00:00+00:00"],
          period: "2026-10-09T00:00:00+00:00",
        }],
      },
    },
    sec_status: { kind: "unavailable", label: "SEC unavailable" },
    quality_flags: [],
    assumptions: { starting_growth: null, fcf_margin: null, discount_rate: 0.1, terminal_growth: 0.02 },
    user_assumptions: null,
    scenario: null,
  });
  return { company, price_history: [{ date: "2026-10-09", close: 100, volume: null }], scenario_context_token: null, scenario_context_status: "not_configured" };
}

function scoredResponse() {
  const response = validResponse();
  const weights = [7 / 13, 6 / 13];
  const values = [80, 70];
  const keys = ["business_quality", "financial_strength"];
  const labels = ["Business quality", "Financial strength"];
  const components = values.map((value, index) => ({
    key: keys[index],
    label: labels[index],
    value,
    weight: weights[index],
    applied_weight: weights[index],
    evidence_coverage: 1,
    contribution: value * weights[index],
  }));
  const score = components.reduce((total, item) => total + item.contribution, 0);
  Object.assign(response.company, {
    score,
    score_available: true,
    score_trace: {
      score,
      components,
      evidence_coverage: 1,
      minimum_evidence_coverage: 0.7,
      growth_comparison_available: false,
      growth_comparison_inputs: { historical_growth: null, required_growth: null, growth_clamped: false },
    },
  });
  return response;
}

test("well-formed analysis data is accepted for the requested ticker", () => {
  const response = validResponse();
  assert.doesNotThrow(() => assertAnalysisResponse(response, "aapl"));
  assert.equal(response.company.ticker, "AAPL");
});

test("historical growth duration cannot be negative or zero when growth is present", () => {
  const negativeDuration = validResponse();
  negativeDuration.company.historical_growth = 0.12;
  negativeDuration.company.historical_growth_years = -1;
  assert.throws(() => assertAnalysisResponse(negativeDuration, "AAPL"), /Research data could not be validated/u);

  const missingDuration = validResponse();
  missingDuration.company.historical_growth = 0.12;
  missingDuration.company.historical_growth_years = 0;
  assert.throws(() => assertAnalysisResponse(missingDuration, "AAPL"), /Research data could not be validated/u);

  const unavailableGrowth = validResponse();
  unavailableGrowth.company.historical_growth = null;
  unavailableGrowth.company.historical_growth_years = 0;
  assert.doesNotThrow(() => assertAnalysisResponse(unavailableGrowth, "AAPL"));
});

test("score response metadata is shape-checked without recalculating the engine score", () => {
  const response = scoredResponse();
  assert.doesNotThrow(() => assertAnalysisResponse(response, "AAPL"));

  const mismatchedTotal = structuredClone(response);
  mismatchedTotal.company.score += 1;
  assert.throws(() => assertAnalysisResponse(mismatchedTotal, "AAPL"), /Research data could not be validated/u);

  const mismatchedContribution = structuredClone(response);
  mismatchedContribution.company.score_trace.components[0].contribution += 0.1;
  assert.throws(() => assertAnalysisResponse(mismatchedContribution, "AAPL"), /Research data could not be validated/u);

  const mismatchedComponentValue = structuredClone(response);
  mismatchedComponentValue.company.score_trace.components[0].value -= 1;
  assert.throws(() => assertAnalysisResponse(mismatchedComponentValue, "AAPL"), /Research data could not be validated/u);

  const malformedContribution = structuredClone(response);
  malformedContribution.company.score_trace.components[0].contribution = Number.POSITIVE_INFINITY;
  assert.throws(() => assertAnalysisResponse(malformedContribution, "AAPL"), /Research data could not be validated/u);

  const unavailableButScored = structuredClone(response);
  unavailableButScored.company.score_available = false;
  assert.throws(() => assertAnalysisResponse(unavailableButScored, "AAPL"), /Research data could not be validated/u);

  const alteredMethodology = structuredClone(response);
  alteredMethodology.company.score_trace.components[0].weight = 0.6;
  alteredMethodology.company.score_trace.components[0].applied_weight = 0.6;
  alteredMethodology.company.score_trace.components[0].contribution = 48;
  alteredMethodology.company.score_trace.components[1].weight = 0.4;
  alteredMethodology.company.score_trace.components[1].applied_weight = 0.4;
  alteredMethodology.company.score_trace.components[1].contribution = 28;
  alteredMethodology.company.score_trace.score = 76;
  alteredMethodology.company.score = 76;
  assert.doesNotThrow(() => assertAnalysisResponse(alteredMethodology, "AAPL"));

  const newEngineContract = structuredClone(response);
  const newComponents = [
    { key: "quality", label: "Quality", value: 80, weight: 0.4, applied_weight: 0.4, evidence_coverage: 1, contribution: 32 },
    { key: "resilience", label: "Resilience", value: 70, weight: 0.3, applied_weight: 0.3, evidence_coverage: 1, contribution: 21 },
    { key: "execution", label: "Execution", value: 90, weight: 0.2, applied_weight: 0.2, evidence_coverage: 1, contribution: 18 },
    { key: "risk", label: "Risk", value: 50, weight: 0.1, applied_weight: 0.1, evidence_coverage: 1, contribution: 5 },
  ];
  newEngineContract.company.score_trace.components = newComponents;
  newEngineContract.company.score_trace.minimum_evidence_coverage = 0.75;
  newEngineContract.company.score_trace.score = 76;
  newEngineContract.company.score = 76;
  newEngineContract.company.score_trace.subscores = {
    quality: { score: 80, evidence_coverage: 1, components: [] },
    resilience: { score: 70, evidence_coverage: 1, components: [] },
    execution: { score: 90, evidence_coverage: 1, components: [] },
  };
  assert.doesNotThrow(() => assertAnalysisResponse(newEngineContract, "AAPL"));

  const changedEngineEvidence = structuredClone(response);
  const financialComponent = changedEngineEvidence.company.score_trace.components[1];
  const previousContribution = financialComponent.contribution;
  financialComponent.value = 72;
  financialComponent.contribution = 72 * financialComponent.applied_weight;
  changedEngineEvidence.company.score_trace.score += financialComponent.contribution - (previousContribution ?? 0);
  changedEngineEvidence.company.score = changedEngineEvidence.company.score_trace.score;
  assert.doesNotThrow(() => assertAnalysisResponse(changedEngineEvidence, "AAPL"));

  const renamedMethodology = structuredClone(response);
  renamedMethodology.company.score_trace.components[0].key = "quality";
  renamedMethodology.company.score_trace.components[0].label = "Quality";
  renamedMethodology.company.score_trace.components[1].key = "resilience";
  renamedMethodology.company.score_trace.components[1].label = "Resilience";
  assert.doesNotThrow(() => assertAnalysisResponse(renamedMethodology, "AAPL"));

  const duplicateKey = structuredClone(response);
  duplicateKey.company.score_trace.components[1].key = duplicateKey.company.score_trace.components[0].key;
  assert.throws(() => assertAnalysisResponse(duplicateKey, "AAPL"), /Research data could not be validated/u);
});

test("monetary values must use the currency declared by their source provenance", () => {
  const response = validResponse();
  response.company.enterprise_value = 1000;
  response.company.reporting_currency = "EUR";
  response.company.sources.enterprise_value = {
    source: "TSRP calculation", period: "market data and balance sheet", currency: "USD", kind: "calculated",
  };
  assert.throws(() => assertAnalysisResponse(response, "AAPL"), /Research data could not be validated/u);

  response.company.sources.enterprise_value.currency = "EUR";
  assert.doesNotThrow(() => assertAnalysisResponse(response, "AAPL"));

  const quoteSubunitMismatch = validResponse();
  quoteSubunitMismatch.company.price = 100;
  quoteSubunitMismatch.company.trading_currency = "GBp";
  quoteSubunitMismatch.company.sources.share_price.currency = "GBP";
  assert.throws(() => assertAnalysisResponse(quoteSubunitMismatch, "AAPL"), /Research data could not be validated/u);

  quoteSubunitMismatch.company.sources.share_price.currency = "GBp";
  assert.doesNotThrow(() => assertAnalysisResponse(quoteSubunitMismatch, "AAPL"));
});

test("score response rejects the unvalidated valuation sensitivity as a scored comparison", () => {
  const response = scoredResponse();
  response.company.score_trace = {
    ...response.company.score_trace!,
    growth_comparison_available: true,
    growth_comparison_inputs: { historical_growth: 0.1, required_growth: 0.12, growth_clamped: false },
  };
  assert.throws(() => assertAnalysisResponse(response, "AAPL"), /Research data could not be validated/u);
});

test("SEC debt component provenance is accepted and malformed component facts are rejected", () => {
  const response = validResponse();
  response.company.sources.debt = {
    source: "SEC EDGAR", period: "2025-12-31", currency: "USD", kind: "reported",
    components: [
      { value: 100, period: "2025-12-31", source_unit: "USD", filing_date: "2026-03-01", filing_form: "10-K/A", taxonomy: "us-gaap", tag: "DebtCurrent" },
      { value: 200, period: "2025-12-31", source_unit: "USD", filing_date: "2026-02-15", filing_form: "10-K", taxonomy: "us-gaap", tag: "LongTermDebtNoncurrent" },
    ],
  };
  assert.doesNotThrow(() => assertAnalysisResponse(response, "AAPL"));

  response.company.sources.debt.components[0].value = Number.POSITIVE_INFINITY;
  assert.throws(() => assertAnalysisResponse(response, "AAPL"), /Research data could not be validated/u);
});

test("FX provenance retains one timestamp per quote pair and rejects mismatched arrays", () => {
  const response = validResponse();
  assert.doesNotThrow(() => assertAnalysisResponse(response, "AAPL"));

  const missingLegTimestamp = structuredClone(response);
  missingLegTimestamp.company.sources.fx.conversions[0].leg_periods.pop();
  assert.throws(() => assertAnalysisResponse(missingLegTimestamp, "AAPL"), /Research data could not be validated/u);
});

test("unsupported currency responses accept unavailable rates without USD fallback values", () => {
  const response = validResponse();
  Object.assign(response.company, {
    reporting_currency: "XYZ",
    rate_currency: "N/A",
    currency_methodology_supported: false,
    discount_rate: null,
    terminal_growth: null,
    risk_free: null,
    equity_risk_premium: null,
    sector_spread: null,
    assumptions: { starting_growth: null, fcf_margin: null, discount_rate: null, terminal_growth: null },
  });
  assert.doesNotThrow(() => assertAnalysisResponse(response, "AAPL"));
});

test("scenario availability status and signed context must agree", () => {
  const base = validResponse();
  const available = { ...base, scenario_context_status: "available", scenario_context_token: "signed-context" };
  assert.doesNotThrow(() => assertAnalysisResponse(available, "AAPL"));

  const missingToken = { ...available, scenario_context_token: null };
  const contradictoryToken = { ...validResponse(), scenario_context_status: "unavailable", scenario_context_token: "stale-context" };
  assert.throws(() => assertAnalysisResponse(missingToken, "AAPL"), /Research data could not be validated/u);
  assert.throws(() => assertAnalysisResponse(contradictoryToken, "AAPL"), /Research data could not be validated/u);
});

test("a different company identity uses the safe mismatch state", () => {
  assert.throws(
    () => assertAnalysisResponse(validResponse(), "MSFT"),
    /could not be matched to the selected ticker/,
  );
});

test("malformed or unsupported analysis shapes fail safely before UI state updates", () => {
  const malformed = [
    null,
    { ...validResponse(), price_history: {} },
    { ...validResponse(), price_history: [{ date: "2026-10-09", close: Number.NaN, volume: 1 }] },
    { ...validResponse(), price_history: [{ date: "2026-10-09", close: -1, volume: 1 }] },
    { ...validResponse(), price_history: [{ date: "2026-10-09", close: 100, volume: -1 }] },
    { ...validResponse(), company: { ...validResponse().company, price: -1 } },
    { ...validResponse(), company: { ...validResponse().company, previous_close: 0 } },
    { ...validResponse(), price_history: [{ date: "2026-02-30", close: 100, volume: null }] },
    { ...validResponse(), price_history: [{ date: "2026-10-10", close: 101, volume: null }, { date: "2026-10-09", close: 100, volume: null }] },
    { ...validResponse(), price_history: [{ date: "2026-10-09", close: 100, volume: null }, { date: "2026-10-09", close: 101, volume: null }] },
    { ...validResponse(), company: { ...validResponse().company, quality_flags: "invalid" } },
    { ...validResponse(), company: { ...validResponse().company, sources: { share_price: "Yahoo Finance" } } },
    { ...validResponse(), company: { ...validResponse().company, score_trace: { score: null, components: "invalid" } } },
    { ...validResponse(), company: { ...validResponse().company, score: 101 } },
    { ...validResponse(), company: { ...validResponse().company, score_trace: { score: 100, components: [{ key: "quality", label: "Quality", value: 100, weight: 1.2, applied_weight: 1, evidence_coverage: 1, contribution: 100 }], evidence_coverage: 1, minimum_evidence_coverage: 0.7, growth_comparison_available: true } } },
    { ...validResponse(), company: { ...validResponse().company, assumptions: { starting_growth: null, fcf_margin: null, discount_rate: Number.POSITIVE_INFINITY, terminal_growth: null } } },
    { ...validResponse(), company: { ...validResponse().company, analysis_schema_version: "analysis-v2" } },
  ];
  for (const response of malformed) {
    assert.throws(
      () => assertAnalysisResponse(response, "AAPL"),
      /Research data could not be validated/,
    );
  }
});
