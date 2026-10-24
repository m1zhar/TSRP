export type BalanceSourceField = "cash" | "debt";

const CURRENT_ESTIMATE_PERIOD = "Current-year analyst revenue estimate";
const NEXT_ESTIMATE_PERIOD = "Next-year analyst revenue estimate";

export function analystEstimateDirection(growth: number | null | undefined): "growth" | "contraction" | "unchanged" | "unavailable" {
  if (typeof growth !== "number" || !Number.isFinite(growth)) return "unavailable";
  if (growth > 0) return "growth";
  if (growth < 0) return "contraction";
  return "unchanged";
}

export function analystEstimateLabel(period: string | null | undefined): string {
  if (period === CURRENT_ESTIMATE_PERIOD || period === NEXT_ESTIMATE_PERIOD) return period;
  return "Analyst revenue estimate";
}

export function analystEstimateShortLabel(period: string | null | undefined): string {
  if (period === CURRENT_ESTIMATE_PERIOD) return "Current-year analyst estimate";
  if (period === NEXT_ESTIMATE_PERIOD) return "Next-year analyst estimate";
  return "Analyst estimate";
}

export function analystEstimateHorizon(period: string | null | undefined): string {
  if (period === CURRENT_ESTIMATE_PERIOD) return "current fiscal year";
  if (period === NEXT_ESTIMATE_PERIOD) return "next fiscal year";
  return "an unspecified fiscal period";
}

export function analystEstimateDescription(period: string | null | undefined, available: boolean): string {
  if (!available) return "Yahoo Finance does not provide a usable current- or next-year analyst revenue estimate.";
  if (period === CURRENT_ESTIMATE_PERIOD) return "Yahoo Finance provider-supplied estimate for the current fiscal year; not company guidance or reported performance.";
  if (period === NEXT_ESTIMATE_PERIOD) return "Yahoo Finance provider-supplied estimate for the next fiscal year; not company guidance or reported performance.";
  return "Yahoo Finance provider-supplied analyst revenue estimate; its forecast period is unavailable, so interpret it cautiously.";
}

export function analystEstimateSignal(
  growth: number | null | undefined,
  period: string | null | undefined,
): { effect: "support" | "risk"; title: string; description: string } | null {
  const direction = analystEstimateDirection(growth);
  if (direction !== "growth" && direction !== "contraction") return null;
  const horizon = analystEstimateHorizon(period);
  const value = `${((growth ?? 0) * 100).toFixed(1)}%`;
  const forecastPeriod = horizon === "an unspecified fiscal period" ? "" : ` for the ${horizon}`;
  const unavailablePeriodNote = horizon === "an unspecified fiscal period" ? ", but its forecast period is unavailable" : "";
  if (direction === "contraction") {
    return {
      effect: "risk",
      title: "Analyst estimate signals revenue contraction",
      description: `Yahoo Finance's analyst estimate is ${value} revenue growth${forecastPeriod}${unavailablePeriodNote}. Negative growth implies lower revenue for that period; this is an estimate, not company guidance.`,
    };
  }
  return {
    effect: "support",
    title: analystEstimateLabel(period),
    description: `Yahoo Finance's analyst estimate is ${value} revenue growth${forecastPeriod}${unavailablePeriodNote}. This is an estimate, not company guidance.`,
  };
}

export function missingBalanceSource(
  cash: number | null,
  debt: number | null,
  sourceFor: (field: BalanceSourceField) => string,
): string {
  const missing: string[] = [];
  if (cash === null) missing.push(`Cash · ${sourceFor("cash")}`);
  if (debt === null) missing.push(`Debt · ${sourceFor("debt")}`);
  return missing.length ? missing.join("; ") : "Source unavailable";
}
