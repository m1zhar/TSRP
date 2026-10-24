import { toCsv } from "./csv.ts";
import { freeCashFlowSourceLabel } from "./fcf-evidence.ts";
import { analystEstimateLabel } from "./research-signals.ts";
import { buildResearchBrief, SCORE_VALIDATION_NOTE, sectorResearchGuide } from "./research-brief.ts";
import type { Company } from "./types.ts";

type SourceLineKey = "share_price" | "analyst_growth" | "market_cap_reporting" | "enterprise_value" | "valuation_assumptions" | "debt";
type Scenario = NonNullable<Company["scenario"]> | null;

/** Pure export builder: use the same analysis object rendered by the UI. */
export function buildResearchExport(
  company: Company,
  scenario: Scenario,
  disclaimer: string,
  sourceLine: (key: SourceLineKey) => string,
) {
  const currencyMethodologySupported = company.currency_methodology_supported ?? (company.rate_currency !== "N/A" && company.rate_currency === company.reporting_currency);
  const valuationMethodologySupported = company.sector_methodology_supported && currencyMethodologySupported;
  const scoreAvailable = company.score_available && company.sector_methodology_supported;
  const score = scoreAvailable ? company.score : null;
  const evSource = company.sources.enterprise_value;
  const enterpriseValueSupported = company.sector_methodology_supported && (currencyMethodologySupported
    || (evSource && "source" in evSource && evSource.currency === company.reporting_currency));
  const enterpriseValue = enterpriseValueSupported ? company.enterprise_value : null;
  const enterpriseValueSource = enterpriseValueSupported
    ? company.sources.enterprise_value
    : { source: "Unavailable", period: "N/A", currency: "N/A", kind: "No validated TSRP sector and currency calibration for valuation" };
  const effectiveScenario = valuationMethodologySupported ? scenario : null;
  const unavailableValuationSource = {
    source: "Unavailable", period: "Unavailable", currency: "N/A",
    kind: !company.sector_methodology_supported
      ? `No validated TSRP sector calibration for ${company.sector}; valuation assumptions are withheld`
      : `No validated TSRP calibration for ${company.reporting_currency}; USD rates are not substituted`,
  };
  const sources = valuationMethodologySupported ? company.sources : {
    ...company.sources,
    enterprise_value: enterpriseValueSource,
    valuation_assumptions: unavailableValuationSource,
  };
  const enterpriseValuePeriod = enterpriseValueSource && "period" in enterpriseValueSource ? enterpriseValueSource.period : "N/A";
  const historicalGrowthSource = company.sources.historical_growth;
  const historicalGrowthPeriod = historicalGrowthSource && "period" in historicalGrowthSource ? historicalGrowthSource.period : "N/A";
  const freeCashFlowSource = company.sources.free_cash_flow;
  const freeCashFlowKind = freeCashFlowSource && "source" in freeCashFlowSource ? freeCashFlowSource.kind : null;
  const sharePriceSource = company.sources.share_price;
  const sharePricePeriod = sharePriceSource && "period" in sharePriceSource ? sharePriceSource.period : company.market_at ?? "N/A";
  const unleveredSource = company.sources.unlevered_cash_flow;
  const unleveredSourceName = unleveredSource && "source" in unleveredSource ? unleveredSource.source : company.valuation_basis?.cash_flow.source ?? "Unavailable";
  const unleveredPeriod = unleveredSource && "period" in unleveredSource ? unleveredSource.period : company.valuation_basis?.cash_flow.period ?? company.revenue_period ?? "N/A";
  const brief = buildResearchBrief(company);
  const payload = {
    company: {
      ticker: company.ticker, name: company.company, exchange: company.exchange,
      retrieved_at: company.retrieved_at, market_at: company.market_at,
      reporting_currency: company.reporting_currency, trading_currency: company.trading_currency,
      score,
      score_label: scoreAvailable ? company.score_label : "Unavailable",
      score_trace: scoreAvailable ? company.score_trace : null,
      price: company.price, market_cap_reporting: company.market_cap_reporting,
      market_cap_quote_currency: company.market_cap, enterprise_value: enterpriseValue, revenue: company.revenue,
      other_claims: company.other_claims ?? null,
      free_cash_flow: company.free_cash_flow, fcf_period: company.fcf_period,
      unlevered_cash_flow: company.unlevered_cash_flow ?? null,
      unlevered_fcf_margin: company.unlevered_fcf_margin ?? null,
      valuation_basis: valuationMethodologySupported ? company.valuation_basis ?? null : null,
      valuation_projection: valuationMethodologySupported ? company.valuation_projection ?? null : null,
      valuation_sensitivity: valuationMethodologySupported ? company.valuation_sensitivity ?? [] : [],
      historical_growth: company.historical_growth, consensus_growth: company.consensus_growth,
      analyst_estimate_period: company.analyst_estimate_period,
      analyst_estimate_end_date: company.analyst_estimate_end_date ?? null,
      analyst_estimate_currency: company.analyst_estimate_currency ?? null,
      required_growth: valuationMethodologySupported ? company.required_growth : null,
      solver_status: valuationMethodologySupported ? company.solver_status : "unavailable",
      solver_low: valuationMethodologySupported ? company.solver_low : null,
      solver_high: valuationMethodologySupported ? company.solver_high : null,
      growth_clamped: valuationMethodologySupported && company.growth_clamped,
      model_fcf_margin: valuationMethodologySupported ? company.model_fcf_margin : null,
      discount_rate: valuationMethodologySupported ? company.discount_rate : null,
      terminal_growth: valuationMethodologySupported ? company.terminal_growth : null,
      assumptions: valuationMethodologySupported ? company.assumptions : { starting_growth: null, fcf_margin: null, discount_rate: null, terminal_growth: null },
      user_assumptions: valuationMethodologySupported ? effectiveScenario?.inputs ?? company.user_assumptions : null,
      scenario: effectiveScenario, sources, quality_flags: company.quality_flags,
      confidence: company.confidence, is_stale: company.is_stale, freshness: company.freshness,
      methodology_version: company.methodology_version, analysis_schema_version: company.analysis_schema_version,
    },
    disclaimer,
    research_takeaway: { ...brief, sector_guidance: sectorResearchGuide(company), validation_status: SCORE_VALIDATION_NOTE },
    limitations: company.quality_flags.map((flag) => flag.message),
  };
  const solverBounds = !valuationMethodologySupported || company.solver_low === null || company.solver_high === null
    ? "tested bounds unavailable"
    : `tested bounds ${(company.solver_low * 100).toFixed(1)}%–${(company.solver_high * 100).toFixed(1)}%`;
  const solverStatus = `${valuationMethodologySupported ? company.solver_status : "unavailable"}; ${solverBounds}`;
  const staleMarketStatus = company.is_stale;
  const sharePriceStatus = staleMarketStatus ? "Stale quote; price-based valuation is unavailable" : "";
  const marketCapStatus = staleMarketStatus ? "Stale quote; verify before relying on this value" : "";
  const csvRows = [
    ["Metric", "Value", "Currency / units", "Source", "Period", "Status / bounds"],
    ["TSRP Score", score ?? "N/A", "0–100 research signal", "TSRP engine", company.methodology_version, scoreAvailable ? "available" : "unavailable"],
    ["Share price", company.price ?? "N/A", company.trading_currency, company.price === null ? "Unavailable" : sourceLine("share_price"), sharePricePeriod, sharePriceStatus],
    ["Market capitalization in reporting currency", company.market_cap_reporting ?? "N/A", company.reporting_currency, sourceLine("market_cap_reporting"), company.market_at ?? "N/A", marketCapStatus],
    ["Enterprise value", enterpriseValue ?? "N/A", company.reporting_currency, enterpriseValueSource && "source" in enterpriseValueSource ? enterpriseValueSource.source : "Unavailable", enterpriseValuePeriod, enterpriseValueSource && "kind" in enterpriseValueSource ? enterpriseValueSource.kind : "Calculation basis unavailable"],
    ["Reported debt", company.debt ?? "N/A", company.reporting_currency, sourceLine("debt"), company.sources.debt && "period" in company.sources.debt ? company.sources.debt.period : "N/A", ""],
    ["Revenue", company.revenue ?? "N/A", company.reporting_currency, company.revenue_source ?? "Unavailable", company.revenue_period ?? "N/A", ""],
    ["Free cash flow", company.free_cash_flow ?? "N/A", company.reporting_currency, freeCashFlowSourceLabel(company.fcf_source, freeCashFlowKind), company.fcf_period ?? "N/A", ""],
    ["Estimated pre-debt operating cash flow", company.unlevered_cash_flow ?? "N/A", company.reporting_currency, unleveredSourceName, unleveredPeriod, company.valuation_basis?.cash_flow.reason ?? "Unavailable"],
    ["Estimated WACC", valuationMethodologySupported ? company.valuation_basis?.capital_cost.value ?? "N/A" : "N/A", "ratio", valuationMethodologySupported ? company.valuation_basis?.capital_cost.reference.source ?? "Unavailable" : "Unavailable", valuationMethodologySupported ? company.valuation_basis?.capital_cost.reference.date ?? "N/A" : "N/A", valuationMethodologySupported ? company.valuation_basis?.capital_cost.reason ?? "Unavailable" : "Unsupported sector or currency"],
    ["Historical revenue growth", company.historical_growth ?? "N/A", "annualized", company.revenue_growth_source ?? "Unavailable", historicalGrowthPeriod, ""],
    [analystEstimateLabel(company.analyst_estimate_period), company.consensus_growth ?? "N/A", "growth rate", company.consensus_growth === null ? "Unavailable" : sourceLine("analyst_growth"), company.analyst_estimate_end_date ?? company.analyst_estimate_period ?? "N/A", company.analyst_estimate_end_date && company.analyst_estimate_currency ? `Forecast currency: ${company.analyst_estimate_currency}` : "Exact fiscal period or estimate currency unavailable; not revision-comparable"],
    ["TSRP model-required revenue growth", valuationMethodologySupported ? company.required_growth ?? "N/A" : "N/A", "annualized model sensitivity", valuationMethodologySupported ? "TSRP illustrative valuation calculation" : "Unavailable", "N/A", solverStatus],
    ["Pre-debt cash-flow margin assumption", valuationMethodologySupported ? company.model_fcf_margin ?? "N/A" : "N/A", "ratio", !valuationMethodologySupported || company.model_fcf_margin === null ? "Unavailable" : "TSRP baseline assumption", company.revenue_period ?? "N/A", ""],
    ["Discount-rate assumption", valuationMethodologySupported ? company.discount_rate ?? "N/A" : "N/A", "ratio", sourceLine("valuation_assumptions"), company.methodology_version, ""],
    ["Terminal-growth assumption", valuationMethodologySupported ? company.terminal_growth ?? "N/A" : "N/A", "ratio", sourceLine("valuation_assumptions"), company.methodology_version, ""],
    ["Scenario starting-growth assumption", effectiveScenario?.inputs.starting_growth ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario pre-debt cash-flow margin assumption", effectiveScenario?.inputs.fcf_margin ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario discount-rate assumption", effectiveScenario?.inputs.discount_rate ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario terminal-growth assumption", effectiveScenario?.inputs.terminal_growth ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario enterprise value", effectiveScenario?.enterprise_value ?? "N/A", company.reporting_currency, "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario equity value", effectiveScenario?.equity_value ?? "N/A", company.reporting_currency, "Scenario less reported debt and identified other claims plus cash", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Analysis retrieved at", company.retrieved_at, "UTC", "TSRP", company.retrieved_at, ""],
    ["Market observation as of", company.market_at ?? "N/A", "UTC", company.market_at ? sourceLine("share_price") : "Unavailable", company.market_at ?? "N/A", ""],
    ["Limitations", company.quality_flags.map((flag) => flag.message).join("; ") || "See methodology and disclaimer", "", "TSRP quality flags", company.methodology_version, ""],
  ];
  const projectionRows = valuationMethodologySupported && company.valuation_projection
    ? company.valuation_projection.annual_cash_flows.flatMap((row) => [
      [`Model year ${row.year} revenue`, row.revenue, company.reporting_currency, "TSRP assumption-dependent projection", `Forecast year ${row.year}`, "Not reported results"],
      [`Model year ${row.year} revenue growth`, row.revenue_growth, "ratio", "TSRP assumption-dependent projection", `Forecast year ${row.year}`, "Not reported results"],
      [`Model year ${row.year} pre-debt cash flow`, row.cash_flow, company.reporting_currency, "TSRP assumption-dependent projection", `Forecast year ${row.year}`, "Not reported results"],
      [`Model year ${row.year} present value`, row.present_value, company.reporting_currency, "TSRP assumption-dependent projection", `Forecast year ${row.year}`, "Discounted cash flow"],
    ]) : [];
  const sensitivityRows = valuationMethodologySupported ? (company.valuation_sensitivity ?? []).map((row) => [
    "Enterprise-value sensitivity", row.enterprise_value ?? "N/A", company.reporting_currency,
    "TSRP assumption-dependent sensitivity", "Ten-year model plus terminal cash flow",
    `Discount rate ${row.discount_rate}; terminal growth ${row.terminal_growth}; rates are ratios`,
  ]) : [];
  const terminalRows = valuationMethodologySupported && company.valuation_projection ? [
    ["Model terminal value", company.valuation_projection.terminal_value, company.reporting_currency, "TSRP assumption-dependent projection", "Beyond forecast year ten", "Not a target price"],
    ["Model terminal present value", company.valuation_projection.terminal_present_value, company.reporting_currency, "TSRP assumption-dependent projection", "Beyond forecast year ten", "Discounted terminal cash flow"],
    ["Model terminal-value share", company.valuation_projection.terminal_value_share, "ratio", "TSRP assumption-dependent projection", "Beyond forecast year ten", "Not a confidence interval"],
  ] : [];
  const csv = toCsv([...csvRows, ...projectionRows, ...terminalRows, ...sensitivityRows, [],
    ["Research takeaway", brief.conclusion], ["Main uncertainty", brief.uncertainty],
    ["Sector guidance", sectorResearchGuide(company)], ["Score validation", SCORE_VALIDATION_NOTE], ["Disclaimer", disclaimer],
  ]);
  return { json: JSON.stringify(payload, null, 2), csv };
}
