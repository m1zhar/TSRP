import { API_ACCESS_TOKEN_TTL_SECONDS, issueApiAccessToken, isStrongServerSecret } from "@/lib/api-access-token";
import { trustedRequestOrigin } from "@/lib/request-origin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function jsonError(status: number, message: string) {
  return Response.json({ error: { message } }, {
    status,
    headers: { "Cache-Control": "no-store", Vary: "Origin" },
  });
}

export async function POST(request: Request) {
  const origin = trustedRequestOrigin(
    request,
    process.env.APP_ORIGIN?.trim(),
    process.env.NODE_ENV === "production",
  );
  if (!origin) return jsonError(503, "Application origin is not securely configured.");
  if (request.headers.get("origin") !== origin) {
    return jsonError(403, "Use the TSRP page on this same site to request research access.");
  }
  const secret = process.env.TSRP_API_TOKEN_SECRET;
  if (!isStrongServerSecret(secret)) return jsonError(503, "Secure API access is not configured for this deployment.");

  const access = issueApiAccessToken(secret);
  return Response.json({ data: { access_token: access.token, expires_in: API_ACCESS_TOKEN_TTL_SECONDS } }, {
    headers: { "Cache-Control": "no-store", Vary: "Origin" },
  });
}
