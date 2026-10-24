export const AUTH_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60;
const AUTH_SESSION_CLOCK_SKEW_SECONDS = 30;

export function isOpaqueBrowserStorageScope(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{32}$/u.test(value);
}

export function minimalAuthToken(
  token: Record<string, unknown>,
  profile: { sub?: unknown; [claim: string]: unknown } | undefined,
  now = Math.floor(Date.now() / 1000),
  browserStorageScope?: string | null,
): { authenticated: true; authTime: number; browserStorageScope: string } | null {
  if (!Number.isSafeInteger(now) || now < 0) return null;

  let authTime: unknown;
  if (profile !== undefined) {
    if (typeof profile.sub !== "string" || profile.sub.length === 0 || profile.sub.length > 512) return null;
    authTime = now;
  } else if (token.authenticated === true) {
    authTime = token.authTime;
  }

  if (!Number.isSafeInteger(authTime)
    || (authTime as number) > now + AUTH_SESSION_CLOCK_SKEW_SECONDS
    || now - (authTime as number) >= AUTH_SESSION_MAX_AGE_SECONDS) return null;
  const scope = profile === undefined ? token.browserStorageScope : browserStorageScope;
  if (!isOpaqueBrowserStorageScope(scope)) return null;
  return { authenticated: true, authTime: authTime as number, browserStorageScope: scope };
}

export function minimalClientSession(expires: string, authenticated: boolean, browserStorageScope?: unknown) {
  return {
    expires,
    user: authenticated && isOpaqueBrowserStorageScope(browserStorageScope)
      ? { authenticated: true as const, browserStorageScope }
      : undefined,
  };
}
