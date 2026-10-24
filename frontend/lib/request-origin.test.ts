import assert from "node:assert/strict";
import test from "node:test";
import { trustedApplicationOrigin, trustedRequestOrigin } from "./request-origin.ts";

const request = (url: string, headers?: HeadersInit) => new Request(url, { headers });

test("a configured secure origin is authoritative over forwarded host headers", () => {
  const incoming = request("https://internal.invalid/api/backend-token", {
    "x-forwarded-host": "attacker.example.test",
    "x-forwarded-proto": "https",
  });

  assert.equal(
    trustedRequestOrigin(incoming, "https://research.example.test/app", true),
    "https://research.example.test",
  );
});

test("production rejects unsafe configured origins instead of falling back", () => {
  const incoming = request("https://research.example.test/api/backend-token", {
    "x-forwarded-host": "research.example.test",
    "x-forwarded-proto": "https",
  });

  assert.equal(trustedRequestOrigin(incoming, "http://research.example.test", true), null);
  assert.equal(trustedRequestOrigin(incoming, "https://user:pass@research.example.test", true), null);
  assert.equal(trustedRequestOrigin(incoming, "https://research.example.test/?next=evil", true), null);
});

test("production requires a pinned canonical origin instead of trusting forwarded headers", () => {
  const incoming = request("https://internal.invalid/api", {
    "x-forwarded-host": "research.example.test",
    "x-forwarded-proto": "https",
  });
  assert.equal(trustedRequestOrigin(incoming, undefined, true), null);
  assert.equal(trustedRequestOrigin(incoming, "https://canonical.example.test", true), "https://canonical.example.test");

  for (const host of ["research.example.test,attacker.test", "research.example.test/path", "bad host", "-invalid.example"]) {
    assert.equal(trustedRequestOrigin(request("https://internal.invalid/api", {
      "x-forwarded-host": host,
      "x-forwarded-proto": "https",
    }), undefined, true), null, host);
  }
  assert.equal(trustedRequestOrigin(request("https://research.example.test/api", {
    "x-forwarded-proto": "http",
  }), undefined, true), null);
});

test("development allows only HTTP localhost and IPv4 loopback origins", () => {
  assert.equal(trustedRequestOrigin(request("http://localhost:3000/api"), undefined, false), "http://localhost:3000");
  assert.equal(trustedRequestOrigin(request("http://127.0.0.1:8503/api"), undefined, false), "http://127.0.0.1:8503");
  assert.equal(trustedRequestOrigin(request("http://research.example.test/api"), undefined, false), null);
  assert.equal(trustedRequestOrigin(request("http://[::1]:3000/api"), undefined, false), null);
});

test("production uses Vercel's canonical project host when APP_ORIGIN is unset", () => {
  const incoming = request("https://internal.invalid/api/backend-token");
  assert.equal(trustedApplicationOrigin(incoming, {
    VERCEL_ENV: "production",
    VERCEL_PROJECT_PRODUCTION_URL: "thesrp.vercel.app",
    VERCEL_URL: "deployment-hash.vercel.app",
  }, true), "https://thesrp.vercel.app");
});

test("Vercel Preview uses its own deployment host rather than the Production host", () => {
  const incoming = request("https://internal.invalid/api/backend-token");
  assert.equal(trustedApplicationOrigin(incoming, {
    VERCEL_ENV: "preview",
    VERCEL_PROJECT_PRODUCTION_URL: "thesrp.vercel.app",
    VERCEL_URL: "tsrp-git-fix-origin-user.vercel.app",
  }, true), "https://tsrp-git-fix-origin-user.vercel.app");
});

test("an explicit APP_ORIGIN remains authoritative over Vercel host fallbacks", () => {
  const incoming = request("https://internal.invalid/api/backend-token");
  assert.equal(trustedApplicationOrigin(incoming, {
    APP_ORIGIN: "https://research.example.test",
    VERCEL_ENV: "production",
    VERCEL_PROJECT_PRODUCTION_URL: "thesrp.vercel.app",
  }, true), "https://research.example.test");
  assert.equal(trustedApplicationOrigin(incoming, {
    APP_ORIGIN: "http://research.example.test",
    VERCEL_ENV: "production",
    VERCEL_PROJECT_PRODUCTION_URL: "thesrp.vercel.app",
  }, true), null, "an invalid explicit value must fail closed instead of falling back");
});

test("production still fails closed when no explicit or Vercel-owned origin exists", () => {
  const incoming = request("https://internal.invalid/api/backend-token", {
    "x-forwarded-host": "attacker.example.test",
    "x-forwarded-proto": "https",
  });
  assert.equal(trustedApplicationOrigin(incoming, {}, true), null);
  assert.equal(trustedApplicationOrigin(incoming, {
    VERCEL_ENV: "production",
    VERCEL_PROJECT_PRODUCTION_URL: "bad host/path",
  }, true), null);
  assert.equal(trustedApplicationOrigin(incoming, {
    VERCEL_ENV: "preview",
    VERCEL_PROJECT_PRODUCTION_URL: "thesrp.vercel.app",
  }, true), null, "a Preview must not borrow the Production origin when its host is absent");
});
