export type ScoreComponent = {
  key: string;
  label: string;
  value: number | null;
  weight: number;
  applied_weight: number;
  evidence_coverage: number;
  contribution: number | null;
};

export type ScoreFactor = { key: string; label: string; value: number | null; weight: number };

export type FxConversion = { from: string; to: string; rate: number | null; pairs: string[]; directions: string[]; leg_periods?: string[]; period: string | null };
export type SourceFact = {
  value: number | null;
  period: string | null;
  source_unit: string | null;
  filing_date: string | null;
  filing_form: string | null;
  taxonomy: string | null;
  tag: string | null;
};
export type Source = { source: string; period: string; currency: string; kind: string; conversions?: FxConversion[]; components?: SourceFact[] };

export type Company = {
  ticker: string;
  company: string;
  exchange: string;
  quote_type: string;
  sector: string;
  sector_methodology_supported: boolean;
  currency_methodology_supported?: boolean;
  industry: string;
  reporting_currency: string;
  trading_currency: string;
  price: number | null;
  previous_close: number | null;
  market_cap: number | null;
  market_cap_reporting: number | null;
  enterprise_value: number | null;
  revenue: number | null;
  revenue_period: string | null;
  revenue_source: string | null;
  free_cash_flow: number | null;
  fcf_period: string | null;
  fcf_source: string | null;
  fcf_margin: number | null;
  model_fcf_margin: number | null;
  model_fcf_note: string | null;
  model_fcf_refused: boolean;
  discount_rate: number | null;
  terminal_growth: number | null;
  risk_free: number | null;
  equity_risk_premium: number | null;
  sector_spread: number | null;
  rate_currency: string;
  historical_growth: number | null;
  historical_growth_years: number;
  historical_growth_3y: number | null;
  consensus_growth: number | null;
  analyst_estimate_period: string | null;
  target_mean_price: number | null;
  analyst_count: number | null;
  required_growth: number | null;
  solver_status: string;
  solver_low: number | null;
  solver_high: number | null;
  growth_clamped: boolean;
  business_quality: number | null;
  financial_strength: number | null;
  market_expectations: number | null;
  score: number | null;
  score_label: string;
  score_trace: { score: number | null; components: ScoreComponent[]; subscores?: Record<string, { score: number | null; evidence_coverage: number; components: ScoreFactor[] }>; evidence_coverage: number; minimum_evidence_coverage: number; growth_comparison_available: boolean; growth_comparison_inputs: { historical_growth: number | null; required_growth: number | null; growth_clamped: boolean } } | null;
  snapshot_proof: string | null;
  score_available: boolean;
  analysis_schema_version: string;
  confidence: string;
  revenue_growth_source: string | null;
  gross_margin: number | null;
  operating_margin: number | null;
  profit_margin: number | null;
  return_on_equity: number | null;
  cash: number | null;
  debt: number | null;
  assets: number | null;
  equity: number | null;
  debt_to_assets: number | null;
  ev_sales: number | null;
  ev_ebitda: number | null;
  pe: number | null;
  sources: Record<string, Source | { kind: string; label: string; company?: string; cik?: string }>;
  sec_status: { kind: string; label: string; company?: string; cik?: string };
  quality_flags: { message: string; severity: string }[];
  retrieved_at: string;
  market_at: string | null;
  is_stale: boolean;
  freshness: string;
  methodology_version: string;
  assumptions: Assumptions;
  user_assumptions: Assumptions | null;
  scenario: { enterprise_value: number | null; equity_value: number | null; inputs: Assumptions; kind: string } | null;
};

export type Assumptions = {
  starting_growth: number | null;
  fcf_margin: number | null;
  discount_rate: number | null;
  terminal_growth: number | null;
};

export type PricePoint = { date: string; close: number; volume: number | null };
export type Analysis = {
  company: Company;
  price_history: PricePoint[];
  scenario_context_token: string | null;
  scenario_context_status?: "available" | "not_configured" | "unsupported" | "unavailable";
};
export type CompanyMatch = { ticker: string; company: string; exchange: string; sector: string };
export type MarketMover = { ticker: string; change_percent: number; absolute_change: number; currency: string; market_at: string };
export type MarketMovers = { source: string; period: string; top_stocks_rank: string; top_stocks_label: string; retrieved_at: string; top_stocks_retrieved_at?: string; items: MarketMover[]; top_stocks: MarketMover[]; coverage_note?: string; top_stocks_coverage_note?: string; popular_stocks?: MarketMover[]; popular_stocks_rank?: string; popular_stocks_label?: string; popular_stocks_retrieved_at?: string; popular_stocks_coverage_note?: string };
export type TopStocksResponse = { source: string; rank: string; label: string; retrieved_at: string; items: MarketMover[]; coverage_note?: string };
export type SectionName = "TSRP Score" | "Expectations vs Reality" | "Valuation" | "Catalysts & Risks" | "What Changed?";

export const SECTIONS: SectionName[] = [
  "TSRP Score",
  "Expectations vs Reality",
  "Valuation",
  "Catalysts & Risks",
  "What Changed?",
];

export type Snapshot = Company & { saved_at: string };
