/** Validate accounting identities in the engine's ledger, not a second DCF model. */
type RecordValue = Record<string, unknown>;

function record(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function equal(left: number, right: number): boolean {
  return finite(left) && finite(right)
    && Math.abs(left - right) <= 1e-8 * Math.max(1, Math.abs(left), Math.abs(right));
}

export function hasValidValuationLedger(company: RecordValue): boolean {
  const projection = company.valuation_projection;
  const sensitivity = company.valuation_sensitivity;
  if (!Array.isArray(sensitivity)) return false;
  if (projection === null) return sensitivity.length === 0 && company.solver_status !== "exact";
  if (!record(projection) || company.solver_status !== "exact"
    || !finite(company.revenue) || company.revenue <= 0
    || !finite(company.enterprise_value) || company.enterprise_value <= 0
    || !finite(company.discount_rate) || !finite(company.terminal_growth)
    || company.discount_rate <= company.terminal_growth || company.discount_rate <= -1
    || !record(company.assumptions) || !finite(company.assumptions.fcf_margin)
    || !finite(projection.enterprise_value) || projection.enterprise_value <= 0
    || !equal(projection.enterprise_value, company.enterprise_value)
    || !finite(projection.terminal_value) || projection.terminal_value <= 0
    || !finite(projection.terminal_present_value)
    || !finite(projection.terminal_value_share)
    || !Array.isArray(projection.annual_cash_flows)
    || projection.annual_cash_flows.length !== 10 || sensitivity.length !== 9) return false;

  let priorRevenue = company.revenue;
  let presentValue = 0;
  let finalCashFlow = 0;
  for (const [index, row] of projection.annual_cash_flows.entries()) {
    if (!record(row) || row.year !== index + 1
      || !finite(row.revenue_growth) || row.revenue_growth <= -1
      || !finite(row.revenue) || row.revenue <= 0
      || !finite(row.cash_flow_margin) || !finite(row.cash_flow) || !finite(row.present_value)
      || !equal(row.revenue, priorRevenue * (1 + row.revenue_growth))
      || !equal(row.cash_flow, row.revenue * row.cash_flow_margin)
      || !equal(row.present_value, row.cash_flow / (1 + company.discount_rate) ** row.year)) return false;
    // Negative early cash flows and terminal shares above 100% are legitimate
    // in an explicitly assumed turnaround; they must not be clamped away.
    priorRevenue = row.revenue;
    presentValue += row.present_value;
    finalCashFlow = row.cash_flow;
    if (row.year === 10 && !equal(row.cash_flow_margin, company.assumptions.fcf_margin)) return false;
  }
  if (!equal(projection.terminal_value, finalCashFlow * (1 + company.terminal_growth) / (company.discount_rate - company.terminal_growth))
    || !equal(projection.terminal_present_value, projection.terminal_value / (1 + company.discount_rate) ** 10)
    || !equal(projection.enterprise_value, presentValue + projection.terminal_present_value)
    || !equal(projection.terminal_value_share, projection.terminal_present_value / projection.enterprise_value)) return false;

  const keys = new Set<string>();
  let includesBaseline = false;
  for (const item of sensitivity) {
    if (!record(item) || !finite(item.discount_rate) || !finite(item.terminal_growth)
      || !(item.enterprise_value === null || (finite(item.enterprise_value) && item.enterprise_value > 0))) return false;
    if (![-.01, 0, .01].some((delta) => equal(item.discount_rate as number, (company.discount_rate as number) + delta))
      || ![-.005, 0, .005].some((delta) => equal(item.terminal_growth as number, (company.terminal_growth as number) + delta))) return false;
    const key = `${item.discount_rate.toFixed(10)}:${item.terminal_growth.toFixed(10)}`;
    if (keys.has(key)) return false;
    keys.add(key);
    if (equal(item.discount_rate, company.discount_rate) && equal(item.terminal_growth, company.terminal_growth)) {
      if (item.enterprise_value === null || !equal(item.enterprise_value, projection.enterprise_value)) return false;
      includesBaseline = true;
    }
    if ((item.discount_rate <= item.terminal_growth || item.discount_rate < .03
      || item.discount_rate > .20 || item.terminal_growth < 0) && item.enterprise_value !== null) return false;
  }
  return includesBaseline;
}
