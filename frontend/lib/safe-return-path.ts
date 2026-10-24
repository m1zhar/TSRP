const RETURN_ORIGIN = "https://tsrp.invalid";

export function safeReturnPath(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/";
  try {
    const parsed = new URL(value, RETURN_ORIGIN);
    return parsed.origin === RETURN_ORIGIN ? `${parsed.pathname}${parsed.search}${parsed.hash}` : "/";
  } catch {
    return "/";
  }
}
