import type { Company, Snapshot } from "./types";

export const SNAPSHOT_STORAGE_KEY = "tsrp.research-snapshots.v1";
export const ACCOUNT_SNAPSHOT_STORAGE_PREFIX = `${SNAPSHOT_STORAGE_KEY}:account:`;
export const LEGACY_PUBLIC_SNAPSHOT_STORAGE_KEY = `${ACCOUNT_SNAPSHOT_STORAGE_PREFIX}${"0".repeat(32)}`;
export const MAX_SNAPSHOTS_PER_TICKER = 8;

export type StoredSnapshot = Pick<Company, "ticker"> & Omit<Partial<Company>, "score_trace" | "sources">
  & { score_trace?: unknown; sources?: unknown; saved_at?: string };
export type SnapshotStoreIssue = "corrupt" | "invalid" | "quota" | "unavailable";
export type SnapshotStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const NUMERIC_FIELDS: (keyof Company)[] = [
  "price", "previous_close", "market_cap", "market_cap_reporting", "enterprise_value", "revenue",
  "free_cash_flow", "fcf_margin", "model_fcf_margin", "discount_rate", "terminal_growth", "risk_free",
  "equity_risk_premium", "sector_spread", "historical_growth", "historical_growth_years",
  "historical_growth_3y", "consensus_growth", "target_mean_price", "analyst_count", "required_growth",
  "solver_low", "solver_high", "business_quality", "financial_strength", "market_expectations", "score",
  "gross_margin", "operating_margin", "profit_margin", "return_on_equity", "cash", "debt", "assets",
  "equity", "debt_to_assets", "ev_sales", "ev_ebitda", "pe",
];

export class SnapshotStoreError extends Error {
  readonly issue: SnapshotStoreIssue;

  constructor(issue: SnapshotStoreIssue, message: string) {
    super(message);
    this.name = "SnapshotStoreError";
    this.issue = issue;
  }
}

function browserStorage(): SnapshotStorage {
  try {
    return window.localStorage;
  } catch {
    throw new SnapshotStoreError("unavailable", "Browser storage is unavailable.");
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function hasSafeTicker(value: unknown): value is StoredSnapshot {
  if (!isObject(value) || typeof value.ticker !== "string" || !/^[A-Z0-9][A-Z0-9.-]{0,14}$/i.test(value.ticker)) return false;
  for (const field of NUMERIC_FIELDS) {
    const candidate = value[field];
    if (candidate !== undefined && candidate !== null && (typeof candidate !== "number" || !Number.isFinite(candidate))) return false;
  }
  for (const field of ["saved_at", "retrieved_at"] as const) {
    const candidate = value[field];
    if (candidate !== undefined && candidate !== null && (typeof candidate !== "string" || !Number.isFinite(Date.parse(candidate)))) return false;
  }
  return true;
}

function classifyStorageError(error: unknown): SnapshotStoreError {
  const name = isObject(error) && typeof error.name === "string" ? error.name : "";
  return name === "QuotaExceededError"
    ? new SnapshotStoreError("quota", "Browser storage is full. Remove older TSRP snapshots and try again.")
    : new SnapshotStoreError("unavailable", "Browser storage is unavailable.");
}

export function readSnapshotStore(storage: SnapshotStorage = browserStorage(), key = SNAPSHOT_STORAGE_KEY): StoredSnapshot[] {
  let raw: string | null;
  try {
    raw = storage.getItem(key);
    if (raw === null && key === SNAPSHOT_STORAGE_KEY) raw = storage.getItem(LEGACY_PUBLIC_SNAPSHOT_STORAGE_KEY);
  } catch (error) {
    throw classifyStorageError(error);
  }
  if (!raw) return [];

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new SnapshotStoreError("corrupt", "Saved research data is malformed and has not been overwritten.");
  }
  if (!Array.isArray(value) || !value.every(hasSafeTicker)) {
    throw new SnapshotStoreError("corrupt", "Saved research data is malformed and has not been overwritten.");
  }
  return value as StoredSnapshot[];
}

export function readSnapshots(ticker: string, storage: SnapshotStorage = browserStorage(), key = SNAPSHOT_STORAGE_KEY): StoredSnapshot[] {
  return readSnapshotStore(storage, key).filter((item) => item.ticker.toUpperCase() === ticker.toUpperCase());
}

export function writeSnapshots(
  ticker: string,
  values: Array<StoredSnapshot | Snapshot>,
  storage: SnapshotStorage = browserStorage(),
  key = SNAPSHOT_STORAGE_KEY,
): StoredSnapshot[] {
  const normalizedTicker = typeof ticker === "string" ? ticker.trim().toUpperCase() : "";
  if (!/^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(normalizedTicker)
    || !Array.isArray(values)
    || values.some((item) => !hasSafeTicker(item) || item.ticker.toUpperCase() !== normalizedTicker)) {
    throw new SnapshotStoreError("invalid", "Saved research does not match the selected company.");
  }
  const all = readSnapshotStore(storage, key).filter((item) => item.ticker.toUpperCase() !== normalizedTicker);
  const next = [...all, ...values.slice(-MAX_SNAPSHOTS_PER_TICKER)];
  try {
    storage.setItem(key, JSON.stringify(next));
  } catch (error) {
    throw classifyStorageError(error);
  }
  if (key === SNAPSHOT_STORAGE_KEY) {
    try {
      storage.removeItem(LEGACY_PUBLIC_SNAPSHOT_STORAGE_KEY);
    } catch {
      // The canonical copy is already saved; a leftover legacy copy is harmless and removable via privacy controls.
    }
  }
  return next;
}

export function clearSnapshotStore(storage: SnapshotStorage = browserStorage(), key = SNAPSHOT_STORAGE_KEY): void {
  try {
    storage.removeItem(key);
    if (key === SNAPSHOT_STORAGE_KEY) storage.removeItem(LEGACY_PUBLIC_SNAPSHOT_STORAGE_KEY);
  } catch (error) {
    throw classifyStorageError(error);
  }
}
