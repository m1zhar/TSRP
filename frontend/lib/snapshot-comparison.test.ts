import assert from "node:assert/strict";
import test from "node:test";
import { toSnapshotComparisonPayload } from "./snapshot-comparison.ts";
import type { Company } from "./types.ts";

test("snapshot comparison sends only required fields and stays within the API body limit", () => {
  const company = {
    ticker: "AAPL",
    methodology_version: "score-v3",
    analysis_schema_version: "analysis-v2",
    reporting_currency: "USD",
    retrieved_at: "2026-10-07T12:00:00Z",
    score: 72,
    score_trace: {
      score: 72,
      evidence_coverage: 0.8,
      components: [{ key: "quality", label: "Quality", value: 70, weight: 0.35, applied_weight: 0.35, evidence_coverage: 1, contribution: 24.5 }],
      subscores: { business_quality: { score: 70, evidence_coverage: 1, components: [{ key: "growth", label: "Growth", value: 70, weight: 0.2 }] } },
    },
    business_quality: 70,
    financial_strength: 68,
    historical_growth: 0.12,
    consensus_growth: 0.1,
    required_growth: 0.08,
    revenue: 100,
    revenue_period: "2025-12-31",
    revenue_source: "Yahoo Finance",
    free_cash_flow: 20,
    fcf_period: "2025-12-31",
    fcf_source: "Yahoo Finance",
    enterprise_value: 1000,
    solver_status: "exact",
    user_assumptions: null,
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
  assert.equal("saved_at" in current, false);
  assert.equal("company" in previous, false);
  assert.equal("price_history" in previous, false);
  assert.equal("unrelated_provider_details" in previous, false);
  assert.equal("label" in previous.score_trace!.components[0], false);
  assert.deepEqual(Object.keys(previous.sources.revenue!), ["source", "period", "currency", "kind"]);
  assert.ok(new TextEncoder().encode(request).byteLength < 16_384);
});
