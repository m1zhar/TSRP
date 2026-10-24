import assert from "node:assert/strict";
import test from "node:test";
import { issueApiAccessToken, isStrongServerSecret } from "./api-access-token.ts";
import { AUTH_SESSION_MAX_AGE_SECONDS, minimalAuthToken, minimalClientSession } from "./auth-session-token.ts";
import { hasOidcClientSecret, secureOidcIssuer } from "./oidc-issuer.ts";
import { mapOidcProfile } from "./oidc-profile.ts";
import { opaqueBrowserStorageScope } from "./browser-storage-scope.ts";

const secret = "auth-security-test-secret-with-more-than-32-bytes";
const issuer = "https://identity.example.test";

test("server signing secrets require length, diversity, and no short repeated pattern", () => {
  assert.equal(isStrongServerSecret("x".repeat(31)), false);
  assert.equal(isStrongServerSecret("x".repeat(32)), false);
  assert.equal(isStrongServerSecret("0123456789abcdef".repeat(2)), false);
  assert.equal(isStrongServerSecret("x".repeat(20) + "y".repeat(12)), false);
  assert.equal(isStrongServerSecret("a securely generated signing key with unique symbols 123"), true);
  assert.equal(isStrongServerSecret(` ${"x".repeat(32)}`), false);
});

test("OIDC issuer rejects insecure or credential-bearing URLs", () => {
  assert.equal(secureOidcIssuer(issuer, true), issuer);
  assert.equal(secureOidcIssuer("http://identity.example.test", true), null);
  assert.equal(secureOidcIssuer("https://user:password@example.test", true), null);
  assert.equal(secureOidcIssuer("http://localhost:9000/issuer", false), "http://localhost:9000/issuer");
});

test("OIDC readiness rejects a blank client secret without rewriting a configured value", () => {
  assert.equal(hasOidcClientSecret(undefined), false);
  assert.equal(hasOidcClientSecret(""), false);
  assert.equal(hasOidcClientSecret(" \t\n"), false);
  assert.equal(hasOidcClientSecret(" secret with spaces "), true);
});

test("OIDC profiles retain only a bounded safe subject", () => {
  assert.deepEqual(mapOidcProfile({ sub: "issuer-user" }), { id: "issuer-user" });
  assert.throws(() => mapOidcProfile({ sub: "bad\nsubject" }));
  assert.throws(() => mapOidcProfile({ sub: "" }));
});

test("sessions retain only an authentication marker and expire after eight hours", () => {
  const now = 2_000_000_000;
  const scope = "a".repeat(32);
  const token = minimalAuthToken({ email: "private@example.test", name: "Private Name" }, { sub: "idp-sub", email: "private@example.test" }, now, scope);
  assert.ok(token);
  assert.deepEqual(token, { authenticated: true, authTime: now, browserStorageScope: scope });
  const clientSession = minimalClientSession(new Date(now * 1000).toISOString(), token.authenticated, token.browserStorageScope);
  assert.deepEqual(clientSession.user, { authenticated: true, browserStorageScope: scope });
  assert.equal("apiSubject" in clientSession.user!, false);
  assert.equal("sub" in clientSession.user!, false);
  assert.equal("email" in clientSession.user!, false);
  assert.equal(minimalAuthToken({ authenticated: true, authTime: now, browserStorageScope: scope }, undefined, now + 8 * 60 * 60), null);
});

test("browser research scope is stable per account without exposing its provider subject", () => {
  const first = opaqueBrowserStorageScope(secret, issuer, "idp-user-one");
  const second = opaqueBrowserStorageScope(secret, issuer, "idp-user-two");
  assert.ok(first && second);
  assert.match(first, /^[a-f0-9]{32}$/u);
  assert.equal(opaqueBrowserStorageScope(secret, issuer, "idp-user-one"), first);
  assert.notEqual(first, second);
  assert.notEqual(first, opaqueBrowserStorageScope(secret, "https://other-idp.example.test", "idp-user-one"));
  assert.notEqual(first, opaqueBrowserStorageScope(`${secret}-rotated`, issuer, "idp-user-one"));
  assert.equal(opaqueBrowserStorageScope("short", issuer, "idp-user-one"), null);
  assert.equal(opaqueBrowserStorageScope(secret, issuer, "bad\nsubject"), null);

  const now = 2_000_000_000;
  const token = minimalAuthToken({}, { sub: "idp-user-one" }, now, first);
  assert.deepEqual(token, { authenticated: true, authTime: now, browserStorageScope: first });
  assert.deepEqual(minimalAuthToken(token!, undefined, now + 1), token);
  const session = minimalClientSession(new Date(now * 1000).toISOString(), true, first);
  assert.deepEqual(session.user, { authenticated: true, browserStorageScope: first });
  assert.equal("sub" in session.user!, false);
  assert.equal("email" in session.user!, false);
  assert.equal(minimalClientSession(new Date(now * 1000).toISOString(), true, "invalid").user, undefined);
});

test("session expiry is absolute and rejects future or malformed authentication times", () => {
  const now = 2_000_000_000;
  const session = (authTime: unknown, at = now) => minimalAuthToken({ authenticated: true, authTime, browserStorageScope: "a".repeat(32) }, undefined, at);

  assert.deepEqual(session(now), { authenticated: true, authTime: now, browserStorageScope: "a".repeat(32) });
  assert.deepEqual(session(now, now + AUTH_SESSION_MAX_AGE_SECONDS - 1), { authenticated: true, authTime: now, browserStorageScope: "a".repeat(32) });
  assert.equal(session(now, now + AUTH_SESSION_MAX_AGE_SECONDS), null);
  assert.equal(session(now + 31), null);
  assert.equal(session(Number.NaN), null);
  assert.equal(session("2000000000"), null);
});

test("legacy sessions without an account scope are rejected and require fresh authentication", () => {
  const now = 2_000_000_000;
  assert.equal(minimalAuthToken({ sub: "old-provider-sub", email: "private@example.test" }, undefined, now), null);
  assert.equal(minimalAuthToken({ apiSubject: "a".repeat(64), authTime: now }, undefined, now + 1), null);
  const scope = "b".repeat(32);
  const scoped = { authenticated: true as const, authTime: now, browserStorageScope: scope };
  assert.deepEqual(minimalAuthToken(scoped, undefined, now + 1), scoped);
});

test("API tokens are short-lived and use unlinkable random subjects", () => {
  const issued = issueApiAccessToken(secret, 2_000_000_000);
  const next = issueApiAccessToken(secret, 2_000_000_000);
  assert.equal(issued.expiresAt, 2_000_000_060);
  assert.equal(issued.token.split(".").length, 3);
  const subject = JSON.parse(Buffer.from(issued.token.split(".")[1], "base64url").toString("utf8")).sub;
  const nextSubject = JSON.parse(Buffer.from(next.token.split(".")[1], "base64url").toString("utf8")).sub;
  assert.match(subject, /^[a-f0-9]{64}$/u);
  assert.match(nextSubject, /^[a-f0-9]{64}$/u);
  assert.notEqual(subject, nextSubject);
  assert.throws(() => issueApiAccessToken("short"));
});
