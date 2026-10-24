import Link from "next/link";
import { PrivacyDataControls } from "@/components/privacy-data-controls";
import { API_ACCESS_TOKEN_TTL_SECONDS } from "@/lib/api-access-token";
import { MARKET_LIST_LABELS, MARKET_LIST_REFRESH_INTERVAL_MINUTES } from "@/lib/market-refresh";

export const metadata = {
  title: "Privacy & data — TSRP",
  description: "A factual overview of the information TSRP processes, stores in your browser, and sends to data providers.",
};

const SECTIONS = [
  "TSRP Score",
  "Expectations vs Reality",
  "Valuation",
  "Catalysts & Risks",
  "What Changed?",
] as const;

type SearchParams = Record<string, string | string[] | undefined>;

function singleParam(value: string | string[] | undefined): string {
  return typeof value === "string" ? value : "";
}

export default async function PrivacyPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const rawTicker = singleParam(params.ticker).trim().toUpperCase();
  const ticker = /^[A-Z0-9][A-Z0-9.-]{0,14}$/u.test(rawTicker) ? rawTicker : "";
  const requestedSection = singleParam(params.section);
  const section = SECTIONS.includes(requestedSection as typeof SECTIONS[number])
    ? requestedSection
    : "TSRP Score";
  const returnParams = new URLSearchParams();
  if (ticker) {
    returnParams.set("ticker", ticker);
    if (section !== "TSRP Score") returnParams.set("section", section);
  }
  const returnHref = returnParams.size ? `/?${returnParams.toString()}` : "/";

  return (
    <main className="page-shell privacy-shell">
      <header className="masthead">
        <Link href="/" className="brand" aria-label="TSRP home">
          <span className="brand-copy"><strong>TSRP</strong><small>The Saleh Research Project</small></span>
        </Link>
        <Link href={returnHref} className="privacy-return">Return to research</Link>
      </header>

      <article className="privacy-page">
        <p className="eyebrow">Beta product disclosure</p>
        <h1>Privacy &amp; data</h1>
        <p className="privacy-intro">What the current TSRP application sends, processes, and stores. This describes the code as it exists today; it is not a complete jurisdiction-specific privacy policy.</p>

        <aside className="privacy-release-notice" role="note">
          <strong>Open educational access</strong>
          <p>TSRP does not require an account or sign-in. Anyone with the site address can open and use it; this is not a private or access-controlled service. Do not enter personal or confidential information. The operator’s legal identity, contact details, launch jurisdictions, and a reviewed legal privacy policy still need to be supplied before a formal public launch.</p>
        </aside>

        <section className="privacy-group" aria-labelledby="privacy-search">
          <h2 id="privacy-search">Search and company research</h2>
          <p>When you search, your text is sent from your browser to TSRP’s company-search API in a JSON body. It is not placed in the browser-to-TSRP request URL or included in TSRP application logs. Eligible company-search text is forwarded to Yahoo Finance, including as a query parameter in the provider’s search request, to find listed companies. Common email-address and phone or long-number patterns are rejected before Yahoo Finance receives them; this filter cannot detect every kind of personal or confidential information. Yahoo and the hosting provider may process request content or metadata under their own terms.</p>
          <p>When you open the main research page, TSRP requests only the selected list—{MARKET_LIST_LABELS.movers}, {MARKET_LIST_LABELS.stocks}, or {MARKET_LIST_LABELS.popular}. Switching lists requests the newly selected list. The active list refreshes every {MARKET_LIST_REFRESH_INTERVAL_MINUTES} minutes while the page is visible; background tabs pause polling. Requests go through TSRP’s API to Yahoo Finance. When you analyze a company, its public ticker is sent to Yahoo Finance; the selected ticker is included in the <code>/api/analyze</code> URL query. The ticker and selected research section also appear in the page URL and browser history, so sharing an analysis link reveals those values. TSRP’s application logs omit query values, but the hosting platform may process request URLs under its own logging and retention settings. Do not submit personal or confidential information in company search.</p>
        </section>

        <section className="privacy-group" aria-labelledby="privacy-sec">
          <h2 id="privacy-sec">Financial data providers</h2>
          <p>SEC EDGAR requests are made only when <code>SEC_USER_AGENT</code> contains a client or organization name and one monitored contact email. Those details are sent to SEC with requests for public data. To identify a company, TSRP may fetch SEC’s public ticker-to-CIK map and then request that issuer’s public company facts by its Central Index Key (CIK). TSRP checks the user-agent's format but cannot verify that the identity is truthful or monitored. If the setting is missing or malformed, SEC data is reported as unavailable.</p>
          <p>Yahoo Finance and SEC are independent services. Their handling, logging, retention, access rules, and data-use terms are not controlled by TSRP. Yahoo data rights for public display have not been cleared for this project.</p>
        </section>

        <section className="privacy-group" aria-labelledby="privacy-scenarios">
          <h2 id="privacy-scenarios">Scenarios and saved analyses</h2>
          <p>When browser session storage is available, the four scenario assumption fields are held in this tab by ticker, so they can be restored after refresh. They remain until the tab session ends or you clear this browser’s site data; they are not encrypted, synced, or backed up by TSRP. If the browser blocks session storage, unsubmitted drafts may not survive refresh. Unsubmitted drafts are not sent to TSRP. When you choose “Calculate scenario,” the assumptions are sent to TSRP’s scenario API for calculation. TSRP does not store scenario requests in an application database.</p>
          <p>If a saved snapshot exists, opening “What Changed?” or saving another snapshot for comparison sends a reduced set of data from the current analysis and the most recent saved snapshot—including the ticker, dates, score breakdown, selected financial metrics and sources, scenario assumptions, and a server-generated signature for the sourced and calculated research data—to TSRP’s <code>/api/snapshot_diff</code> service. Opening the section with no saved snapshot, or saving the first snapshot, does not make this comparison request. Comparisons use the server-signed research values, not editable copies in browser storage; user-entered scenario assumptions remain separate. TSRP computes the comparison per request and does not persist that payload. The hosting provider processes API request bodies and metadata while serving requests and may retain its own logs under its policies.</p>
          <p>If you choose “Save current analysis,” up to eight snapshots per ticker are stored in this browser’s local storage. Saving another snapshot after reaching that limit automatically removes the oldest saved snapshot for that ticker. New snapshots keep only fields needed for comparison—such as ticker, dates, score and breakdown, selected financial values and their source labels, methodology identifiers, and scenario assumptions—not the full analysis object or price history. Snapshots created before this data-minimization change may retain additional analysis fields until removed, pruned, or cleared. TSRP does not encrypt, sync, or back them up. Anyone using this browser profile can access the same saved snapshots. Remove a ticker’s snapshots in “What Changed?” or use the control below to clear all snapshots and this tab’s scenario drafts. Anyone with access to the browser profile, developer tools, or site data may be able to read stored values.</p>
          <p>CSV and JSON exports are assembled in your browser and downloaded to your device; TSRP does not upload or retain the export file. Formula-like text in CSV cells is prefixed with a tab to reduce spreadsheet formula execution risk; some spreadsheet apps may display it differently, and the tab may remain if imported by other software. This is not a universal CSV-safety guarantee. The downloaded file may remain in your downloads folder after you remove a browser snapshot, and anyone with access to that device may be able to read it.</p>
          <PrivacyDataControls />
        </section>

        <section className="privacy-group" aria-labelledby="privacy-logs">
          <h2 id="privacy-logs">Application logs and access</h2>
          <p>TSRP application logs include a generated request ID, allow-listed API route, method, response status, duration, and safe error classification. They exclude search text, request bodies, IP addresses, query values, raw exception messages, and yfinance diagnostics that may contain a ticker or search text. Vercel runtime log details can show request paths and search parameters to people with project log access; retention follows Vercel’s plan and settings. Restrict project-log access and do not share sensitive logs. See <a href="https://vercel.com/docs/logs/runtime">Vercel Runtime Logs</a>. Hosting providers may also process connection metadata and other request URLs under their own logging and retention settings.</p>
          <p>The current application does not send research inputs to a generative-AI analysis provider, use an identity provider, or load a third-party analytics SDK. This does not limit the hosting or financial-data providers’ own operational logging and retention.</p>
          <p>No user account is created. The browser requests a signed API token from the same site to communicate with TSRP’s research API. That token expires after at most {API_ACCESS_TOKEN_TTL_SECONDS} seconds and is not saved in browser storage. It protects the internal browser-to-API request flow; it does not restrict who can use the site. Anyone who can access the site can request research. The hosting provider may process connection metadata and maintain its own logs.</p>
        </section>

        <section className="privacy-group" aria-labelledby="privacy-retention">
          <h2 id="privacy-retention">Retention and your choices</h2>
          <p>TSRP has no server-side saved-analysis database. Browser snapshots remain until you remove them, clear site data, or an older snapshot is automatically pruned when you save more than eight for the same ticker. Older values remain in browser storage until you clear saved research or site data. Unsaved scenario drafts are kept in session storage when the browser allows it and expire with the tab session or when site data is cleared. TSRP cannot set or promise Yahoo’s, SEC’s, or the host’s retention periods; consult those providers’ current notices for their practices.</p>
          <p>TSRP does not have user accounts, account sync, or a server-side account-deletion workflow. Clear saved research in this browser or clear its site data to remove browser-stored analyses and drafts.</p>
        </section>

        <section className="privacy-group privacy-release-status" aria-labelledby="privacy-status">
          <h2 id="privacy-status">Release status</h2>
          <p>This beta disclosure is not legal advice and does not claim GDPR compliance or complete privacy protection. Before public release, the operator must publish a complete notice with a real contact, identify applicable jurisdictions and processors, confirm hosting/provider retention terms, and obtain qualified legal review.</p>
        </section>
      </article>

      <footer className="site-footer"><span>TSRP · Saleh Research Project</span><Link href={returnHref}>Return to research</Link></footer>
    </main>
  );
}
