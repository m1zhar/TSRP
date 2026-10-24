import { finite, pct } from "./format.ts";
import { scoreFactorEvidence } from "./score-evidence.ts";
import type { Company, ScoreFactor } from "./types.ts";

export const SCORE_VALIDATION_NOTE = "Experimental research signal. Formula checks do not establish predictive accuracy; the score has no independent validation or historical return backtest.";

export function plainFinancialReason(reason: string): string {
  const labels: Record<string, string> = {
    working_capital_change: "working-capital cash movement", capex: "capital spending",
    opening_debt: "debt at the previous year-end", interest_expense: "interest expense",
    pretax_income: "profit before tax", tax_expense: "tax expense", equity_value: "market value of equity",
  };
  return reason.replace(/\b(?:working_capital_change|capex|opening_debt|interest_expense|pretax_income|tax_expense|equity_value)\b/g, (key) => labels[key]);
}

export function sectorResearchGuide(c: Pick<Company, "sector" | "sector_methodology_supported">): string {
  if (c.sector === "Financial Services") return "Banks and insurers need equity-focused analysis: deposits, regulatory capital and lending risk do not fit an industrial cash-flow model. TSRP shows company facts but does not calculate a sector score or valuation. Review capital adequacy, credit losses and returns on equity in the filings.";
  if (!c.sector_methodology_supported) return "No score or valuation framework is configured for this sector. Reported facts remain usable; consult sector-specific disclosures rather than applying another sector’s assumptions.";
  switch (c.sector) {
    case "Technology": return "Technology: check whether growth converts into cash after investment and share-based compensation. The score does not measure customer retention, competitive durability or future dilution.";
    case "Communication Services": return "Communication services: subscription, advertising and network businesses have different economics. Check the business mix and investment needs; a sector label is not a peer comparison.";
    case "Energy":
    case "Basic Materials": return "Commodity-sensitive businesses: one year can reflect a price-cycle peak or trough. Compare cash flow across a full cycle and inspect replacement investment; TSRP does not normalize commodity prices or model reserves.";
    case "Real Estate": return "Real estate: property values, occupancy and recurring maintenance matter. For a real-estate investment trust, inspect funds from operations and net asset value separately. TSRP does not calculate those measures; its generic cash-flow illustration is not a property valuation.";
    case "Utilities": return "Utilities: regulation, allowed returns and long investment cycles can dominate value. TSRP does not model rate cases or distinguish maintenance from expansion spending.";
    case "Healthcare": return "Healthcare: research spending, patents and regulatory decisions can change future cash flow. TSRP does not model clinical outcomes, patent expiries or product-level probabilities.";
    case "Consumer Cyclical": return "Consumer cyclical: check demand through an economic cycle, leases and inventory. Recent growth and margins may not represent normal conditions.";
    case "Consumer Defensive": return "Consumer defensive: check volume versus price increases and whether margins persist. TSRP does not separate those growth drivers or establish pricing power.";
    case "Industrials": return "Industrials: inspect order cycles, working capital and maintenance investment. A single annual period may not represent cash generation across a full cycle.";
    default: return "Sector thresholds are research-policy choices, not measured peer averages. Check the company’s business mix and accounting before drawing comparisons.";
  }
}

export function valuationAvailabilityReason(c: Company): string {
  if (!c.sector_methodology_supported) return "No suitable sector valuation is configured. Company facts remain available; retrying will not add a missing methodology.";
  if (!(c.currency_methodology_supported ?? (c.rate_currency !== "N/A" && c.rate_currency === c.reporting_currency))) return `No baseline discount-rate model is configured for ${c.reporting_currency}. TSRP does not substitute a USD rate. Reported financial evidence remains available.`;
  if (c.is_stale) return "The market quote is missing a usable timestamp or is outside the freshness window. Refresh once; if it remains unavailable, verify the quote with the source. Reported annual evidence is separate.";
  if (!finite(c.enterprise_value)) return "A comparable market value, cash balance or debt figure is unavailable. Check their source dates and currencies below. Refresh may help a temporary provider failure; missing filings or failed currency conversion cannot be filled with assumptions.";
  if (!finite(c.model_fcf_margin) || c.model_fcf_refused) return c.valuation_basis?.cash_flow.reason
    ? `No defensible starting cash-flow margin: ${plainFinancialReason(c.valuation_basis.cash_flow.reason)} Reported cash flow remains separate; no positive substitute is invented.`
    : "Positive, period-aligned pre-debt cash flow is unavailable. Negative cash flow is not replaced with a profitable margin; reported results remain visible.";
  if (!finite(c.discount_rate)) return c.valuation_basis?.capital_cost.reason
    ? `The baseline discount rate is unavailable: ${plainFinancialReason(c.valuation_basis.capital_cost.reason)} An explicit user scenario, when available, is an assumption rather than a recovered market fact.`
    : "The baseline discount rate lacks required financing inputs. Reported financial evidence remains usable; an explicit user scenario is an assumption, not a recovered market fact.";
  return "The supplied inputs do not support a solution inside the model’s requirements. Inspect the cash-flow bridge, discount rate and terminal-growth assumptions; do not interpret N/A as zero value.";
}

export function scoreAvailabilityReason(c: Company): string {
  if (!c.sector_methodology_supported) return "No sector score is configured. This is a methodology limitation, not a judgment that the company is weak; reported company facts remain available.";
  const trace = c.score_trace;
  if (!trace) {
    const missing = [
      !finite(c.historical_growth) && "historical revenue growth",
      !finite(c.operating_margin) && "operating margin",
      !finite(c.return_on_equity) && "return on average equity",
      (!finite(c.cash) || !finite(c.debt)) && "cash relative to debt",
      !finite(c.fcf_margin) && "free-cash-flow margin",
      !finite(c.debt_to_assets) && "debt to assets",
    ].filter(Boolean);
    const gaps = missing.length ? ` Unavailable displayed inputs: ${missing.join(", ")}.` : "";
    return `Usable evidence did not meet the score’s coverage rules.${gaps} Reported facts may still be usable; check their sources and periods. A refresh only helps if the failure is temporary.`;
  }
  const missing = Object.values(trace.subscores ?? {}).flatMap((group) => group.components).filter((factor) => !finite(factor.value)).map((factor) => factor.label.toLowerCase());
  const gaps = missing.length ? ` Missing score inputs: ${[...new Set(missing)].join(", ")}.` : "";
  return `Usable evidence covers ${Math.round(trace.evidence_coverage * 100)}% of factor weight; both evidence coverage and supported score-group weight must reach ${Math.round(trace.minimum_evidence_coverage * 100)}%.${gaps} Available facts remain visible; absent evidence is not scored as zero.`;
}

export function metricAvailabilityReason(c: Company, key: "share_price" | "market_cap_reporting" | "enterprise_value" | "revenue"): string {
  if (key === "share_price") return "No usable listing price was returned. Refresh once or check the exchange/provider; this does not make the reported annual results unavailable.";
  if (key === "revenue") return "No usable annual revenue was returned for the reporting period. Check the company filing; TSRP does not replace it with an estimate or zero.";
  if (key === "enterprise_value" && !c.sector_methodology_supported) return "Enterprise value is withheld because the sector valuation framework is not supported. The listed share price and reported facts remain separate.";
  if (key === "market_cap_reporting") return "The market value or its currency conversion could not be verified. An unconverted value is not relabeled; check the listing and reporting currencies in Sources.";
  return "Market capitalization, cash and debt must be available on a comparable basis to calculate enterprise value. Inspect their dates and currencies in Sources.";
}

type BriefFactor = ScoreFactor & { observed: string; sourceKeys: string[] };

/** Describes server-returned factor scores; never calculates or changes the score. */
export function buildResearchBrief(c: Company): { conclusion: string; supports: BriefFactor[]; watch: BriefFactor | null; uncertainty: string } {
  const available = c.score_available && finite(c.score);
  const factors = c.sector_methodology_supported ? Object.values(c.score_trace?.subscores ?? {})
    .flatMap((group) => group.components)
    .filter((factor) => finite(factor.value) && factor.weight > 0)
    .map((factor) => ({ ...factor, ...scoreFactorEvidence(c, factor.key) }))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || a.key.localeCompare(b.key)) : [];
  const unique = factors.filter((factor, index) => factors.findIndex((other) => other.key === factor.key) === index);
  const supports = available ? unique.slice(0, 2) : [];
  const last = unique.at(-1);
  // A tied factor is not called a relative weakness, and never appears twice.
  const watch = available && last && !supports.some((factor) => factor.key === last.key) && last.value !== unique[0]?.value ? last : null;
  const conclusion = available
    ? `${c.score_label} in the available operating evidence. This does not establish whether the shares are cheap or predict their return.`
    : scoreAvailabilityReason(c);
  const uncertainty = !available ? "The missing score is not a negative rating. Review the available facts and sector limitations below."
    : c.is_stale ? "The market quote is not current enough for price-based conclusions. The operating score uses separately dated financial evidence."
    : (c.score_trace?.evidence_coverage ?? 0) < 1 ? "Some score inputs are unavailable. Remaining factors have more influence; do not directly compare scores with different coverage."
    : c.confidence === "Low" ? "Data confidence is low. Verify the financial periods and source warnings before relying on the score."
    : c.solver_status === "unavailable" ? valuationAvailabilityReason(c)
    : "Valuation is assumption-dependent. A high operating score can coexist with demanding expectations; examine the growth requirement and cash-flow assumptions separately.";
  return { conclusion, supports, watch: watch ?? null, uncertainty };
}

export function analystAvailabilityReason(c: Company): string {
  return finite(c.consensus_growth) ? `Provider estimate${c.analyst_estimate_end_date ? ` for the fiscal year ending ${c.analyst_estimate_end_date}` : "; fiscal end date unavailable"}${c.analyst_estimate_currency ? ` (${c.analyst_estimate_currency})` : "; estimate currency unavailable"}. Not reported performance or company guidance.`
    : "No usable current- or next-year analyst revenue estimate was returned. Reported growth remains separate; retry only if the provider failure is temporary. Missing estimates do not mean zero growth.";
}

export function modelGrowthLabel(c: Company): string {
  return c.solver_status === "exact" && finite(c.required_growth) ? pct(c.required_growth)
    : c.solver_status === "above_range" ? "Above range"
    : c.solver_status === "below_range" ? "Below range" : "N/A";
}
