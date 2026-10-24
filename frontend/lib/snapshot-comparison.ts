import type { Company } from "./types";

export type SnapshotComparisonInput = Pick<Company, "ticker">
  & Omit<Partial<Company>, "score_trace" | "sources">
  & { score_trace?: unknown; sources?: unknown };

function sourceForComparison(source: unknown) {
  if (!source || typeof source !== "object" || Array.isArray(source) || !("source" in source)) return null;
  const value = source as Record<string, unknown>;
  return {
    source: value.source,
    period: value.period,
    currency: value.currency,
    kind: value.kind,
  };
}

function scoreComponents(items: unknown[]) {
  const components = [];
  for (const item of items) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const value = item as Record<string, unknown>;
    if (typeof value.key !== "string" || (value.value !== null && (typeof value.value !== "number" || !Number.isFinite(value.value)))) return null;
    if (value.evidence_coverage !== undefined && (typeof value.evidence_coverage !== "number" || !Number.isFinite(value.evidence_coverage))) return null;
    components.push({ key: value.key, value: value.value, ...(value.evidence_coverage === undefined ? {} : { evidence_coverage: value.evidence_coverage }) });
  }
  return components;
}

function scoreFactors(items: unknown[]) {
  const factors = [];
  for (const item of items) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const value = item as Record<string, unknown>;
    if (typeof value.key !== "string"
      || (value.value !== null && (typeof value.value !== "number" || !Number.isFinite(value.value)))
      || typeof value.weight !== "number" || !Number.isFinite(value.weight)) return null;
    factors.push({ key: value.key, value: value.value, weight: value.weight });
  }
  return factors;
}

function scoreTraceForComparison(trace: unknown) {
  if (!trace || typeof trace !== "object" || Array.isArray(trace)) return null;
  const value = trace as Company["score_trace"];
  if (!value || !Array.isArray(value.components)) return null;
  const growthInputs = value.growth_comparison_inputs;
  if (!growthInputs
    || !(growthInputs.historical_growth === null || (typeof growthInputs.historical_growth === "number" && Number.isFinite(growthInputs.historical_growth)))
    || !(growthInputs.required_growth === null || (typeof growthInputs.required_growth === "number" && Number.isFinite(growthInputs.required_growth)))
    || typeof growthInputs.growth_clamped !== "boolean") return null;
  const components = scoreComponents(value.components);
  if (!components) return null;
  let subscores: Record<string, { evidence_coverage: number; components: { key: string; value: number | null; weight: number }[] }> | undefined;
  if (value.subscores) {
    if (typeof value.subscores !== "object" || Array.isArray(value.subscores)) return null;
    subscores = {};
    for (const [name, detail] of Object.entries(value.subscores)) {
      if (!detail || typeof detail !== "object" || !Array.isArray(detail.components) || typeof detail.evidence_coverage !== "number" || !Number.isFinite(detail.evidence_coverage)) return null;
      const entries = scoreFactors(detail.components);
      if (!entries) return null;
      subscores[name] = { evidence_coverage: detail.evidence_coverage, components: entries };
    }
  }
  return {
    score: value.score,
    evidence_coverage: value.evidence_coverage,
    growth_comparison_inputs: growthInputs,
    components,
    ...(subscores ? { subscores } : {}),
  };
}

/** Send only the data required to compare saved research; full snapshots stay in browser storage. */
export function toSnapshotComparisonPayload(company: SnapshotComparisonInput, savedAt?: string) {
  const sources = company.sources && typeof company.sources === "object" && !Array.isArray(company.sources)
    ? company.sources as Record<string, unknown>
    : {};
  return {
    snapshot_proof: company.snapshot_proof,
    ticker: company.ticker,
    methodology_version: company.methodology_version,
    analysis_schema_version: company.analysis_schema_version,
    sector: company.sector,
    reporting_currency: company.reporting_currency,
    retrieved_at: company.retrieved_at,
    ...(savedAt ? { saved_at: savedAt } : {}),
    score: company.score,
    score_trace: scoreTraceForComparison(company.score_trace),
    business_quality: company.business_quality,
    financial_strength: company.financial_strength,
    market_expectations: company.market_expectations,
    historical_growth: company.historical_growth,
    consensus_growth: company.consensus_growth,
    analyst_estimate_period: company.analyst_estimate_period,
    analyst_estimate_end_date: company.analyst_estimate_end_date ?? null,
    analyst_estimate_currency: company.analyst_estimate_currency ?? null,
    required_growth: company.required_growth,
    revenue: company.revenue,
    revenue_period: company.revenue_period,
    revenue_source: company.revenue_source,
    free_cash_flow: company.free_cash_flow,
    fcf_period: company.fcf_period,
    fcf_source: company.fcf_source,
    enterprise_value: company.enterprise_value,
    model_fcf_margin: company.model_fcf_margin,
    discount_rate: company.discount_rate,
    terminal_growth: company.terminal_growth,
    solver_status: company.solver_status,
    user_assumptions: company.user_assumptions,
    sources: {
      revenue: sourceForComparison(sources.revenue),
      free_cash_flow: sourceForComparison(sources.free_cash_flow),
    },
  };
}

/** Persist only the comparison payload; older full-company records remain readable. */
export function toStoredSnapshot(company: SnapshotComparisonInput & { saved_at: string }) {
  return {
    ...toSnapshotComparisonPayload(company, company.saved_at),
    saved_at: company.saved_at,
  };
}
