import { number } from "./format.ts";
import type { FxConversion, SourceFact } from "./types.ts";

const conversionRateFormat = new Intl.NumberFormat("en-US", {
  maximumSignificantDigits: 8,
  useGrouping: false,
});

export function currencyConversionLine(item: FxConversion, observation: string): string {
  const fixedUnitScale = item.period === "Fixed quote-subunit conversion";
  const method = fixedUnitScale ? "Exact unit scale" : "FX conversion";
  const detail = fixedUnitScale ? "fixed quote-subunit ratio" : observation;
  const rate = item.rate === null ? "N/A" : conversionRateFormat.format(item.rate);
  return `${method} ${item.from} → ${item.to}: ${rate} · ${detail}`;
}

export function fxSourceLine(conversions: FxConversion[], kind: string): string {
  const visibleConversions = conversions.filter((item) => item.period !== "Same-currency identity");
  const unavailable = kind.toLowerCase().includes("unavailable");
  if (visibleConversions.length === 0) {
    return unavailable ? "A required currency conversion is unavailable" : "No currency conversion required";
  }
  const details = visibleConversions.map((item) => currencyConversionLine(item, item.period ?? "observation time unavailable")).join("; ");
  return unavailable ? `${details}; some requested conversions are unavailable` : details;
}

export function sourceComponentsLine(components: SourceFact[]): string {
  if (components.length === 0) return "";
  const facts = components.map((item) => {
    const label = item.tag?.replace(/([a-z])([A-Z])/gu, "$1 $2") ?? "Reported component";
    const taxonomy = item.taxonomy ? `${item.taxonomy}: ` : "";
    const amount = item.value === null ? "N/A" : `${number(item.value)}${item.source_unit ? ` ${item.source_unit}` : ""}`;
    const filing = item.filing_form || item.filing_date
      ? ` · ${item.filing_form ?? "filing"}${item.filing_date ? ` filed ${item.filing_date}` : ""}`
      : "";
    return `${taxonomy}${label}: ${amount} · ${item.period ?? "period unavailable"}${filing}`;
  });
  return ` · Components: ${facts.join("; ")}`;
}
