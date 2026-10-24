export function finite(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function pct(value: number | null | undefined, digits = 1): string {
  return finite(value) && Number.isFinite(value * 100) ? `${(value * 100).toFixed(digits)}%` : "N/A";
}

export function money(value: number | null | undefined, currency: string, compact = false): string {
  if (!finite(value) || typeof currency !== "string") return "N/A";
  if (["GBp", "GBX", "ZAc", "ILA"].includes(currency)) {
    const formatted = new Intl.NumberFormat("en-US", {
      notation: compact ? "compact" : "standard",
      maximumFractionDigits: 2,
    }).format(value);
    return `${formatted} ${currency}`;
  }
  if (!/^[A-Z]{3}$/.test(currency)) return "N/A";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      notation: compact ? "compact" : "standard",
      // Compact currency can inherit different currency minor-unit defaults across ICU versions.
      minimumFractionDigits: compact ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${currency} ${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)}`;
  }
}

export function number(value: number | null | undefined, compact = false): string {
  if (!finite(value)) return "N/A";
  return new Intl.NumberFormat("en-US", {
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 2 : 1,
  }).format(value);
}

export function dateLabel(value: string | null | undefined, includeTime = false): string {
  if (!value) return "Not available";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    ...(includeTime ? { timeStyle: "short" as const } : {}),
    timeZone: "UTC",
  }).format(date);
}

export function safeLabel(value: string | null | undefined): string {
  return value?.trim() ? value : "Unavailable";
}

export function methodologyVersionLabel(value: string | null | undefined): string {
  const match = typeof value === "string" ? /^score-v([1-9]\d*)$/iu.exec(value.trim()) : null;
  return match ? `Methodology v${match[1]}` : "TSRP methodology";
}
