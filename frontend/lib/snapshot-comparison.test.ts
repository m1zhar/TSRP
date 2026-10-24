import assert from "node:assert/strict";
import test from "node:test";
import { toSnapshotComparisonPayload, toStoredSnapshot } from "./snapshot-comparison.ts";
import type { Company } from "./types.ts";

test("snapshot comparison sends only required fields and stays within the API body limit", () => {
  const company = {
    ticker: "AAPL",
    methodology_version: "score-v9",
    analysis_schema_version: "analysis-v4",
    snapshot_proof: "signed-snapshot-proof",
    sector: "Technology",
    reporting_currency: "USD",
    retrieved_at: "2026-10-07T12:00:00Z",
    score: 70,
    score_trace: {
      score: 70,
      evidence_coverage: 1,
      growth_comparison_inputs: { historical_growth: null, required_growth: null, growth_clamped: false },
      components: [
        { key: "business_quality", label: "Business quality", value: 70, weight: 7 / 13, applied_weight: 7 / 13, evidence_coverage: 1, contribution: 70 * 7 / 13 },
        { key: "financial_strength", label: "Financial strength", value: 68, weight: 6 / 13, applied_weight: 6 / 13, evidence_coverage: 1, contribution: 68 * 6 / 13 },
      ],
      subscores: {
        business_quality: { score: 70, evidence_coverage: 1, components: [
          { key: "growth", label: "Growth", value: 70, weight: 0.25 },
          { key: "gross", label: "Gross", value: 70, weight: 0.20 },
          { key: "operating", label: "Operating", value: 70, weight: 0.15 },
          { key: "fcf", label: "FCF", value: 70, weight: 0.20 },
          { key: "roe", label: "ROE", value: 70, weight: 0.10 },
          { key: "balance", label: "Balance", value: 70, weight: 0.10 },
        ] },
        financial_strength: { score: 68, evidence_coverage: 1, components: [
          { key: "cash_debt", label: "Cash relative to debt", value: 68, weight: 0.30 },
          { key: "operating", label: "Operating", value: 68, weight: 0.20 },
          { key: "fcf", label: "FCF", value: 68, weight: 0.30 },
          { key: "leverage", label: "Leverage", value: 68, weight: 0.20 },
        ] },
      },
    },
    business_quality: 70,
    financial_strength: 68,
    market_expectations: null,
    historical_growth: 0.12,
    consensus_growth: 0.1,
    analyst_estimate_period: "Next-year analyst revenue estimate",
    required_growth: 0.08,
    revenue: 100,
    revenue_period: "2025-12-31",
    revenue_source: "Yahoo Finance",
    free_cash_flow: 20,
    fcf_period: "2025-12-31",
    fcf_source: "Yahoo Finance",
    enterprise_value: 1000,
    model_fcf_margin: 0.12,
    discount_rate: 0.09,
    terminal_growth: 0.02,
    solver_status: "exact",
    user_assumptions: { starting_growth: 0.1, fcf_margin: 0.15, discount_rate: 0.09, terminal_growth: 0.02 },
    sources: {
      revenue: { source: "Yahoo Finance", period: "2025-12-31", currency: "USD", kind: "reported" },
      free_cash_flow: { source: "Yahoo Finance", period: "2025-12-31", currency: "USD", kind: "calculated" },
    },
    company: "Example Corporation",
    price_history: Array.from({ length: 1000 }, (_, index) => ({ date: `2026-${index}`, close: index, volume: index })),
    unrelated_provider_details: "x".repeat(20_000),
  } as unknown as Company;

  const previous = toSnapshotComparisonPayload(company, "2026-10-07T12:01:00Z");
  const current = toSnapshotComparisonPayload(company);
  const request = JSON.stringify({ previous, current });

  assert.equal(previous.saved_at, "2026-10-07T12:01:00Z");
  assert.equal(previous.snapshot_proof, "signed-snapshot-proof");
  assert.equal(previous.analyst_estimate_period, "Next-year analyst revenue estimate");
  assert.equal(previous.sector, "Technology");
  assert.equal(previous.model_fcf_margin, 0.12);
  assert.equal(previous.discount_rate, 0.09);
  assert.equal(previous.terminal_growth, 0.02);
  assert.equal(previous.market_expectations, null);
  assert.deepEqual(previous.user_assumptions, company.user_assumptions);
  assert.equal("saved_at" in current, false);
  assert.equal("company" in previous, false);
  assert.equal("price_history" in previous, false);
  assert.equal("unrelated_provider_details" in previous, false);
  assert.equal("label" in previous.score_trace!.components[0], false);
  assert.deepEqual(previous.score_trace?.subscores, {
    business_quality: { evidence_coverage: 1, components: [
      { key: "growth", value: 70, weight: 0.25 }, { key: "gross", value: 70, weight: 0.20 },
      { key: "operating", value: 70, weight: 0.15 }, { key: "fcf", value: 70, weight: 0.20 },
      { key: "roe", value: 70, weight: 0.10 }, { key: "balance", value: 70, weight: 0.10 },
    ] },
    financial_strength: { evidence_coverage: 1, components: [
      { key: "cash_debt", value: 68, weight: 0.30 }, { key: "operating", value: 68, weight: 0.20 },
      { key: "fcf", value: 68, weight: 0.30 }, { key: "leverage", value: 68, weight: 0.20 },
    ] },
  });
  assert.deepEqual(previous.score_trace?.components.map(({ key }) => key), [
    "business_quality", "financial_strength",
  ]);
  assert.deepEqual(previous.score_trace?.growth_comparison_inputs, {
    historical_growth: null, required_growth: null, growth_clamped: false,
  });
  assert.deepEqual(Object.keys(previous.sources.revenue!), ["source", "period", "currency", "kind"]);
  assert.ok(new TextEncoder().encode(request).byteLength < 16_384);
});

test("older or malformed snapshot fields serialize as an incompatible payload instead of crashing", () => {
  const legacy = {
    ticker: "AAPL",
    sources: "invalid legacy value",
    score_trace: "invalid legacy value",
    methodology_version: undefined,
    analysis_schema_version: undefined,
  } as unknown as Company;
  const payload = toSnapshotComparisonPayload(legacy);

  assert.equal(payload.ticker, "AAPL");
  assert.equal(payload.score_trace, null);
  assert.equal(payload.sources.revenue, null);
  assert.equal(payload.sources.free_cash_flow, null);
  assert.equal("methodology_version" in payload, true);
  assert.equal(payload.methodology_version, undefined);

  const partialScore = toSnapshotComparisonPayload({
    ticker: "AAPL",
    score_trace: { score: 60, evidence_coverage: 0.7, growth_comparison_inputs: { historical_growth: null, required_growth: null, growth_clamped: false }, components: [{ key: "missing", value: null, evidence_coverage: 0 }] },
  } as unknown as Company);
  assert.equal(partialScore.score_trace?.components[0]?.value, null);

  const malformedComponents = toSnapshotComparisonPayload({
    ticker: "AAPL",
    score_trace: { score: 60, evidence_coverage: 0.7, components: [null] },
  } as unknown as Company);
  assert.equal(malformedComponents.score_trace, null);
});

test("new browser snapshots persist only the comparable research fields", () => {
  const company = {
    ticker: "AAPL",
    saved_at: "2026-10-07T12:01:00Z",
    methodology_version: "score-v9",
    analysis_schema_version: "analysis-v4",
    snapshot_proof: "signed-snapshot-proof",
    score: 70,
    score_trace: {
      score: 70,
      evidence_coverage: 1,
      growth_comparison_inputs: { historical_growth: null, required_growth: null, growth_clamped: false },
      components: [],
    },
    sources: { revenue: { source: "Yahoo Finance", period: "2025-12-31", currency: "USD", kind: "reported", components: [{ value: 100, tag: "TotalRevenue" }] } },
    user_assumptions: { starting_growth: 0.08, fcf_margin: 0.12, discount_rate: 0.09, terminal_growth: 0.02 },
    company: "Example Corporation",
    price: 120,
    price_history: Array.from({ length: 1000 }, (_, index) => ({ date: `2026-${index}`, close: index, volume: index })),
    unrelated_provider_details: "x".repeat(20_000),
  } as unknown as Company & { saved_at: string };

  const stored = toStoredSnapshot(company);

  assert.equal(stored.saved_at, company.saved_at);
  assert.equal(stored.snapshot_proof, company.snapshot_proof);
  assert.equal(stored.score, company.score);
  assert.deepEqual(stored.user_assumptions, company.user_assumptions);
  assert.deepEqual(stored.sources.revenue, { source: "Yahoo Finance", period: "2025-12-31", currency: "USD", kind: "reported" });
  assert.equal("company" in stored, false);
  assert.equal("price" in stored, false);
  assert.equal("price_history" in stored, false);
  assert.equal("unrelated_provider_details" in stored, false);
  assert.deepEqual(toSnapshotComparisonPayload(stored, stored.saved_at), toSnapshotComparisonPayload(company, company.saved_at));
  assert.ok(new TextEncoder().encode(JSON.stringify(stored)).byteLength < 16_384);
});
