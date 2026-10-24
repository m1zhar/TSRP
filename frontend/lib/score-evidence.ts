import { money, pct } from "./format.ts";
import type { Company } from "./types.ts";

export type ScoreFactorEvidence = { observed: string; sourceKeys: string[] };

/** Connects each score factor to the observed company value and provenance shown in the API. */
export function scoreFactorEvidence(company: Company, key: string): ScoreFactorEvidence {
  switch (key) {
    case "growth":
      return {
        observed: `${pct(company.historical_growth)}${company.historical_growth_years > 0 ? ` · ${company.historical_growth_years.toFixed(1)} years` : ""}`,
        sourceKeys: ["historical_growth"],
      };
    case "gross":
      return { observed: pct(company.gross_margin), sourceKeys: ["gross_margin"] };
    case "operating":
      return { observed: pct(company.operating_margin), sourceKeys: ["operating_margin"] };
    case "fcf":
      return { observed: pct(company.fcf_margin), sourceKeys: ["free_cash_flow_margin"] };
    case "roe":
      return { observed: pct(company.return_on_equity), sourceKeys: ["return_on_equity"] };
    case "balance":
    case "leverage":
      return { observed: pct(company.debt_to_assets), sourceKeys: ["debt_to_assets"] };
    case "cash_debt":
      return {
        observed: `${money(company.cash, company.reporting_currency, true)} cash · ${money(company.debt, company.reporting_currency, true)} debt`,
        sourceKeys: ["cash", "debt"],
      };
    default:
      return { observed: "N/A", sourceKeys: [] };
  }
}
