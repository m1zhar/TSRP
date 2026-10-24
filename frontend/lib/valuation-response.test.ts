import assert from "node:assert/strict";
import test from "node:test";
import { hasValidValuationLedger } from "./valuation-response.ts";

function ledger(startingMargin = .15, rate = .09, terminalGrowth = .02) {
  let revenue = 100;
  const annualCashFlows = Array.from({ length: 10 }, (_, index) => {
    const year = index + 1;
    revenue *= 1 + terminalGrowth;
    const cashFlowMargin = startingMargin + (.15 - startingMargin) * year / 10;
    const cashFlow = revenue * cashFlowMargin;
    return { year, revenue_growth: terminalGrowth, revenue,
      cash_flow_margin: cashFlowMargin, cash_flow: cashFlow,
      present_value: cashFlow / (1 + rate) ** year };
  });
  const terminalValue = annualCashFlows[9].cash_flow * (1 + terminalGrowth) / (rate - terminalGrowth);
  const terminalPresentValue = terminalValue / (1 + rate) ** 10;
  const enterpriseValue = annualCashFlows.reduce((total, row) => total + row.present_value, 0) + terminalPresentValue;
  const sensitivity = [-.01, 0, .01].flatMap((rateDelta) => [-.005, 0, .005].map((growthDelta) => ({
    discount_rate: rate + rateDelta,
    terminal_growth: terminalGrowth + growthDelta,
    // Non-central values are synthetic positive engine outputs. The boundary
    // validates their shape/grid, not a duplicate engine's valuation formula.
    enterprise_value: rate + rateDelta < .03 || rate + rateDelta > .20 || terminalGrowth + growthDelta < 0
      || rate + rateDelta <= terminalGrowth + growthDelta ? null : enterpriseValue,
  })));
  return {
    solver_status: "exact", revenue: 100, discount_rate: rate, terminal_growth: terminalGrowth,
    enterprise_value: enterpriseValue, assumptions: { fcf_margin: .15 },
    valuation_projection: { enterprise_value: enterpriseValue, annual_cash_flows: annualCashFlows,
      terminal_value: terminalValue, terminal_present_value: terminalPresentValue,
      terminal_value_share: terminalPresentValue / enterpriseValue },
    valuation_sensitivity: sensitivity,
  };
}

test("an auditable ledger reconciles to an independently known perpetuity", () => {
  const company = ledger();
  assert.ok(Math.abs(company.enterprise_value - 100 * .15 * 1.02 / (.09 - .02)) < 1e-10);
  assert.ok(hasValidValuationLedger(company));
});

test("unavailable and out-of-range solves do not claim a forecast ledger", () => {
  for (const status of ["unavailable", "below_range", "above_range"]) {
    assert.ok(hasValidValuationLedger({ solver_status: status, valuation_projection: null, valuation_sensitivity: [] }));
    assert.equal(hasValidValuationLedger({ ...ledger(), solver_status: status }), false);
  }
  assert.equal(hasValidValuationLedger({ ...ledger(), valuation_projection: null, valuation_sensitivity: [] }), false);
  assert.equal(hasValidValuationLedger({ valuation_projection: undefined, valuation_sensitivity: [] }), false);
});

test("every forecast row must have finite, sequential, reconciling values", () => {
  for (const field of ["year", "revenue_growth", "revenue", "cash_flow_margin", "cash_flow", "present_value"] as const) {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 999]) {
      const company = ledger();
      company.valuation_projection.annual_cash_flows[4][field] = bad;
      assert.equal(hasValidValuationLedger(company), false, `${field}: ${bad}`);
    }
  }
  for (const length of [0, 9, 11]) {
    const company = ledger();
    company.valuation_projection.annual_cash_flows.length = length;
    assert.equal(hasValidValuationLedger(company), false);
  }
});

test("totals and terminal value cannot disagree with the ledger or headline", () => {
  for (const field of ["enterprise_value", "terminal_value", "terminal_present_value", "terminal_value_share"] as const) {
    const company = ledger();
    company.valuation_projection[field] += 1;
    assert.equal(hasValidValuationLedger(company), false, field);
  }
  const company = ledger();
  company.enterprise_value += 1;
  assert.equal(hasValidValuationLedger(company), false);
  company.enterprise_value -= 1;
  company.assumptions.fcf_margin = .25;
  assert.equal(hasValidValuationLedger(company), false);
});

test("sensitivity grid must be unique, bounded, complete and include the same baseline value", () => {
  const mutations = [
    (company: ReturnType<typeof ledger>) => { company.valuation_sensitivity.pop(); },
    (company: ReturnType<typeof ledger>) => { company.valuation_sensitivity[0] = company.valuation_sensitivity[1]; },
    (company: ReturnType<typeof ledger>) => { company.valuation_sensitivity[4].enterprise_value = null; },
    (company: ReturnType<typeof ledger>) => { company.valuation_sensitivity[4].enterprise_value = 999; },
    (company: ReturnType<typeof ledger>) => { company.valuation_sensitivity[0].enterprise_value = Number.NaN; },
    (company: ReturnType<typeof ledger>) => { company.valuation_sensitivity[0].discount_rate = .5; },
    (company: ReturnType<typeof ledger>) => { company.valuation_sensitivity[0].terminal_growth = .2; },
  ];
  for (const mutate of mutations) {
    const company = ledger();
    mutate(company);
    assert.equal(hasValidValuationLedger(company), false);
  }
});

test("valid discount-rate boundary cases retain unavailable sensitivity cells", () => {
  for (const rate of [.03, .20]) {
    const company = ledger(.15, rate, 0);
    assert.ok(company.valuation_sensitivity.some((item) => item.enterprise_value === null));
    assert.ok(hasValidValuationLedger(company));
    const invalidCell = company.valuation_sensitivity.find((item) => item.enterprise_value === null)!;
    invalidCell.enterprise_value = 100;
    assert.equal(hasValidValuationLedger(company), false);
  }
});

test("negative early cash flow and a terminal share over 100% are not clipped", () => {
  const company = ledger(-.2);
  assert.ok(company.valuation_projection.annual_cash_flows.some((row) => row.cash_flow < 0));
  assert.ok(company.valuation_projection.terminal_value_share > 1);
  assert.ok(hasValidValuationLedger(company));
});
