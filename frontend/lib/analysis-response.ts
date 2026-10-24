import type { Analysis, Assumptions, Company } from "./types.ts";
import { hasValidValuationLedger } from "./valuation-response.ts";

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isCalendarDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/u.test(value)) return false;
  const timestamp = Date.parse(`${value}T00:00:00.000Z`);
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value;
}

function isPriceHistory(value: unknown): boolean {
  if (!Array.isArray(value)) return false;
  let previousDate = "";
  for (const point of value) {
    if (!isRecord(point)
      || !isCalendarDate(point.date)
      || (previousDate !== "" && point.date <= previousDate)
      || !isFiniteNumber(point.close)
      || point.close <= 0
      || !isNullableNumber(point.volume)
      || (point.volume !== null && point.volume < 0)) return false;
    previousDate = point.date;
  }
  return true;
}

function isInRange(value: unknown, low: number, high: number): value is number {
  return isFiniteNumber(value) && value >= low && value <= high;
}

function numbersMatch(left: number, right: number): boolean {
  return Math.abs(left - right) <= 1e-8 * Math.max(1, Math.abs(left), Math.abs(right));
}

function matchesFields(
  record: UnknownRecord,
  fields: readonly string[],
  predicate: (value: unknown) => boolean,
): boolean {
  return fields.every((field) => predicate(record[field]));
}

function isAssumptions(value: unknown): value is Assumptions {
  return isRecord(value) && matchesFields(
    value,
    ["starting_growth", "fcf_margin", "discount_rate", "terminal_growth"],
    isNullableNumber,
  );
}

function isConversion(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return typeof value.from === "string"
    && typeof value.to === "string"
    && isNullableNumber(value.rate)
    && Array.isArray(value.pairs) && value.pairs.every((pair) => typeof pair === "string")
    && Array.isArray(value.directions) && value.directions.every((direction) => typeof direction === "string")
    && (value.leg_periods === undefined || (Array.isArray(value.leg_periods)
      && value.leg_periods.length === value.pairs.length
      && value.leg_periods.every((period) => typeof period === "string")))
    && isNullableString(value.period);
}

function isSourceFact(value: unknown): boolean {
  return isRecord(value)
    && isNullableNumber(value.value)
    && isNullableString(value.period)
    && isNullableString(value.source_unit)
    && isNullableString(value.filing_date)
    && isNullableString(value.filing_form)
    && isNullableString(value.taxonomy)
    && isNullableString(value.tag);
}

function isSource(value: unknown): boolean {
  if (!isRecord(value) || typeof value.kind !== "string") return false;
  if ("source" in value) {
    return typeof value.source === "string"
      && typeof value.period === "string"
      && typeof value.currency === "string"
      && (value.conversions === undefined || (Array.isArray(value.conversions) && value.conversions.every(isConversion)))
      && (value.components === undefined || (Array.isArray(value.components) && value.components.every(isSourceFact)));
  }
  return typeof value.label === "string"
    && (value.company === undefined || typeof value.company === "string")
    && (value.cik === undefined || typeof value.cik === "string");
}

function isScoreComponent(value: unknown): boolean {
  return isRecord(value)
    && typeof value.key === "string"
    && typeof value.label === "string"
    && (value.value === null || isInRange(value.value, 0, 100))
    && isInRange(value.weight, 0, 1)
    && isInRange(value.applied_weight, 0, 1)
    && isInRange(value.evidence_coverage, 0, 1)
    && (value.contribution === null || isInRange(value.contribution, 0, 100));
}

function isScoreFactor(value: unknown): boolean {
  return isRecord(value)
    && typeof value.key === "string"
    && typeof value.label === "string"
    && (value.value === null || isInRange(value.value, 0, 100))
    && isInRange(value.weight, 0, 1);
}

function isScoreTrace(value: unknown): boolean {
  const growthInputs = isRecord(value) ? value.growth_comparison_inputs : null;
  if (!isRecord(value)
    || !isInRange(value.score, 0, 100)
    || !isInRange(value.evidence_coverage, 0, 1)
    || !isInRange(value.minimum_evidence_coverage, 0, 1)
    || typeof value.growth_comparison_available !== "boolean"
    || !isRecord(growthInputs)
    || !(growthInputs.historical_growth === null || isFiniteNumber(growthInputs.historical_growth))
    || !(growthInputs.required_growth === null || isFiniteNumber(growthInputs.required_growth))
    || typeof growthInputs.growth_clamped !== "boolean"
    || value.growth_comparison_available !== false
    || growthInputs.historical_growth !== null
    || growthInputs.required_growth !== null
    || growthInputs.growth_clamped
    || !Array.isArray(value.components)
    || value.components.length === 0
    || value.components.length > 20
    || !value.components.every(isScoreComponent)) return false;

  const keys = new Set<string>();
  for (const component of value.components) {
    if (keys.has(component.key)) return false;
    keys.add(component.key);
  }

  let contributionTotal = 0;
  let appliedWeightTotal = 0;
  let includedComponents = 0;
  for (const component of value.components) {
    if (component.value === null) {
      if (component.applied_weight !== 0 || component.contribution !== null) return false;
      continue;
    }
    if (component.contribution === null
      || !numbersMatch(component.contribution, component.value * component.applied_weight)) return false;
    contributionTotal += component.contribution;
    appliedWeightTotal += component.applied_weight;
    includedComponents += 1;
  }
  if (includedComponents === 0
    || !numbersMatch(appliedWeightTotal, 1)
    || !numbersMatch(contributionTotal, value.score)) return false;

  const sensitivity = value.weight_sensitivity;
  if (sensitivity !== undefined && sensitivity !== null && (!isRecord(sensitivity)
    || !isInRange(sensitivity.low, 0, value.score)
    || !isInRange(sensitivity.high, value.score, 100)
    || !isInRange(sensitivity.weight_variation, 0, 1)
    || typeof sensitivity.kind !== "string")) return false;

  if (value.subscores === undefined) return true;
  if (!isRecord(value.subscores)
    || Object.keys(value.subscores).length === 0
    || Object.keys(value.subscores).length > 20) return false;
  return Object.values(value.subscores).every((subscore) => isRecord(subscore)
    && (subscore.score === null || isInRange(subscore.score, 0, 100))
    && isInRange(subscore.evidence_coverage, 0, 1)
    && Array.isArray(subscore.components)
    && subscore.components.every(isScoreFactor));
}

function isValuationBasis(value: unknown): boolean {
  if (!isRecord(value) || !isRecord(value.cash_flow) || !isRecord(value.capital_cost)) return false;
  for (const bridge of [value.cash_flow, value.capital_cost]) {
    if (!isNullableNumber(bridge.value) || !isNullableNumber(bridge.tax_rate)
      || !isNullableString(bridge.period) || typeof bridge.currency !== "string"
      || typeof bridge.source !== "string" || typeof bridge.reason !== "string"
      || !isRecord(bridge.inputs) || !Object.values(bridge.inputs).every(isNullableNumber)) return false;
  }
  const cost = value.capital_cost;
  const reference = cost.reference;
  return matchesFields(cost, ["cost_of_equity", "cost_of_debt", "equity_weight", "debt_weight", "beta"], isNullableNumber)
    && isRecord(reference) && isCalendarDate(reference.date)
    && matchesFields(reference, ["currency", "source", "url"], (field) => typeof field === "string")
    && isFiniteNumber(reference.risk_free) && isFiniteNumber(reference.equity_risk_premium);
}

function hasConsistentMonetaryCurrencies(company: UnknownRecord): boolean {
  const sources = company.sources;
  if (!isRecord(sources)) return false;
  const mappings = [
    { value: "price", source: "share_price", currency: "trading_currency" },
    { value: "previous_close", source: "share_price", currency: "trading_currency" },
    { value: "market_cap_reporting", source: "market_cap_reporting", currency: "reporting_currency" },
    { value: "enterprise_value", source: "enterprise_value", currency: "reporting_currency" },
    { value: "revenue", source: "revenue", currency: "reporting_currency" },
    { value: "free_cash_flow", source: "free_cash_flow", currency: "reporting_currency" },
    { value: "unlevered_cash_flow", source: "unlevered_cash_flow", currency: "reporting_currency" },
    { value: "cash", source: "cash", currency: "reporting_currency" },
    { value: "debt", source: "debt", currency: "reporting_currency" },
    { value: "equity", source: "equity", currency: "reporting_currency" },
  ] as const;

  return mappings.every(({ value, source, currency }) => {
    if (company[value] === null) return true;
    const provenance = sources[source];
    return isRecord(provenance)
      && "source" in provenance
      && typeof provenance.currency === "string"
      && typeof company[currency] === "string"
      && provenance.currency.trim() === company[currency].trim();
  });
}

function isScenario(value: unknown): boolean {
  return isRecord(value)
    && isNullableNumber(value.enterprise_value)
    && isNullableNumber(value.equity_value)
    && isAssumptions(value.inputs)
    && typeof value.kind === "string";
}

function isCompany(value: unknown): value is Company {
  if (!isRecord(value)) return false;

  const requiredStrings = [
    "ticker", "company", "exchange", "quote_type", "sector", "industry",
    "reporting_currency", "trading_currency", "rate_currency", "solver_status",
    "score_label", "analysis_schema_version", "confidence", "retrieved_at",
    "freshness", "methodology_version",
  ] as const;
  const nullableStrings = [
    "revenue_period", "revenue_source", "fcf_period", "fcf_source", "model_fcf_note",
    "analyst_estimate_period", "revenue_growth_source", "market_at", "snapshot_proof",
  ] as const;
  const finiteNumbers = ["historical_growth_years"] as const;
  const nullableNumbers = [
    "discount_rate", "terminal_growth", "risk_free", "equity_risk_premium", "sector_spread",
    "price", "previous_close", "market_cap", "market_cap_reporting", "enterprise_value",
    "revenue", "free_cash_flow", "fcf_margin", "model_fcf_margin", "historical_growth",
    "historical_growth_3y", "consensus_growth", "target_mean_price", "analyst_count",
    "required_growth", "solver_low", "solver_high", "business_quality", "financial_strength",
    "market_expectations", "score", "gross_margin", "operating_margin", "profit_margin",
    "return_on_equity", "cash", "debt", "assets", "equity", "debt_to_assets", "ev_sales",
    "ev_ebitda", "pe",
  ] as const;
  const booleans = ["sector_methodology_supported", "model_fcf_refused", "growth_clamped", "score_available", "is_stale"] as const;
  const historicalGrowthYears = value.historical_growth_years;
  const historicalGrowth = value.historical_growth;

  if (!matchesFields(value, requiredStrings, (field) => typeof field === "string")
    || !matchesFields(value, nullableStrings, isNullableString)
    || !matchesFields(value, finiteNumbers, isFiniteNumber)
    || !matchesFields(value, nullableNumbers, isNullableNumber)
    || !matchesFields(value, ["price", "previous_close"], (field) => field === null || (isFiniteNumber(field) && field > 0))
    || !isFiniteNumber(historicalGrowthYears)
    || historicalGrowthYears < 0
    || (historicalGrowth !== null && (!isNullableNumber(historicalGrowth) || historicalGrowthYears <= 0))
    || !matchesFields(value, booleans, (field) => typeof field === "boolean")
    || (value.currency_methodology_supported !== undefined && typeof value.currency_methodology_supported !== "boolean")) return false;

  if (!hasConsistentMonetaryCurrencies(value)) return false;
  if ((value.analyst_estimate_end_date !== undefined && value.analyst_estimate_end_date !== null && !isCalendarDate(value.analyst_estimate_end_date))
    || (value.analyst_estimate_currency !== undefined && value.analyst_estimate_currency !== null
      && (typeof value.analyst_estimate_currency !== "string" || !/^[A-Z]{3}$/u.test(value.analyst_estimate_currency) || value.analyst_estimate_currency === "XXX"))) return false;

  const score = value.score;
  if (value.analysis_schema_version !== "analysis-v5"
    || !isNullableNumber(value.unlevered_cash_flow)
    || !isNullableNumber(value.unlevered_fcf_margin)
    || !isValuationBasis(value.valuation_basis)
    || !hasValidValuationLedger(value)
    || !isFiniteNumber(value.other_claims) || value.other_claims < 0
    || (score !== null && !isInRange(score, 0, 100))
    || !matchesFields(value, ["business_quality", "financial_strength", "market_expectations"], (field) => field === null || isInRange(field, 0, 100))
    || !isRecord(value.sources)
    || !Object.values(value.sources).every(isSource)
    || !isRecord(value.sec_status)
    || typeof value.sec_status.kind !== "string"
    || typeof value.sec_status.label !== "string"
    || !Array.isArray(value.quality_flags)
    || !value.quality_flags.every((flag) => isRecord(flag)
      && typeof flag.message === "string"
      && typeof flag.severity === "string")
    || !(value.score_trace === null || isScoreTrace(value.score_trace))
    || !isAssumptions(value.assumptions)
    || !(value.user_assumptions === null || isAssumptions(value.user_assumptions))
    || !(value.scenario === null || isScenario(value.scenario))) return false;

  const basis = value.valuation_basis as UnknownRecord;
  const operating = basis.cash_flow as UnknownRecord;
  if (operating.value !== value.unlevered_cash_flow
    || (operating.value !== null && (operating.currency !== value.reporting_currency || operating.period !== value.revenue_period))) return false;

  const trace = value.score_trace;
  if (value.score_available !== (score !== null && trace !== null)
    || (score !== null && (!isRecord(trace)
      || !isInRange(trace.score, 0, 100)
      || !numbersMatch(score, trace.score)))) return false;

  return true;
}

export function assertAnalysisResponse(value: unknown, expectedTicker: string): asserts value is Analysis {
  if (!isRecord(value) || !isCompany(value.company)
    || !isPriceHistory(value.price_history)
    || !(value.scenario_context_token === null || typeof value.scenario_context_token === "string")
    || (value.scenario_context_status !== undefined && !["available", "not_configured", "unsupported", "unavailable"].includes(String(value.scenario_context_status)))
    || (value.scenario_context_status === "available" && typeof value.scenario_context_token !== "string")
    || (value.scenario_context_status !== undefined && value.scenario_context_status !== "available" && value.scenario_context_token !== null)) {
    throw new Error("Research data could not be validated. Refresh and try again.");
  }

  const expected = expectedTicker.trim().toUpperCase();
  if (!expected || value.company.ticker !== expected) {
    throw new Error("Research data could not be matched to the selected ticker. Refresh and try again.");
  }
}
