import { SECTIONS, type SectionName } from "./types.ts";

export function sectionFromQuery(value: string | null): SectionName {
  return SECTIONS.includes(value as SectionName) ? value as SectionName : "TSRP Score";
}

export function researchUrl(ticker: string | null, section: SectionName): string {
  const params = new URLSearchParams();
  const normalizedTicker = ticker?.trim().toUpperCase();
  if (normalizedTicker) params.set("ticker", normalizedTicker);
  if (section !== "TSRP Score") params.set("section", section);
  const query = params.toString();
  return query ? `/?${query}` : "/";
}
