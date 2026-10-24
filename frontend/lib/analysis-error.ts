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
  return code !== "provider_disabled" && code !== "rate_limited";
}
