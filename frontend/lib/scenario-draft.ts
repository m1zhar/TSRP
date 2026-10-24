import type { Assumptions } from "./types.ts";

export type ScenarioDraft = Record<keyof Assumptions, string>;
export type ScenarioDraftStorage = Pick<Storage, "getItem" | "setItem"> & Partial<Pick<Storage, "removeItem">>;
export type ScenarioDraftWriteResult = "stored" | "invalid" | "unavailable";

export const SCENARIO_DRAFT_STORAGE_PREFIX = "tsrp.scenario-draft.v3:";
const LEGACY_PUBLIC_DRAFT_PREFIX = `tsrp.scenario-draft.v2:${"0".repeat(32)}:`;

const FIELDS = ["starting_growth", "fcf_margin", "discount_rate", "terminal_growth"] as const;
export const SCENARIO_DRAFT_LIMITS: Record<keyof Assumptions, readonly [number, number]> = {
  starting_growth: [-10, 40],
  fcf_margin: [1, 50],
  discount_rate: [3, 20],
  terminal_growth: [0, 6],
};
const NUMBER_INPUT = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/iu;

function browserSessionStorage(): ScenarioDraftStorage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function storageKey(ticker: string): string | null {
  const normalized = ticker.trim().toUpperCase();
  return /^[A-Z0-9][A-Z0-9.-]{0,14}$/u.test(normalized)
    ? `${SCENARIO_DRAFT_STORAGE_PREFIX}${normalized}`
    : null;
}

function legacyPublicStorageKey(ticker: string): string | null {
  const normalized = ticker.trim().toUpperCase();
  return /^[A-Z0-9][A-Z0-9.-]{0,14}$/u.test(normalized)
    ? `${LEGACY_PUBLIC_DRAFT_PREFIX}${normalized}`
    : null;
}

function validInput(field: keyof Assumptions, value: unknown): value is string {
  if (typeof value !== "string" || value.length > 32) return false;
  const normalized = value.trim();
  if (!normalized) return value === "";
  if (!NUMBER_INPUT.test(normalized)) return false;
  const numeric = Number(normalized);
  const [minimum, maximum] = SCENARIO_DRAFT_LIMITS[field];
  return Number.isFinite(numeric) && numeric >= minimum && numeric <= maximum;
}

function isDraft(value: unknown): value is ScenarioDraft {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return FIELDS.every((field) => validInput(field, record[field]));
}

export function scenarioDraftValidationErrors(draft: ScenarioDraft): Partial<Record<keyof Assumptions, string>> {
  const errors: Partial<Record<keyof Assumptions, string>> = {};
  for (const field of FIELDS) {
    const rawValue: unknown = draft?.[field];
    const value = typeof rawValue === "string" ? rawValue.trim() : "";
    const [minimum, maximum] = SCENARIO_DRAFT_LIMITS[field];
    if (!value || !validInput(field, rawValue)) {
      errors[field] = `Enter a value from ${minimum}% to ${maximum}%.`;
    }
  }
  const discountRate = Number(draft.discount_rate) / 100;
  const terminalGrowth = Number(draft.terminal_growth) / 100;
  if (!errors.discount_rate && !errors.terminal_growth && discountRate <= terminalGrowth) {
    errors.discount_rate = "Must be greater than long-run growth.";
  }
  return errors;
}

export function readScenarioDraft(
  ticker: string,
  fallback: ScenarioDraft,
  storage: ScenarioDraftStorage | null = browserSessionStorage(),
): { draft: ScenarioDraft; storageAvailable: boolean } {
  const key = storageKey(ticker);
  if (!key || !storage) return { draft: fallback, storageAvailable: false };
  try {
    let raw = storage.getItem(key);
    let legacyKey: string | null = null;
    if (raw === null) {
      legacyKey = legacyPublicStorageKey(ticker);
      if (legacyKey) raw = storage.getItem(legacyKey);
    }
    if (raw === null) return { draft: fallback, storageAvailable: true };
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { draft: fallback, storageAvailable: true };
    }
    if (!isDraft(parsed)) return { draft: fallback, storageAvailable: true };
    const draft = Object.fromEntries(FIELDS.map((field) => [field, parsed[field]])) as ScenarioDraft;
    if (legacyKey) {
      try {
        storage.setItem(key, JSON.stringify(draft));
      } catch {
        return { draft, storageAvailable: false };
      }
      try {
        storage.removeItem?.(legacyKey);
      } catch {
        // The migrated draft is already saved; privacy controls can remove a leftover legacy key.
      }
    }
    return { draft, storageAvailable: true };
  } catch {
    return { draft: fallback, storageAvailable: false };
  }
}

export function writeScenarioDraft(
  ticker: string,
  draft: ScenarioDraft,
  storage: ScenarioDraftStorage | null = browserSessionStorage(),
): ScenarioDraftWriteResult {
  const key = storageKey(ticker);
  if (!key || !isDraft(draft)) return "invalid";
  if (!storage) return "unavailable";
  try {
    storage.setItem(key, JSON.stringify(Object.fromEntries(FIELDS.map((field) => [field, draft[field]]))));
    return "stored";
  } catch {
    return "unavailable";
  }
}
