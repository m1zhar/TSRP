import assert from "node:assert/strict";
import { request } from "node:http";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { createServer as createHttpServer } from "node:http";
import { createHmac } from "node:crypto";
import { cp, mkdtemp, rm, symlink } from "node:fs/promises";
import { basename, delimiter, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { tmpdir } from "node:os";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const host = "127.0.0.1";
async function allocatePort() {
  const probe = createServer();
  await new Promise((resolve, reject) => {
    probe.once("error", reject);
    probe.listen(0, host, resolve);
  });
  const address = probe.address();
  if (!address || typeof address === "string") throw new Error("Could not allocate a local test port.");
  await new Promise((resolve, reject) => probe.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

const frontendPort = await allocatePort();
const apiPort = await allocatePort();
const proxyPort = await allocatePort();
const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../..");
const configuredPython = process.env.TSRP_PYTHON;
let python = resolve(repoRoot, "backend/.venv/bin/python");
if (configuredPython) {
  python = isAbsolute(configuredPython) || !/[\\/]/u.test(configuredPython)
    ? configuredPython
    : resolve(process.cwd(), configuredPython);
}
const publicOrigin = `http://${host}:${proxyPort}`;
const canonicalOrigin = "https://tsrp-production-smoke.invalid";
const observedNonces = new Set();
const apiTestSecret = "local-production-smoke-api-secret-not-for-deployment-123456";
const scenarioTestSecret = "local-production-smoke-scenario-secret-not-for-deployment-123456";
const pythonPath = [resolve(repoRoot, "backend"), process.env.PYTHONPATH].filter(Boolean).join(delimiter);
const processStartErrors = new WeakMap();

const frontendSource = resolve(repoRoot, "frontend");
const productionFrontend = await mkdtemp(join(tmpdir(), "tsrp-production-smoke-"));
const excludedFrontendPaths = new Set([".git", "node_modules", ".next", ".vercel", ".venv", "__pycache__", ".pytest_cache", "tsconfig.tsbuildinfo"]);
const isolatedNextEnv = {
  PATH: process.env.PATH,
  HOME: process.env.HOME,
  TMPDIR: process.env.TMPDIR,
  NODE_ENV: "production",
  NEXT_TELEMETRY_DISABLED: "1",
  VERCEL_ENV: "production",
  VERCEL_PROJECT_PRODUCTION_URL: new URL(canonicalOrigin).host,
  TSRP_API_TOKEN_SECRET: apiTestSecret,
};
try {
await cp(frontendSource, productionFrontend, {
  recursive: true,
  filter(path) {
    const relativePath = relative(frontendSource, path);
    if (!relativePath) return true;
    const parts = relativePath.split(sep);
    if (parts.some((part) => excludedFrontendPaths.has(part))) return false;
    const entry = basename(path);
    return !entry.startsWith(".env") || entry === ".env.example";
  },
});
await symlink(resolve(frontendSource, "node_modules"), join(productionFrontend, "node_modules"), "dir");

let buildOutput = "";
const nextBuild = spawn(process.execPath, ["node_modules/next/dist/bin/next", "build", "--webpack"], {
  cwd: productionFrontend,
  env: isolatedNextEnv,
  stdio: ["ignore", "pipe", "pipe"],
});
for (const stream of [nextBuild.stdout, nextBuild.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => { buildOutput = `${buildOutput}${chunk}`.slice(-5000); });
}
await new Promise((resolveBuild, rejectBuild) => {
  nextBuild.once("error", rejectBuild);
  nextBuild.once("exit", (code) => code === 0
    ? resolveBuild()
    : rejectBuild(new Error(`Fresh disposable Next.js production build failed (exit ${code}).\n${buildOutput}`)));
});
} catch (error) {
  await rm(productionFrontend, { recursive: true, force: true });
  throw error;
}

function testAccessToken() {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const signed = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ iss: "tsrp-web", aud: "tsrp-api", sub: "a".repeat(64), iat: now, exp: now + 60 })}`;
  return `${signed}.${createHmac("sha256", apiTestSecret).update(signed, "ascii").digest("base64url")}`;
}

const apiServer = spawn(python, [resolve(scriptDir, "asgi-http-server.py"), String(apiPort)], {
  cwd: repoRoot,
  env: {
    ...process.env,
    PYTHONPATH: pythonPath,
    TSRP_API_TOKEN_SECRET: apiTestSecret,
    TSRP_SCENARIO_SIGNING_SECRET: scenarioTestSecret,
    // This child runs the synthetic MockEquity adapter above; never enable this
    // in the real deployment environment or a test without provider mocks.
    TSRP_YAHOO_FINANCE_ENABLED: "true",
    SEC_USER_AGENT: "",
  },
  stdio: ["ignore", "pipe", "pipe"],
});
apiServer.once("error", (error) => processStartErrors.set(apiServer, error));
let apiOutput = "";
for (const stream of [apiServer.stdout, apiServer.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => { apiOutput = `${apiOutput}${chunk}`.slice(-4000); });
}

const server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "--hostname", host, "--port", String(frontendPort)], {
  cwd: productionFrontend,
  env: isolatedNextEnv,
  stdio: ["ignore", "pipe", "pipe"],
});
server.once("error", (error) => processStartErrors.set(server, error));
let serverOutput = "";
for (const stream of [server.stdout, server.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => { serverOutput = `${serverOutput}${chunk}`.slice(-4000); });
}

const proxy = createHttpServer((incoming, outgoing) => {
  const isFrontendApiRequest = incoming.url === "/api/backend-token" || incoming.url?.startsWith("/api/auth/") === true;
  const isApiRequest = incoming.url?.startsWith("/api/") === true && !isFrontendApiRequest;
  const upstreamPort = isApiRequest ? apiPort : frontendPort;
  const upstream = request({
    hostname: host,
    port: upstreamPort,
    path: incoming.url,
    method: incoming.method,
    headers: { ...incoming.headers, host: `${host}:${upstreamPort}` },
  }, (response) => {
    outgoing.writeHead(response.statusCode ?? 502, response.headers);
    response.pipe(outgoing);
  });
  upstream.on("error", () => {
    if (!outgoing.headersSent) outgoing.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    outgoing.end("The local integration upstream is unavailable.");
  });
  incoming.pipe(upstream);
});

async function waitForProcessUrl(url, child, output, label) {
  const deadline = Date.now() + 20_000;
  let lastError;
  while (Date.now() < deadline) {
    const startError = processStartErrors.get(child);
    if (startError) {
      const guidance = child === apiServer
        ? " Create and sync the backend Python 3.12 environment as documented, or set TSRP_PYTHON to that interpreter."
        : " Check that the frontend runtime and production build are available.";
      throw new Error(`${label} could not start: ${startError.message}.${guidance}`);
    }
    if (child.exitCode !== null) throw new Error(`${label} exited early.\n${output()}`);
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
      lastError = new Error(`Unexpected HTTP ${response.status}.`);
    } catch (error) {
      lastError = error;
    }
    await delay(200);
  }
  throw new Error(`${label} did not become ready: ${lastError?.message ?? "unknown error"}\n${output()}`);
}

async function getRenderedPage(path) {
  const deadline = Date.now() + 20_000;
  let lastError;
  while (Date.now() < deadline) {
    const apiStartError = processStartErrors.get(apiServer);
    if (apiStartError) {
      throw new Error(`Production API adapter could not start: ${apiStartError.message}. Create and sync the backend Python 3.12 environment as documented, or set TSRP_PYTHON to that interpreter.`);
    }
    const serverStartError = processStartErrors.get(server);
    if (serverStartError) {
      throw new Error(`Production server could not start: ${serverStartError.message}. Check that the frontend runtime and production build are available.`);
    }
    if (server.exitCode !== null) throw new Error(`Production server exited early.\n${serverOutput}`);
    if (apiServer.exitCode !== null) throw new Error(`Production API adapter exited early.\n${apiOutput}`);
    try {
      const response = await fetch(`${publicOrigin}${path}`, { signal: AbortSignal.timeout(1500) });
      if (response.status === 200) return { response, html: await response.text() };
      lastError = new Error(`Unexpected HTTP ${response.status} for ${path}.`);
    } catch (error) {
      lastError = error;
    }
    await delay(200);
  }
  throw new Error(`Production server did not become ready: ${lastError?.message ?? "unknown error"}\n${serverOutput}`);
}

function assertStrictCsp(response, html, label) {
  const policy = response.headers.get("content-security-policy") ?? "";
  const scriptSource = policy.match(/(?:^|;\s*)script-src\s+([^;]+)/u)?.[1] ?? "";
  const styleSource = policy.match(/(?:^|;\s*)style-src\s+([^;]+)/u)?.[1] ?? "";
  const nonce = scriptSource.match(/'nonce-([^']+)'/u)?.[1];
  assert.ok(nonce, `${label} must have a per-response script nonce.`);
  assert.match(scriptSource, /'strict-dynamic'/u);
  assert.doesNotMatch(scriptSource, /'unsafe-inline'|'unsafe-eval'/u);
  assert.ok(styleSource.includes(`'nonce-${nonce}'`), `${label} must share its nonce with styles.`);
  assert.doesNotMatch(styleSource, /'unsafe-inline'/u);
  assert.doesNotMatch(html, /\sstyle=/u, `${label} must not emit inline style attributes.`);

  const scripts = html.match(/<script\b[^>]*>/gu) ?? [];
  assert.ok(scripts.length > 0, `${label} must render framework scripts.`);
  for (const script of scripts) {
    assert.equal(script.match(/\bnonce="([^"]+)"/u)?.[1], nonce, `${label} script must carry the policy nonce.`);
  }
  for (const style of html.match(/<style\b[^>]*>/gu) ?? []) {
    assert.equal(style.match(/\bnonce="([^"]+)"/u)?.[1], nonce, `${label} inline style must carry the policy nonce.`);
  }
  assert.equal(observedNonces.has(nonce), false, "Each rendered response must use a fresh nonce.");
  observedNonces.add(nonce);
}

try {
  await waitForProcessUrl(`http://${host}:${apiPort}/health`, apiServer, () => apiOutput, "Production ASGI API");
  await new Promise((resolve, reject) => {
    proxy.once("error", reject);
    proxy.listen(proxyPort, host, resolve);
  });
  await waitForProcessUrl(`http://${host}:${frontendPort}/`, server, () => serverOutput, "Next.js production server");

  const home = await getRenderedPage("/");
  assertStrictCsp(home.response, home.html, "Home page");
  assert.equal(home.response.headers.get("x-frame-options"), "DENY");
  assert.equal(home.response.headers.get("x-content-type-options"), "nosniff");
  assert.match(home.html, /<title>TSRP — Saleh Research Project<\/title>/);
  assert.doesNotMatch(home.html, /Sign in to TSRP|Sign-in setup is incomplete|Sign out/);
  assert.match(home.html, /TSRP Score/);
  const signInPage = await fetch(`${publicOrigin}/signin?callbackUrl=%2F%3Fticker%3DAAPL`, {
    redirect: "manual",
    signal: AbortSignal.timeout(3000),
  });
  assert.ok([307, 308].includes(signInPage.status), "The former sign-in URL must return visitors to public research.");
  assert.equal(signInPage.headers.get("location"), "/?ticker=AAPL");
  const authEndpoint = await fetch(`${publicOrigin}/api/auth/session`, { signal: AbortSignal.timeout(3000) });
  assert.equal(authEndpoint.status, 404, "Account authentication endpoints must not be exposed.");
  const tokenResponse = await fetch(`${publicOrigin}/api/backend-token`, {
    method: "POST",
    headers: { Origin: canonicalOrigin },
    signal: AbortSignal.timeout(3000),
  });
  const tokenPayload = await tokenResponse.json();
  const browserAccessToken = tokenPayload.data?.access_token;
  assert.equal(tokenResponse.status, 200, "A reviewer must receive API access without a cookie or account.");
  assert.equal(tokenResponse.headers.get("cache-control"), "no-store");
  assert.equal(tokenResponse.headers.get("set-cookie"), null, "Anonymous API access must not create an account session.");
  assert.equal(typeof browserAccessToken, "string");
  assert.match(browserAccessToken, /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/u);
  const crossOriginToken = await fetch(`${publicOrigin}/api/backend-token`, {
    method: "POST",
    headers: { Origin: "https://other.example.test" },
    signal: AbortSignal.timeout(3000),
  });
  assert.equal(crossOriginToken.status, 403, "The public token endpoint must still reject cross-origin browser requests.");
  const sectionLabels = ["TSRP Score", "Expectations vs Reality", "Valuation", "Catalysts &amp; Risks", "What Changed?"];

  for (const section of sectionLabels) {
    const sectionName = section.replaceAll("&amp;", "&");
    const params = new URLSearchParams({ ticker: "AAPL" });
    if (sectionName !== "TSRP Score") params.set("section", sectionName);
    const page = await getRenderedPage(`/?${params.toString()}`);
    assertStrictCsp(page.response, page.html, `${sectionName} deep link`);
    assert.doesNotMatch(page.html, /Sign in to TSRP|Sign-in setup is incomplete/);
    assert.equal(new URL(page.response.url).searchParams.get("ticker"), "AAPL", `${sectionName} link must retain its ticker.`);
    assert.equal(new URL(page.response.url).searchParams.get("section") || "TSRP Score", sectionName, `${sectionName} link must retain its section.`);
  }

  const deepLink = await getRenderedPage("/?ticker=AAPL&section=Valuation");
  assertStrictCsp(deepLink.response, deepLink.html, "Valuation direct link");
  assert.equal(new URL(deepLink.response.url).searchParams.get("ticker"), "AAPL");
  assert.equal(new URL(deepLink.response.url).searchParams.get("section"), "Valuation");

  const invalidSection = await getRenderedPage("/?ticker=AAPL&section=Unknown");
  assertStrictCsp(invalidSection.response, invalidSection.html, "Invalid-section fallback");
  assert.doesNotMatch(invalidSection.html, /Sign in to TSRP|Sign-in setup is incomplete/);

  const privacy = await getRenderedPage("/privacy?ticker=AAPL&section=Valuation");
  assertStrictCsp(privacy.response, privacy.html, "Privacy page");
  const privacyCopy = privacy.html.replace(/<!-- -->/gu, "");
  assert.match(privacy.html, /<title>Privacy &amp; data — TSRP<\/title>/);
  assert.match(privacyCopy, /operator’s legal identity, contact details, launch jurisdictions/i);
  assert.match(privacyCopy, /Common email-address and phone or long-number patterns are rejected before Yahoo Finance receives them/i);
  assert.match(privacyCopy, /does not require an account or sign-in/i);
  assert.match(privacyCopy, /anyone with the site address can open and use it; this is not a private or access-controlled service/i);
  assert.match(privacyCopy, /yfinance diagnostics that may contain a ticker or search text/i);
  assert.match(privacyCopy, /runtime log details can show request paths and search parameters/i);
  assert.match(privacyCopy, /does not send research inputs to a generative-AI analysis provider, use an identity provider, or load a third-party analytics SDK/i);
  assert.match(privacyCopy, /That token expires after at most 60 seconds/i);
  assert.match(privacyCopy, /selected ticker is included in the <code>\/api\/analyze<\/code> URL query/i);
  assert.match(privacyCopy, /may fetch SEC’s public ticker-to-CIK map and then request that issuer’s public company facts by its Central Index Key \(CIK\)/i);
  assert.match(privacyCopy, /requests only the selected list—Top movers, Most active, or Popular stocks\. Switching lists requests the newly selected list\. The active list refreshes every 5 minutes while the page is visible; background tabs pause polling/i);
  assert.doesNotMatch(privacyCopy, /selected list—Top Movers or Top Stocks/u);
  assert.match(privacyCopy, /If a saved snapshot exists, opening “What Changed\?” or saving another snapshot for comparison sends a reduced set of data from the current analysis and the most recent saved snapshot/i);
  assert.match(privacyCopy, /Opening the section with no saved snapshot, or saving the first snapshot, does not make this comparison request/i);
  assert.match(privacyCopy, /<code>\/api\/snapshot_diff<\/code>/);
  assert.match(privacyCopy, /hosting provider processes API request bodies and metadata/i);
  assert.match(privacyCopy, /four scenario assumption fields are held in this tab by ticker/i);
  assert.match(privacyCopy, /unsubmitted drafts are not sent to TSRP/i);
  assert.match(privacyCopy, /up to eight snapshots per ticker are stored in this browser’s local storage/i);
  assert.match(privacyCopy, /automatically removes the oldest saved snapshot for that ticker/i);
  assert.match(privacyCopy, /new snapshots keep only fields needed for comparison/i);
  assert.match(privacyCopy, /snapshots created before this data-minimization change may retain additional analysis fields/i);
  assert.match(privacyCopy, /remove a ticker’s snapshots in “What Changed\?” or use the control below to clear all snapshots and this tab’s scenario drafts/i);
  assert.match(privacyCopy, /Clear saved research/i);
  assert.match(privacyCopy, /scenario drafts from this tab/i);
  assert.match(privacyCopy, /anyone using this browser profile can access the same saved snapshots/i);
  assert.match(privacyCopy, /CSV and JSON exports are assembled in your browser and downloaded to your device; TSRP does not upload or retain the export file/i);
  assert.match(privacyCopy, /Formula-like text in CSV cells is prefixed with a tab to reduce spreadsheet formula execution risk.*This is not a universal CSV-safety guarantee/i);
  assert.match(privacyCopy, /SEC EDGAR requests are made only when <code>SEC_USER_AGENT<\/code> contains a client or organization name and one monitored contact email/i);
  assert.match(privacyCopy, /Yahoo data rights for public display have not been cleared/i);
  assert.match(privacy.html, /href="\/\?ticker=AAPL&amp;section=Valuation"/);
  assert.match(home.html, /Privacy &amp; data/);

  const notFoundResponse = await fetch(`${publicOrigin}/__tsrp-csp-not-found__`);
  assert.equal(notFoundResponse.status, 404);
  const notFoundHtml = await notFoundResponse.text();
  assert.match(notFoundHtml, /We couldn’t find that page\./u);
  assert.match(notFoundHtml, /Return to TSRP/u);
  assertStrictCsp(notFoundResponse, notFoundHtml, "Not-found page");

  const apiResponse = await fetch(`${publicOrigin}/api/analyze?ticker=%5EGSPC`, { headers: { Authorization: `Bearer ${testAccessToken()}` }, signal: AbortSignal.timeout(3000) });
  const apiPayload = await apiResponse.json();
  assert.equal(apiResponse.status, 400, "The same-origin API rewrite must reach the ASGI validation handler.");
  assert.equal(apiPayload.error?.code, "invalid_ticker");
  assert.match(apiResponse.headers.get("x-request-id") ?? "", /^[a-f0-9]{32}$/u);
  assert.match(apiResponse.headers.get("content-type") ?? "", /^application\/json/u);
  assert.equal(apiResponse.headers.get("cache-control"), "no-store");
  assert.equal(apiResponse.headers.get("x-content-type-options"), "nosniff");

  const coldAnalysisStartedAt = performance.now();
  const successfulAnalysisResponse = await fetch(`${publicOrigin}/api/analyze?ticker=AAPL`, {
    headers: { Authorization: `Bearer ${browserAccessToken}` },
    signal: AbortSignal.timeout(25_000),
  });
  const successfulAnalysis = await successfulAnalysisResponse.json();
  assert.equal(successfulAnalysisResponse.status, 200, "A listed-equity analysis must succeed through the production Next.js-to-ASGI boundary with a local fixture.");
  assert.equal(successfulAnalysis.data?.company?.ticker, "AAPL");
  assert.equal(successfulAnalysis.data?.company?.company, "Synthetic QA · Example Corporation", "The analysis fixture must remain clearly synthetic.");
  assert.equal(successfulAnalysis.data?.company?.score_available, true);
  assert.equal(successfulAnalysis.data?.scenario_context_status, "available");
  assert.equal(typeof successfulAnalysis.data?.scenario_context_token, "string");
  assert.match(successfulAnalysis.data.company.snapshot_proof ?? "", /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/u,
    "The server must sign the research snapshot before it is saved in browser storage.");
  assert.ok(Number.isFinite(successfulAnalysis.data.company.score));
  assert.ok(successfulAnalysis.data.company.score >= 0 && successfulAnalysis.data.company.score <= 100);
  assert.equal(successfulAnalysis.data.company.sources?.revenue?.source, "Synthetic local QA fixture");
  assert.ok(Array.isArray(successfulAnalysis.data?.price_history));
  if (process.env.TSRP_E2E_FUTURE_PRICE_BAR === "1") {
    assert.match(apiOutput, /TSRP_E2E_FUTURE_PRICE_BAR injected/u);
    assert.ok(successfulAnalysis.data.price_history.length > 0);
    assert.ok(successfulAnalysis.data.price_history.every((point) => point.close !== 999));
    assert.ok(successfulAnalysis.data.price_history.every((point) => Date.parse(`${point.date}T00:00:00Z`) <= Date.now() + 5 * 60_000));
  }
  assert.equal(successfulAnalysisResponse.headers.get("cache-control"), "private, no-store");
  assert.match(successfulAnalysisResponse.headers.get("x-request-id") ?? "", /^[a-f0-9]{32}$/u);
  const coldAnalysisMs = performance.now() - coldAnalysisStartedAt;

  const warmAnalysisStartedAt = performance.now();
  const warmAnalysisResponse = await fetch(`${publicOrigin}/api/analyze?ticker=AAPL`, {
    headers: { Authorization: `Bearer ${testAccessToken()}` },
    signal: AbortSignal.timeout(25_000),
  });
  const warmAnalysis = await warmAnalysisResponse.json();
  assert.equal(warmAnalysisResponse.status, 200);
  assert.equal(warmAnalysis.data?.company?.ticker, "AAPL");
  assert.equal(warmAnalysis.data?.company?.score, successfulAnalysis.data.company.score);
  const firstRetrievedAt = Date.parse(successfulAnalysis.data.company.retrieved_at);
  const repeatedRetrievedAt = Date.parse(warmAnalysis.data?.company?.retrieved_at);
  assert.ok(Number.isFinite(firstRetrievedAt) && Number.isFinite(repeatedRetrievedAt));
  assert.ok(repeatedRetrievedAt >= firstRetrievedAt, "A repeated analysis must not move its retrieval timestamp backwards.");
  const warmAnalysisMs = performance.now() - warmAnalysisStartedAt;

  const scenarioAssumptions = {
    starting_growth: 0.12,
    fcf_margin: 0.30,
    discount_rate: 0.09,
    terminal_growth: 0.02,
  };
  const scenarioResponse = await fetch(`${publicOrigin}/api/scenario`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      Authorization: `Bearer ${testAccessToken()}`,
    },
    body: JSON.stringify({
      ticker: "AAPL",
      assumptions: scenarioAssumptions,
      context_token: successfulAnalysis.data.scenario_context_token,
    }),
    signal: AbortSignal.timeout(3000),
  });
  const scenarioPayload = await scenarioResponse.json();
  assert.equal(scenarioResponse.status, 200, "The signed scenario must calculate through the production Next.js-to-ASGI boundary.");
  assert.equal(scenarioPayload.data?.inputs?.fcf_margin, scenarioAssumptions.fcf_margin);
  assert.ok(Number.isFinite(scenarioPayload.data?.enterprise_value));
  assert.equal(scenarioResponse.headers.get("cache-control"), "no-store");

  const wrongSearchMethod = await fetch(`${publicOrigin}/api/search`, { signal: AbortSignal.timeout(3000) });
  assert.equal(wrongSearchMethod.status, 405, "Search must reject GET at the production same-origin boundary.");
  assert.equal(wrongSearchMethod.headers.get("allow"), "POST");

  const anonymousAnalyze = await fetch(`${publicOrigin}/api/analyze?ticker=AAPL`, { signal: AbortSignal.timeout(3000) });
  assert.equal(anonymousAnalyze.status, 401, "Backend research routes must reject requests without a server-issued API token.");
  assert.equal(anonymousAnalyze.headers.get("www-authenticate"), 'Bearer realm="tsrp-api"');

  const searchResponse = await fetch(`${publicOrigin}/api/search`, {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${testAccessToken()}` },
    body: JSON.stringify({ q: 42 }),
    signal: AbortSignal.timeout(3000),
  });
  const searchPayload = await searchResponse.json();
  assert.equal(searchResponse.status, 400, "Search JSON must reach the ASGI handler and validate its body.");
  assert.equal(searchPayload.error?.code, "invalid_request");
  assert.match(searchResponse.headers.get("x-request-id") ?? "", /^[a-f0-9]{32}$/u);

  const signedCompany = successfulAnalysis.data.company;
  const snapshotTime = signedCompany.retrieved_at;
  const snapshot = {
    ticker: signedCompany.ticker, methodology_version: signedCompany.methodology_version,
    analysis_schema_version: signedCompany.analysis_schema_version, sector: signedCompany.sector,
    snapshot_proof: signedCompany.snapshot_proof, reporting_currency: signedCompany.reporting_currency,
    retrieved_at: snapshotTime, score: signedCompany.score, score_trace: signedCompany.score_trace,
    business_quality: signedCompany.business_quality, financial_strength: signedCompany.financial_strength,
    market_expectations: signedCompany.market_expectations,
    historical_growth: signedCompany.historical_growth, consensus_growth: signedCompany.consensus_growth,
    analyst_estimate_period: signedCompany.analyst_estimate_period,
    required_growth: signedCompany.required_growth, revenue: signedCompany.revenue,
    revenue_period: signedCompany.revenue_period, revenue_source: signedCompany.revenue_source,
    free_cash_flow: signedCompany.free_cash_flow, fcf_period: signedCompany.fcf_period,
    fcf_source: signedCompany.fcf_source, enterprise_value: signedCompany.enterprise_value,
    model_fcf_margin: signedCompany.model_fcf_margin, discount_rate: signedCompany.discount_rate,
    terminal_growth: signedCompany.terminal_growth, solver_status: signedCompany.solver_status,
    user_assumptions: { starting_growth: 0.10, fcf_margin: 0.15, discount_rate: 0.09, terminal_growth: 0.02 },
    sources: {
      revenue: signedCompany.sources.revenue,
      free_cash_flow: signedCompany.sources.free_cash_flow,
    },
  };
  const changedSnapshot = {
    ...snapshot,
    user_assumptions: { starting_growth: 0.12, fcf_margin: 0.18, discount_rate: 0.10, terminal_growth: 0.025 },
  };
  const comparisonResponse = await fetch(`${publicOrigin}/api/snapshot_diff`, {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${testAccessToken()}` },
    body: JSON.stringify({
      previous: { ...snapshot, saved_at: new Date(Date.now() - 60_000).toISOString() },
      current: changedSnapshot,
    }),
    signal: AbortSignal.timeout(3000),
  });
  const comparisonPayload = await comparisonResponse.json();
  assert.equal(comparisonResponse.status, 200, "Snapshot comparison must pass through the production ASGI boundary.");
  assert.equal(comparisonPayload.data?.comparable, true, JSON.stringify(comparisonPayload.data));
  assert.equal(comparisonPayload.data?.previous_retrieved_at, snapshotTime,
    "The previous comparison timestamp must come from the verified signed snapshot.");
  assert.equal(comparisonPayload.data?.current_retrieved_at, snapshotTime,
    "The current comparison timestamp must come from the verified signed snapshot.");
  const comparisonChanges = Object.fromEntries(comparisonPayload.data.changes.map((change) => [change.field, change.delta]));
  const expectedChanges = {
    user_starting_growth: 0.02,
    user_fcf_margin: 0.03,
    user_discount_rate: 0.01,
    user_terminal_growth: 0.005,
  };
  assert.deepEqual(Object.keys(comparisonChanges).sort(), Object.keys(expectedChanges).sort());
  for (const [field, expected] of Object.entries(expectedChanges)) {
    assert.ok(Math.abs(comparisonChanges[field] - expected) < 1e-12, `${field} should have a meaningful delta.`);
  }

  const forgedScoreResponse = await fetch(`${publicOrigin}/api/snapshot_diff`, {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${testAccessToken()}` },
    body: JSON.stringify({
      previous: { ...snapshot, saved_at: new Date(Date.now() - 60_000).toISOString() },
      current: { ...snapshot, score: 95, score_trace: { ...snapshot.score_trace, score: 95 } },
    }),
    signal: AbortSignal.timeout(3000),
  });
  const forgedScorePayload = await forgedScoreResponse.json();
  assert.equal(forgedScorePayload.data?.comparable, true);
  assert.equal(forgedScorePayload.data?.changes?.some((change) => change.field === "score"), false);

  const forgedMetricResponse = await fetch(`${publicOrigin}/api/snapshot_diff`, {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${testAccessToken()}` },
    body: JSON.stringify({
      previous: { ...snapshot, saved_at: new Date(Date.now() - 60_000).toISOString() },
      current: { ...snapshot, market_expectations: 99 },
    }),
    signal: AbortSignal.timeout(3000),
  });
  const forgedMetricPayload = await forgedMetricResponse.json();
  assert.equal(forgedMetricPayload.data?.comparable, true);
  assert.equal(forgedMetricPayload.data?.changes?.some((change) => change.field === "market_expectations"), false);

  const anonymousSearch = await fetch(`${publicOrigin}/api/search`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ q: "person@example.test" }),
    signal: AbortSignal.timeout(3000),
  });
  assert.equal(anonymousSearch.status, 401, "Direct backend search calls without a server-issued API token must be rejected.");

  for (const query of ["person@example.test", "+1 (415) 555-0134"]) {
    const rejectedSearch = await fetch(`${publicOrigin}/api/search`, {
      method: "POST",
      headers: { "content-type": "application/json", Authorization: `Bearer ${testAccessToken()}` },
      body: JSON.stringify({ q: query }),
      signal: AbortSignal.timeout(3000),
    });
    const rejectedPayload = await rejectedSearch.json();
    assert.equal(rejectedSearch.status, 400, "Common contact-detail patterns must be rejected before provider access.");
    assert.equal(rejectedPayload.error?.code, "personal_data_not_allowed");
    assert.equal(apiOutput.includes(query), false);
  }
  const rejectedTicker = await fetch(`${publicOrigin}/api/analyze?ticker=14155550134`, {
    headers: { Authorization: `Bearer ${testAccessToken()}` },
    signal: AbortSignal.timeout(3000),
  });
  const rejectedTickerPayload = await rejectedTicker.json();
  assert.equal(rejectedTicker.status, 400, "Phone-like ticker input must be rejected before provider access.");
  assert.equal(rejectedTickerPayload.error?.code, "personal_data_not_allowed");
  assert.equal(JSON.stringify(rejectedTickerPayload).includes("14155550134"), false);
  console.log(`Synthetic production analysis timings (local only; no provider calls): first analysis ${coldAnalysisMs.toFixed(1)} ms, repeated warm-process analysis ${warmAnalysisMs.toFixed(1)} ms.`);
  console.log("Production-mode HTTP integration passed: anonymous reviewer page and short-lived same-origin API token, ticker/section deep links, privacy disclosure, synthetic company analysis and signed valuation scenario through Next.js→ASGI, search/snapshot comparison, method rejection, and server-side personal-data rejection. Cross-origin token requests were rejected. Yahoo and SEC were mocked/unavailable; no public provider calls.");
} finally {
  if (proxy.listening) await new Promise((resolve) => proxy.close(resolve));
  for (const child of [server, apiServer]) {
    if (processStartErrors.has(child)) continue;
    if (child.exitCode !== null) continue;
    child.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => child.once("exit", resolve)),
      delay(3000),
    ]);
    if (child.exitCode === null) child.kill("SIGKILL");
  }
  await rm(productionFrontend, { recursive: true, force: true });
}
