import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { delimiter, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { decode, encode } from "next-auth/jwt";

const host = "127.0.0.1";
const authSecret = "local-auth-session-production-test-secret-0123456789";
const apiSecret = "local-api-token-production-test-secret-9876543210";
const cookieName = "__Secure-authjs.session-token";

async function freePort() {
  const probe = createServer();
  await new Promise((resolve, reject) => {
    probe.once("error", reject);
    probe.listen(0, host, resolve);
  });
  const address = probe.address();
  if (!address || typeof address === "string") throw new Error("Could not allocate a local port.");
  await new Promise((resolve, reject) => probe.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

const port = await freePort();
const origin = `http://${host}:${port}`;
const publicOrigin = "https://tsrp.test";
const server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "--hostname", host, "--port", String(port)], {
  env: {
    ...process.env,
    NODE_ENV: "production",
    NEXT_TELEMETRY_DISABLED: "1",
    AUTH_URL: publicOrigin,
    AUTH_SECRET: authSecret,
    TSRP_API_TOKEN_SECRET: apiSecret,
    AUTH_OIDC_ISSUER: "https://identity.invalid",
    AUTH_OIDC_CLIENT_ID: "local-session-boundary-test",
    AUTH_OIDC_CLIENT_SECRET: "local-only-not-a-real-credential",
  },
  stdio: ["ignore", "pipe", "pipe"],
});
let output = "";
for (const stream of [server.stdout, server.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => { output = `${output}${chunk}`.slice(-4000); });
}

async function waitUntilReady() {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`Production server exited early.\n${output}`);
    try {
      const response = await fetch(`${origin}/signin`, { signal: AbortSignal.timeout(1000) });
      if (response.status === 200) return;
    } catch {}
    await delay(150);
  }
  throw new Error(`Production server did not become ready.\n${output}`);
}

try {
  await waitUntilReady();

  const now = Math.floor(Date.now() / 1000);
  const unscopedLegacyCookie = await encode({
    token: {
      apiSubject: "a".repeat(64),
      authenticated: true,
      authTime: now,
      sub: "provider-account-subject",
      email: "not-for-session@example.test",
      name: "Do not expose this name",
    },
    secret: authSecret,
    salt: cookieName,
    maxAge: 8 * 60 * 60,
  });

  const unscopedResponse = await fetch(`${origin}/api/auth/session`, {
    headers: { Cookie: `${cookieName}=${unscopedLegacyCookie}` },
  });
  assert.equal(unscopedResponse.status, 200);
  const unscopedSession = await unscopedResponse.json();
  assert.equal(unscopedSession, null, "Legacy sessions without an account scope must require fresh authentication.");
  assert.doesNotMatch(JSON.stringify(unscopedSession), /provider-account-subject|not-for-session|Do not expose this name/u);

  const oldCookie = await encode({
    token: {
      apiSubject: "a".repeat(64),
      authenticated: true,
      authTime: now,
      browserStorageScope: "b".repeat(32),
      sub: "provider-account-subject",
      email: "not-for-session@example.test",
      name: "Do not expose this name",
    },
    secret: authSecret,
    salt: cookieName,
    maxAge: 8 * 60 * 60,
  });

  const sessionResponse = await fetch(`${origin}/api/auth/session`, {
    headers: { Cookie: `${cookieName}=${oldCookie}` },
  });
  assert.equal(sessionResponse.status, 200, `Session endpoint returned ${sessionResponse.status}: ${await sessionResponse.clone().text()}\n${output}`);
  const session = await sessionResponse.json();
  assert.ok(session, `Scoped legacy session was not accepted.\n${output}`);
  assert.deepEqual(session.user, { authenticated: true, browserStorageScope: "b".repeat(32) });
  assert.doesNotMatch(JSON.stringify(session), /provider-account-subject|not-for-session|Do not expose/u);

  const renewedCookie = sessionResponse.headers.getSetCookie()
    .map((entry) => entry.split(";", 1)[0] ?? "")
    .find((entry) => entry.startsWith(`${cookieName}=`));
  assert.ok(renewedCookie, "Auth.js should renew the migrated session cookie.");
  const renewedValue = renewedCookie.slice(cookieName.length + 1);
  const renewedToken = await decode({ token: renewedValue, secret: authSecret, salt: cookieName });
  assert.ok(renewedToken);
  for (const claim of ["apiSubject", "sub", "email", "name"]) assert.equal(claim in renewedToken, false);
  assert.equal(renewedToken.authenticated, true);

  async function requestAccessToken(requestOrigin = publicOrigin) {
    return fetch(`${origin}/api/backend-token`, {
      method: "POST",
      headers: { Cookie: renewedCookie, Origin: requestOrigin },
    });
  }

  const firstResponse = await requestAccessToken();
  assert.equal(firstResponse.status, 200, await firstResponse.clone().text());
  const first = (await firstResponse.json()).data.access_token;
  const secondResponse = await requestAccessToken();
  assert.equal(secondResponse.status, 200);
  const second = (await secondResponse.json()).data.access_token;
  const firstClaims = JSON.parse(Buffer.from(first.split(".")[1], "base64url").toString("utf8"));
  const secondClaims = JSON.parse(Buffer.from(second.split(".")[1], "base64url").toString("utf8"));
  assert.deepEqual(Object.keys(firstClaims).sort(), ["aud", "exp", "iat", "iss", "sub"]);
  assert.equal(firstClaims.iss, "tsrp-web");
  assert.equal(firstClaims.aud, "tsrp-api");
  assert.equal(firstClaims.exp - firstClaims.iat, 60);
  assert.match(firstClaims.sub, /^[a-f0-9]{64}$/u);
  assert.match(secondClaims.sub, /^[a-f0-9]{64}$/u);
  assert.notEqual(firstClaims.sub, secondClaims.sub);

  const python = process.env.TSRP_PYTHON;
  assert.ok(python, "Set TSRP_PYTHON to the installed project Python environment.");
  const [firstHeader, firstPayload, firstSignature] = first.split(".");
  const alteredSignature = `${firstSignature.startsWith("A") ? "B" : "A"}${firstSignature.slice(1)}`;
  const tampered = `${firstHeader}.${firstPayload}.${alteredSignature}`;
  const backendPath = resolve(process.cwd(), "../backend");
  const pythonPath = [backendPath, process.env.PYTHONPATH].filter(Boolean).join(delimiter);
  const pythonCheck = spawnSync(python, ["-c", [
    "import os, sys",
    "from api_auth import verify_api_access_token",
    "good = verify_api_access_token(os.environ['TSRP_TEST_ACCESS_TOKEN'])",
    "bad = verify_api_access_token(os.environ['TSRP_TEST_TAMPERED_TOKEN'])",
    "sys.exit(0 if good and not bad else 1)",
  ].join("; ")], {
    cwd: backendPath,
    env: {
      ...process.env,
      PYTHONPATH: pythonPath,
      TSRP_API_TOKEN_SECRET: apiSecret,
      TSRP_TEST_ACCESS_TOKEN: first,
      TSRP_TEST_TAMPERED_TOKEN: tampered,
    },
    encoding: "utf8",
    timeout: 10_000,
    maxBuffer: 1024 * 1024,
  });
  assert.equal(pythonCheck.error, undefined, `Python token verifier could not run: ${pythonCheck.error?.message ?? "unknown error"}`);
  assert.equal(pythonCheck.status, 0, `Python API verifier did not accept the Node-issued token and reject the altered signature: ${pythonCheck.stderr}`);

  const crossOrigin = await requestAccessToken("https://attacker.invalid");
  assert.equal(crossOrigin.status, 403);
  console.log("Production Auth.js scoped legacy-session migration and unscoped-session rejection, identity minimization, random token minting, Python JWT verification/tamper rejection, and origin checks passed.");
} finally {
  server.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => server.once("exit", resolve)),
    delay(2000),
  ]);
  if (server.exitCode === null) server.kill("SIGKILL");
}
