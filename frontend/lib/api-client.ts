type ApiEnvelope<T> = { data?: T; error?: { code?: string; message?: string } };
type ApiResponseObserver = (response: Response) => void;
type AccessTokenEnvelope = { data?: { access_token?: unknown; expires_in?: unknown }; error?: { message?: string } };

export class ApiResponseError extends Error {
  readonly code?: string;
  readonly status?: number;
  readonly retryAfterSeconds?: number;

  constructor(message: string, code?: string, status?: number, retryAfterSeconds?: number) {
    super(message);
    this.name = "ApiResponseError";
    this.code = code;
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function parseRetryAfterSeconds(value: string | null, serverDate: string | null, nowMs = Date.now()): number | undefined {
  if (!value) return undefined;
  const candidate = value.trim();
  let delay: number;
  if (/^\d+$/u.test(candidate)) {
    const significantDigits = candidate.replace(/^0+/u, "") || "0";
    if (significantDigits.length > 5) return undefined;
    delay = Number(significantDigits);
  } else {
    const weekday = "(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)";
    const month = "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)";
    const imfFixdate = new RegExp(`^${weekday}, \\d{2} ${month} \\d{4} \\d{2}:\\d{2}:\\d{2} GMT$`, "u");
    const rfc850 = /^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), \d{2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2} \d{2}:\d{2}:\d{2} GMT$/u;
    const asctime = new RegExp(`^${weekday} ${month} +\\d{1,2} \\d{2}:\\d{2}:\\d{2} \\d{4}$`, "u");
    if (!imfFixdate.test(candidate) && !rfc850.test(candidate) && !asctime.test(candidate)) return undefined;
    const retryAt = Date.parse(asctime.test(candidate) ? `${candidate} GMT` : candidate);
    const serverAt = serverDate ? Date.parse(serverDate) : nowMs;
    if (!Number.isFinite(retryAt) || !Number.isFinite(serverAt)) return undefined;
    delay = (retryAt - serverAt) / 1000;
  }
  if (!Number.isFinite(delay) || delay <= 0 || delay > 86_400) return undefined;
  return Math.ceil(delay);
}

function rateLimitError(response: Response, requestId: string | null): ApiResponseError {
  const retrySeconds = parseRetryAfterSeconds(response.headers.get("Retry-After"), response.headers.get("Date"));
  const wait = retrySeconds ? ` Wait about ${retrySeconds} seconds before trying again.` : " Please wait before trying again.";
  const reference = requestId ? ` (Reference ID: ${requestId})` : "";
  return new ApiResponseError(`The service is temporarily limiting requests.${wait}${reference}`, "rate_limited", 429, retrySeconds);
}

async function getAccessToken(fetcher: typeof fetch, signal: AbortSignal): Promise<string> {
  const response = await fetcher("/api/backend-token", {
    method: "POST", headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store", signal,
  });
  if (response.status === 429) throw rateLimitError(response, response.headers.get("X-Request-ID"));
  let body: AccessTokenEnvelope;
  try { body = await response.json() as AccessTokenEnvelope; }
  catch { throw new Error("Research access could not be verified. Reload the page and try again."); }
  const token = body?.data?.access_token;
  const expiresIn = body?.data?.expires_in;
  if (!response.ok || typeof token !== "string" || !token || token.length > 2048
    || typeof expiresIn !== "number" || !Number.isSafeInteger(expiresIn) || expiresIn < 10 || expiresIn > 90) {
    throw new Error(typeof body?.error?.message === "string" ? body.error.message : "Research access could not be verified. Reload the page and try again.");
  }
  return token;
}

export async function requestJson<T>(
  url: string,
  init: RequestInit = {},
  timeoutMs = 32_000,
  fetcher: typeof fetch = fetch,
  observeResponse?: ApiResponseObserver,
): Promise<T> {
  const controller = new AbortController();
  const callerSignal = init.signal;
  let timedOut = false;
  const relayAbort = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) relayAbort();
  else callerSignal?.addEventListener("abort", relayAbort, { once: true });

  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const accessToken = await getAccessToken(fetcher, controller.signal);
    const response = await fetcher(url, {
      ...init,
      cache: "no-store",
      credentials: "same-origin",
      headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers, Authorization: `Bearer ${accessToken}` },
      signal: controller.signal,
    });
    observeResponse?.(response);
    const requestId = response.headers.get("X-Request-ID");
    if (response.status === 429) throw rateLimitError(response, requestId);
    const withReference = (message: string) => requestId ? `${message} (Reference ID: ${requestId})` : message;
    let value: unknown;
    try {
      value = await response.json();
    } catch {
      throw new Error(withReference("The research service returned an unreadable response. Try again shortly."));
    }
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new Error(withReference("The research service returned an unexpected response. Try again shortly."));
    }
    const payload = value as ApiEnvelope<T>;
    if (!response.ok || payload.data === undefined) {
      throw new ApiResponseError(
        withReference(payload.error?.message || "Research data is temporarily unavailable."),
        typeof payload.error?.code === "string" ? payload.error.code : undefined,
        response.status,
      );
    }
    return payload.data;
  } catch (error) {
    if (timedOut) throw new Error("The research service took too long to respond. Try again shortly.");
    throw error;
  } finally {
    clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", relayAbort);
  }
}
