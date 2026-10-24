export function hasOidcClientSecret(value: string | undefined): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

export function secureOidcIssuer(value: string, isProduction: boolean): string | null {
  try {
    const parsed = new URL(value);
    const localDevelopment = !isProduction
      && (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1");
    if (parsed.protocol !== "https:" && !localDevelopment) return null;
    if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
    return value;
  } catch {
    return null;
  }
}
