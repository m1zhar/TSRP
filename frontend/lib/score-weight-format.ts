export function formatScoreWeight(weight: number): string {
  if (!Number.isFinite(weight)) return "N/A";
  return `${(weight * 100).toFixed(1)}%`;
}
