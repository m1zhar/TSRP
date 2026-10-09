import type { Company } from "./types";

function sourceForComparison(source: Company["sources"][string]) {
  if (!source || !("source" in source)) return source ?? null;
  return { source: source.source, period: source.period, currency: source.currency, kind: source.kind };
}

function scoreTraceForComparison(trace: Company["score_trace"]) {
  if (!trace) return null;
  const components = trace.components.map(({ key, value, evidence_coverage }) => ({ key, value, evidence_coverage }));
  const subscores = trace.subscores
    ? Object.fromEntries(Object.entries(trace.subscores).map(([name, detail]) => [name, {
      evidence_coverage: detail.evidence_coverage,
      components: detail.components.map(({ key, value }) => ({ key, value })),
    }]))
    : undefined;
  return {
    score: trace.score,
    evidence_coverage: trace.evidence_coverage,
    components,
    ...(subscores ? { subscores } : {}),
  };
}

/** Send only the data required to compare saved research; full snapshots stay in browser storage. */
export function toSnapshotComparisonPayload(company: Company, savedAt?: string) {
  return {
    ticker: company.ticker,
    methodology_version: company.methodology_version,
    analysis_schema_version: company.analysis_schema_version,
    reporting_currency: company.reporting_currency,
    retrieved_at: company.retrieved_at,
    ...(savedAt ? { saved_at: savedAt } : {}),
    score: company.score,
    score_trace: scoreTraceForComparison(company.score_trace),
    business_quality: company.business_quality,
    financial_strength: company.financial_strength,
    historical_growth: company.historical_growth,
    consensus_growth: company.consensus_growth,
    required_growth: company.required_growth,
    revenue: company.revenue,
    revenue_period: company.revenue_period,
    revenue_source: company.revenue_source,
    free_cash_flow: company.free_cash_flow,
    fcf_period: company.fcf_period,
    fcf_source: company.fcf_source,
    enterprise_value: company.enterprise_value,
    solver_status: company.solver_status,
    user_assumptions: company.user_assumptions,
    sources: {
      revenue: sourceForComparison(company.sources.revenue),
      free_cash_flow: sourceForComparison(company.sources.free_cash_flow),
    },
  };
}
