export type TickerScopedError = {
  ticker: string;
  message: string;
  code?: string;
};

export function analysisErrorForTicker(
  error: TickerScopedError | null,
  ticker: string,
): string {
  return error?.ticker === ticker ? error.message : "";
}

export function analysisErrorCanRetry(code?: string): boolean {
  return !new Set([
    "provider_disabled", "rate_limited", "auth_not_configured", "origin_not_configured",
    "origin_not_allowed", "invalid_ticker", "unsupported_asset", "unsupported_sector", "unsupported_currency",
  ]).has(code ?? "");
}
