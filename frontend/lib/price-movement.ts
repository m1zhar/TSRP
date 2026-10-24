/** Price-only change, not total shareholder return. Never label zero as a gain. */
export function priceMovementDescription(first: number | undefined, last: number | undefined, observations: number): string {
  if (observations < 2 || first === undefined || last === undefined
    || !Number.isFinite(first) || !Number.isFinite(last) || first <= 0 || last <= 0) return "Change unavailable";
  const change = (last - first) / Math.abs(first) * 100;
  if (!Number.isFinite(change)) return "Change unavailable: price movement exceeds the supported numeric range";
  if (change === 0) return "Unchanged (0.0%) over this displayed period";
  const direction = change > 0 ? "Increased" : "Decreased";
  return `${direction} ${Math.abs(change) < .05 ? "less than 0.1%" : `${Math.abs(change).toFixed(1)}%`} over this displayed period`;
}
