export type SnapshotChange = { before: unknown; after: unknown; delta: number | null };

export function snapshotChangeStatus({ before, after, delta }: SnapshotChange): "Added" | "Removed" | "Updated" | null {
  if (before === null || before === undefined) return after === null || after === undefined ? "Updated" : "Added";
  if (after === null || after === undefined) return "Removed";
  return delta === null ? "Updated" : null;
}

export function snapshotValueFallback(field: string, value: unknown): string | null {
  if (value !== null && value !== undefined) return null;
  return field.startsWith("user_") ? "Not saved" : "N/A";
}
