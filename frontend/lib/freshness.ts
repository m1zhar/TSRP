export const MARKET_DATA_MAX_AGE_MS = 48 * 60 * 60 * 1000;
export const MARKET_DATA_FUTURE_TOLERANCE_MS = 5 * 60 * 1000;

export type ServerClockAnchor = { serverEpochMs: number; monotonicMs: number };

export function createServerClockAnchor(dateHeader: string | null, monotonicMs: number): ServerClockAnchor | null {
  const serverEpochMs = typeof dateHeader === "string" ? Date.parse(dateHeader) : Number.NaN;
  if (!Number.isFinite(serverEpochMs) || !Number.isFinite(monotonicMs)) return null;
  return { serverEpochMs, monotonicMs };
}

export function currentClockMs(
  anchor: ServerClockAnchor | null,
  wallClockMs = Date.now(),
  monotonicMs = performance.now(),
): number {
  if (!anchor || !Number.isFinite(monotonicMs)) return wallClockMs;
  return anchor.serverEpochMs + Math.max(0, monotonicMs - anchor.monotonicMs);
}

export function isMarketDataStale(marketAt: string | null | undefined, now = Date.now()): boolean {
  const timestamp = typeof marketAt === "string" && marketAt.trim() ? Date.parse(marketAt) : Number.NaN;
  if (!Number.isFinite(timestamp) || !Number.isFinite(now)) return true;
  const age = now - timestamp;
  return age > MARKET_DATA_MAX_AGE_MS || age < -MARKET_DATA_FUTURE_TOLERANCE_MS;
}
