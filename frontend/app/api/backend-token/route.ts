import { API_ACCESS_TOKEN_TTL_SECONDS, issueApiAccessToken, isStrongServerSecret } from "@/lib/api-access-token";
import { trustedApplicationOrigin } from "@/lib/request-origin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function jsonError(status: number, code: string, message: string) {
  return Response.json({ error: { code, message } }, {
    status,
    headers: { "Cache-Control": "no-store", Vary: "Origin" },
  });
}

export async function POST(request: Request) {
  const origin = trustedApplicationOrigin(
    request,
    {
      APP_ORIGIN: process.env.APP_ORIGIN,
      VERCEL_ENV: process.env.VERCEL_ENV,
      VERCEL_URL: process.env.VERCEL_URL,
      VERCEL_PROJECT_PRODUCTION_URL: process.env.VERCEL_PROJECT_PRODUCTION_URL,
    },
    process.env.NODE_ENV === "production",
  );
  if (!origin) return jsonError(503, "origin_not_configured", "Application origin is not securely configured.");
  if (request.headers.get("origin") !== origin) {
    return jsonError(403, "origin_not_allowed", "Use the TSRP page on this same site to request research access.");
  }
  const secret = process.env.TSRP_API_TOKEN_SECRET;
  if (!isStrongServerSecret(secret)) return jsonError(503, "auth_not_configured", "Secure API access is not configured for this deployment.");

  const access = issueApiAccessToken(secret);
  return Response.json({ data: { access_token: access.token, expires_in: API_ACCESS_TOKEN_TTL_SECONDS } }, {
    headers: { "Cache-Control": "no-store", Vary: "Origin" },
  });
}
