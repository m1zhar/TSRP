export type CompanySearchMatch = { ticker: string };
export type CompanyLookupState = "idle" | "pending" | "loading" | "done" | "failed";
export type CompanySearchDecision =
  | { kind: "select"; ticker: string }
  | { kind: "wait" }
  | { kind: "confirm-ticker"; ticker: string }
  | { kind: "invalid" };

const TICKER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9.-]{0,14}$/;
const EMAIL_PATTERN = /[^\s@]+@[^\s@]+\.[^\s@]+/u;
const PERSONAL_NUMBER_PATTERN = /(?<![\p{L}\p{N}])\+?\p{Number}[\p{Number}\s()./‐‑‒–—―−﹘﹣－-]{7,}\p{Number}(?![\p{L}\p{N}])/gu;

function decodePercentEscapes(value: string): string {
  return value.replace(/(?:%[\da-f]{2})+/giu, (escapedBytes) => {
    const bytes = (escapedBytes.match(/[\da-f]{2}/giu) ?? []).map((byte) => Number.parseInt(byte, 16));
    return new TextDecoder().decode(Uint8Array.from(bytes));
  });
}

function normalizePersonalSearchText(value: string): string {
  let decoded = value;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const nextValue = decodePercentEscapes(decoded);
    if (nextValue === decoded) break;
    decoded = nextValue;
  }
  return decoded.normalize("NFKC").replace(/\p{Cf}/gu, "").replace(/[\u3002\uFF61]/gu, ".");
}

/** Avoid sending common contact/identifier patterns through the company-search provider. */
export function containsPersonalSearchText(value: string): boolean {
  const normalized = normalizePersonalSearchText(value);
  if (EMAIL_PATTERN.test(normalized)) return true;
  for (const match of normalized.matchAll(PERSONAL_NUMBER_PATTERN)) {
    const digits = match[0].match(/\p{Number}/gu)?.length ?? 0;
    if (digits >= 9 && digits <= 19) return true;
  }
  return false;
}

export const PERSONAL_SEARCH_MESSAGE = "Remove email, phone, or long identification numbers from company search.";
export const COMPANY_SEARCH_DISABLED_MESSAGE = "Company search and stock analysis are not enabled for this deployment. Contact its operator.";

export function canConfirmTickerAfterSearchFailure(errorCode?: string): boolean {
  return errorCode !== "provider_disabled" && errorCode !== "rate_limited";
}

/** Prefer a resolved company match over guessing that mixed/lowercase text is a ticker. */
export function decideCompanySearch(
  query: string,
  matches: CompanySearchMatch[],
  state: CompanyLookupState,
): CompanySearchDecision {
  const value = query.trim();
  if (!value) return { kind: "invalid" };

  const exact = matches.find((match) => match.ticker.toUpperCase() === value.toUpperCase());
  if (exact) return { kind: "select", ticker: exact.ticker };
  if (TICKER_PATTERN.test(value) && value === value.toUpperCase()) {
    return { kind: "select", ticker: value };
  }
  if (state === "pending" || state === "loading") return { kind: "wait" };
  if (state === "done" && matches.length > 0) return { kind: "select", ticker: matches[0].ticker };
  if (TICKER_PATTERN.test(value)) return { kind: "confirm-ticker", ticker: value.toUpperCase() };
  return { kind: "invalid" };
}
