import assert from "node:assert/strict";
import { createHash, createHmac, createSign, generateKeyPairSync } from "node:crypto";
import { cp, lstat, mkdir, mkdtemp, readdir, rm, symlink } from "node:fs/promises";
import { createServer } from "node:net";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { existsSync, readFileSync } from "node:fs";
import { basename, delimiter, join, relative, sep } from "node:path";
import { tmpdir } from "node:os";
import { execFileSync, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const loopbackHost = "127.0.0.1";
const appHost = "localhost";
const clientId = "tsrp-auth-e2e-client";
const clientSecret = "fixture-oidc-client-secret-not-a-real-credential";
const authSecret = "fixture-auth-secret-with-at-least-32-random-bytes";
const apiSecret = "fixture-api-secret-with-at-least-32-random-bytes";
const providerSubject = "mock-oidc-account-7091";
const checkout = process.cwd();
const productionMode = process.argv.includes("--production");
const browserE2EMode = process.argv.includes("--browser-e2e") && !productionMode;
const interactiveMode = (process.argv.includes("--interactive") || browserE2EMode) && !productionMode;
const interactiveSearchRace = process.argv.includes("--search-race") && interactiveMode;
const interactiveAnalysisRace = process.argv.includes("--analysis-race") && interactiveMode;
const interactiveAnalysisFailure = process.argv.includes("--analysis-failure") && interactiveMode;
const interactiveAnalysisRecovery = process.argv.includes("--analysis-recovery") && interactiveMode;
const interactiveProviderDisabled = process.argv.includes("--provider-disabled") && interactiveMode;
const interactiveRateLimit = process.argv.includes("--rate-limit") && interactiveMode;
const interactiveScenarioRace = process.argv.includes("--scenario-race") && interactiveMode;
const interactiveSnapshotRace = process.argv.includes("--snapshot-race") && interactiveMode;
const interactiveSingleChartPoint = process.argv.includes("--single-chart-point") && interactiveMode;
const interactiveUnsupportedSector = process.env.TSRP_E2E_UNSUPPORTED_SECTOR === "1" && interactiveMode;
const scenarioSecret = "fixture-scenario-secret-not-for-deployment-1234567890";
let projectCopy;
let app;
let idp;
let proxy;
let devApi;
let fixtureApi;
let appOutput = "";
let fixtureApiOutput = "";

function json(response, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(200, { "Content-Type": "application/json", "Content-Length": body.length, "Cache-Control": "no-store" });
  response.end(body);
}

function encode(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
}

function signIdToken(privateKey, issuer, nonce, wrongNonce = false) {
  const now = Math.floor(Date.now() / 1000);
  const header = encode({ alg: "RS256", kid: "tsrp-e2e-key", typ: "JWT" });
  const claims = encode({
    iss: issuer,
    sub: providerSubject,
    aud: clientId,
    iat: now,
    exp: now + 180,
    nonce: wrongNonce ? `${nonce}-invalid` : nonce,
    email: "must-not-enter-session@example.test",
    name: "Must Not Enter Session",
  });
  const signed = `${header}.${claims}`;
  return `${signed}.${createSign("RSA-SHA256").update(signed).end().sign(privateKey).toString("base64url")}`;
}

async function freePort(host) {
  const probe = createServer();
  await new Promise((resolve, reject) => {
    probe.once("error", reject);
    probe.listen(0, host, resolve);
  });
  const address = probe.address();
  if (!address || typeof address === "string") throw new Error("Cannot allocate test port.");
  await new Promise((resolve, reject) => probe.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

function makeCookieJar() {
  const cookies = new Map();
  return {
    header() { return [...cookies].map(([name, value]) => `${name}=${value}`).join("; "); },
    values() { return [...cookies].map(([name, value]) => ({ name, value })); },
    absorb(response) {
      for (const entry of response.headers.getSetCookie()) {
        const pair = entry.split(";", 1)[0] ?? "";
        const index = pair.indexOf("=");
        if (index < 1) continue;
        const name = pair.slice(0, index);
        const value = pair.slice(index + 1);
        if (!value || /max-age=0/iu.test(entry)) cookies.delete(name);
        else cookies.set(name, value);
      }
    },
  };
}

async function startMockIssuer(port, tlsOptions) {
  const issuerProtocol = tlsOptions ? "https" : "http";
  const issuer = `${issuerProtocol}://${loopbackHost}:${port}/issuer`;
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const jwk = publicKey.export({ format: "jwk" });
  let wrongNonce = false;
  let latestAuthorization;
  let tokenRequests = 0;
  const handleRequest = (request, response) => {
    const url = new URL(request.url ?? "/", issuer);
    if (request.method === "GET" && url.pathname === "/issuer/.well-known/openid-configuration") {
      return json(response, {
        issuer,
        authorization_endpoint: `${issuer}/authorize`,
        token_endpoint: `${issuer}/token`,
        jwks_uri: `${issuer}/jwks`,
        userinfo_endpoint: `${issuer}/userinfo`,
        response_types_supported: ["code"],
        subject_types_supported: ["public"],
        id_token_signing_alg_values_supported: ["RS256"],
        token_endpoint_auth_methods_supported: ["client_secret_basic", "client_secret_post"],
        code_challenge_methods_supported: ["S256"],
        grant_types_supported: ["authorization_code"],
      });
    }
    if (request.method === "GET" && url.pathname === "/issuer/jwks") {
      return json(response, { keys: [{ ...jwk, kid: "tsrp-e2e-key", use: "sig", alg: "RS256" }] });
    }
    if (request.method === "GET" && url.pathname === "/issuer/authorize") {
      latestAuthorization = Object.fromEntries(url.searchParams);
      const callback = new URL(latestAuthorization.redirect_uri);
      callback.searchParams.set("code", "fixture-code");
      callback.searchParams.set("state", latestAuthorization.state);
      response.writeHead(302, { Location: callback.toString() });
      return response.end();
    }
    if (request.method === "POST" && url.pathname === "/issuer/token") {
      const chunks = [];
      request.on("data", (chunk) => chunks.push(chunk));
      request.on("end", () => {
        try {
          const form = new URLSearchParams(Buffer.concat(chunks).toString("utf8"));
          assert.equal(form.get("grant_type"), "authorization_code");
          assert.equal(form.get("code"), "fixture-code");
          const verifier = form.get("code_verifier") ?? "";
          assert.ok(verifier.length >= 43 && verifier.length <= 128);
          assert.equal(createHash("sha256").update(verifier).digest("base64url"), latestAuthorization.code_challenge);
          assert.equal(form.get("redirect_uri"), latestAuthorization.redirect_uri);
          tokenRequests += 1;
          json(response, {
            access_token: "fixture-access-token",
            token_type: "Bearer",
            expires_in: 180,
            id_token: signIdToken(privateKey, issuer, latestAuthorization.nonce, wrongNonce),
          });
        } catch (error) {
          response.writeHead(400, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ error: error.message }));
        }
      });
      return;
    }
    if (request.method === "GET" && url.pathname === "/issuer/userinfo") {
      return json(response, { sub: providerSubject, email: "must-not-enter-session@example.test", name: "Must Not Enter Session" });
    }
    response.writeHead(404);
    response.end();
  };
  const server = tlsOptions
    ? createHttpsServer(tlsOptions, handleRequest)
    : createHttpServer(handleRequest);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, loopbackHost, resolve);
  });
  return {
    issuer,
    server,
    latestAuthorization: () => latestAuthorization,
    tokenRequests: () => tokenRequests,
    useWrongNonce: () => { wrongNonce = true; },
    setWrongNonce: (value) => { wrongNonce = value; },
  };
}

async function startMockDevApi(port) {
  const requests = [];
  const server = createHttpServer((incoming, response) => {
    requests.push({ method: incoming.method, path: incoming.url });
    json(response, { method: incoming.method, path: incoming.url });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, loopbackHost, resolve);
  });
  return { server, requests };
}

async function waitForApp(baseUrl) {
  const deadline = Date.now() + 45_000;
  let lastError;
  while (Date.now() < deadline) {
    if (app.exitCode !== null) throw new Error(`Next ${productionMode ? "production server" : "dev server"} exited before readiness.\n${appOutput}`);
    try {
      const response = await fetch(`${baseUrl}/signin`, { redirect: "manual", signal: AbortSignal.timeout(2000) });
      if (response.status === 200) return;
      lastError = new Error(`Sign-in returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await delay(250);
  }
  throw new Error(`Next ${productionMode ? "production server" : "dev server"} did not become ready: ${lastError?.message ?? "unknown error"}\n${appOutput}`);
}

async function startFixtureApi(port, { searchRace = false, analysisRace = false, analysisFailure = false, analysisRecovery = false, providerDisabled = false, scenarioRace = false, snapshotRace = false, singleChartPoint = false } = {}) {
  const python = process.env.TSRP_PYTHON || "python3";
  const version = execFileSync(python, ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], { encoding: "utf8" }).trim();
  const [major, minor] = version.split(".").map(Number);
  if (!Number.isFinite(major) || !Number.isFinite(minor) || major < 3 || (major === 3 && minor < 12)) {
    throw new Error(`The synthetic API browser test requires Python 3.12 or newer; ${python} provides ${version || "unknown"}. Set TSRP_PYTHON to the project environment.`);
  }
  const projectRoot = join(checkout, "..");
  const pythonPath = [join(projectRoot, "backend"), process.env.PYTHONPATH].filter(Boolean).join(delimiter);
  fixtureApi = spawn(python, [join(checkout, "tests/asgi-http-server.py"), String(port)], {
    cwd: projectRoot,
    env: {
      ...process.env,
      PYTHONPATH: pythonPath,
      TSRP_API_TOKEN_SECRET: apiSecret,
      TSRP_SCENARIO_SIGNING_SECRET: scenarioSecret,
      // Synthetic MockEquity fixtures need to pass the production provider gate.
      // The ASGI fixture replaces the transport methods with hard failures first.
      TSRP_YAHOO_FINANCE_ENABLED: "true",
      SEC_USER_AGENT: "",
      TSRP_E2E_SEARCH_RACE: searchRace ? "1" : "",
      TSRP_E2E_ANALYSIS_RACE: analysisRace ? "1" : "",
      TSRP_E2E_ANALYSIS_DELAY_SECONDS: analysisRace ? "8" : "",
      TSRP_E2E_ANALYSIS_FAILURE: analysisFailure ? "1" : "",
      TSRP_E2E_ANALYSIS_RECOVERY: analysisRecovery ? "1" : "",
      TSRP_E2E_PROVIDER_DISABLED: providerDisabled ? "1" : "",
      TSRP_E2E_SCENARIO_RACE: scenarioRace ? "1" : "",
      TSRP_E2E_SNAPSHOT_RACE: snapshotRace ? "1" : "",
      TSRP_E2E_SINGLE_CHART_POINT: singleChartPoint ? "1" : "",
      PYTHONDONTWRITEBYTECODE: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  for (const stream of [fixtureApi.stdout, fixtureApi.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      fixtureApiOutput = `${fixtureApiOutput}${chunk}`.slice(-3000);
      if ((searchRace || analysisRace || analysisFailure || analysisRecovery || scenarioRace || snapshotRace || singleChartPoint) && stream === fixtureApi.stdout) process.stdout.write(chunk);
    });
  }
  const healthUrl = `http://${loopbackHost}:${port}/health`;
  const deadline = Date.now() + 15_000;
  let lastError;
  while (Date.now() < deadline) {
    if (fixtureApi.exitCode !== null) throw new Error(`Synthetic API fixture exited early.\n${fixtureApiOutput}`);
    try {
      const response = await fetch(healthUrl, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
      lastError = new Error(`Synthetic API health returned ${response.status}.`);
    } catch (error) {
      lastError = error;
    }
    await delay(200);
  }
  throw new Error(`Synthetic API fixture did not become ready: ${lastError?.message ?? "unknown error"}\n${fixtureApiOutput}`);
}

async function startTlsProxy(port, appPort, tlsOptions) {
  const server = createHttpsServer(tlsOptions, (incoming, outgoing) => {
    const forwardedHost = `${appHost}:${port}`;
    const upstream = httpRequest({
      hostname: appHost,
      port: appPort,
      method: incoming.method,
      path: incoming.url,
      headers: {
        ...incoming.headers,
        host: forwardedHost,
        "x-forwarded-host": forwardedHost,
        "x-forwarded-port": String(port),
        "x-forwarded-proto": "https",
      },
    }, (response) => {
      outgoing.writeHead(response.statusCode ?? 502, response.headers);
      response.pipe(outgoing);
    });
    upstream.on("error", () => {
      if (!outgoing.headersSent) outgoing.writeHead(502, { "Cache-Control": "no-store" });
      outgoing.end("Local production test proxy failed.");
    });
    incoming.pipe(upstream);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, appHost, resolve);
  });
  return server;
}

async function beginSignIn(baseUrl, jar) {
  const csrfResponse = await fetch(`${baseUrl}/api/auth/csrf`, { headers: { Cookie: jar.header() } });
  assert.equal(csrfResponse.status, 200);
  jar.absorb(csrfResponse);
  const { csrfToken } = await csrfResponse.json();
  const response = await fetch(`${baseUrl}/api/auth/signin/tsrp-oidc`, {
    method: "POST",
    redirect: "manual",
    headers: { Origin: baseUrl, "Content-Type": "application/x-www-form-urlencoded", Cookie: jar.header() },
    body: new URLSearchParams({ csrfToken, callbackUrl: `${baseUrl}/?ticker=AAPL&section=Valuation` }),
  });
  assert.ok([302, 303].includes(response.status), `Auth.js sign-in start failed (${response.status}).`);
  jar.absorb(response);
  return response.headers.get("location");
}

async function finishSignIn(baseUrl, authorizationUrl, jar, stateOverride) {
  const authorization = new URL(authorizationUrl);
  assert.equal(authorization.searchParams.get("scope"), "openid");
  assert.ok(authorization.searchParams.get("state"));
  assert.ok(authorization.searchParams.get("nonce"));
  assert.ok(authorization.searchParams.get("code_challenge"));
  assert.equal(authorization.searchParams.get("code_challenge_method"), "S256");
  const providerResponse = await fetch(authorizationUrl, { redirect: "manual" });
  assert.equal(providerResponse.status, 302);
  assert.equal(authorization.searchParams.get("redirect_uri"), `${baseUrl}/api/auth/callback/tsrp-oidc`, "OIDC callback must use the same trusted public origin as the page.");
  const callback = new URL(providerResponse.headers.get("location"));
  if (stateOverride !== undefined) callback.searchParams.set("state", stateOverride);
  return fetch(callback, { redirect: "manual", headers: { Cookie: jar.header() } });
}

async function runResearchBrowserE2E(baseUrl, jar) {
  const { chromium } = await import("playwright");
  const configuredExecutable = process.env.TSRP_PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  const macChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const executablePath = configuredExecutable || (process.platform === "darwin" && existsSync(macChrome) ? macChrome : undefined);
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  const browserErrors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
    await context.addCookies(jar.values().map(({ name, value }) => ({ name, value, url: baseUrl })));
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    page.on("pageerror", (error) => browserErrors.push(error.message));
    let scenarioRequestCount = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/scenario") scenarioRequestCount += 1;
    });

    const sections = [
      "TSRP Score",
      "Expectations vs Reality",
      "Valuation",
      "Catalysts & Risks",
      "What Changed?",
    ];
    const sectionUrl = (section, ticker = "AAPL") => {
      const url = new URL("/", baseUrl);
      url.searchParams.set("ticker", ticker);
      if (section !== "TSRP Score") url.searchParams.set("section", section);
      return url.toString();
    };
    const assertSection = async (section, ticker = "AAPL") => {
      const heading = page.locator("#section-title");
      await heading.waitFor({ state: "visible" });
      assert.equal((await heading.innerText()).trim(), section, `Expected ${section} heading.`);
      const current = page.locator('nav[aria-label="Research sections"] a[aria-current="page"]');
      assert.equal(await current.count(), 1, `${section} must have exactly one active navigation link.`);
      assert.equal((await current.innerText()).trim(), section, `${section} must be the active navigation item.`);
      await page.waitForFunction(() => {
        const nav = document.querySelector('nav[aria-label="Research sections"]');
        const active = nav?.querySelector('a[aria-current="page"]');
        if (!nav || !active) return false;
        const navBounds = nav.getBoundingClientRect();
        const activeBounds = active.getBoundingClientRect();
        return activeBounds.left >= navBounds.left - 1 && activeBounds.right <= navBounds.right + 1;
      }, undefined, { timeout: 3_000 });
      assert.equal(await page.locator("#company-search").inputValue(), ticker, `${section} must preserve the selected ticker.`);
    };

    await page.goto(sectionUrl("TSRP Score"), { waitUntil: "domcontentloaded" });
    await assertSection("TSRP Score");

    for (const section of sections) {
      await page.getByRole("link", { name: section, exact: true }).click();
      await page.waitForFunction((expected) => new URL(location.href).searchParams.get("section") === expected, section === "TSRP Score" ? null : section);
      await assertSection(section);
    }

    await page.getByRole("link", { name: "TSRP Score", exact: true }).click();
    await page.waitForFunction(() => !new URL(location.href).searchParams.has("section"));
    for (const section of sections.slice(1)) {
      const preview = page.locator(".preview-row").filter({ hasText: section });
      await preview.getByRole("button", { name: /^Open\b/ }).click();
      await page.waitForFunction((expected) => new URL(location.href).searchParams.get("section") === expected, section);
      await assertSection(section);
      await page.getByRole("link", { name: "TSRP Score", exact: true }).click();
      await page.waitForFunction(() => !new URL(location.href).searchParams.has("section"));
      await assertSection("TSRP Score");
    }

    await page.getByRole("link", { name: "TSRP Score", exact: true }).focus();
    await page.getByRole("link", { name: "TSRP Score", exact: true }).press("Enter");
    await page.waitForFunction(() => !new URL(location.href).searchParams.has("section"));
    await assertSection("TSRP Score");
    await page.getByRole("link", { name: "Valuation", exact: true }).click();
    await page.waitForFunction(() => new URL(location.href).searchParams.get("section") === "Valuation");
    await page.goBack();
    await page.waitForFunction(() => !new URL(location.href).searchParams.has("section"));
    await assertSection("TSRP Score");
    await page.goForward();
    await page.waitForFunction(() => new URL(location.href).searchParams.get("section") === "Valuation");
    await assertSection("Valuation");

    const slowAnalysisRequest = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname === "/api/analyze" && url.searchParams.get("ticker") === "SLOW";
    });
    await page.goto(sectionUrl("TSRP Score", "SLOW"), { waitUntil: "domcontentloaded" });
    await slowAnalysisRequest;
    const fastAnalysisResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === "/api/analyze" && url.searchParams.get("ticker") === "FAST";
    });
    await page.evaluate(() => window.history.pushState(null, "", "/?ticker=FAST"));
    const fastResponse = await fastAnalysisResponse;
    assert.equal(fastResponse.status(), 200, "The latest company analysis must succeed.");
    await page.locator(".ticker-pill").filter({ hasText: /^FAST$/u }).waitFor({ state: "visible" });
    await delay(8_300);
    assert.equal(await page.locator(".ticker-pill").innerText(), "FAST", "A slower prior analysis must not overwrite the latest company.");
    assert.equal(await page.locator("#company-search").inputValue(), "FAST", "The selected ticker must remain aligned with its displayed analysis.");
    assert.equal(new URL(page.url()).searchParams.get("ticker"), "FAST", "The URL must remain aligned after the stale response finishes.");

    if (interactiveAnalysisRecovery) {
      const failedRetryResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === "/api/analyze" && url.searchParams.get("ticker") === "RETRY";
      });
      await page.goto(sectionUrl("TSRP Score", "RETRY"), { waitUntil: "domcontentloaded" });
      assert.equal((await failedRetryResponse).status(), 503, "The recovery fixture must fail the first selected-company request.");
      await page.getByRole("heading", { name: "Research could not be completed" }).waitFor({ state: "visible" });
      const recoveredResponse = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === "/api/analyze" && url.searchParams.get("ticker") === "RETRY";
      });
      await page.getByRole("button", { name: "Try again" }).click();
      assert.equal((await recoveredResponse).status(), 200, "Retry must recover after a transient provider failure.");
      await page.locator(".ticker-pill").filter({ hasText: /^RETRY$/u }).waitFor({ state: "visible" });
      assert.equal(new URL(page.url()).searchParams.get("ticker"), "RETRY", "Successful recovery must preserve the selected ticker in the URL.");
      assert.equal(await page.getByRole("heading", { name: "Research could not be completed" }).count(), 0, "The recovered company must replace the error state.");
    }

    let layoutChecks = 0;
    for (const width of [375, 430, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const section of sections) {
        await page.goto(sectionUrl(section), { waitUntil: "domcontentloaded" });
        await assertSection(section);
        const widths = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
        assert.equal(widths.document, widths.viewport, `${section} overflows horizontally at ${width}px.`);
        layoutChecks += 1;
      }
    }

    await page.setViewportSize({ width: 375, height: 900 });
    await page.goto(sectionUrl("Valuation"), { waitUntil: "domcontentloaded" });
    await assertSection("Valuation");
    for (const listName of ["Top movers", "Most active", "Popular stocks"]) {
      const listButton = page.getByRole("button", { name: listName, exact: true });
      await listButton.click();
      await page.waitForFunction((name) => {
        const button = [...document.querySelectorAll(".market-movers-tab")].find((item) => item.textContent?.trim() === name);
        return button?.getAttribute("aria-pressed") === "true";
      }, listName);
    }
    await page.getByRole("button", { name: /Open research for MSFT/u }).waitFor({ state: "visible" });
    await page.locator(".market-movers-window").hover();
    await page.getByRole("button", { name: /Open research for MSFT/u }).click();
    await page.locator(".ticker-pill").filter({ hasText: /^MSFT$/u }).waitFor({ state: "visible" });
    await assertSection("Valuation", "MSFT");
    assert.equal(new URL(page.url()).searchParams.get("ticker"), "MSFT", "A selected market-list company must update the URL.");

    const fcfMargin = page.locator("#scenario-fcf_margin");
    const discountRate = page.locator("#scenario-discount_rate");
    const terminalGrowth = page.locator("#scenario-terminal_growth");
    await fcfMargin.fill("0");
    await page.getByRole("button", { name: "Calculate scenario", exact: true }).click();
    await page.locator("#scenario-validation-summary").waitFor({ state: "visible" });
    assert.equal(await fcfMargin.getAttribute("aria-invalid"), "true");
    assert.match(await page.locator("#scenario-validation-summary").innerText(), /1% to 50%/u);
    assert.equal(await page.evaluate(() => document.activeElement?.id), "scenario-validation-summary", "Invalid assumptions should move focus to the error summary.");
    assert.equal(scenarioRequestCount, 0, "Invalid assumptions must be rejected before sending a scenario request.");

    await fcfMargin.fill("18.3");
    await discountRate.fill("2.9");
    await page.getByRole("button", { name: "Calculate scenario", exact: true }).click();
    await page.locator("#scenario-validation-summary").waitFor({ state: "visible" });
    assert.match(await page.locator("#scenario-validation-summary").innerText(), /3% to 20%/u);
    assert.equal(scenarioRequestCount, 0, "Below-minimum discount rate must not reach the API.");

    await discountRate.fill("3.0");
    await terminalGrowth.fill("3.0");
    await page.getByRole("button", { name: "Calculate scenario", exact: true }).click();
    await page.locator("#scenario-validation-summary").waitFor({ state: "visible" });
    assert.match(await page.locator("#scenario-validation-summary").innerText(), /Must be greater than long-run growth/u);
    assert.equal(scenarioRequestCount, 0, "An invalid discount/terminal growth pair must not reach the API.");

    await discountRate.fill("9.4");
    await page.getByRole("button", { name: "Calculate scenario", exact: true }).click();
    await page.locator(".scenario-result").waitFor({ state: "visible" });
    assert.equal(scenarioRequestCount, 1, "Valid assumptions should make exactly one scenario request.");
    const csvDownloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export CSV", exact: true }).click();
    const csvDownload = await csvDownloadPromise;
    assert.match(csvDownload.suggestedFilename(), /msft-research\.csv$/u);
    const csvContent = readFileSync(await csvDownload.path(), "utf8");
    const enterpriseValueRow = csvContent.split(/\r?\n/u).find((row) => row.startsWith('"Enterprise value",'));
    assert.ok(enterpriseValueRow, "CSV must contain the displayed enterprise-value metric.");
    assert.match(enterpriseValueRow, /TSRP calculation/u);
    assert.ok(!enterpriseValueRow.endsWith(',""'), "CSV must include the calculation basis and limitation, not just the source and date.");
    assert.match(csvContent, /"Scenario enterprise value"/u);
    const jsonDownloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export JSON", exact: true }).click();
    const jsonDownload = await jsonDownloadPromise;
    assert.match(jsonDownload.suggestedFilename(), /msft-research\.json$/u);
    const jsonContent = JSON.parse(readFileSync(await jsonDownload.path(), "utf8"));
    assert.equal(jsonContent.company.ticker, "MSFT");
    assert.ok(jsonContent.company.scenario, "The JSON export must include the scenario shown in the interface.");
    assert.ok(csvContent.includes(String(jsonContent.company.scenario.enterprise_value)), "CSV and JSON must export the same calculated scenario enterprise value.");

    assert.deepEqual(browserErrors, [], "The browser journey must not emit uncaught page errors.");
    console.log(`Authenticated Chromium research E2E passed: all five sections and four Open actions; browser history and keyboard navigation; latest-analysis-wins race (slow SLOW completed after FAST)${interactiveAnalysisRecovery ? "; transient analysis failure and successful retry" : ""}; scenario form rejects unsupported margins/rates and invalid discount/terminal pairs before API requests; three market-list states and company selection preserve Valuation; CSV/JSON exports include the selected company and calculated scenario; ${layoutChecks} section/viewport checks at 375/430/768/1024/1440 px; no horizontal overflow or uncaught page errors.`);
  } finally {
    await browser.close();
  }
}

async function runProviderDisabledBrowserE2E(baseUrl, jar) {
  const { chromium } = await import("playwright");
  const configuredExecutable = process.env.TSRP_PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  const macChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const executablePath = configuredExecutable || (process.platform === "darwin" && existsSync(macChrome) ? macChrome : undefined);
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  const pageErrors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addCookies(jar.values().map(({ name, value }) => ({ name, value, url: baseUrl })));
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto(`${baseUrl}/?ticker=AAPL`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Research could not be completed" }).waitFor({ state: "visible" });
    await page.getByText(/Market-data access is not enabled for this deployment\. Contact its operator\./u).waitFor({ state: "visible" });
    assert.equal(await page.getByRole("button", { name: "Try again" }).count(), 0, "A disabled provider must not offer a retry that cannot succeed.");
    assert.equal(new URL(page.url()).searchParams.get("ticker"), "AAPL", "The disabled-provider state must preserve the selected ticker.");

    await page.locator("#company-search").fill("Apple");
    await page.getByText("Company search and stock analysis are not enabled for this deployment. Contact its operator.", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await page.getByRole("button", { name: /^Analyze AAPL as a ticker$/u }).count(), 0, "Disabled company search must not offer a ticker fallback into disabled analysis.");
    assert.deepEqual(pageErrors, [], "The disabled-provider state must not emit browser errors.");
    console.log("Provider-disabled Chromium E2E passed: clear operator-directed analysis/search messages, preserved ticker, no futile Retry or ticker-fallback controls, and no uncaught browser errors.");
  } finally {
    await browser.close();
  }
}

async function runRateLimitBrowserE2E(baseUrl, jar) {
  const { chromium } = await import("playwright");
  const configuredExecutable = process.env.TSRP_PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  const macChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const executablePath = configuredExecutable || (process.platform === "darwin" && existsSync(macChrome) ? macChrome : undefined);
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  const pageErrors = [];
  const rateLimitHeaders = {
    "content-type": "text/plain; charset=utf-8",
    "retry-after": "5",
    "x-request-id": "e".repeat(32),
  };
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addCookies(jar.values().map(({ name, value }) => ({ name, value, url: baseUrl })));
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.route("**/api/analyze**", (route) => route.fulfill({ status: 429, headers: rateLimitHeaders, body: "Too Many Requests" }));
    await page.route("**/api/search**", (route) => route.fulfill({ status: 429, headers: rateLimitHeaders, body: "Too Many Requests" }));
    for (const path of ["movers", "top_stocks"]) {
      await page.route(`**/api/${path}`, (route) => route.fulfill({
        status: 503,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ error: { code: "provider_disabled", message: "Fixture only." } }),
      }));
    }

    await page.goto(`${baseUrl}/?ticker=AAPL`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Research could not be completed" }).waitFor({ state: "visible" });
    const analysisAlert = page.getByRole("alert");
    await analysisAlert.getByText(/temporarily limiting requests\. Wait about 5 seconds/u).waitFor({ state: "visible" });
    await analysisAlert.getByText(new RegExp(`Reference ID: ${"e".repeat(32)}`, "u")).waitFor({ state: "visible" });
    assert.equal(await page.getByRole("button", { name: "Try again" }).count(), 0, "A rate-limited analysis must not offer an immediate retry.");
    assert.equal(new URL(page.url()).searchParams.get("ticker"), "AAPL", "Rate-limited analysis must preserve its selected ticker.");

    const searchInput = page.locator("#company-search");
    await searchInput.fill("");
    await page.waitForTimeout(300);
    const search429 = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/search");
    await searchInput.fill("AAPL");
    assert.equal((await search429).status(), 429, "Company-search fixture must deliver an edge-style plain-text 429.");
    await page.locator(".form-error").getByText(/temporarily limiting requests\. Wait about 5 seconds/u).waitFor({ state: "visible" });
    assert.equal(await page.getByRole("button", { name: /^Analyze AAPL as a ticker$/u }).count(), 0, "Rate-limited search must not offer an immediate ticker fallback.");
    assert.deepEqual(pageErrors, [], "Rate-limit states must not emit browser errors.");
    console.log("Rate-limit Chromium E2E passed: plain-text 429 guidance and request reference render; analysis retry and search ticker fallback are suppressed; ticker is preserved; no uncaught browser errors.");
  } finally {
    await browser.close();
  }
}

async function cloneTreeWithoutRuntimeArtifacts(source, destination) {
  const excluded = new Set([".git", "node_modules", ".next", ".vercel", ".venv", "__pycache__", ".pytest_cache"]);
  await cp(source, destination, {
    recursive: true,
    filter(path) {
      const relativePath = relative(source, path);
      if (!relativePath) return true;
      const parts = relativePath.split(sep);
      if (parts.some((part) => excluded.has(part))) return false;
      const entry = basename(path);
      if (entry.startsWith(".env") && entry !== ".env.example") return false;
      return true;
    },
  });
  const dependencyView = join(destination, "node_modules");
  await mkdir(dependencyView);

  async function linkPackages(from, into, preserveExisting) {
    for (const entry of await readdir(from, { withFileTypes: true })) {
      if (entry.name === ".bin" || entry.name === ".pnpm") continue;
      const sourcePath = join(from, entry.name);
      if (entry.name.startsWith("@") && entry.isDirectory()) {
        const scopePath = join(into, entry.name);
        await mkdir(scopePath, { recursive: true });
        for (const scopedPackage of await readdir(sourcePath)) {
          const destinationPath = join(scopePath, scopedPackage);
          if (preserveExisting) {
            try { await lstat(destinationPath); continue; } catch {}
          }
          await symlink(join(sourcePath, scopedPackage), destinationPath, "dir");
        }
        continue;
      }
      const destinationPath = join(into, entry.name);
      if (preserveExisting) {
        try { await lstat(destinationPath); continue; } catch {}
      }
      await symlink(sourcePath, destinationPath, "dir");
    }
  }

  const originalModules = join(source, "node_modules");
  await linkPackages(originalModules, dependencyView, false);
  await symlink(join(originalModules, ".pnpm"), join(dependencyView, ".pnpm"), "dir");
  await linkPackages(join(originalModules, ".pnpm", "node_modules"), dependencyView, true);
}

async function runFlow() {
try {
  const idpPort = await freePort(loopbackHost);
  const appPort = await freePort(appHost);
  const externalPort = productionMode ? await freePort(appHost) : appPort;
  const tlsOptions = productionMode ? {
    key: readFileSync(process.env.TSRP_AUTH_E2E_TLS_KEY ?? ""),
    cert: readFileSync(process.env.TSRP_AUTH_E2E_TLS_CERT ?? ""),
  } : null;
  const identity = await startMockIssuer(idpPort, tlsOptions);
  idp = identity.server;
  const developmentApiPort = await freePort(loopbackHost);
  devApi = await startMockDevApi(developmentApiPort);
  const baseUrl = productionMode
    ? `https://${appHost}:${externalPort}`
    : `http://${appHost}:${appPort}`;

  if (productionMode) {
    proxy = await startTlsProxy(externalPort, appPort, tlsOptions);
  }
  projectCopy = await mkdtemp(join(tmpdir(), "tsrp-auth-e2e-"));
  assert.match(projectCopy, /[/\\]tsrp-auth-e2e-[^/\\]+$/u);
  await cloneTreeWithoutRuntimeArtifacts(checkout, projectCopy);

  const childEnv = {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    TMPDIR: process.env.TMPDIR,
    NODE_ENV: productionMode ? "production" : "development",
    NEXT_TELEMETRY_DISABLED: "1",
    AUTH_TRUST_HOST: "true",
    AUTH_OIDC_ISSUER: identity.issuer,
    AUTH_OIDC_CLIENT_ID: clientId,
    AUTH_OIDC_CLIENT_SECRET: clientSecret,
    AUTH_SECRET: authSecret,
    TSRP_API_TOKEN_SECRET: apiSecret,
    TSRP_DEV_API_ORIGIN: `http://${loopbackHost}:${developmentApiPort}`,
    ...(productionMode ? {
      ...(!process.env.TSRP_AUTH_E2E_INFER_ORIGIN ? { AUTH_URL: baseUrl } : {}),
      NODE_EXTRA_CA_CERTS: process.env.TSRP_AUTH_E2E_TLS_CERT,
    } : {}),
  };
  if (productionMode) {
    app = spawn(process.execPath, ["node_modules/next/dist/bin/next", "build", "--webpack"], {
      cwd: projectCopy,
      env: childEnv,
      stdio: ["ignore", "pipe", "pipe"],
    });
    for (const stream of [app.stdout, app.stderr]) {
      stream.setEncoding("utf8");
      stream.on("data", (chunk) => { appOutput = `${appOutput}${chunk}`.slice(-5000); });
    }
    const buildStatus = await new Promise((resolve, reject) => {
      app.once("error", reject);
      app.once("close", (status, signal) => resolve(status ?? (signal ? 1 : 0)));
    });
    app = null;
    if (buildStatus !== 0) throw new Error(`Isolated production build failed with status ${buildStatus}.\n${appOutput}`);
    appOutput = "";
  }
  app = spawn(process.execPath, [
    "node_modules/next/dist/bin/next",
    ...(productionMode ? ["start"] : ["dev", "--webpack"]),
    "--hostname", appHost,
    "--port", String(appPort),
  ], {
    cwd: projectCopy,
    env: childEnv,
    stdio: ["ignore", "pipe", "pipe"],
  });
  for (const stream of [app.stdout, app.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => { appOutput = `${appOutput}${chunk}`.slice(-5000); });
  }
  await waitForApp(baseUrl);

  const anonymous = await fetch(`${baseUrl}/?ticker=AAPL&section=Valuation`, { redirect: "manual" });
  assert.ok([302, 307].includes(anonymous.status));
  assert.match(anonymous.headers.get("location") ?? "", /^\/signin\?/u);
  const unauthenticatedToken = await fetch(`${baseUrl}/api/backend-token`, {
    method: "POST",
    headers: { Origin: baseUrl },
  });
  assert.equal(unauthenticatedToken.status, 401, await unauthenticatedToken.clone().text());
  assert.equal(unauthenticatedToken.headers.get("cache-control"), "private, no-store");

  const cookies = makeCookieJar();
  const authorizationUrl = await beginSignIn(baseUrl, cookies);
  assert.equal(new URL(authorizationUrl).origin, new URL(identity.issuer).origin);
  const callsBeforeBadState = identity.tokenRequests();
  const badStateResponse = await finishSignIn(baseUrl, authorizationUrl, cookies, "attacker-state");
  assert.ok([302, 303].includes(badStateResponse.status));
  assert.equal(badStateResponse.headers.get("cache-control"), "private, no-store");
  assert.equal(badStateResponse.headers.get("referrer-policy"), "no-referrer");
  assert.match(badStateResponse.headers.get("location") ?? "", /error=/u);
  assert.equal(identity.tokenRequests(), callsBeforeBadState, "State failure must occur before token exchange.");
  const auth = identity.latestAuthorization();
  assert.ok(auth);
  assert.equal(auth.client_id, clientId);

  const callbackResponse = await finishSignIn(baseUrl, authorizationUrl, cookies);
  assert.ok([302, 303].includes(callbackResponse.status));
  assert.equal(callbackResponse.headers.get("cache-control"), "private, no-store");
  assert.equal(callbackResponse.headers.get("referrer-policy"), "no-referrer");
  assert.equal(callbackResponse.headers.get("location"), `${baseUrl}/?ticker=AAPL&section=Valuation`);
  assert.equal(identity.tokenRequests(), callsBeforeBadState + 1);
  if (productionMode) {
    const sessionCookie = callbackResponse.headers.getSetCookie().find((cookie) => /session-token/iu.test(cookie));
    assert.ok(sessionCookie, "Production sign-in must issue a session cookie.");
    assert.match(sessionCookie, /;\s*Secure(?:;|$)/iu, "Production session cookie must be Secure.");
    assert.match(sessionCookie, /;\s*HttpOnly(?:;|$)/iu, "Production session cookie must be HttpOnly.");
    assert.match(sessionCookie, /;\s*SameSite=Lax(?:;|$)/iu, "Production session cookie must use SameSite=Lax.");
  }
  cookies.absorb(callbackResponse);

  const sessionResponse = await fetch(`${baseUrl}/api/auth/session`, { headers: { Cookie: cookies.header() } });
  assert.equal(sessionResponse.status, 200);
  assert.doesNotMatch(sessionResponse.headers.get("content-security-policy") ?? "", /'nonce-/u, "JSON auth endpoints must not be handled as nonce-bearing documents.");
  const session = await sessionResponse.json();
  assert.deepEqual(Object.keys(session.user), ["authenticated", "browserStorageScope"]);
  assert.equal(session.user.authenticated, true);
  assert.match(session.user.browserStorageScope, /^[a-f0-9]{32}$/u);
  assert.doesNotMatch(JSON.stringify(session), /mock-oidc-account|must-not-enter-session@example\.test|Must Not Enter Session/u);

  const observedNonces = new Set();
  function assertNoncedPage(response, html, label) {
    const policy = response.headers.get("content-security-policy") ?? "";
    if (productionMode) {
      assert.match(response.headers.get("cache-control") ?? "", /\b(?:private|no-store|no-cache)\b/iu, `${label} must not be reusable from a shared cache.`);
    }
    assert.equal((policy.match(/\bscript-src\b/gu) ?? []).length, 1, `${label} must receive exactly one script policy.`);
    assert.equal((policy.match(/\bstyle-src\b/gu) ?? []).length, 1, `${label} must receive exactly one style policy.`);
    const scriptSource = policy.match(/(?:^|;\s*)script-src\s+([^;]+)/u)?.[1] ?? "";
    const styleSource = policy.match(/(?:^|;\s*)style-src\s+([^;]+)/u)?.[1] ?? "";
    const connectSource = policy.match(/(?:^|;\s*)connect-src\s+([^;]+)/u)?.[1] ?? "";
    const nonce = scriptSource.match(/'nonce-([^']+)'/u)?.[1];
    assert.ok(nonce, `${label} must receive a CSP nonce.`);
    assert.match(scriptSource, /'strict-dynamic'/u);
    assert.doesNotMatch(scriptSource, /'unsafe-inline'/u);
    if (productionMode) {
      assert.equal(connectSource, "'self'", "Production browser requests must remain same-origin.");
      assert.doesNotMatch(scriptSource, /'unsafe-eval'/u, "Production script policy must not allow eval.");
      assert.ok(styleSource.includes(`'nonce-${nonce}'`), "Production style policy must carry the document nonce.");
      assert.doesNotMatch(styleSource, /'unsafe-inline'/u, "Production style policy must not allow inline styles.");
    } else {
      assert.equal(connectSource, "'self'", "Development browser requests must remain same-origin through the Next API proxy.");
      assert.match(scriptSource, /'unsafe-eval'/u, "Development tooling requires eval only in development.");
      assert.match(styleSource, /'unsafe-inline'/u, "Next.js development tooling injects inline style elements.");
    }
    assert.doesNotMatch(html, /\sstyle="/u, `${label} must not emit inline style attributes.`);
    const scripts = html.match(/<script\b[^>]*>/gu) ?? [];
    assert.ok(scripts.length > 0, `${label} must contain rendered Next.js scripts.`);
    for (const script of scripts) {
      assert.equal(script.match(/\bnonce="([^"]+)"/u)?.[1], nonce, `${label}: every rendered script must use the response CSP nonce.`);
    }
    if (productionMode) {
      const styleTags = html.match(/<style\b[^>]*>/gu) ?? [];
      for (const style of styleTags) {
        assert.equal(style.match(/\bnonce="([^"]+)"/u)?.[1], nonce, "Every rendered style tag must use the response CSP nonce.");
      }
    }
    assert.equal(observedNonces.has(nonce), false, "Each HTML response must use a fresh CSP nonce.");
    observedNonces.add(nonce);
  }

  const signInPage = await fetch(`${baseUrl}/signin?callbackUrl=%2F%3Fticker%3DAAPL%26section%3DValuation`);
  assert.equal(signInPage.status, 200);
  const signInHtml = await signInPage.text();
  assert.match(signInHtml, /Sign in to TSRP/);
  assertNoncedPage(signInPage, signInHtml, "Sign-in page");

  const privacyPage = await fetch(`${baseUrl}/privacy?ticker=AAPL&section=Valuation`);
  assert.equal(privacyPage.status, 200);
  const privacyHtml = await privacyPage.text();
  assert.match(privacyHtml, /Privacy &amp; data/);
  assert.match(privacyHtml, /selected ticker is included in the <code>\/api\/analyze<\/code> URL query/i);
  const privacyCopy = privacyHtml.replace(/<!-- -->/gu, "");
  assert.match(privacyCopy, /requests only the selected list—Top movers, Most active, or Popular stocks\. Switching lists requests the newly selected list\. The active list refreshes every 5 minutes while the page is visible; background tabs pause polling/i);
  assert.doesNotMatch(privacyCopy, /Top Movers or Top Stocks/u);
  assert.match(privacyHtml, /ticker and selected research section also appear in the page URL and browser history/i);
  assert.match(privacyHtml, /not placed in the browser-to-TSRP request URL or included in TSRP application logs/i);
  assert.match(privacyHtml, /short-lived authorization code and state in the callback URL/i);
  assert.match(privacyHtml, /runtime log details can show request paths and search parameters/i);
  assert.match(privacyHtml, /PKCE is required to redeem the code/i);
  assert.match(privacyHtml, /callback responses are not cached and use a no-referrer policy; this does not hide query values from platform logs/i);
  assert.match(privacyHtml, /forwarded to Yahoo Finance, including as a query parameter in the provider’s search request/i);
  assert.match(privacyHtml, /does not send research inputs to a generative-AI analysis provider or load a third-party analytics SDK/i);
  assert.match(privacyHtml, /does not limit the hosting, identity, or financial-data providers’ own operational logging and retention/i);
  assert.match(privacyCopy, /A token expires after at most 60 seconds and is not individually revoked when you sign out/i);
  assert.match(privacyHtml, /If a saved snapshot exists, opening “What Changed\?” or saving another snapshot for comparison sends a reduced set of data from the current analysis and the most recent saved snapshot/i);
  assert.match(privacyHtml, /Opening the section with no saved snapshot, or saving the first snapshot, does not make this comparison request/i);
  assert.match(privacyHtml, /<code>\/api\/snapshot_diff<\/code>/);
  assert.match(privacyHtml, /hosting provider processes API request bodies and metadata/i);
  assert.match(privacyHtml, /four scenario assumption fields are held in this tab under an opaque account-specific namespace and ticker/i);
  assert.match(privacyHtml, /unsubmitted drafts are not sent to TSRP/i);
  assert.match(privacyHtml, /up to eight snapshots per ticker are stored in this browser’s local storage under an opaque account-specific namespace/i);
  assert.match(privacyHtml, /automatically removes the oldest saved snapshot for that ticker/i);
  assert.match(privacyHtml, /new snapshots keep only fields needed for comparison/i);
  assert.match(privacyHtml, /snapshots created before this data-minimization change may retain additional analysis fields/i);
  assert.match(privacyHtml, /remove a ticker’s snapshots in “What Changed\?” or use the control below to remove all account-scoped snapshots, any legacy unscoped snapshots, and this tab’s scenario drafts/i);
  assert.match(privacyHtml, /different TSRP sign-ins in the same browser do not see each other’s snapshots in the app/i);
  assert.match(privacyHtml, /HMAC-derived opaque storage namespace so saved local data is separated between sign-ins/i);
  assert.match(privacyHtml, /Clear saved research/i);
  assert.match(privacyHtml, /CSV and JSON exports are assembled in your browser and downloaded to your device; TSRP does not upload or retain the export file/i);
  assert.match(privacyHtml, /Formula-like text in CSV cells is prefixed with a tab to reduce spreadsheet formula execution risk.*This is not a universal CSV-safety guarantee/i);
  assert.match(privacyHtml, /SEC EDGAR requests are made only when <code>SEC_USER_AGENT<\/code> contains a client or organization name and one monitored contact email/i);
  assert.match(privacyHtml, /Yahoo data rights for public display have not been cleared/i);
  assert.match(privacyHtml, /href="\/\?ticker=AAPL&amp;section=Valuation"/);
  assertNoncedPage(privacyPage, privacyHtml, "Privacy page");

  const notFoundPage = await fetch(`${baseUrl}/__tsrp-csp-not-found__`);
  assert.equal(notFoundPage.status, 404);
  const notFoundHtml = await notFoundPage.text();
  assert.match(notFoundHtml, /We couldn’t find that page\./u);
  assertNoncedPage(notFoundPage, notFoundHtml, "Not-found page");

  const primarySections = [
    "TSRP Score",
    "Expectations vs Reality",
    "Valuation",
    "Catalysts & Risks",
    "What Changed?",
  ];
  for (const section of primarySections) {
    const pageUrl = new URL("/", baseUrl);
    pageUrl.searchParams.set("ticker", "AAPL");
    if (section !== "TSRP Score") pageUrl.searchParams.set("section", section);
    const signedInPage = await fetch(pageUrl, { headers: { Cookie: cookies.header() } });
    assert.equal(signedInPage.status, 200, `${section} must render for the authenticated user.`);
    const html = await signedInPage.text();
    const nav = html.match(/<nav\b[^>]*aria-label="Research sections"[^>]*>([\s\S]*?)<\/nav>/u)?.[1] ?? "";
    const activeLinks = nav.match(/<a\b[^>]*aria-current="page"[^>]*>[\s\S]*?<\/a>/gu) ?? [];
    assert.equal(activeLinks.length, 1, `${section} must have exactly one active primary section.`);
    assert.equal(activeLinks[0].replace(/<[^>]*>/gu, ""), section.replaceAll("&", "&amp;"));
    assert.match(html, /value="AAPL"/);
    assert.doesNotMatch(html, /must-not-enter-session@example\.test|Must Not Enter Session/u);

    assertNoncedPage(signedInPage, html, section);
  }
  if (!productionMode) {
    const proxyCases = [
      { method: "GET", path: "/api/analyze?ticker=AAPL" },
      { method: "GET", path: "/api/movers" },
      { method: "GET", path: "/api/top_stocks" },
      { method: "GET", path: "/api/top_stocks?list=popular" },
      { method: "POST", path: "/api/scenario", body: { ticker: "AAPL" } },
      { method: "POST", path: "/api/search", body: { q: "Apple" } },
      { method: "POST", path: "/api/snapshot_diff", body: { previous: {}, current: {} } },
    ];
    for (const { method, path, body } of proxyCases) {
      const proxyResponse = await fetch(`${baseUrl}${path}`, {
        method,
        headers: { Origin: baseUrl, ...(body ? { "Content-Type": "application/json" } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      assert.equal(proxyResponse.status, 200, `${method} ${path} must reach the local Python API proxy.`);
      assert.deepEqual(await proxyResponse.json(), { method, path });
    }
    assert.deepEqual(devApi.requests, proxyCases.map(({ method, path }) => ({ method, path })));
  } else {
    await fetch(`${baseUrl}/api/search`, { method: "POST", headers: { Cookie: cookies.header() } });
    assert.deepEqual(devApi.requests, [], "Production must ignore TSRP_DEV_API_ORIGIN and never use the development API proxy.");
  }

  const tokenResponse = await fetch(`${baseUrl}/api/backend-token`, {
    method: "POST",
    headers: { Origin: baseUrl, Cookie: cookies.header() },
  });
  assert.equal(tokenResponse.status, 200);
  assert.equal(tokenResponse.headers.get("cache-control"), "private, no-store");
  const tokenPayload = await tokenResponse.json();
  const [header, claims, signature] = tokenPayload.data.access_token.split(".");
  assert.deepEqual(JSON.parse(Buffer.from(header, "base64url").toString()), { alg: "HS256", typ: "JWT" });
  const issuedClaims = JSON.parse(Buffer.from(claims, "base64url").toString());
  assert.deepEqual(Object.keys(issuedClaims).sort(), ["aud", "exp", "iat", "iss", "sub"]);
  assert.equal(issuedClaims.iss, "tsrp-web");
  assert.equal(issuedClaims.aud, "tsrp-api");
  assert.equal(issuedClaims.exp, issuedClaims.iat + 60);
  assert.match(issuedClaims.sub, /^[a-f0-9]{64}$/u);
  assert.notEqual(issuedClaims.sub, createHmac("sha256", apiSecret).update("tsrp-session-subject\0").update(identity.issuer).update("\0").update(providerSubject).digest("hex"));
  assert.equal(signature, createHmac("sha256", apiSecret).update(`${header}.${claims}`).digest("base64url"));
  assert.equal(tokenPayload.data.expires_in, 60);
  const crossOriginToken = await fetch(`${baseUrl}/api/backend-token`, {
    method: "POST",
    headers: { Origin: "https://attacker.example", Cookie: cookies.header() },
  });
  assert.equal(crossOriginToken.status, 403);
  assert.equal(crossOriginToken.headers.get("cache-control"), "private, no-store");

  const csrfResponse = await fetch(`${baseUrl}/api/auth/csrf`, { headers: { Cookie: cookies.header() } });
  cookies.absorb(csrfResponse);
  const { csrfToken } = await csrfResponse.json();
  const signOut = await fetch(`${baseUrl}/api/auth/signout`, {
    method: "POST",
    redirect: "manual",
    headers: { Origin: baseUrl, "Content-Type": "application/x-www-form-urlencoded", Cookie: cookies.header() },
    body: new URLSearchParams({ csrfToken, callbackUrl: `${baseUrl}/signin` }),
  });
  assert.ok([302, 303].includes(signOut.status));
  cookies.absorb(signOut);
  assert.doesNotMatch(cookies.header(), /(?:__Secure-)?(?:authjs|next-auth)\.session-token=/u);
  const signedOutToken = await fetch(`${baseUrl}/api/backend-token`, {
    method: "POST",
    headers: { Origin: baseUrl, Cookie: cookies.header() },
  });
  assert.equal(signedOutToken.status, 401);

  identity.useWrongNonce();
  const secondCookies = makeCookieJar();
  const secondAuthorization = await beginSignIn(baseUrl, secondCookies);
  const badNonceResponse = await finishSignIn(baseUrl, secondAuthorization, secondCookies);
  assert.ok([302, 303].includes(badNonceResponse.status));
  assert.match(badNonceResponse.headers.get("location") ?? "", /error=/u);
  const badNonceSession = await fetch(`${baseUrl}/api/auth/session`, { headers: { Cookie: secondCookies.header() } });
  assert.equal(await badNonceSession.json(), null);

  console.log(`${productionMode ? "Production HTTPS" : "Development HTTP"} OIDC integration passed: anonymous access denied; state and PKCE checked; fresh CSP nonces cover sign-in, privacy, not-found, and all five research sections${productionMode ? "; rendered scripts/styles match strict nonce policies; eval and inline styles are disabled; session cookie is Secure/HttpOnly/SameSite=Lax" : "; API requests use the same-origin Next.js proxy; inline styles are allowed only for Next.js development tooling"}; AAPL state/active navigation preserved; token is signed with a random per-token subject/no-store; sign-out clears the browser session cookie and blocks new backend-token issuance. Stateless sessions and already-issued backend tokens are not globally revoked.`);

  if (interactiveMode) {
    identity.setWrongNonce(false);
    await new Promise((resolve, reject) => devApi.server.close((error) => error ? reject(error) : resolve()));
    devApi = null;
    if (!interactiveRateLimit) {
      await startFixtureApi(developmentApiPort, { searchRace: interactiveSearchRace, analysisRace: interactiveAnalysisRace || (browserE2EMode && !interactiveProviderDisabled), analysisFailure: interactiveAnalysisFailure, analysisRecovery: interactiveAnalysisRecovery, providerDisabled: interactiveProviderDisabled, scenarioRace: interactiveScenarioRace, snapshotRace: interactiveSnapshotRace, singleChartPoint: interactiveSingleChartPoint });
    }
    console.log(interactiveUnsupportedSector
      ? "Local browser QA is using synthetic Financial Services company data to verify unavailable score/valuation handling; public market providers and SEC are disabled."
      : interactiveSearchRace || interactiveAnalysisRace || interactiveAnalysisFailure || interactiveAnalysisRecovery || interactiveProviderDisabled || interactiveRateLimit || interactiveScenarioRace || interactiveSnapshotRace || interactiveSingleChartPoint
      ? `Local browser QA is using synthetic Example Corporation data; ${[
        interactiveSearchRace ? "slow search resolves after fast search" : "",
        interactiveAnalysisRace ? "slow company analysis resolves after fast analysis" : "",
        interactiveAnalysisFailure ? "company analysis returns a synthetic provider-unavailable error for retry-loop verification" : "",
        interactiveAnalysisRecovery ? "a selected company fails once and then succeeds through the Retry button" : "",
        interactiveProviderDisabled ? "provider access is disabled and the interface suppresses futile retries" : "",
        interactiveRateLimit ? "browser requests receive synthetic plain-text HTTP 429 responses" : "",
        interactiveScenarioRace ? "delayed scenario completes after an assumption edit" : "",
        interactiveSnapshotRace ? "saved comparison completes after navigation" : "",
        interactiveSingleChartPoint ? "one valid price-history observation" : "",
      ].filter(Boolean).join(" and ")}. Market feeds are unavailable; Yahoo/SEC access is disabled.`
      : "Local browser QA is using synthetic Example Corporation analysis data. Market feeds/search are unavailable; Yahoo/SEC access is disabled.");
    if (browserE2EMode) {
      const browserCookies = makeCookieJar();
      const browserAuthorization = await beginSignIn(baseUrl, browserCookies);
      const browserCallback = await finishSignIn(baseUrl, browserAuthorization, browserCookies);
      assert.ok([302, 303].includes(browserCallback.status), "Browser E2E sign-in must complete against the local mock issuer.");
      browserCookies.absorb(browserCallback);
      if (interactiveProviderDisabled) await runProviderDisabledBrowserE2E(baseUrl, browserCookies);
      else if (interactiveRateLimit) await runRateLimitBrowserE2E(baseUrl, browserCookies);
      else await runResearchBrowserE2E(baseUrl, browserCookies);
    } else {
      console.log(`Open ${baseUrl}/?ticker=AAPL to inspect the authenticated research flow.`);
      console.log("Press Ctrl+C in this terminal to stop the temporary app, mock identity provider, and synthetic API.");
      await new Promise((resolve) => {
        process.once("SIGINT", resolve);
        process.once("SIGTERM", resolve);
      });
    }
  }
} finally {
  if (app && app.exitCode === null) {
    app.kill("SIGTERM");
    await Promise.race([new Promise((resolve) => app.once("exit", resolve)), delay(3000)]);
    if (app.exitCode === null) app.kill("SIGKILL");
  }
  if (idp?.listening) await new Promise((resolve) => idp.close(resolve));
  if (proxy?.listening) await new Promise((resolve) => proxy.close(resolve));
  if (devApi?.server.listening) await new Promise((resolve) => devApi.server.close(resolve));
  if (fixtureApi && fixtureApi.exitCode === null) {
    fixtureApi.kill("SIGTERM");
    await Promise.race([new Promise((resolve) => fixtureApi.once("exit", resolve)), delay(3000)]);
    if (fixtureApi.exitCode === null) fixtureApi.kill("SIGKILL");
  }
  if (projectCopy && /^[/\\].*[/\\]tsrp-auth-e2e-[^/\\]+$/u.test(projectCopy)) {
    await rm(projectCopy, { recursive: true, force: true });
  }
}
}

async function runProductionWithEphemeralCertificate() {
  const certificateDirectory = await mkdtemp(join(tmpdir(), "tsrp-auth-prod-cert-"));
  assert.match(certificateDirectory, /[/\\]tsrp-auth-prod-cert-[^/\\]+$/u);
  const cert = join(certificateDirectory, "localhost.pem");
  const key = join(certificateDirectory, "localhost-key.pem");
  try {
    execFileSync("openssl", [
      "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
      "-keyout", key, "-out", cert, "-subj", "/CN=localhost",
      "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
      "-addext", "basicConstraints=critical,CA:TRUE",
    ], { stdio: "ignore" });
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), "--production"], {
      env: {
        ...process.env,
        NODE_EXTRA_CA_CERTS: cert,
        TSRP_AUTH_E2E_TLS_CERT: cert,
        TSRP_AUTH_E2E_TLS_KEY: key,
      },
      stdio: "inherit",
    });
    const code = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (status, signal) => resolve(status ?? (signal ? 1 : 0)));
    });
    if (code !== 0) throw new Error(`Production OIDC integration exited with status ${code}.`);
  } finally {
    if (/[/\\]tsrp-auth-prod-cert-[^/\\]+$/u.test(certificateDirectory)) {
      await rm(certificateDirectory, { recursive: true, force: true });
    }
  }
}

try {
  if (productionMode && !process.env.TSRP_AUTH_E2E_TLS_CERT) {
    await runProductionWithEphemeralCertificate();
  } else {
    await runFlow();
  }
} catch (error) {
  if (appOutput) console.error(appOutput);
  throw error;
}
