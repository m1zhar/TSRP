import assert from "node:assert/strict";
import test from "node:test";
import { buildResearchExport } from "./research-export.ts";
import { downloadResearchFile, type DownloadEnvironment } from "./download.ts";
import { attemptResearchExport, exportFeedbackMessage } from "./export-feedback.ts";
import { sourceComponentsLine } from "./source-format.ts";
import type { Company } from "./types.ts";

function companyFixture(overrides: Partial<Company> = {}): Company {
  return {
    ticker: "AAPL", company: "Apple Inc.", exchange: "Nasdaq", quote_type: "EQUITY", sector: "Technology", sector_methodology_supported: true, industry: "Consumer Electronics",
    reporting_currency: "USD", trading_currency: "USD", price: 192.3, previous_close: 190, market_cap: 3_000_000_000_000, market_cap_reporting: 3_000_000_000_000, enterprise_value: 3_010_000_000_000,
    revenue: 400_000_000_000, revenue_period: "FY2025", revenue_source: "SEC", free_cash_flow: -2_000_000, fcf_period: "FY2025", fcf_source: "SEC", fcf_margin: -0.005, model_fcf_margin: 0.12, model_fcf_note: null, model_fcf_refused: false,
    discount_rate: 0.09, terminal_growth: 0.025, risk_free: 0.04, equity_risk_premium: 0.05, sector_spread: 0.01, rate_currency: "USD", historical_growth: 0.08, historical_growth_years: 3, historical_growth_3y: 0.07, consensus_growth: 0.06, analyst_estimate_period: "FY2026",
    target_mean_price: null, analyst_count: null, required_growth: 0.1, solver_status: "exact", solver_low: 0.1, solver_high: 0.1, growth_clamped: false,
    business_quality: 70, financial_strength: 65, market_expectations: 55, score: 67.6923076923077, score_label: "Mixed but explainable", score_trace: { score: 67.6923076923077, components: [{ key: "business_quality", label: "Business quality", value: 70, weight: 7 / 13, applied_weight: 7 / 13, evidence_coverage: 1, contribution: 70 * 7 / 13 }, { key: "financial_strength", label: "Financial strength", value: 65, weight: 6 / 13, applied_weight: 6 / 13, evidence_coverage: 1, contribution: 65 * 6 / 13 }], evidence_coverage: 1, minimum_evidence_coverage: 0.7, growth_comparison_available: false, growth_comparison_inputs: { historical_growth: null, required_growth: null, growth_clamped: false } }, score_available: true,
    analysis_schema_version: "analysis-v5", snapshot_proof: "signed-test-proof", confidence: "Moderate", revenue_growth_source: "SEC", gross_margin: 0.45, operating_margin: 0.3, profit_margin: 0.25, return_on_equity: 1.2, cash: 20_000_000, debt: 100_000_000, assets: 500_000_000, equity: 100_000_000,
    debt_to_assets: 0.2, ev_sales: 7.5, ev_ebitda: 25, pe: 30,
    sources: { enterprise_value: { source: "Yahoo Finance", period: "2026-10-09", currency: "USD", kind: "market" }, historical_growth: { source: "SEC EDGAR", period: "FY2023–FY2025", currency: "USD", kind: "reported" } },
    sec_status: { kind: "available", label: "SEC data available" }, quality_flags: [{ message: "FCF is negative", severity: "warning" }],
    retrieved_at: "2026-10-10T12:00:00Z", market_at: "2026-10-09T20:00:00Z", is_stale: false, freshness: "current", methodology_version: "score-v10",
    assumptions: { starting_growth: 0.1, fcf_margin: 0.12, discount_rate: 0.09, terminal_growth: 0.025 }, user_assumptions: null, scenario: null,
    ...overrides,
  };
}

test("JSON export preserves the displayed analysis, provenance, warnings, and user scenario", () => {
  const company = companyFixture();
  company.sources.debt = {
    source: "SEC EDGAR", period: "2025-12-31", currency: "USD", kind: "reported",
    components: [
      { value: 100, period: "2025-12-31", source_unit: "USD", filing_date: "2026-03-01", filing_form: "10-K/A", taxonomy: "us-gaap", tag: "DebtCurrent" },
      { value: 200, period: "2025-12-31", source_unit: "USD", filing_date: "2026-02-15", filing_form: "10-K", taxonomy: "us-gaap", tag: "LongTermDebtNoncurrent" },
    ],
  };
  const scenario = { enterprise_value: 500, equity_value: 450, inputs: { starting_growth: 0.11, fcf_margin: 0.13, discount_rate: 0.1, terminal_growth: 0.02 }, kind: "user_scenario" };
  const output = buildResearchExport(company, scenario, "Research disclaimer.", (key) => `source for ${key}`);
  const parsed = JSON.parse(output.json) as { company: Record<string, unknown>; disclaimer: string; limitations: string[] };

  assert.equal(parsed.company.ticker, "AAPL");
  assert.equal(parsed.company.score, company.score);
  assert.deepEqual(parsed.company.score_trace, company.score_trace);
  assert.equal(parsed.company.free_cash_flow, -2_000_000);
  assert.equal(parsed.company.market_at, company.market_at);
  assert.equal(parsed.company.methodology_version, "score-v10");
  assert.equal(parsed.company.analyst_estimate_period, "FY2026");
  assert.deepEqual(parsed.company.scenario, scenario);
  assert.deepEqual(parsed.limitations, ["FCF is negative"]);
  assert.equal(parsed.disclaimer, "Research disclaimer.");
  assert.equal("scenario_context_token" in parsed.company, false);
  const debtSource = (parsed.company.sources as Company["sources"]).debt;
  assert.ok(debtSource && "source" in debtSource);
  assert.deepEqual(debtSource.components?.map((fact) => fact.value), [100, 200]);
});

test("CSV identifies provider-reported free cash flow and its fallback limitation", () => {
  const company = companyFixture();
  company.fcf_source = "Yahoo Finance";
  company.sources.free_cash_flow = {
    source: "Yahoo Finance", period: "FY2025", currency: "USD",
    kind: "provider-reported free cash flow; OCF or capex unavailable",
  };
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "source");

  assert.match(output.csv, /"Free cash flow",-2000000,"USD","Yahoo Finance · Provider-reported free cash flow; operating cash flow or capital-spending detail was unavailable\."/u);
});

test("CSV export labels the same source, period, units, negative values, N/A, and scenario assumptions", () => {
  const company = companyFixture({ required_growth: null, solver_status: "unavailable", revenue: null, revenue_period: null, market_at: null });
  company.sources.share_price = { source: "Synthetic local QA fixture", period: "2026-10-10T20:00:00Z", currency: "USD", kind: "fixture" };
  company.sources.debt = { source: "SEC EDGAR", period: "FY2025", currency: "USD", kind: "reported" };
  const scenario = { enterprise_value: 500, equity_value: 450, inputs: { starting_growth: 0.11, fcf_margin: 0.13, discount_rate: 0.1, terminal_growth: 0.02 }, kind: "user_scenario" };
  const output = buildResearchExport(company, scenario, "Research disclaimer.", (key) => `source for ${key}`);

  assert.match(output.csv, /"TSRP Score",67\.6923076923077,"0–100 research signal","TSRP engine","score-v10"/u);
  assert.match(output.csv, /"Share price",192\.3,"USD","source for share_price","2026-10-10T20:00:00Z"/u);
  assert.match(output.csv, /"Free cash flow",-2000000,"USD","SEC","FY2025"/u);
  assert.match(output.csv, /"Reported debt",100000000,"USD","source for debt","FY2025"/u);
  assert.match(output.csv, /"TSRP model-required revenue growth","N\/A"/u);
  assert.match(output.csv, /"Scenario starting-growth assumption",0\.11,"ratio","User-entered scenario","Scenario assumptions"/u);
  assert.match(output.csv, /"Market observation as of","N\/A","UTC","Unavailable","N\/A"/u);
  assert.match(output.csv, /"Disclaimer","Research disclaimer\."/u);
});

test("CSV export retains the enterprise-value calculation basis and limitation shown in valuation", () => {
  const company = companyFixture();
  company.sources.enterprise_value = {
    source: "TSRP calculation",
    period: "2025-12-31",
    currency: "USD",
    kind: "Market capitalization + same-date reported debt − cash; excludes other claims not separately captured",
  };
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "source");

  assert.match(output.csv, /"Enterprise value",3010000000000,"USD","TSRP calculation","2025-12-31","Market capitalization \+ same-date reported debt − cash; excludes other claims not separately captured"/u);
});

test("scenario equity export discloses identified additional ownership claims", () => {
  const company = companyFixture();
  company.other_claims = 25;
  const scenario = { enterprise_value: 500, equity_value: 425,
    inputs: { starting_growth: .11, fcf_margin: .13, discount_rate: .1, terminal_growth: .02 }, kind: "user_scenario" };
  const output = buildResearchExport(company, scenario, "Research disclaimer.", () => "source");
  assert.match(output.csv, /"Scenario equity value",425,"USD","Scenario less reported debt and identified other claims plus cash"/u);
  assert.equal(JSON.parse(output.json).company.other_claims, 25);
  assert.equal(JSON.parse(output.json).company.scenario.equity_value, 425);
});

test("estimate exports retain the exact fiscal year and do not invent missing forecast currency", () => {
  const company = companyFixture({ analyst_estimate_period: "Next-year analyst revenue estimate", analyst_estimate_end_date: "2027-06-30", analyst_estimate_currency: "USD" });
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "source");
  const exported = JSON.parse(output.json).company;
  assert.equal(exported.analyst_estimate_end_date, "2027-06-30");
  assert.equal(exported.analyst_estimate_currency, "USD");
  assert.match(output.csv, /"Next-year analyst revenue estimate",0\.06,"growth rate","source","2027-06-30","Forecast currency: USD"/u);
  company.analyst_estimate_currency = null;
  const unknown = buildResearchExport(company, null, "Research disclaimer.", () => "source");
  assert.equal(JSON.parse(unknown.json).company.analyst_estimate_currency, null);
  assert.match(unknown.csv, /Exact fiscal period or estimate currency unavailable; not revision-comparable/u);
});

test("CSV export retains the calculated Yahoo debt basis and both dated components", () => {
  const company = companyFixture();
  const debtSource = {
    source: "Yahoo Finance",
    period: "2025-12-31",
    currency: "USD",
    kind: "calculated: Yahoo Finance current debt + long-term debt",
    components: [
      { value: 50, period: "2025-12-31", source_unit: "USD", filing_date: null, filing_form: null, taxonomy: "Yahoo Finance statement", tag: "Current Debt" },
      { value: 200, period: "2025-12-31", source_unit: "USD", filing_date: null, filing_form: null, taxonomy: "Yahoo Finance statement", tag: "Long Term Debt And Capital Lease Obligation" },
    ],
  };
  company.debt = 250;
  company.sources.debt = debtSource;
  const debtDetail = `Yahoo Finance · 2025-12-31 · USD · ${debtSource.kind}${sourceComponentsLine(debtSource.components)}`;
  const output = buildResearchExport(company, null, "Research disclaimer.", (key) => key === "debt" ? debtDetail : `source for ${key}`);

  assert.ok(output.csv.includes(`"Reported debt",250,"USD","${debtDetail}","2025-12-31"`));
});

test("CSV export labels an analyst estimate by its actual horizon", () => {
  const company = companyFixture({
    consensus_growth: 0.09,
    analyst_estimate_period: "Current-year analyst revenue estimate",
  });
  company.sources.analyst_growth = { source: "Yahoo Finance", period: "Current-year analyst revenue estimate", currency: "USD", kind: "consensus estimate" };
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "source");

  assert.match(output.csv, /"Current-year analyst revenue estimate",0\.09,"growth rate","source","Current-year analyst revenue estimate"/u);
});

test("CSV analyst-estimate provenance follows the analysis source instead of assuming Yahoo Finance", () => {
  const period = "Next-year analyst revenue estimate";
  const company = companyFixture({ consensus_growth: 0.12, analyst_estimate_period: period });
  company.sources.analyst_growth = { source: "Synthetic local QA fixture", period, currency: "USD", kind: "consensus estimate" };
  const output = buildResearchExport(company, null, "Research disclaimer.", (key) => key === "analyst_growth" ? `Synthetic local QA fixture · ${period} · USD` : "source");

  assert.match(output.csv, /"Next-year analyst revenue estimate",0\.12,"growth rate","Synthetic local QA fixture · Next-year analyst revenue estimate · USD","Next-year analyst revenue estimate"/u);
});

test("CSV never guesses a market or estimate provider when source provenance is missing", () => {
  const company = companyFixture({ price: 192.3, consensus_growth: 0.12, analyst_estimate_period: "Next-year analyst revenue estimate" });
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "Source unavailable");

  assert.match(output.csv, /"Share price",192\.3,"USD","Source unavailable","2026-10-09T20:00:00Z"/u);
  assert.match(output.csv, /"Next-year analyst revenue estimate",0\.12,"growth rate","Source unavailable","Next-year analyst revenue estimate"/u);
});

test("CSV market provenance follows the quote source instead of assuming Yahoo Finance", () => {
  const marketAt = "2026-10-09T20:00:00Z";
  const company = companyFixture({ market_at: marketAt });
  company.sources.share_price = { source: "Synthetic local QA fixture", period: marketAt, currency: "USD", kind: "fixture" };
  const output = buildResearchExport(company, null, "Research disclaimer.", (key) => key === "share_price" ? `Synthetic local QA fixture · ${marketAt} · USD` : "source");

  assert.match(output.csv, new RegExp(`"Share price",192\\.3,"USD","Synthetic local QA fixture · ${marketAt} · USD","${marketAt}"`, "u"));
  assert.match(output.csv, new RegExp(`"Market observation as of","${marketAt}","UTC","Synthetic local QA fixture · ${marketAt} · USD","${marketAt}"`, "u"));
});

test("CSV marks retained market figures as stale when their quote is not current", () => {
  const company = companyFixture({ is_stale: true, freshness: "stale", enterprise_value: null, required_growth: null, solver_status: "unavailable" });
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "source");

  assert.match(output.csv, /"Share price",192\.3,"USD","source","2026-10-09T20:00:00Z","Stale quote; price-based valuation is unavailable"/u);
  assert.match(output.csv, /"Market capitalization in reporting currency",3000000000000,"USD","source","2026-10-09T20:00:00Z","Stale quote; verify before relying on this value"/u);
  assert.match(output.csv, /"Enterprise value","N\/A"/u);
});

test("CSV research export prefixes locale-specific formula text in limitations", () => {
  const message = "\u3000＝1+1";
  const company = companyFixture({ quality_flags: [{ message, severity: "warning" }] });
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "source");

  assert.ok(output.csv.includes(`"Limitations","\t${message}"`));
});

test("exports reverse-DCF bounds and status separately from reporting period", () => {
  const company = companyFixture({
    required_growth: null,
    solver_status: "above_range",
    solver_low: 0,
    solver_high: 1.2,
    growth_clamped: true,
  });
  const output = buildResearchExport(company, null, "Research disclaimer.", () => "source");
  const parsed = JSON.parse(output.json) as { company: Record<string, unknown> };

  assert.equal(parsed.company.solver_status, "above_range");
  assert.equal(parsed.company.solver_low, 0);
  assert.equal(parsed.company.solver_high, 1.2);
  assert.equal(parsed.company.growth_clamped, true);
  assert.match(output.csv, /"TSRP model-required revenue growth","N\/A","annualized model sensitivity","TSRP illustrative valuation calculation","N\/A","above_range; tested bounds 0\.0%–120\.0%"/u);
});

test("exports mask cached USD-derived assumptions and scenarios for an unsupported currency", () => {
  const company = companyFixture({
    reporting_currency: "XYZ", rate_currency: "USD", currency_methodology_supported: false,
    discount_rate: 0.09, terminal_growth: 0.02,
    assumptions: { starting_growth: 0.1, fcf_margin: 0.12, discount_rate: 0.09, terminal_growth: 0.02 },
  });
  const scenario = { enterprise_value: 700, equity_value: 600, inputs: { starting_growth: 0.1, fcf_margin: 0.12, discount_rate: 0.09, terminal_growth: 0.02 }, kind: "cached fallback scenario" };
  const output = buildResearchExport(company, scenario, "Research disclaimer.", () => "USD fallback");
  const parsed = JSON.parse(output.json) as { company: { assumptions: Record<string, unknown>; scenario: unknown; sources: Record<string, { source: string; currency: string; kind: string }> } };

  assert.equal(parsed.company.assumptions.discount_rate, null);
  assert.equal(parsed.company.assumptions.terminal_growth, null);
  assert.equal(parsed.company.scenario, null);
  assert.equal(parsed.company.sources.valuation_assumptions.source, "Unavailable");
  assert.equal(parsed.company.sources.valuation_assumptions.currency, "N/A");
  assert.match(parsed.company.sources.valuation_assumptions.kind, /USD rates are not substituted/u);
  assert.match(output.csv, /"Discount-rate assumption","N\/A"/u);
  assert.match(output.csv, /"Terminal-growth assumption","N\/A"/u);
  assert.match(output.csv, /"Scenario enterprise value","N\/A"/u);
});

test("exports withhold valuation assumptions and provenance for an unsupported sector", () => {
  const company = companyFixture({
    sector: "Financial Services", sector_methodology_supported: false,
    discount_rate: 0.088, terminal_growth: 0.024, sector_spread: 0,
    score_available: false, score: 64.5, score_label: "Mixed",
    enterprise_value: 3_010_000_000_000, required_growth: 0.1, model_fcf_margin: 0.12,
    sources: {
      enterprise_value: { source: "TSRP calculation", period: "2026-10-09", currency: "USD", kind: "market value" },
      valuation_assumptions: { source: "TSRP static calibration", period: "No live market-yield timestamp", currency: "USD", kind: "Static illustrative TSRP assumption" },
    },
    assumptions: { starting_growth: 0.1, fcf_margin: 0.12, discount_rate: 0.088, terminal_growth: 0.024 },
    user_assumptions: { starting_growth: 0.1, fcf_margin: 0.12, discount_rate: 0.088, terminal_growth: 0.024 },
  });
  const scenario = { enterprise_value: 700, equity_value: 600, inputs: { starting_growth: 0.1, fcf_margin: 0.12, discount_rate: 0.088, terminal_growth: 0.024 }, kind: "stale scenario" };
  const output = buildResearchExport(company, scenario, "Research disclaimer.", () => "unavailable");
  const parsed = JSON.parse(output.json) as { company: Record<string, unknown> };
  const exportedCompany = parsed.company as {
    assumptions: Record<string, unknown>;
    user_assumptions: unknown;
    scenario: unknown;
    score: number | null;
    enterprise_value: number | null;
    required_growth: number | null;
    model_fcf_margin: number | null;
    sources: Record<string, { source: string; currency: string; kind: string }>;
  };

  assert.equal(exportedCompany.score, null);
  assert.match(output.csv, /"TSRP Score","N\/A","0–100 research signal","TSRP engine","score-v10","unavailable"/u);
  assert.equal(exportedCompany.enterprise_value, null);
  assert.equal(exportedCompany.required_growth, null);
  assert.equal(exportedCompany.model_fcf_margin, null);
  assert.deepEqual(exportedCompany.assumptions, { starting_growth: null, fcf_margin: null, discount_rate: null, terminal_growth: null });
  assert.equal(exportedCompany.user_assumptions, null);
  assert.equal(exportedCompany.scenario, null);
  assert.equal(exportedCompany.sources.valuation_assumptions.source, "Unavailable");
  assert.equal(exportedCompany.sources.valuation_assumptions.currency, "N/A");
  assert.match(exportedCompany.sources.valuation_assumptions.kind, /no validated TSRP sector calibration/iu);
  assert.equal(exportedCompany.sources.enterprise_value.source, "Unavailable");
  assert.match(output.csv, /"Enterprise value","N\/A","USD","Unavailable","N\/A"/u);
  assert.match(output.csv, /"Discount-rate assumption","N\/A"/u);
  assert.match(output.csv, /"Terminal-growth assumption","N\/A"/u);
  assert.match(output.csv, /"Scenario enterprise value","N\/A"/u);
  assert.match(output.csv, /"Pre-debt cash-flow margin assumption","N\/A","ratio","Unavailable"/u);
  assert.equal(parsed.company.valuation_basis, null);
});

test("download keeps its URL until delayed cleanup and removes its temporary link", async () => {
  const events: string[] = [];
  let scheduled: (() => void) | undefined;
  let scheduledDelay = 0;
  let objectUrl = "";
  let createdBlob: Blob | undefined;
  const anchor = {
    href: "", download: "", click: () => events.push("click"), remove: () => events.push("remove"),
  } as unknown as HTMLAnchorElement;
  const environment: DownloadEnvironment = {
    createObjectURL: (blob) => { createdBlob = blob; events.push("create"); return "blob:test"; },
    revokeObjectURL: (url) => { objectUrl = url; events.push("revoke"); },
    createAnchor: () => anchor,
    appendAnchor: (element) => { events.push("append"); assert.equal(element.download, "tsrp-aapl-unsafe-research.csv"); assert.equal(element.href, "blob:test"); },
    schedule: (callback, delay) => { scheduled = callback; scheduledDelay = delay; },
  };

  downloadResearchFile("metric,value\n", "csv", "AAPL/unsafe", environment);
  assert.deepEqual(events, ["create", "append", "click", "remove"]);
  assert.equal(await createdBlob?.text(), "metric,value\n");
  assert.equal(scheduledDelay, 1000);
  assert.equal(objectUrl, "");
  scheduled?.();
  assert.equal(objectUrl, "blob:test");
  assert.equal(events.at(-1), "revoke");
});

test("JSON download uses the JSON media type, extension, and exact serialized content", async () => {
  let createdBlob: Blob | undefined;
  let downloadName = "";
  let revokedUrl = "";
  let scheduled: (() => void) | undefined;
  const environment: DownloadEnvironment = {
    createObjectURL: (blob) => { createdBlob = blob; return "blob:json-test"; },
    revokeObjectURL: (url) => { revokedUrl = url; },
    createAnchor: () => ({
      href: "", download: "", click: () => undefined, remove: () => undefined,
    } as unknown as HTMLAnchorElement),
    appendAnchor: (anchor) => { downloadName = anchor.download; },
    schedule: (callback) => { scheduled = callback; },
  };
  const source = JSON.stringify({ ticker: "AAPL", score: 81.4, enterprise_value: null });

  downloadResearchFile(source, "json", "AAPL", environment);

  assert.equal(downloadName, "tsrp-aapl-research.json");
  assert.equal(createdBlob?.type, "application/json");
  assert.equal(await createdBlob?.text(), source);
  scheduled?.();
  assert.equal(revokedUrl, "blob:json-test");
});

test("download setup failure returns safe export feedback and still cleans up its temporary link", () => {
  const events: string[] = [];
  let scheduled: (() => void) | undefined;
  let revokedUrl = "";
  const anchor = {
    href: "", download: "", click: () => events.push("click"), remove: () => events.push("remove"),
  } as unknown as HTMLAnchorElement;
  const environment: DownloadEnvironment = {
    createObjectURL: () => { events.push("create"); return "blob:private"; },
    revokeObjectURL: (url) => { revokedUrl = url; events.push("revoke"); },
    createAnchor: () => anchor,
    appendAnchor: () => { events.push("append"); throw new Error("private browser detail"); },
    schedule: (callback) => { scheduled = callback; },
  };

  const state = attemptResearchExport("json", () => downloadResearchFile("{}", "json", "AAPL", environment));
  assert.deepEqual(state, { format: "JSON", status: "failed" });
  assert.equal(exportFeedbackMessage(state), "JSON export could not be prepared. Please try again.");
  assert.deepEqual(events, ["create", "append", "remove"]);
  assert.equal(revokedUrl, "");
  scheduled?.();
  assert.equal(revokedUrl, "blob:private");
  assert.equal(events.at(-1), "revoke");
});
