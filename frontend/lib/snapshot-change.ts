export type SnapshotChange = { before: unknown; after: unknown; delta: number | null };

const RATE_FIELDS = new Set([
  "historical_growth", "consensus_growth", "required_growth", "model_fcf_margin",
  "discount_rate", "terminal_growth", "user_starting_growth", "user_fcf_margin",
  "user_discount_rate", "user_terminal_growth",
]);

export function snapshotComparisonReason(reason: string | undefined): string {
  const messages: Record<string, string> = {
    snapshot_unverified: "At least one saved analysis could not be verified as unaltered TSRP research data.",
    methodology_mismatch: "The saved research uses a different TSRP methodology.",
    schema_mismatch: "The saved research uses a different analysis format.",
    currency_mismatch: "The analyses use different reporting currencies.",
    snapshot_schema_invalid: "A saved analysis is incomplete or malformed.",
    snapshot_order_invalid: "The saved analyses are in reverse time order. Refresh the current research before comparing.",
  };
  return reason && Object.hasOwn(messages, reason) ? messages[reason] : "The saved research is missing metadata required for a safe comparison.";
}

/** A difference between rates is measured in percentage points, not percent. */
export function snapshotRateDelta(field: string, delta: number | null): string | null {
  if (!RATE_FIELDS.has(field) || delta === null) return null;
  const points = delta * 100;
  if (!Number.isFinite(points)) return "N/A";
  if (points !== 0 && Math.abs(points) < .05) return `${points > 0 ? "Increase" : "Decrease"} <0.1 percentage points`;
  return `${points > 0 ? "+" : points < 0 ? "−" : ""}${Math.abs(points).toFixed(1)} percentage points`;
}

export function snapshotChangeStatus({ before, after, delta }: SnapshotChange): "Added" | "Removed" | "Updated" | null {
  if (before === null || before === undefined) return after === null || after === undefined ? "Updated" : "Added";
  if (after === null || after === undefined) return "Removed";
  return delta === null ? "Updated" : null;
}

export function snapshotValueFallback(field: string, value: unknown): string | null {
  if (value !== null && value !== undefined) return null;
  return field.startsWith("user_") ? "Not saved" : "N/A";
}
