import type { PricePoint } from "./types.ts";

const DAY_MS = 24 * 60 * 60 * 1000;

/** Keep the plotted observations inside the selected window; never bridge it with older quotes. */
export function pricePointsInRange(points: readonly PricePoint[], days: number): PricePoint[] {
  if (!Number.isInteger(days) || days <= 0 || points.length === 0) return [];
  const latestTime = Date.parse(points[points.length - 1].date);
  if (!Number.isFinite(latestTime)) return [];
  const cutoff = latestTime - days * DAY_MS;
  return points.filter((point) => {
    const time = Date.parse(point.date);
    return Number.isFinite(time) && time >= cutoff && time <= latestTime;
  });
}
