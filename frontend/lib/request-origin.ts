const AUTHORITY_PATTERN = /^(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*)(?::([0-9]{1,5}))?$/iu;

function secureOrigin(url: URL, isProduction: boolean): string | null {
  if (url.username || url.password || url.search || url.hash) return null;
  if (url.protocol === "https:") return url.origin;
  if (isProduction || url.protocol !== "http:") return null;
  if (url.hostname === "localhost" || url.hostname === "127.0.0.1") return url.origin;
  return null;
}

function parseForwardedOrigin(protocolHeader: string, authorityHeader: string, isProduction: boolean): string | null {
  const protocol = protocolHeader.trim().replace(/:$/u, "").toLowerCase();
  const authority = authorityHeader.trim();
  if (authority.length > 260 || (protocol !== "https" && protocol !== "http") || !AUTHORITY_PATTERN.test(authority)) return null;

  try {
    const url = new URL(`${protocol}://${authority}`);
    if (url.pathname !== "/") return null;
    return secureOrigin(url, isProduction);
  } catch {
    return null;
  }
}

/** Resolve the application origin from a configured pin or trusted proxy headers. */
export function trustedRequestOrigin(
  request: Request,
  configuredBase: string | undefined,
  isProduction: boolean,
): string | null {
  if (configuredBase) {
    try {
      return secureOrigin(new URL(configuredBase), isProduction);
    } catch {
      return null;
    }
  }
  if (isProduction) return null;

  let requestUrl: URL;
  try {
    requestUrl = new URL(request.url);
  } catch {
    return null;
  }

  const forwardedHost = request.headers.get("x-forwarded-host");
  const forwardedProto = request.headers.get("x-forwarded-proto");
  const authority = forwardedHost === null ? requestUrl.host : forwardedHost;
  const protocol = forwardedProto === null ? requestUrl.protocol : forwardedProto;
  return parseForwardedOrigin(protocol, authority, isProduction);
}
