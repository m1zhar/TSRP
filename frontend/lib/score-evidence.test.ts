import assert from "node:assert/strict";
import test from "node:test";
import { scoreFactorEvidence } from "./score-evidence.ts";
import type { Company } from "./types.ts";

const company = {
  reporting_currency: "USD",
  historical_growth: 0.12,
  historical_growth_years: 3.5,
  gross_margin: 0.4,
  operating_margin: 0.2,
  fcf_margin: -0.05,
  return_on_equity: 0.18,
  cash: 60_000_000_000,
  debt: 110_000_000_000,
  debt_to_assets: 0.3,
} as Company;

test("score factors map to the observed inputs and exact provenance keys", () => {
  assert.deepEqual(scoreFactorEvidence(company, "growth"), {
    observed: "12.0% · 3.5 years", sourceKeys: ["historical_growth"],
  });
  assert.deepEqual(scoreFactorEvidence(company, "fcf"), {
    observed: "-5.0%", sourceKeys: ["free_cash_flow_margin"],
  });
  assert.deepEqual(scoreFactorEvidence(company, "cash_debt"), {
    observed: "$60B cash · $110B debt", sourceKeys: ["cash", "debt"],
  });
  assert.deepEqual(scoreFactorEvidence(company, "leverage"), {
    observed: "30.0%", sourceKeys: ["debt_to_assets"],
  });
});

test("missing and unknown score-factor inputs remain unavailable", () => {
  const missing = { ...company, gross_margin: null } as Company;
  assert.deepEqual(scoreFactorEvidence(missing, "gross"), {
    observed: "N/A", sourceKeys: ["gross_margin"],
  });
  assert.deepEqual(scoreFactorEvidence(company, "unrecognized"), {
    observed: "N/A", sourceKeys: [],
  });
});
