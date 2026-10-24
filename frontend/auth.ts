import NextAuth from "next-auth";
import { opaqueBrowserStorageScope } from "./lib/browser-storage-scope.ts";
import { isStrongServerSecret } from "./lib/api-access-token.ts";
import { AUTH_SESSION_MAX_AGE_SECONDS, minimalAuthToken, minimalClientSession } from "./lib/auth-session-token.ts";
import { hasOidcClientSecret, secureOidcIssuer } from "./lib/oidc-issuer.ts";
import { mapOidcProfile } from "./lib/oidc-profile.ts";

const issuer = process.env.AUTH_OIDC_ISSUER?.trim() ?? "";
const clientId = process.env.AUTH_OIDC_CLIENT_ID?.trim() ?? "";
const clientSecret = process.env.AUTH_OIDC_CLIENT_SECRET ?? "";
const authSecret = process.env.AUTH_SECRET;
const apiSecret = process.env.TSRP_API_TOKEN_SECRET;

export const AUTH_ISSUER = secureOidcIssuer(issuer, process.env.NODE_ENV === "production");
export const AUTH_READY = Boolean(
  AUTH_ISSUER
  && clientId
  && hasOidcClientSecret(clientSecret)
  && isStrongServerSecret(authSecret)
  && isStrongServerSecret(apiSecret)
  && authSecret !== apiSecret,
);

const providers = AUTH_READY && AUTH_ISSUER ? [{
  id: "tsrp-oidc",
  name: (process.env.AUTH_PROVIDER_NAME?.trim().slice(0, 48) || "your identity provider"),
  type: "oidc" as const,
  issuer: AUTH_ISSUER,
  clientId,
  clientSecret,
  checks: ["state", "pkce", "nonce"] as ["state", "pkce", "nonce"],
  authorization: { params: { scope: "openid" } },
  profile: mapOidcProfile,
}] : [];

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers,
  secret: authSecret,
  trustHost: true,
  session: { strategy: "jwt", maxAge: AUTH_SESSION_MAX_AGE_SECONDS },
  pages: { signIn: "/signin" },
  callbacks: {
    jwt({ token, profile }) {
      const browserStorageScope = profile?.sub === undefined
        ? undefined
        : opaqueBrowserStorageScope(authSecret, AUTH_ISSUER, profile.sub);
      return minimalAuthToken(token, profile, undefined, browserStorageScope);
    },
    session({ session, token }) {
      return minimalClientSession(session.expires, token.authenticated === true, token.browserStorageScope);
    },
  },
  logger: {
    error(error) {
      console.error("auth_error", error.name === "AuthError" ? "AuthError" : "unknown");
    },
    warn(code) {
      console.warn("auth_warning", String(code).slice(0, 64));
    },
    debug() {},
  },
});
