import { toCsv } from "./csv.ts";
import { freeCashFlowSourceLabel } from "./fcf-evidence.ts";
import { analystEstimateLabel } from "./research-signals.ts";
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
  const enterpriseValue = valuationMethodologySupported ? company.enterprise_value : null;
  const enterpriseValueSource = valuationMethodologySupported
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
      free_cash_flow: company.free_cash_flow, fcf_period: company.fcf_period,
      historical_growth: company.historical_growth, consensus_growth: company.consensus_growth,
      analyst_estimate_period: company.analyst_estimate_period,
      required_growth: valuationMethodologySupported ? company.required_growth : null,
      solver_status: valuationMethodologySupported ? company.solver_status : "unavailable",
      solver_low: valuationMethodologySupported ? company.solver_low : null,
      solver_high: valuationMethodologySupported ? company.solver_high : null,
      growth_clamped: valuationMethodologySupported && company.growth_clamped,
      model_fcf_margin: valuationMethodologySupported ? company.model_fcf_margin : null,
      assumptions: valuationMethodologySupported ? company.assumptions : { starting_growth: null, fcf_margin: null, discount_rate: null, terminal_growth: null },
      user_assumptions: valuationMethodologySupported ? company.user_assumptions : null,
      scenario: effectiveScenario, sources, quality_flags: company.quality_flags,
      confidence: company.confidence, is_stale: company.is_stale, freshness: company.freshness,
      methodology_version: company.methodology_version, analysis_schema_version: company.analysis_schema_version,
    },
    disclaimer,
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
    ["Historical revenue growth", company.historical_growth ?? "N/A", "annualized", company.revenue_growth_source ?? "Unavailable", historicalGrowthPeriod, ""],
    [analystEstimateLabel(company.analyst_estimate_period), company.consensus_growth ?? "N/A", "growth rate", company.consensus_growth === null ? "Unavailable" : sourceLine("analyst_growth"), company.analyst_estimate_period ?? "N/A", ""],
    ["TSRP model-required revenue growth", valuationMethodologySupported ? company.required_growth ?? "N/A" : "N/A", "annualized model sensitivity", valuationMethodologySupported ? "TSRP illustrative valuation calculation" : "Unavailable", "N/A", solverStatus],
    ["Free cash flow margin assumption", valuationMethodologySupported ? company.model_fcf_margin ?? "N/A" : "N/A", "ratio", !valuationMethodologySupported || company.model_fcf_margin === null ? "Unavailable" : "TSRP baseline assumption", company.fcf_period ?? "N/A", ""],
    ["Discount-rate assumption", valuationMethodologySupported ? company.discount_rate ?? "N/A" : "N/A", "ratio", sourceLine("valuation_assumptions"), company.methodology_version, ""],
    ["Terminal-growth assumption", valuationMethodologySupported ? company.terminal_growth ?? "N/A" : "N/A", "ratio", sourceLine("valuation_assumptions"), company.methodology_version, ""],
    ["Scenario starting-growth assumption", effectiveScenario?.inputs.starting_growth ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario free cash flow margin assumption", effectiveScenario?.inputs.fcf_margin ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario discount-rate assumption", effectiveScenario?.inputs.discount_rate ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario terminal-growth assumption", effectiveScenario?.inputs.terminal_growth ?? "N/A", "ratio", "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario enterprise value", effectiveScenario?.enterprise_value ?? "N/A", company.reporting_currency, "User-entered scenario", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Scenario equity value", effectiveScenario?.equity_value ?? "N/A", company.reporting_currency, "Scenario less reported debt plus cash", effectiveScenario ? "Scenario assumptions" : "Not calculated", ""],
    ["Analysis retrieved at", company.retrieved_at, "UTC", "TSRP", company.retrieved_at, ""],
    ["Market observation as of", company.market_at ?? "N/A", "UTC", company.market_at ? sourceLine("share_price") : "Unavailable", company.market_at ?? "N/A", ""],
    ["Limitations", company.quality_flags.map((flag) => flag.message).join("; ") || "See methodology and disclaimer", "", "TSRP quality flags", company.methodology_version, ""],
  ];
  const csv = toCsv([...csvRows, [], ["Disclaimer", disclaimer]]);
  return { json: JSON.stringify(payload, null, 2), csv };
}
