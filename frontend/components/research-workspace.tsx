"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PriceChart } from "@/components/price-chart";
import { ApiResponseError, requestJson } from "@/lib/api-client";
import { shouldReloadSubmittedTicker } from "@/lib/analysis-submit";
import { cancelPendingAnalysisForDifferentTicker, cancelRequestsOnHome, claimAutomaticAnalysisAttempt, isAnalysisTickerStillSelected, releaseAutomaticAnalysisAttempt } from "@/lib/analysis-request-lifecycle";
import { assertAnalysisResponse } from "@/lib/analysis-response";
import { analysisErrorCanRetry, analysisErrorForTicker } from "@/lib/analysis-error";
import { dateLabel, finite, methodologyVersionLabel, money, number, pct } from "@/lib/format";
import { freeCashFlowEvidenceNote } from "@/lib/fcf-evidence";
import { buildResearchExport } from "@/lib/research-export";
import { attemptResearchExport, exportFeedbackMessage, type ExportFeedbackState } from "@/lib/export-feedback";
import { canConfirmTickerAfterSearchFailure, COMPANY_SEARCH_DISABLED_MESSAGE, containsPersonalSearchText, decideCompanySearch, PERSONAL_SEARCH_MESSAGE } from "@/lib/company-search";
import { downloadResearchFile } from "@/lib/download";
import { LatestRequestGate } from "@/lib/latest-request";
import { createServerClockAnchor, currentClockMs, isMarketDataStale, type ServerClockAnchor } from "@/lib/freshness";
import { filterFreshMarketMovers, mergeMarketMovers, pruneExpiredMarketMovers } from "@/lib/market-movers";
import { MARKET_LIST_LABELS, MARKET_LIST_REFRESH_INTERVAL_MS, marketListEndpoint, shouldPollMarketList, type MarketListKind } from "@/lib/market-refresh";
import { analystEstimateDescription, analystEstimateLabel, analystEstimateShortLabel, analystEstimateSignal, missingBalanceSource } from "@/lib/research-signals";
import { scoreFactorEvidence } from "@/lib/score-evidence";
import { formatScoreWeight } from "@/lib/score-weight-format";
import { currencyConversionLine, fxSourceLine, sourceComponentsLine } from "@/lib/source-format";
import { toSnapshotComparisonPayload, toStoredSnapshot } from "@/lib/snapshot-comparison";
import { snapshotChangeStatus, snapshotComparisonReason, snapshotRateDelta, snapshotValueFallback } from "@/lib/snapshot-change";
import { researchUrl, sectionFromQuery } from "@/lib/research-navigation";
import { researchScrollKey, scrollToResearchHeading } from "@/lib/research-scroll";
import { readScenarioDraft, SCENARIO_DRAFT_LIMITS, scenarioDraftValidationErrors, writeScenarioDraft } from "@/lib/scenario-draft";
import { clearSnapshotStore, MAX_SNAPSHOTS_PER_TICKER, readSnapshots, SNAPSHOT_STORAGE_KEY, SnapshotStoreError, writeSnapshots } from "@/lib/snapshot-store";
import { isTsrpSnapshotStorageChange } from "@/lib/browser-research-data";
import { SECTIONS, type Analysis, type Assumptions, type Company, type CompanyMatch, type FxConversion, type MarketMover, type MarketMovers, type SectionName, type TopStocksResponse } from "@/lib/types";

type Diff = { comparable: boolean; reason: string; changes: { field: string; before: unknown; after: unknown; delta: number | null }[]; excluded_fields?: { field: string; reason: string }[]; previous_retrieved_at?: string; current_retrieved_at?: string };
type ScenarioOutput = NonNullable<Company["scenario"]>;
type DraftAssumptions = Record<keyof Assumptions, string>;

function scenarioBaselineValue(field: keyof Assumptions, value: number | null): string {
  if (!finite(value)) return "";
  const percent = value * 100;
  const [minimum, maximum] = SCENARIO_DRAFT_LIMITS[field];
  return percent >= minimum && percent <= maximum ? percent.toFixed(1) : "";
}

const HOME_RESEARCH_SECTIONS: { section: SectionName; description: string }[] = [
  { section: "TSRP Score", description: "See the overall signal, its drivers, and data confidence." },
  { section: "Expectations vs Reality", description: "Review reported results beside analyst estimates and model-implied growth." },
  { section: "Valuation", description: "Inspect the growth and assumptions implied by the current company value." },
  { section: "Catalysts & Risks", description: "Review data-backed supports, risks, and what to check next." },
  { section: "What Changed?", description: "Compare analyses saved in this browser." },
];
const DISCLAIMER = "TSRP is an independent research tool. Its score and analysis are educational, are not a probability or price target, and are not personalized investment advice. Data may be incomplete, delayed, revised, or unavailable. Verify material information against company filings and other primary sources.";
// Client deadlines leave a small response margin beyond each Vercel function cap.
const API_TIMEOUT_MS = { default: 32_000, search: 22_000, snapshotDiff: 12_000 } as const;

function fxQuotePeriods(item: FxConversion) {
  const quotes = item.pairs.map((pair, index) => `${pair} as of ${item.leg_periods?.[index] ?? item.period ?? "timestamp unavailable"}`);
  return quotes.join(", ") || item.period || "timestamp unavailable";
}

async function api<T>(url: string, init?: RequestInit, observeResponse?: (response: Response) => void): Promise<T> {
  const timeoutMs = url.startsWith("/api/search")
    ? API_TIMEOUT_MS.search
    : url.startsWith("/api/snapshot_diff") ? API_TIMEOUT_MS.snapshotDiff : API_TIMEOUT_MS.default;
  return requestJson(url, init, timeoutMs, fetch, observeResponse);
}

function sourceLine(company: Company, key: string) {
  if (key === "valuation_assumptions" && !hasValuationMethodology(company)) {
    return !hasCurrencyMethodology(company)
      ? `Unavailable: no validated TSRP calibration for ${company.reporting_currency}; USD rates are not substituted`
      : `Unavailable: no validated TSRP sector calibration for ${company.sector}; valuation assumptions are withheld`;
  }
  const value = company.sources[key];
  if (!value || !("source" in value)) return "Source unavailable";
  if (key === "fx" && "conversions" in value) {
    return fxSourceLine(value.conversions ?? [], value.kind);
  }
  const base = `${value.source} · ${value.period}${value.currency && value.currency !== "N/A" ? ` · ${value.currency}` : ""}`;
  const calculationBasis = key === "debt" && value.kind.startsWith("calculated:") ? ` · ${value.kind}` : "";
  const conversions = "conversions" in value ? value.conversions ?? [] : [];
  const componentDetail = "components" in value ? sourceComponentsLine(value.components ?? []) : "";
  const conversionDetail = conversions.length
    ? ` · ${conversions.map((item) => currencyConversionLine(item, fxQuotePeriods(item))).join("; ")}`
    : "";
  return `${base}${calculationBasis}${componentDetail}${conversionDetail}`;
}

function freeCashFlowSourceKind(company: Company): string | null {
  const value = company.sources.free_cash_flow;
  return value && "source" in value ? value.kind : null;
}

function ResearchNavigation({ ticker, section }: { ticker: string; section: SectionName }) {
  const navRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const nav = navRef.current;
    const activeLink = nav?.querySelector<HTMLElement>(".research-nav-link.is-active");
    if (!nav || !activeLink) return;
    const navLeft = nav.getBoundingClientRect().left;
    const activeLeft = activeLink.getBoundingClientRect().left + nav.scrollLeft - navLeft;
    const left = activeLeft - (nav.clientWidth - activeLink.clientWidth) / 2;
    nav.scrollTo({
      left: Math.max(0, left),
      // Keep links stationary for the next tap; animated recentering can move
      // a different link under a user's pointer during rapid navigation.
      behavior: "instant",
    });
  }, [section]);

  return <div className="research-nav-wrap">
    <p className="research-nav-label">Research questions</p>
    <nav ref={navRef} className="research-nav" aria-label="Research sections">
      {SECTIONS.map((name) => <Link key={name} href={researchUrl(ticker, name)} scroll={false} aria-current={section === name ? "page" : undefined} className={`research-nav-link ${section === name ? "is-active" : ""}`}>{name}</Link>)}
    </nav>
  </div>;
}

function ResearchActions({ company, scenario, copyState, onShare }: {
  company: Company;
  scenario: ScenarioOutput | null;
  copyState: string;
  onShare: () => Promise<void>;
}) {
  const [exportState, setExportState] = useState<ExportFeedbackState | null>(null);
  const feedbackTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
  }, []);

  const exportResearch = (format: "json" | "csv") => {
    if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current);
    setExportState(attemptResearchExport(format, () => {
      const output = buildResearchExport(company, scenario, DISCLAIMER, (key) => sourceLine(company, key));
      downloadResearchFile(format === "json" ? output.json : output.csv, format, company.ticker);
    }));
    feedbackTimer.current = window.setTimeout(() => {
      feedbackTimer.current = null;
      setExportState(null);
    }, 4000);
  };

  return <div className="section-actions">
    <button type="button" className="button button-quiet" onClick={() => void onShare()}>{copyState || "Copy link"}</button>
    <button type="button" className="button button-quiet" onClick={() => exportResearch("csv")}>Export CSV</button>
    <button type="button" className="button button-quiet" onClick={() => exportResearch("json")}>Export JSON</button>
    {exportState && <p className={`export-feedback ${exportState.status === "failed" ? "is-error" : ""}`} role={exportState.status === "failed" ? "alert" : "status"} aria-live={exportState.status === "failed" ? "assertive" : "polite"}>{exportFeedbackMessage(exportState)}</p>}
  </div>;
}

function MarketMoversTicker({ onSelect }: { onSelect: (ticker: string) => void }) {
  const [market, setMarket] = useState<MarketMovers | null>(null);
  const [moversStatus, setMoversStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [stocksStatus, setStocksStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [popularStatus, setPopularStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [activeList, setActiveList] = useState<MarketListKind>("movers");

  useEffect(() => {
    let active = true;
    const requests = new LatestRequestGate();
    const loadMovers = async () => {
      const request = requests.begin();
      try {
        const result = await api<MarketMovers>(marketListEndpoint("movers"), { signal: request.signal });
        const items = filterFreshMarketMovers(result.items, result.retrieved_at);
        if (active && requests.isCurrent(request)) {
          setMarket((previous) => mergeMarketMovers(previous, { kind: "movers", value: result, items }));
          setMoversStatus(items.length ? "ready" : "unavailable");
        }
      } catch {
        if (active && requests.isCurrent(request)) {
          setMarket((previous) => pruneExpiredMarketMovers(previous, "movers"));
          setMoversStatus("unavailable");
        }
      }
    };
    const loadTopStocks = async () => {
      const request = requests.begin();
      try {
        const result = await api<TopStocksResponse>(marketListEndpoint("stocks"), { signal: request.signal });
        const topStocks = filterFreshMarketMovers(result.items, result.retrieved_at);
        if (active && requests.isCurrent(request)) {
          setMarket((previous) => mergeMarketMovers(previous, { kind: "stocks", value: result, items: topStocks }));
          setStocksStatus(topStocks.length ? "ready" : "unavailable");
        }
      } catch {
        if (active && requests.isCurrent(request)) {
          setMarket((previous) => pruneExpiredMarketMovers(previous, "stocks"));
          setStocksStatus("unavailable");
        }
      }
    };
    const loadPopularStocks = async () => {
      const request = requests.begin();
      try {
        const result = await api<TopStocksResponse>(marketListEndpoint("popular"), { signal: request.signal });
        const items = filterFreshMarketMovers(result.items, result.retrieved_at);
        if (active && requests.isCurrent(request)) {
          setMarket((previous) => mergeMarketMovers(previous, { kind: "popular", value: result, items }));
          setPopularStatus(items.length ? "ready" : "unavailable");
        }
      } catch {
        if (active && requests.isCurrent(request)) {
          setMarket((previous) => pruneExpiredMarketMovers(previous, "popular"));
          setPopularStatus("unavailable");
        }
      }
    };
    const load = () => {
      if (!shouldPollMarketList(document.visibilityState)) return;
      if (activeList === "movers") {
        setMoversStatus("loading");
        setMarket((previous) => pruneExpiredMarketMovers(previous, "movers"));
        void loadMovers();
      } else if (activeList === "stocks") {
        setStocksStatus("loading");
        setMarket((previous) => pruneExpiredMarketMovers(previous, "stocks"));
        void loadTopStocks();
      } else {
        setPopularStatus("loading");
        setMarket((previous) => pruneExpiredMarketMovers(previous, "popular"));
        void loadPopularStocks();
      }
    };
    const handleVisibilityChange = () => {
      if (shouldPollMarketList(document.visibilityState)) load();
    };
    load();
    const timer = window.setInterval(load, MARKET_LIST_REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      requests.cancel();
    };
  }, [activeList]);

  const renderItems = (items: MarketMover[], duplicate: boolean) => items.map((mover) => {
    const change = mover.change_percent;
    const tone = change > 0 ? "positive" : change < 0 ? "negative" : "neutral";
    const display = change > 0 ? `+${change.toFixed(2)}%` : change < 0 ? `−${Math.abs(change).toFixed(2)}%` : "0.00%";
    const direction = change > 0 ? "up" : change < 0 ? "down" : "unchanged";
    return <button
      className={`mover-item mover-${tone}`}
      key={`${duplicate ? "copy-" : ""}${mover.ticker}`}
      type="button"
      tabIndex={duplicate ? -1 : undefined}
      aria-label={`Open research for ${mover.ticker}, ${direction} ${Math.abs(change).toFixed(2)} percent, as of ${mover.market_at}`}
      onClick={() => onSelect(mover.ticker)}
    ><span>{mover.ticker}</span><strong className="tabular">{display}</strong></button>;
  });
  const activeItems = activeList === "movers"
    ? market?.items ?? []
    : activeList === "stocks"
      ? market?.top_stocks ?? []
      : market?.popular_stocks ?? [];
  const activeStatus = activeList === "movers"
    ? moversStatus
    : activeList === "stocks"
      ? stocksStatus
      : popularStatus;
  const activeLabel = activeList === "movers"
    ? MARKET_LIST_LABELS.movers
    : activeList === "stocks"
      ? market?.top_stocks_label || MARKET_LIST_LABELS.stocks
      : market?.popular_stocks_label || MARKET_LIST_LABELS.popular;
  const activeRank = activeList === "movers"
    ? "Ranked by daily percentage movement."
    : activeList === "stocks"
      ? market?.top_stocks_rank || "Most active US equities, ordered by trading volume."
      : market?.popular_stocks_rank || "Popular large-cap stocks; not ranked by daily trading volume.";
  const coverageNote = activeList === "movers"
    ? market?.coverage_note
    : activeList === "stocks"
      ? market?.top_stocks_coverage_note
      : market?.popular_stocks_coverage_note;

  return <section
    className="market-movers"
    aria-label={`${activeLabel}. ${activeRank} Daily changes from Yahoo Finance.`}
  >
    <div className="market-movers-tabs" role="group" aria-label="Choose ticker list">
      <button type="button" className={`market-movers-tab ${activeList === "movers" ? "is-active" : ""}`} aria-pressed={activeList === "movers"} onClick={() => setActiveList("movers")}>{MARKET_LIST_LABELS.movers}</button>
      <button type="button" className={`market-movers-tab ${activeList === "stocks" ? "is-active" : ""}`} aria-pressed={activeList === "stocks"} onClick={() => setActiveList("stocks")}>{MARKET_LIST_LABELS.stocks}</button>
      <button type="button" className={`market-movers-tab ${activeList === "popular" ? "is-active" : ""}`} aria-pressed={activeList === "popular"} onClick={() => setActiveList("popular")}>{MARKET_LIST_LABELS.popular}</button>
    </div>
    {coverageNote && <p className="market-movers-coverage" role="status">{coverageNote}</p>}
    {activeItems.length > 0 && activeStatus === "unavailable" && <p className="market-movers-coverage" role="status">Refresh unavailable. Showing the most recent valid list.</p>}
    <div className="market-movers-window" onTouchStart={(event) => { event.currentTarget.dataset.touched = "true"; }} onTouchEnd={(event) => { event.currentTarget.dataset.touched = "false"; }} onTouchCancel={(event) => { event.currentTarget.dataset.touched = "false"; }}>
      {activeItems.length ? <div key={activeList} className="market-movers-track" role="group" aria-label={activeLabel}>
        <div className="market-movers-group">{renderItems(activeItems, false)}</div>
        <div className="market-movers-group" aria-hidden="true">{renderItems(activeItems, true)}</div>
      </div> : <span className="market-movers-status" role="status">{activeStatus === "loading" ? `Loading ${activeLabel.toLowerCase()}` : `${activeLabel} unavailable`}</span>}
    </div>
  </section>;
}

function CompanySearch({ initialTicker, loading, loadingTicker, onAnalyze }: { initialTicker: string; loading: boolean; loadingTicker: string; onAnalyze: (ticker: string) => void }) {
  const [value, setValue] = useState(initialTicker);
  const [userEdited, setUserEdited] = useState(false);
  const [activeMatch, setActiveMatch] = useState(-1);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [lookup, setLookup] = useState<{ query: string; matches: CompanyMatch[]; status: "loading" | "done" | "failed"; errorCode?: string; errorMessage?: string }>({ query: "", matches: [], status: "done" });
  const [formError, setFormError] = useState("");
  const [pendingSubmit, setPendingSubmit] = useState<string | null>(null);
  const pendingSubmitRef = useRef<string | null>(null);
  const [directTickerFallback, setDirectTickerFallback] = useState<string | null>(null);
  const query = value.trim();
  const currentLookup = lookup.query === query;
  const matches = currentLookup ? lookup.matches : [];
  const searchLoading = currentLookup && lookup.status === "loading";

  const chooseTicker = useCallback((ticker: string) => {
    setValue(ticker);
    setUserEdited(false);
    setFormError("");
    setSuggestionsOpen(false);
    setActiveMatch(-1);
    pendingSubmitRef.current = null;
    setPendingSubmit(null);
    setDirectTickerFallback(null);
    setLookup({ query, matches: [], status: "done" });
    onAnalyze(ticker);
  }, [onAnalyze, query]);

  const completePendingSearch = useCallback((searchQuery: string, searchMatches: CompanyMatch[], status: "done" | "failed", errorCode?: string, errorMessage?: string) => {
    if (pendingSubmitRef.current !== searchQuery) return;
    pendingSubmitRef.current = null;
    setPendingSubmit(null);
    if (status === "failed" && !canConfirmTickerAfterSearchFailure(errorCode)) {
      setDirectTickerFallback(null);
      setFormError(errorCode === "rate_limited"
        ? errorMessage || "Company search is temporarily rate-limited. Wait before trying again."
        : COMPANY_SEARCH_DISABLED_MESSAGE);
      return;
    }
    const decision = decideCompanySearch(searchQuery, searchMatches, status);
    if (decision.kind === "select") {
      setFormError("");
      chooseTicker(decision.ticker);
      return;
    }
    if (decision.kind === "confirm-ticker") {
      setDirectTickerFallback(decision.ticker);
      setFormError(status === "failed"
        ? "Company search is unavailable. Confirm the symbol before analyzing it directly."
        : "No company match was found. If this is a ticker, you can analyze it directly.");
      return;
    }
    setFormError(status === "failed"
      ? "Company search is unavailable. Enter a ticker in uppercase to analyze it directly."
      : "Choose a listed company from the suggestions, or enter its ticker symbol.");
  }, [chooseTicker]);

  useEffect(() => {
    if (!userEdited || query.length < 2 || containsPersonalSearchText(query)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLookup({ query, matches: [], status: "loading" });
      void api<CompanyMatch[]>("/api/search", { method: "POST", body: JSON.stringify({ q: query }), signal: controller.signal })
        .then((items) => { if (!controller.signal.aborted) { setLookup({ query, matches: items, status: "done" }); setSuggestionsOpen(items.length > 0); setActiveMatch(-1); completePendingSearch(query, items, "done"); } })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            const errorCode = reason instanceof ApiResponseError ? reason.code : undefined;
            const errorMessage = reason instanceof ApiResponseError ? reason.message : undefined;
            setLookup({ query, matches: [], status: "failed", errorCode, errorMessage });
            setSuggestionsOpen(false);
            completePendingSearch(query, [], "failed", errorCode, errorMessage);
          }
        });
    }, 240);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [completePendingSearch, query, userEdited]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (containsPersonalSearchText(query)) {
      setFormError(PERSONAL_SEARCH_MESSAGE);
      setDirectTickerFallback(null);
      pendingSubmitRef.current = null;
      setPendingSubmit(null);
      return;
    }
    const lookupState = !userEdited ? "idle" : !currentLookup ? query.length >= 2 ? "pending" : "idle" : lookup.status;
    const decision = decideCompanySearch(query, matches, lookupState);
    if (decision.kind === "invalid") {
      setFormError("Enter a company name or ticker to begin.");
      return;
    }
    if (decision.kind === "wait") {
      setDirectTickerFallback(null);
      setFormError("");
      pendingSubmitRef.current = query;
      setPendingSubmit(query);
      setSuggestionsOpen(true);
      return;
    }
    if (decision.kind === "confirm-ticker" && currentLookup && lookup.status === "failed" && !canConfirmTickerAfterSearchFailure(lookup.errorCode)) {
      setDirectTickerFallback(null);
      setFormError(lookup.errorCode === "rate_limited"
        ? lookup.errorMessage || "Company search is temporarily rate-limited. Wait before trying again."
        : COMPANY_SEARCH_DISABLED_MESSAGE);
      return;
    }
    if (decision.kind === "confirm-ticker") {
      setDirectTickerFallback(decision.ticker);
      setFormError(currentLookup && lookup.status === "failed"
        ? "Company search is unavailable. Confirm the symbol before analyzing it directly."
        : "No company match was found. If this is a ticker, you can analyze it directly.");
      return;
    }
    setFormError("");
    setDirectTickerFallback(null);
    chooseTicker(decision.ticker);
  };

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!matches.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSuggestionsOpen(true);
      setActiveMatch((current) => Math.min(current + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSuggestionsOpen(true);
      setActiveMatch((current) => current < 0 ? matches.length - 1 : Math.max(current - 1, 0));
    } else if (event.key === "Escape") {
      event.preventDefault();
      setSuggestionsOpen(false);
      setActiveMatch(-1);
    } else if (event.key === "Enter" && suggestionsOpen && activeMatch >= 0) {
      event.preventDefault();
      chooseTicker(matches[activeMatch].ticker);
    }
  };

  return <section className="search-region" aria-label="Company search">
    <form className="search-form" onSubmit={submit}>
      <label className="visually-hidden" htmlFor="company-search">Company name or ticker</label>
      <div className="search-input-wrap">
        <span className="search-icon" aria-hidden="true"><svg viewBox="0 0 20 20" focusable="false"><circle cx="8.7" cy="8.7" r="5.7" /><path d="m13 13 4 4" /></svg></span>
      <input id="company-search" type="search" role="combobox" autoComplete="off" spellCheck={false} value={value} onChange={(event) => { const nextValue = event.target.value; setValue(nextValue); setUserEdited(true); setFormError(containsPersonalSearchText(nextValue) ? PERSONAL_SEARCH_MESSAGE : ""); pendingSubmitRef.current = null; setPendingSubmit(null); setDirectTickerFallback(null); setActiveMatch(-1); setSuggestionsOpen(true); }} onKeyDown={handleSearchKeyDown} onFocus={() => { if (matches.length) setSuggestionsOpen(true); }} onBlur={() => { setSuggestionsOpen(false); setActiveMatch(-1); }} placeholder="Search a listed company or ticker" aria-autocomplete="list" aria-describedby="company-search-help" aria-controls={suggestionsOpen && matches.length > 0 ? "company-suggestions" : undefined} aria-expanded={suggestionsOpen && matches.length > 0} aria-activedescendant={suggestionsOpen && activeMatch >= 0 ? `company-option-${activeMatch}` : undefined} />
        {(searchLoading || pendingSubmit === query) && <span className="search-status" role="status" aria-live="polite">Searching</span>}
      </div>
      <button className="button button-primary analyze-button" type="submit" disabled={loading && loadingTicker === query.toUpperCase()}>
        {loading && loadingTicker === query.toUpperCase() ? <><span className="spinner" aria-hidden="true" />Analyzing</> : "Analyze company"}
      </button>
      {suggestionsOpen && matches.length > 0 && <div className="suggestions" id="company-suggestions" role="listbox" aria-label="Company matches">
        {matches.map((match, index) => <div className="suggestion" role="option" tabIndex={-1} id={`company-option-${index}`} aria-label={`Select ${match.company}, ticker ${match.ticker}`} aria-selected={activeMatch === index} key={match.ticker} onPointerDown={(event) => event.preventDefault()} onMouseEnter={() => setActiveMatch(index)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); chooseTicker(match.ticker); } }} onClick={() => chooseTicker(match.ticker)}><span><strong>{match.company}</strong><small>{match.exchange || "Listed company"}{match.sector ? ` · ${match.sector}` : ""}</small></span><b>{match.ticker}</b></div>)}
      </div>}
    </form>
    <p className="search-help" id="company-search-help">Type a company name and choose a match, or enter a ticker. Analysis opens on TSRP Score; public market data may be delayed.</p>
    {(formError || (currentLookup && lookup.status === "failed")) && <p className="form-error" role="alert">{formError || (!canConfirmTickerAfterSearchFailure(lookup.errorCode) ? lookup.errorCode === "rate_limited" ? lookup.errorMessage || "Company search is temporarily rate-limited. Wait before trying again." : COMPANY_SEARCH_DISABLED_MESSAGE : "Company search is temporarily unavailable. You can still enter a ticker symbol.")}</p>}
    {directTickerFallback && <button className="button button-quiet direct-ticker-fallback" type="button" onClick={() => chooseTicker(directTickerFallback)}>Analyze {directTickerFallback} as a ticker</button>}
    {currentLookup && lookup.status === "done" && query.length >= 2 && matches.length === 0 && !/^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(query) && <p className="search-empty" role="status">No listed-company matches for that search.</p>}
  </section>;
}

export function ResearchWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const snapshotKey = SNAPSHOT_STORAGE_KEY;
  const tickerParam = (searchParams.get("ticker") || "").trim().toUpperCase();
  const section = sectionFromQuery(searchParams.get("section"));
  const requestId = useRef(0);
  const scenarioRequestId = useRef(0);
  const snapshotRequestId = useRef(0);
  const analysisController = useRef<AbortController | null>(null);
  const scenarioController = useRef<AbortController | null>(null);
  const automaticAnalysisTicker = useRef<string | null>(null);
  const skipFirstScroll = useRef(true);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingTicker, setLoadingTicker] = useState("");
  const [analysisError, setAnalysisError] = useState<{ ticker: string; message: string; code?: string } | null>(null);
  const [scenario, setScenario] = useState<ScenarioOutput | null>(null);
  const [scenarioError, setScenarioError] = useState("");
  const [scenarioValidationErrors, setScenarioValidationErrors] = useState<Partial<Record<keyof Assumptions, string>>>({});
  const [scenarioLoading, setScenarioLoading] = useState(false);
  const [draft, setDraft] = useState<DraftAssumptions>({ starting_growth: "", fcf_margin: "", discount_rate: "", terminal_growth: "" });
  const [scenarioDraftStorageAvailable, setScenarioDraftStorageAvailable] = useState(true);
  const [copyState, setCopyState] = useState("");
  const [snapshotCount, setSnapshotCount] = useState(0);
  const [snapshotStoreRevision, setSnapshotStoreRevision] = useState(0);
  const [snapshotStoreCorrupt, setSnapshotStoreCorrupt] = useState(false);
  const [snapshotState, setSnapshotState] = useState<"idle" | "loading" | "saved" | "empty" | "incompatible" | "error">("idle");
  const [diff, setDiff] = useState<Diff | null>(null);
  const [snapshotMessage, setSnapshotMessage] = useState("");
  const pendingMoverScroll = useRef(false);
  const serverClockAnchor = useRef<ServerClockAnchor | null>(null);
  const [freshnessNow, setFreshnessNow] = useState(() => Date.now());

  const loadAnalysis = useCallback(async (ticker: string) => {
    analysisController.current?.abort();
    const controller = new AbortController();
    analysisController.current = controller;
    const id = ++requestId.current;
    scenarioRequestId.current += 1;
    scenarioController.current?.abort();
    setLoading(true);
    setLoadingTicker(ticker);
    setAnalysisError(null);
    setScenario(null);
    setScenarioError("");
    setScenarioValidationErrors({});
    setScenarioLoading(false);
    try {
      let responseClockAnchor: ServerClockAnchor | null = null;
      const result = await api<unknown>(`/api/analyze?ticker=${encodeURIComponent(ticker)}`, { signal: controller.signal }, (response) => {
        responseClockAnchor = createServerClockAnchor(response.headers.get("Date"), performance.now());
      });
      assertAnalysisResponse(result, ticker);
      if (id === requestId.current) {
        if (responseClockAnchor) serverClockAnchor.current = responseClockAnchor;
        setFreshnessNow(currentClockMs(serverClockAnchor.current));
        setAnalysis(result);
        const baselineDraft: DraftAssumptions = {
          starting_growth: result.company.solver_status === "exact" ? scenarioBaselineValue("starting_growth", result.company.required_growth) : "",
          fcf_margin: scenarioBaselineValue("fcf_margin", result.company.model_fcf_margin),
          discount_rate: scenarioBaselineValue("discount_rate", result.company.discount_rate),
          terminal_growth: scenarioBaselineValue("terminal_growth", result.company.terminal_growth),
        };
        for (const field of Object.keys(scenarioDraftValidationErrors(baselineDraft)) as (keyof Assumptions)[]) {
          baselineDraft[field] = "";
        }
        const restoredDraft = readScenarioDraft(ticker, baselineDraft);
        setDraft(restoredDraft.draft);
        setScenarioValidationErrors({});
        setScenarioDraftStorageAvailable(restoredDraft.storageAvailable);
      }
    } catch (reason) {
      if (id === requestId.current && !controller.signal.aborted) {
        setAnalysisError({
          ticker,
          message: reason instanceof Error ? reason.message : "Research data is temporarily unavailable.",
          code: reason instanceof ApiResponseError ? reason.code : undefined,
        });
      }
    } finally {
      if (id === requestId.current && analysisController.current === controller) {
        analysisController.current = null;
        if (!controller.signal.aborted) { setLoading(false); setLoadingTicker(""); }
      }
    }
  }, [setAnalysis, setAnalysisError, setDraft, setFreshnessNow, setLoading, setLoadingTicker, setScenario, setScenarioDraftStorageAvailable, setScenarioError, setScenarioLoading, setScenarioValidationErrors]);

  useEffect(() => () => {
    analysisController.current?.abort();
    scenarioController.current?.abort();
  }, []);

  useEffect(() => {
    if (!tickerParam) return;
    const interval = window.setInterval(() => setFreshnessNow(currentClockMs(serverClockAnchor.current)), 60_000);
    return () => window.clearInterval(interval);
  }, [tickerParam]);

  useEffect(() => {
    if (!tickerParam) {
      automaticAnalysisTicker.current = null;
      const { cancelAnalysis, cancelScenario } = cancelRequestsOnHome(analysisController, scenarioController, requestId, scenarioRequestId);
      if (cancelAnalysis || cancelScenario) queueMicrotask(() => {
        if (new URLSearchParams(window.location.search).has("ticker")) return;
        if (cancelAnalysis && !analysisController.current) { setLoading(false); setLoadingTicker(""); }
        if (cancelScenario && !scenarioController.current) setScenarioLoading(false);
      });
      return;
    }
    const cancelled = cancelPendingAnalysisForDifferentTicker(
      analysisController, requestId, loadingTicker, tickerParam,
    );
    if (cancelled) { setLoading(false); setLoadingTicker(""); }
    if (analysis?.company.ticker === tickerParam) {
      return;
    }
    if (!claimAutomaticAnalysisAttempt(automaticAnalysisTicker, tickerParam)) return;
    queueMicrotask(() => {
      if (!isAnalysisTickerStillSelected(window.location.search, tickerParam)) {
        releaseAutomaticAnalysisAttempt(automaticAnalysisTicker, tickerParam);
        return;
      }
      if (!analysisController.current) void loadAnalysis(tickerParam);
    });
  }, [tickerParam, analysis?.company.ticker, loadingTicker, loadAnalysis]);

  const sectionScrollKey = researchScrollKey(section, tickerParam, analysis?.company.ticker);
  useEffect(() => {
    if (skipFirstScroll.current) { skipFirstScroll.current = false; return; }
    const heading = document.getElementById("section-title");
    scrollToResearchHeading(heading);
  }, [sectionScrollKey]);

  useEffect(() => {
    if (!pendingMoverScroll.current || !tickerParam || analysis?.company.ticker !== tickerParam) return;
    pendingMoverScroll.current = false;
    const heading = document.getElementById("section-title");
    scrollToResearchHeading(heading);
  }, [tickerParam, analysis?.company.ticker]);

  const company = analysis?.company.ticker === tickerParam ? analysis.company : null;
  const currentAnalysis = company && analysis ? analysis : null;
  const analysisIsLoading = loading && Boolean(tickerParam && loadingTicker === tickerParam);
  const scenarioIsLoading = scenarioLoading && Boolean(tickerParam && company);
  const selectedAnalysisError = analysisErrorForTicker(analysisError, tickerParam);
  const selectedAnalysisErrorCode = analysisError?.ticker === tickerParam ? analysisError.code : undefined;

  const marketDataStale = company
    ? company.is_stale || isMarketDataStale(company.market_at, freshnessNow)
    : false;

  const navigateSection = (next: SectionName) => {
    if (next === section) {
      const heading = document.getElementById("section-title");
      scrollToResearchHeading(heading);
      return;
    }
    router.push(researchUrl(tickerParam || null, next), { scroll: false });
  };

  const openMover = (ticker: string) => {
    if (ticker === tickerParam) {
      if (!company) {
        pendingMoverScroll.current = true;
        if (!analysisIsLoading) void loadAnalysis(ticker);
        return;
      }
      const heading = document.getElementById("section-title");
      scrollToResearchHeading(heading);
      return;
    }
    pendingMoverScroll.current = true;
    router.push(researchUrl(ticker, section), { scroll: false });
  };

  const beginAnalysis = useCallback((ticker: string) => {
    const normalized = ticker.trim().toUpperCase();
    setAnalysisError(null);
    router.push(researchUrl(normalized, "TSRP Score"), { scroll: false });
    // A failed same-ticker analysis leaves the URL unchanged, so the URL effect
    // cannot retry it. Re-submit must start a fresh request even without loaded data.
    const currentTicker = new URLSearchParams(window.location.search).get("ticker") ?? "";
    if (shouldReloadSubmittedTicker(currentTicker, normalized)) void loadAnalysis(normalized);
  }, [loadAnalysis, router, setAnalysisError]);

  const openShare = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopyState("Link copied");
    } catch { setCopyState("Copy is unavailable in this browser"); }
    window.setTimeout(() => setCopyState(""), 2400);
  };

  useEffect(() => {
    if (!company) return;
    const snapshotRequest = ++snapshotRequestId.current;
    let active = true;
    const timer = window.setTimeout(() => {
      if (!active || snapshotRequestId.current !== snapshotRequest) return;
      try {
        const saved = readSnapshots(company.ticker, undefined, snapshotKey);
        setSnapshotStoreCorrupt(false);
        setSnapshotCount(saved.length);
        setDiff(null);
        setSnapshotMessage("");
        if (!saved.length) { setSnapshotState("empty"); return; }
        if (section !== "What Changed?") { setSnapshotState("saved"); return; }
        setSnapshotState("loading");
        const previous = saved[saved.length - 1];
        const currentSnapshot = scenario ? { ...company, user_assumptions: scenario.inputs } : company;
        void api<Diff>("/api/snapshot_diff", { method: "POST", body: JSON.stringify({ previous: toSnapshotComparisonPayload(previous, previous.saved_at), current: toSnapshotComparisonPayload(currentSnapshot) }) })
          .then((value) => { if (active && snapshotRequestId.current === snapshotRequest) { setDiff(value); setSnapshotState(value.comparable ? "saved" : "incompatible"); } })
          .catch(() => { if (active && snapshotRequestId.current === snapshotRequest) setSnapshotState("error"); });
      } catch (reason) {
        if (!active || snapshotRequestId.current !== snapshotRequest) return;
        setSnapshotCount(0);
        setSnapshotStoreCorrupt(reason instanceof SnapshotStoreError && reason.issue === "corrupt");
        setSnapshotMessage(reason instanceof Error ? reason.message : "Saved research could not be read from this browser.");
        setSnapshotState("error");
      }
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [company, scenario, section, snapshotKey, snapshotStoreRevision]);

  useEffect(() => {
    const syncSnapshots = (event: StorageEvent) => {
      if (!isTsrpSnapshotStorageChange(event.key)) return;
      try {
        if (event.storageArea === window.localStorage) setSnapshotStoreRevision((revision) => revision + 1);
      } catch { /* Browser storage can be unavailable in private or restricted contexts. */ }
    };
    window.addEventListener("storage", syncSnapshots);
    return () => window.removeEventListener("storage", syncSnapshots);
  }, []);

  const saveSnapshot = async () => {
    if (!company) return;
    const analysisRequest = requestId.current;
    const snapshotRequest = ++snapshotRequestId.current;
    let savedToBrowser = false;
    const snapshot = {
      ...company,
      user_assumptions: scenario?.inputs ?? null,
      saved_at: new Date().toISOString(),
    };
    const storedSnapshot = toStoredSnapshot(snapshot);
    try {
      const existing = readSnapshots(company.ticker, undefined, snapshotKey);
      const previous = existing.at(-1);
      const updated = [...existing, storedSnapshot].slice(-MAX_SNAPSHOTS_PER_TICKER);
      writeSnapshots(company.ticker, updated, undefined, snapshotKey);
      savedToBrowser = true;
      setSnapshotStoreCorrupt(false);
      setSnapshotCount(updated.length);
      setSnapshotMessage("Saved in this browser only.");
      if (!previous) { setDiff(null); setSnapshotState("empty"); return; }
      setSnapshotState("loading");
      const result = await api<Diff>("/api/snapshot_diff", { method: "POST", body: JSON.stringify({ previous: toSnapshotComparisonPayload(previous, previous.saved_at), current: toSnapshotComparisonPayload(snapshot) }) });
      if (snapshotRequestId.current !== snapshotRequest || requestId.current !== analysisRequest || (new URLSearchParams(window.location.search).get("ticker") || "").trim().toUpperCase() !== company.ticker) return;
      setDiff(result);
      setSnapshotState(result.comparable ? "saved" : "incompatible");
    } catch (reason) {
      if (snapshotRequestId.current !== snapshotRequest || requestId.current !== analysisRequest || (new URLSearchParams(window.location.search).get("ticker") || "").trim().toUpperCase() !== company.ticker) return;
      setSnapshotStoreCorrupt(reason instanceof SnapshotStoreError && reason.issue === "corrupt");
      setSnapshotState("error");
      setSnapshotMessage(savedToBrowser
        ? "Snapshot saved in this browser, but its comparison could not be completed. Try again shortly."
        : reason instanceof Error ? reason.message : "Storage or comparison is unavailable in this browser.");
    }
  };

  const clearSnapshots = () => {
    if (!company) return;
    try {
      if (snapshotStoreCorrupt) {
        const confirmed = window.confirm("Saved data cannot be read safely. Resetting it will remove TSRP snapshots from this browser. Continue?");
        if (!confirmed) return;
        snapshotRequestId.current += 1;
        clearSnapshotStore(undefined, snapshotKey);
        setSnapshotMessage("Saved research was removed from this browser.");
      } else {
        snapshotRequestId.current += 1;
        writeSnapshots(company.ticker, [], undefined, snapshotKey);
        setSnapshotMessage("Saved research removed from this browser.");
      }
      setSnapshotStoreCorrupt(false);
      setSnapshotCount(0);
      setDiff(null);
      setSnapshotState("empty");
    } catch (reason) {
      setSnapshotState("error");
      setSnapshotMessage(reason instanceof Error ? reason.message : "Saved research could not be removed.");
    }
  };

  const setScenarioField = (field: keyof Assumptions, value: string) => {
    scenarioRequestId.current += 1;
    scenarioController.current?.abort();
    setScenarioLoading(false);
    setScenario(null);
    setScenarioError("");
    const nextDraft = { ...draft, [field]: value };
    setDraft(nextDraft);
    setScenarioValidationErrors((current) => Object.keys(current).length ? scenarioDraftValidationErrors(nextDraft) : current);
    if (company) {
      const saved = writeScenarioDraft(company.ticker, nextDraft);
      if (saved !== "invalid") setScenarioDraftStorageAvailable(saved === "stored");
    }
  };
  const runScenario = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!company) return;
    const validationErrors = scenarioDraftValidationErrors(draft);
    if (Object.keys(validationErrors).length) {
      setScenarioValidationErrors(validationErrors);
      setScenarioError("Review the highlighted assumptions before calculating.");
      window.requestAnimationFrame(() => document.getElementById("scenario-validation-summary")?.focus());
      return;
    }
    setScenarioValidationErrors({});
    const converted = Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, value.trim() === "" ? null : Number(value) / 100])) as Assumptions;
    scenarioController.current?.abort();
    const controller = new AbortController();
    scenarioController.current = controller;
    const id = ++scenarioRequestId.current;
    setScenarioLoading(true); setScenarioError("");
    try {
      const contextToken = currentAnalysis?.scenario_context_token;
      if (!contextToken) {
        setScenarioError("Secure scenario verification is not configured for this deployment. Company research remains available.");
        return;
      }
      const result = await api<ScenarioOutput>("/api/scenario", { method: "POST", body: JSON.stringify({ ticker: company.ticker, assumptions: converted, context_token: contextToken }), signal: controller.signal });
      if (id === scenarioRequestId.current) {
        setScenario(result);
      }
    } catch (reason) { if (id === scenarioRequestId.current && !controller.signal.aborted) setScenarioError(reason instanceof Error ? reason.message : "Scenario calculation is temporarily unavailable."); }
    finally {
      if (id === scenarioRequestId.current) {
        scenarioController.current = null;
        setScenarioLoading(false);
      }
    }
  };

  const brandHeader = <header className="masthead">
    <Link href="/" className="brand" aria-label="TSRP home" onClick={(event) => { event.preventDefault(); setAnalysis(null); setAnalysisError(null); router.push("/"); }}>
      <span className="brand-copy"><strong>TSRP</strong><small>The Saleh Research Project</small></span>
    </Link>
  </header>;
  const searchForm = <CompanySearch key={tickerParam || "home"} initialTicker={tickerParam} loading={analysisIsLoading} loadingTicker={analysisIsLoading ? loadingTicker : ""} onAnalyze={beginAnalysis} />;
  const privacyHref = tickerParam
    ? `/privacy?${new URLSearchParams({ ticker: tickerParam, section }).toString()}`
    : "/privacy";

  return (
    <main className="page-shell">
      <MarketMoversTicker onSelect={openMover} />
      {tickerParam ? <>{brandHeader}{searchForm}</> : <section className="home-hero" aria-label="Start company research">{brandHeader}<div className="home-hero-search">{searchForm}</div></section>}
      {!tickerParam && <>
        <section className="home-research-guide" aria-labelledby="home-guide-title">
          <div className="home-guide-intro">
            <p className="eyebrow">Inside a company analysis</p>
            <h2 id="home-guide-title">Follow the signal back to its evidence.</h2>
            <p>TSRP keeps reported results, market expectations, and model assumptions distinct, so you can see what supports the research view—and what remains uncertain.</p>
          </div>
          <ol className="home-research-steps">
            {HOME_RESEARCH_SECTIONS.map(({ section, description }, index) => <li key={section}>
              <span className="home-guide-number tabular">{String(index + 1).padStart(2, "0")}</span>
              <div><h3>{section}</h3><p>{description}</p></div>
            </li>)}
          </ol>
        </section>
      </>}

      {analysisIsLoading && !company && <section className="loading-state" aria-live="polite" aria-busy="true"><span className="spinner spinner-large" aria-hidden="true" /><div><strong>Researching {loadingTicker}</strong><p>Checking company identity, market data, and available financial statements.</p></div></section>}

      {selectedAnalysisError && tickerParam && !company && <section className="error-state" role="alert"><span className="error-symbol" aria-hidden="true">!</span><div><h1>Research could not be completed</h1><p>{selectedAnalysisError}</p>{analysisErrorCanRetry(selectedAnalysisErrorCode) && <button className="button button-secondary" type="button" onClick={() => void loadAnalysis(tickerParam)}>Try again</button>}</div></section>}
      {tickerParam && !company && <ResearchNavigation ticker={tickerParam} section={section} />}

      {company && currentAnalysis && <>
        <section className="company-header" aria-label={`${company.company} company information`}>
          <div className="company-identification"><h1>{company.company}</h1><span className="ticker-pill">{company.ticker}</span></div>
          <div className="company-meta"><span>{company.exchange}</span><span>{company.sector}</span><span>Reporting currency {company.reporting_currency}</span></div>
          <div className="company-header-tools"><span className={`freshness-badge ${marketDataStale ? "is-warning" : ""}`}><span className="status-dot" />{marketDataStale ? "Market data may be stale" : "Data retrieved"} · {dateLabel(company.retrieved_at, true)} UTC</span><button type="button" className="text-action" onClick={() => void loadAnalysis(company.ticker)} disabled={analysisIsLoading && loadingTicker === company.ticker} aria-label={`Refresh ${company.ticker} research data`}>{analysisIsLoading && loadingTicker === company.ticker ? "Refreshing…" : "Refresh data"}</button></div>
        </section>

        {selectedAnalysisError && <p className="form-error refresh-error" role="alert">Refresh failed. Showing the previously loaded analysis. {selectedAnalysisError}</p>}

        {(marketDataStale || company.quality_flags.length > 0) && <section className="data-notes" aria-label="Data quality and limitations" aria-live="polite">
          {marketDataStale && <p className="warning-note">A usable market timestamp is missing or outside TSRP’s 48-hour freshness window. Confirm the quote before relying on it.</p>}
          {company.quality_flags.map((flag, index) => <p className={`quality-note severity-${flag.severity}`} key={`${flag.message}-${index}`}>{flag.message}</p>)}
        </section>}

        <ResearchNavigation ticker={company.ticker} section={section} />

        <section className="research-section" id="research-detail" aria-labelledby="section-title">
          <div className="section-title-row"><div><h1 id="section-title" tabIndex={-1}>{section}</h1></div><ResearchActions key={`${company.ticker}:${section}`} company={company} scenario={scenario} copyState={copyState} onShare={openShare} /></div>
          <div className="research-view-transition" key={section}>
            {section === "TSRP Score" && <ScoreView analysis={currentAnalysis} onOpen={navigateSection} snapshotCount={snapshotCount} />}
            {section === "Expectations vs Reality" && <ExpectationsView company={company} />}
            {section === "Valuation" && <ValuationView company={company} draft={draft} setDraftField={setScenarioField} onSubmit={runScenario} result={scenario} loading={scenarioIsLoading} error={scenarioError} validationErrors={scenarioValidationErrors} sectorMethodologySupported={company.sector_methodology_supported} currencyMethodologySupported={hasCurrencyMethodology(company)} scenarioContextAvailable={currentAnalysis.scenario_context_status === "available" && Boolean(currentAnalysis.scenario_context_token)} scenarioContextStatus={currentAnalysis.scenario_context_status ?? (currentAnalysis.scenario_context_token ? "unavailable" : "not_configured")} scenarioDraftStorageAvailable={scenarioDraftStorageAvailable} />}
            {section === "Catalysts & Risks" && <CatalystsView company={company} />}
            {section === "What Changed?" && <ChangesView company={company} count={snapshotCount} state={snapshotState} diff={diff} message={snapshotMessage} corrupt={snapshotStoreCorrupt} onSave={saveSnapshot} onClear={clearSnapshots} />}
          </div>
          <Methodology company={company} />
        </section>
      </>}

      <footer className="site-footer"><span>TSRP · Saleh Research Project</span><span>Independent research, not investment advice.</span><Link href={privacyHref}>Privacy &amp; data</Link></footer>
    </main>
  );
}

function ScoreView({ analysis, onOpen, snapshotCount }: { analysis: Analysis; onOpen: (section: SectionName) => void; snapshotCount: number }) {
  const c = analysis.company;
  const drivers = c.score_trace?.components ?? [];
  const subscores = c.score_trace?.subscores;
  return <>
    <section className="score-hero" aria-label="TSRP research score">
      <div className="score-main"><span className="eyebrow">Research signal · 0 to 100</span><div className="score-number">{finite(c.score) ? c.score.toFixed(1) : "N/A"}</div><div className="score-rating">{c.score_label}</div><p>{finite(c.score) ? "A transparent research index of business and financial evidence. Each factor is counted once. Valuation assumptions and analyst forecasts do not change the score." : !c.sector_methodology_supported ? `TSRP has no validated score methodology for ${c.sector}; company facts remain available without a score.` : "The available evidence does not support a defensible TSRP Score."}</p></div>
      <div className="score-drivers"><div className="panel-heading"><h2>What drives this score</h2><span className="confidence-label">Data confidence: {c.confidence}</span></div>{drivers.length ? drivers.map((driver) => <div className="driver-row" key={driver.key}><div className="driver-label"><span>{driver.label}</span><small>{formatScoreWeight(driver.weight)} base weight{c.score !== null && driver.value !== null ? ` · ${formatScoreWeight(driver.applied_weight)} applied · ${driver.contribution?.toFixed(2) ?? "N/A"} score points` : " · not included"}</small></div><svg className="driver-track" aria-hidden="true" viewBox="0 0 100 5" preserveAspectRatio="none"><rect className="driver-track-fill" x="0" y="0" width={driver.value === null ? 0 : Math.max(0, Math.min(100, driver.value))} height="5" /></svg><strong className="tabular">{driver.value === null ? "N/A" : driver.value.toFixed(1)}</strong></div>) : <p className="empty-inline">{c.sector_methodology_supported ? "Score drivers are unavailable because core inputs are missing." : `Score drivers are unavailable because TSRP has no calibrated model for ${c.sector}.`}</p>}
        <p className="score-explain">{c.score_trace ? `Evidence coverage: ${Math.round(c.score_trace.evidence_coverage * 100)}%. Missing evidence is excluded, not assigned a substitute score. Remaining weights are rebalanced; scores with different coverage are not directly comparable. Valuation and analyst forecasts remain separate research context and never change this operating-evidence index.` : !c.sector_methodology_supported ? "No score drivers are shown because TSRP has no validated sector calibration." : c.quality_flags.find((flag) => flag.message.startsWith("TSRP Score is withheld:"))?.message ?? "Score evidence is unavailable."}</p>
        {subscores && <details className="score-factor-details"><summary>Inspect underlying financial evidence</summary><div className="score-factor-groups">{Object.entries(subscores).map(([key, detail]) => {
          const title = key.split("_").map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
          return <section className="score-factor-group" key={key}><div className="score-factor-heading"><h3>{title}</h3><span>{detail.score === null ? "N/A" : `${detail.score.toFixed(0)} / 100`} · {Math.round(detail.evidence_coverage * 100)}% evidence</span></div>{detail.components.map((factor) => {
            const evidence = scoreFactorEvidence(c, factor.key);
            const provenance = evidence.sourceKeys.map((sourceKey) => `${sourceKey.replaceAll("_", " ")}: ${sourceLine(c, sourceKey)}`).join(" · ") || "Source unavailable";
            return <div className="score-factor-row" key={factor.key}><div><span>{factor.label}</span><small>Observed: {evidence.observed}</small><small>{provenance}</small>{factor.normalization && <small>{factor.normalization}</small>}</div><strong className="tabular">{factor.value === null ? "N/A" : `${factor.value.toFixed(0)} / 100`}</strong><small>{Math.round(factor.weight * 100)}% factor weight</small></div>;
          })}</section>;
        })}</div></details>}
        {c.score_trace?.weight_sensitivity && <p className="coverage-note">Weight sensitivity: {number(c.score_trace.weight_sensitivity.low)}–{number(c.score_trace.weight_sensitivity.high)} / 100 when one factor weight changes by ±25%. This is not a confidence interval or evidence of predictive accuracy.</p>}
      </div>
    </section>

    <section className="metric-strip" aria-label="Key company metrics">
      <Metric label="Share price" value={money(c.price, c.trading_currency)} source={sourceLine(c, "share_price")} />
      <Metric label="Market capitalization" value={money(c.market_cap_reporting, c.reporting_currency, true)} source={sourceLine(c, "market_cap_reporting")} />
      <Metric label="Enterprise value" value={money(c.enterprise_value, c.reporting_currency, true)} source={sourceLine(c, "enterprise_value")} />
      <Metric label="Revenue" value={money(c.revenue, c.reporting_currency, true)} source={sourceLine(c, "revenue")} />
    </section>

    <div className="main-research-grid">
      <PriceChart points={analysis.price_history} currency={c.trading_currency} ticker={c.ticker} source={c.sources.share_price && "source" in c.sources.share_price ? c.sources.share_price.source : "the configured market-data source"} />
      <section className="summary-panel">
        <div className="panel-heading"><div><h2>Valuation at a glance</h2><p>Model output, not a price target</p></div></div>
        <div className="summary-stat"><span>10-year model-required growth</span><strong className="tabular">{pct(c.required_growth)}</strong><small>{solverDescription(c)}</small></div>
        <div className="summary-stat"><span>Historical revenue growth</span><strong className="tabular">{pct(c.historical_growth)}</strong><small>{c.historical_growth_years ? `${c.historical_growth_years.toFixed(1)} years · ${c.revenue_growth_source || "Source unavailable"}` : "Comparable annual history unavailable"}</small></div>
        <div className="summary-stat"><span>{analystEstimateShortLabel(c.analyst_estimate_period)}</span><strong className="tabular">{pct(c.consensus_growth)}</strong><small>{c.analyst_estimate_period || "Yahoo Finance estimate unavailable"}</small></div>
        <div className="summary-divider" />
        <button className="text-action summary-link" type="button" onClick={() => onOpen("Valuation")}>Explore valuation assumptions <span aria-hidden="true">→</span></button>
      </section>
    </div>

    <section className="preview-section" aria-label="Research section previews">
      <div className="preview-heading"><h2>Continue the research</h2><p>Each view uses the same analysis and ticker.</p></div>
      <div className="preview-grid">
        <Preview section="Expectations vs Reality" title="Compare evidence and expectations" summary={expectationsSummary(c)} onOpen={onOpen} />
        <Preview section="Valuation" title="Review what the price requires" summary={solverDescription(c)} onOpen={onOpen} />
        <Preview section="Catalysts & Risks" title="Track supported signals" summary={c.fcf_margin === null ? "Cash-flow evidence is unavailable; this limits what can be concluded." : `Latest compatible free-cash-flow margin: ${pct(c.fcf_margin)}.`} onOpen={onOpen} />
        <Preview section="What Changed?" title="Compare saved research" summary={snapshotCount ? `${snapshotCount} saved ${snapshotCount === 1 ? "snapshot" : "snapshots"} in this browser.` : "Save an analysis snapshot to compare it later."} onOpen={onOpen} />
      </div>
    </section>
  </>;
}

function Metric({ label, value, source }: { label: string; value: string; source: string }) {
  return <div className="metric"><span className="metric-label">{label}</span><strong className="metric-value tabular">{value}</strong><small className="metric-source">{source}</small></div>;
}

function Preview({ section, title, summary, onOpen }: { section: SectionName; title: string; summary: string; onOpen: (section: SectionName) => void }) {
  return <article className="preview-row"><div><p className="preview-section-label">{section}</p><h3>{title}</h3><p>{summary}</p></div><button className="button button-quiet preview-open" type="button" onClick={() => onOpen(section)}>Open <span aria-hidden="true">→</span></button></article>;
}

function EvidenceRow({ label, value, note, source }: { label: string; value: string; note: string; source: string }) {
  return <div className="evidence-row"><div><span className="evidence-label">{label}</span><strong className="evidence-value tabular">{value}</strong></div><p>{note}</p><small>{source}</small></div>;
}

function ExpectationsView({ company: c }: { company: Company }) {
  return <div className="view-stack">
    <section className="content-panel"><div className="panel-heading"><div><h2>What is the market asking the company to deliver?</h2><p>Reported results, analyst estimates, and model-implied requirements are shown separately.</p></div></div>
      <div className="evidence-grid three-columns">
        <EvidenceRow label="Historical revenue growth" value={pct(c.historical_growth)} note={c.historical_growth_years ? `Annualized over approximately ${c.historical_growth_years.toFixed(1)} years, using actual reporting dates. This is backward-looking, not a forecast.` : "Comparable positive annual revenue observations are unavailable."} source={sourceLine(c, "historical_growth")} />
        <EvidenceRow label={analystEstimateLabel(c.analyst_estimate_period)} value={pct(c.consensus_growth)} note={analystEstimateDescription(c.analyst_estimate_period, finite(c.consensus_growth))} source={sourceLine(c, "analyst_growth")} />
        <EvidenceRow label="TSRP model-required revenue growth" value={pct(c.required_growth)} note={`${solverDescription(c)} This is the annual revenue growth used to reconcile TSRP’s 10-year illustration to enterprise value—not a one-year analyst estimate or an observed market expectation.`} source={`TSRP calculation · ${c.reporting_currency} assumptions · ${methodologyVersionLabel(c.methodology_version)}`} />
      </div>
      <div className="comparison-note"><strong>Different evidence, different meanings.</strong><span>Past revenue growth, analyst estimates, and TSRP’s modeled growth requirement cover different periods. The model output also depends on illustrative cash-flow and discount assumptions, so it is not observed market consensus. These figures alone do not establish whether the company is beating or missing expectations.</span></div>
    </section>
    <section className="content-panel"><div className="panel-heading"><div><h2>Reported financial evidence</h2><p>Latest available annual observations. Values retain their reporting currency and period.</p></div></div>
      <div className="evidence-grid two-columns">
        <EvidenceRow label="Revenue" value={money(c.revenue, c.reporting_currency, true)} note={c.revenue_period ? `Latest annual period ending ${dateLabel(c.revenue_period)}.` : "Reporting period is unavailable; treat this value cautiously."} source={sourceLine(c, "revenue")} />
        <EvidenceRow label="Free cash flow" value={money(c.free_cash_flow, c.reporting_currency, true)} note={freeCashFlowEvidenceNote(c.fcf_margin, freeCashFlowSourceKind(c), c.fcf_margin === null ? "" : pct(c.fcf_margin))} source={sourceLine(c, "free_cash_flow")} />
      </div>
    </section>
  </div>;
}

function solverDescription(c: Company): string {
  if (!c.sector_methodology_supported) return "Unavailable: TSRP has no validated enterprise-value and free-cash-flow method for this sector.";
  if (!hasCurrencyMethodology(c)) return "Unavailable: TSRP has no validated valuation calibration for this reporting currency.";
  if (c.solver_status === "exact" && finite(c.required_growth)) return "Exact model solution within tested bounds.";
  if (c.solver_status === "below_range") return `Below the tested range (${pct(c.solver_low)} to ${pct(c.solver_high)}); no precise rate is shown.`;
  if (c.solver_status === "above_range") return `Above the tested range (${pct(c.solver_low)} to ${pct(c.solver_high)}); no precise rate is shown.`;
  return c.model_fcf_refused ? "Unavailable: current cash-flow evidence is non-positive or insufficient." : "Unavailable: required market value, revenue, or cash-flow inputs are missing.";
}

function hasCurrencyMethodology(c: Company): boolean {
  return c.currency_methodology_supported ?? (c.rate_currency !== "N/A" && c.rate_currency === c.reporting_currency);
}

function hasValuationMethodology(c: Company): boolean {
  return c.sector_methodology_supported && hasCurrencyMethodology(c);
}

function expectationsSummary(c: Company) {
  if (finite(c.historical_growth) && finite(c.consensus_growth)) return `Historical revenue growth ${pct(c.historical_growth)}; ${analystEstimateShortLabel(c.analyst_estimate_period).toLowerCase()} ${pct(c.consensus_growth)}. Different periods.`;
  if (finite(c.required_growth)) return `TSRP’s illustrative 10-year model requires ${pct(c.required_growth)} annualized revenue growth to reconcile to enterprise value; this is assumption-dependent, not a market forecast.`;
  return "Reported history or comparable expectations are incomplete.";
}

function ValuationView({ company: c, draft, setDraftField, onSubmit, result, loading, error, validationErrors, sectorMethodologySupported, currencyMethodologySupported, scenarioContextAvailable, scenarioContextStatus, scenarioDraftStorageAvailable }: {
  company: Company; draft: DraftAssumptions; setDraftField: (field: keyof Assumptions, value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void; result: ScenarioOutput | null; loading: boolean; error: string;
  validationErrors: Partial<Record<keyof Assumptions, string>>;
  sectorMethodologySupported: boolean; currencyMethodologySupported: boolean; scenarioContextAvailable: boolean;
  scenarioContextStatus: NonNullable<Analysis["scenario_context_status"]>; scenarioDraftStorageAvailable: boolean;
}) {
  const baseCurrency = c.reporting_currency;
  const fieldLabels: [keyof Assumptions, string, string][] = [
    ["starting_growth", "Starting revenue growth", "User assumption for the first forecast years"],
    ["fcf_margin", "Modeled pre-debt cash-flow margin", "Estimated unlevered cash flow divided by revenue, after reinvestment and estimated tax. Scenario range: 1%–50%. The reconstructed starting margin transitions toward this level. If unavailable, your assumed margin applies throughout."],
    ["discount_rate", "Discount rate", "Return required to discount future cash flows. Scenario range: 3%–20%, and it must exceed long-run growth."],
    ["terminal_growth", "Long-run growth", "Assumed growth beyond the forecast period"],
  ];
  return <div className="view-stack">
    <section className="content-panel"><div className="panel-heading"><div><h2>What growth does TSRP’s model need to reconcile to enterprise value?</h2><p>{!sectorMethodologySupported ? "Market and reported financial facts are shown, but TSRP withholds valuation outputs because this sector lacks a validated model." : !currencyMethodologySupported ? "Market and reported financial facts are shown, but currency-dependent valuation is unavailable because TSRP has no validated calibration for this reporting currency." : "A ten-year enterprise DCF sensitivity using estimated pre-debt operating cash flow and a disclosed financing-cost estimate. Inspect the assumptions; this is not a price target."}</p></div></div>
      <div className="valuation-overview"><div><span className="eyebrow">Enterprise value</span><strong className="valuation-value tabular">{money(c.enterprise_value, baseCurrency, true)}</strong><small>{sourceLine(c, "enterprise_value")}</small></div><div><span className="eyebrow">10-year model-required growth</span><strong className="valuation-value tabular">{c.solver_status === "exact" ? pct(c.required_growth) : c.solver_status === "unavailable" ? "N/A" : "Outside range"}</strong><small>{solverDescription(c)}</small></div><div><span className="eyebrow">Pre-debt cash-flow margin used</span><strong className="valuation-value tabular">{pct(c.model_fcf_margin)}</strong><small>{c.model_fcf_note || sourceLine(c, "free_cash_flow")}</small></div></div>
      <div className="comparison-note"><strong>{!sectorMethodologySupported ? "Valuation method unavailable for this sector." : !currencyMethodologySupported ? "Valuation calibration unavailable for this currency." : "Illustrative model limitation:"}</strong><span>{!sectorMethodologySupported ? `TSRP has no validated enterprise-value/free-cash-flow method for ${c.sector}. It does not apply generic industrial-company assumptions to financial companies; use sector-appropriate equity valuation methods instead.` : !currencyMethodologySupported ? `TSRP has no validated valuation calibration for ${c.reporting_currency}. It does not substitute USD rates for this company.` : "This is not observed consensus or a forecast. Estimated pre-debt cash flow uses operating profit after estimated tax, depreciation, capital spending and working-capital cash movement. WACC weights an issuer-beta equity cost and historical borrowing-cost proxy. Tax, reinvestment, beta and dated market inputs require judgment; terminal margins assume growth can be funded within that cash conversion. Other ownership claims and lease conventions can affect the EV approximation."}</span></div>
      {sectorMethodologySupported && <div className="rate-grid"><div><span>Discount rate</span><strong className="tabular">{currencyMethodologySupported ? pct(c.discount_rate) : "N/A"}</strong></div><div><span>Long-run growth</span><strong className="tabular">{currencyMethodologySupported ? pct(c.terminal_growth) : "N/A"}</strong></div><div><span>Enterprise value / annual revenue</span><strong className="tabular">{finite(c.ev_sales) ? `${number(c.ev_sales)}×` : "N/A"}</strong></div><div><span>Debt / assets</span><strong className="tabular">{pct(c.debt_to_assets)}</strong></div></div>}
      {sectorMethodologySupported && c.valuation_basis && <details className="score-evidence"><summary>Inspect cash flow and financing assumptions</summary>
        <p>{c.valuation_basis.cash_flow.reason}</p>
        <EvidenceRow label="Estimated pre-debt cash flow" value={money(c.unlevered_cash_flow ?? null, baseCurrency, true)} note={`Annual period ${c.valuation_basis.cash_flow.period ?? "unavailable"}; effective tax ${pct(c.valuation_basis.cash_flow.tax_rate)}. This is calculated, not a reported cash-flow subtotal.`} source={sourceLine(c, "unlevered_cash_flow")} />
        {Object.entries(c.valuation_basis.cash_flow.inputs).map(([key, value]) => <EvidenceRow key={key} label={key === "capex" ? "Capital spending" : key === "depreciation" ? "Depreciation and amortization" : key.replaceAll("_", " ")} value={money(value, baseCurrency, true)} note="Same annual period and reporting currency; missing inputs are not zero." source={c.valuation_basis!.cash_flow.source} />)}
        <p>{c.valuation_basis.capital_cost.reason}</p>
        <EvidenceRow label="Cost of equity / borrowing-cost proxy" value={`${pct(c.valuation_basis.capital_cost.cost_of_equity)} / ${pct(c.valuation_basis.capital_cost.cost_of_debt)}`} note={`Equity weight ${pct(c.valuation_basis.capital_cost.equity_weight)}; debt weight ${pct(c.valuation_basis.capital_cost.debt_weight)}; provider beta ${number(c.valuation_basis.capital_cost.beta)}.`} source={`${c.valuation_basis.capital_cost.reference.source} · ${c.valuation_basis.capital_cost.reference.date}`} />
        <p>Risk-free assumption {pct(c.risk_free)}; equity-risk premium {pct(c.equity_risk_premium)}. Book debt approximates market debt. Historical interest cost and effective tax are not forward borrowing quotes or marginal tax rates. The perpetual cash-flow margin embeds future reinvestment; acquisitions, dilution, leases and other ownership claims may require adjustments.</p>
        {c.valuation_projection && <>
          <EvidenceRow label="Value from beyond year ten" value={pct(c.valuation_projection.terminal_value_share)} note="Share of modeled enterprise value from the perpetual terminal cash flow. A larger share makes the result more dependent on long-run assumptions." source="TSRP discounted cash-flow calculation" />
          <h3>Annual model cash flows</h3>
          <div className="table-scroll"><table><thead><tr><th scope="col">Forecast year</th><th scope="col">Revenue growth</th><th scope="col">Pre-debt cash flow</th><th scope="col">Present value</th></tr></thead><tbody>{c.valuation_projection.annual_cash_flows.map((row) => <tr key={row.year}><th scope="row">{row.year}</th><td>{pct(row.revenue_growth)}</td><td>{money(row.cash_flow, baseCurrency, true)}</td><td>{money(row.present_value, baseCurrency, true)}</td></tr>)}</tbody></table></div>
          <h3>Discount-rate and long-run-growth sensitivity</h3><p>Starting revenue growth and cash-flow assumptions are held fixed. These are alternative model values, not forecast probabilities.</p>
          <div className="table-scroll"><table><thead><tr><th scope="col">Discount rate</th><th scope="col">Long-run growth</th><th scope="col">Enterprise value</th></tr></thead><tbody>{c.valuation_sensitivity?.map((row) => <tr key={`${row.discount_rate}-${row.terminal_growth}`}><td>{pct(row.discount_rate)}</td><td>{pct(row.terminal_growth)}</td><td>{money(row.enterprise_value, baseCurrency, true)}</td></tr>)}</tbody></table></div>
        </>}
      </details>}
    </section>
    {!sectorMethodologySupported ? <section className="content-panel scenario-unavailable"><div className="panel-heading"><div><h2>Scenarios are unavailable</h2><p>The current model uses enterprise value and free cash flow, which are not a suitable framework for this sector. No scenario output is calculated.</p></div></div></section> : !currencyMethodologySupported ? <section className="content-panel scenario-unavailable"><div className="panel-heading"><div><h2>Scenarios are unavailable</h2><p>TSRP has no validated valuation calibration for this reporting currency. No scenario assumptions or outputs are presented.</p></div></div></section> : !scenarioContextAvailable ? <section className="content-panel scenario-unavailable"><div className="panel-heading"><div><h2>Scenarios are unavailable</h2><p>{scenarioContextStatus === "not_configured" ? "Secure scenario verification is not configured for this deployment." : scenarioContextStatus === "unavailable" ? "Current market or reported company facts are stale, incomplete, or not comparable. Refresh the analysis before testing a scenario." : "Scenario analysis is unavailable for this company."} Company research remains available.</p></div></div></section> : <section className="content-panel"><div className="panel-heading"><div><h2>Test your own assumptions</h2><p>Scenario values use the server-checked company facts shown in this analysis plus your assumptions. Data is not re-fetched for each scenario; outputs are illustrations, not forecasts.</p><p className="scenario-storage-note">{scenarioDraftStorageAvailable ? "Unsubmitted assumptions stay in this browser tab and are restored after refresh. They are sent to TSRP only when you calculate." : "This browser is blocking tab storage; unsubmitted assumptions may be lost if you refresh. They are sent to TSRP only when you calculate."}</p></div></div>
      <form className="assumption-form" onSubmit={onSubmit} noValidate>
        {fieldLabels.map(([field, label, help]) => {
          const errorMessage = validationErrors[field];
          const [minimum, maximum] = SCENARIO_DRAFT_LIMITS[field];
          return <label className="assumption-field" key={field}>
            <span>{label}</span>
            <div className={`percent-input${errorMessage ? " input-invalid" : ""}`}><input id={`scenario-${field}`} type="number" min={minimum} max={maximum} step="0.1" value={draft[field]} onChange={(event) => setDraftField(field, event.target.value)} aria-invalid={Boolean(errorMessage)} aria-describedby={errorMessage ? `${field}-help ${field}-error` : `${field}-help`} /><b>%</b></div>
            <small id={`${field}-help`}>{help}</small>
            {errorMessage && <small className="field-error" id={`${field}-error`}>{errorMessage}</small>}
          </label>;
        })}
        {Object.keys(validationErrors).length > 0 && <div className="form-error scenario-validation-summary" id="scenario-validation-summary" role="alert" tabIndex={-1}>
          <strong>Correct the highlighted assumptions:</strong>
          <ul>{fieldLabels.filter(([field]) => validationErrors[field]).map(([field, label]) => <li key={field}><a href={`#scenario-${field}`}>{label}: {validationErrors[field]}</a></li>)}</ul>
        </div>}
        <div className="assumption-actions"><button className="button button-primary" type="submit" disabled={loading}>{loading && <span className="spinner" aria-hidden="true" />}{loading ? "Calculating" : "Calculate scenario"}</button><span>Forecast horizon: 10 years</span></div>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      {result && <div className="scenario-result" role="status"><div><span className="eyebrow">Scenario enterprise value</span><strong className="tabular">{money(result.enterprise_value, baseCurrency, true)}</strong><small>Calculated from the assumptions above · Not a target price</small></div><div><span className="eyebrow">Scenario equity value</span><strong className="tabular">{money(result.equity_value, baseCurrency, true)}</strong><small>{result.equity_value === null ? "Unavailable because cash or debt is missing." : "Enterprise value less reported debt and identified other ownership claims, plus cash."}</small></div></div>}
    </section>}
  </div>;
}

type EvidenceItem = { title: string; description: string; checkpoint: string; source: string; tone: "positive" | "risk" | "neutral" };

function deriveCatalystEvidence(c: Company) {
  const positive: EvidenceItem[] = [];
  const risks: EvidenceItem[] = [];
  if (finite(c.historical_growth) && c.historical_growth > 0) positive.push({ title: "Positive reported revenue trend", description: `Revenue grew at ${pct(c.historical_growth)} annualized over approximately ${c.historical_growth_years.toFixed(1)} years. This is historical evidence, not a forecast.`, checkpoint: "Confirm against the next comparable annual revenue filing; a sustained decline would weaken this signal.", source: sourceLine(c, "historical_growth"), tone: "positive" });
  if (finite(c.fcf_margin) && c.fcf_margin > 0) positive.push({ title: "Positive cash conversion", description: `Period-aligned free cash flow was ${pct(c.fcf_margin)} of revenue.`, checkpoint: "Recheck operating cash flow and capital spending in the next same-period filing; a negative margin would weaken this signal.", source: sourceLine(c, "free_cash_flow"), tone: "positive" });
  const estimateSignal = analystEstimateSignal(c.consensus_growth, c.analyst_estimate_period);
  if (estimateSignal?.effect === "support") {
    positive.push({
      title: estimateSignal.title,
      description: estimateSignal.description,
      checkpoint: "Compare with reported revenue for the corresponding fiscal period when available.",
      source: sourceLine(c, "analyst_growth"),
      tone: "neutral",
    });
  } else if (estimateSignal?.effect === "risk") {
    risks.push({
      title: estimateSignal.title,
      description: estimateSignal.description,
      checkpoint: "Compare with reported revenue for the corresponding fiscal period when available.",
      source: sourceLine(c, "analyst_growth"),
      tone: "risk",
    });
  }
  if (finite(c.historical_growth) && c.historical_growth < 0) risks.push({ title: "Historical revenue contraction", description: `Revenue declined at ${pct(c.historical_growth)} annualized over approximately ${c.historical_growth_years.toFixed(1)} years.`, checkpoint: "Check the next comparable annual filing; a return to positive growth would reduce this concern.", source: sourceLine(c, "historical_growth"), tone: "risk" });
  if (finite(c.fcf_margin) && c.fcf_margin <= 0) risks.push({ title: "Negative or zero free cash flow", description: `Period-aligned free-cash-flow margin is ${pct(c.fcf_margin)}. Reported cash flow is distinct from the separately estimated pre-debt valuation cash flow.`, checkpoint: "Review operating cash flow and capital spending in the next same-period filing.", source: sourceLine(c, "free_cash_flow"), tone: "risk" });
  if (c.cash === null || c.debt === null) risks.push({ title: "Incomplete balance-sheet evidence", description: "Cash or debt could not be verified from the available provider data; leverage conclusions are limited.", checkpoint: "Confirm cash and debt from the next company filing.", source: missingBalanceSource(c.cash, c.debt, (field) => sourceLine(c, field)), tone: "risk" });
  if (!positive.length && !risks.length) risks.push({ title: "Company-specific signals unavailable", description: "Available financial data does not support a specific catalyst or risk statement. This does not mean none exist.", checkpoint: "Review primary filings, company guidance, and event disclosures directly.", source: "No sufficient evidence", tone: "neutral" });
  return { positive, risks };
}

function CatalystsView({ company }: { company: Company }) {
  const evidence = useMemo(() => deriveCatalystEvidence(company), [company]);
  return <div className="view-stack">
    <div className="comparison-note"><strong>Evidence-based signals only.</strong><span>TSRP does not infer future events, probabilities, or dates. Each item below is derived from reported or provider-supplied data and includes a measurable point to revisit.</span></div>
    <section className="content-panel"><div className="panel-heading"><div><h2>Potential supports</h2><p>Observed conditions that could strengthen the research thesis if they persist.</p></div><span className="signal-count">{evidence.positive.length}</span></div><div className="signal-list">{evidence.positive.length ? evidence.positive.map((item) => <Signal key={item.title} item={item} />) : <p className="empty-inline">No positive company-specific signal is supported by the available data.</p>}</div></section>
    <section className="content-panel"><div className="panel-heading"><div><h2>Risks and evidence gaps</h2><p>Conditions that could weaken the thesis or reduce confidence in the analysis.</p></div><span className="signal-count">{evidence.risks.length}</span></div><div className="signal-list">{evidence.risks.length ? evidence.risks.map((item) => <Signal key={item.title} item={item} />) : <p className="empty-inline">No material risk signal was generated from these limited data points. This is not evidence that no risks exist.</p>}</div></section>
  </div>;
}

function Signal({ item }: { item: EvidenceItem }) {
  return <article className="signal-row"><span className={`signal-marker tone-${item.tone}`} aria-hidden="true" /><div><h3>{item.title}</h3><p>{item.description}</p><p className="signal-check"><strong>What to check:</strong> {item.checkpoint}</p><small>{item.source}</small></div></article>;
}

const CHANGE_LABELS: Record<string, string> = {
  score: "TSRP Score", business_quality: "Business quality", financial_strength: "Financial strength", market_expectations: "Market expectations",
  historical_growth: "Historical revenue growth", required_growth: "Growth required by price",
  revenue: "Revenue", revenue_period: "Revenue period", free_cash_flow: "Free cash flow", fcf_period: "Free-cash-flow period", enterprise_value: "Enterprise value",
  model_fcf_margin: "Pre-debt cash-flow margin used", discount_rate: "Discount rate", terminal_growth: "Long-run growth", solver_status: "Valuation status",
  user_starting_growth: "Scenario starting growth", user_fcf_margin: "Scenario pre-debt cash-flow margin",
  user_discount_rate: "Scenario discount rate", user_terminal_growth: "Scenario long-run growth",
};

function ChangesView({ company, count, state, diff, message, corrupt, onSave, onClear }: { company: Company; count: number; state: string; diff: Diff | null; message: string; corrupt: boolean; onSave: () => void; onClear: () => void }) {
  const renderValue = (field: string, value: unknown) => {
    const fallback = snapshotValueFallback(field, value);
    if (fallback) return fallback;
    if (typeof value === "number") {
      if (field.includes("growth") || field.includes("margin") || field.includes("rate") || field === "terminal_growth") return pct(value);
      if (["score", "business_quality", "financial_strength", "market_expectations"].includes(field)) return value.toFixed(1);
      if (["revenue", "free_cash_flow", "enterprise_value"].includes(field)) return money(value, company.reporting_currency, true);
      return number(value);
    }
    if (value && typeof value === "object") return "Assumptions updated";
    return String(value);
  };
  return <div className="view-stack">
    <section className="content-panel"><div className="panel-heading"><div><h2>What is different from the last saved analysis?</h2><p>Snapshots are stored only in this browser. Comparisons require the same ticker, methodology, analysis format, and reporting currency.</p></div></div>
      <div className="snapshot-toolbar"><div><strong>{count} saved {count === 1 ? "snapshot" : "snapshots"}</strong><span>Up to {MAX_SNAPSHOTS_PER_TICKER} are retained on this browser for {company.ticker}.</span></div><div className="button-pair"><button className="button button-primary" type="button" onClick={onSave} disabled={state === "loading"}>{state === "loading" ? "Comparing…" : "Save current analysis"}</button><button className="button button-quiet" type="button" onClick={onClear} disabled={state === "loading" || (count === 0 && !corrupt)}>{corrupt ? "Reset saved research" : "Remove saved research"}</button></div></div>
      {message && <p className={state === "error" ? "form-error" : "form-success"} role={state === "error" ? "alert" : "status"}>{message}</p>}
      {state === "loading" && <p className="empty-inline" role="status">Comparing compatible saved research…</p>}
      {state === "error" && <p className="form-error" role="alert">Saved research could not be read or compared. Your current analysis is unchanged.</p>}
        {state === "incompatible" && <p className="comparison-note"><strong>These analyses cannot be compared.</strong><span>{snapshotComparisonReason(diff?.reason)} Save a new snapshot to begin a comparable series. Older snapshots remain in this browser until you remove them.</span></p>}
      {state === "empty" && (count === 0
        ? <div className="empty-state"><strong>No comparable saved analysis yet</strong><p>Save this result. The next saved analysis for {company.ticker} can be compared with it.</p></div>
        : <div className="empty-state"><strong>First analysis saved</strong><p>Save another analysis later to see what changed for {company.ticker}.</p></div>)}
      {state === "saved" && diff?.comparable && <div className="change-table-wrap">
        <p className="snapshot-date">Previous data retrieved: {dateLabel(diff.previous_retrieved_at, true)} UTC · Current data retrieved: {dateLabel(diff.current_retrieved_at, true)} UTC · {methodologyVersionLabel(company.methodology_version)}</p>
        {diff.excluded_fields?.length ? <p className="comparison-note">Some values are omitted because their periods, currencies, source definitions, or score evidence are missing or not comparable.</p> : null}
        {diff.changes.length ? <div className="change-table" role="table" aria-label="Research changes">
          <div className="change-table-head" role="row"><span role="columnheader">Measure</span><span role="columnheader">Previous</span><span role="columnheader">Current</span><span role="columnheader">Change</span></div>
          {diff.changes.map((change, index) => <div className="change-table-row" role="row" key={`${change.field}-${index}`}>
            <strong role="cell">{change.field === "consensus_growth" ? analystEstimateShortLabel(company.analyst_estimate_period) : CHANGE_LABELS[change.field] || change.field}</strong>
            <span role="cell" className="tabular">{renderValue(change.field, change.before)}</span>
            <span role="cell" className="tabular">{renderValue(change.field, change.after)}</span>
            <span role="cell" className={`tabular ${change.delta === null ? "" : change.delta > 0 ? "positive-text" : change.delta < 0 ? "negative-text" : ""}`}>{snapshotChangeStatus(change) ?? snapshotRateDelta(change.field, change.delta) ?? (change.delta === null ? "Updated" : change.delta > 0 ? `+${renderValue(change.field, change.delta)}` : renderValue(change.field, change.delta))}</span>
          </div>)}
        </div> : <div className="empty-state"><strong>No measured changes detected</strong><p>The comparable fields match the latest saved analysis.</p></div>}
      </div>}
    </section>
    <p className="snapshot-privacy">Stored in this browser only—not encrypted, synced, or backed up. Up to eight per ticker; saving another removes the oldest. Remove this ticker’s snapshots here or clear site data.</p>
  </div>;
}

function Methodology({ company: c }: { company: Company }) {
  const sourceEntries = [
    ["Share price", sourceLine(c, "share_price")],
    ["Market capitalization in reporting currency", sourceLine(c, "market_cap_reporting")],
    ["Enterprise value", sourceLine(c, "enterprise_value")],
    ["Enterprise value / annual revenue", sourceLine(c, "ev_sales")],
    ["Debt / assets", sourceLine(c, "debt_to_assets")],
    ["Revenue", sourceLine(c, "revenue")],
    ["Free cash flow", sourceLine(c, "free_cash_flow")],
    ["Historical revenue growth", sourceLine(c, "historical_growth")],
    ["Analyst revenue estimate", sourceLine(c, "analyst_growth")],
    ["Cash and debt", `${sourceLine(c, "cash")} · ${sourceLine(c, "debt")}`],
    ["Discount and terminal-growth assumptions", sourceLine(c, "valuation_assumptions")],
    ["Currency conversions", sourceLine(c, "fx")],
  ];
  return <details className="methodology" id="methodology"><summary>Sources, methodology, privacy & limitations</summary><div className="methodology-content">
    <div className="methodology-block"><h2>Sources and as-of dates</h2><p>Analysis retrieved {dateLabel(c.retrieved_at, true)} UTC. Latest market observation: {dateLabel(c.market_at, true)} UTC. The market provider may delay, revise, or omit data.</p><dl className="source-list">{sourceEntries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p>SEC EDGAR status: {c.sec_status.label}. SEC filings are not inferred from Yahoo values. SEC requests require a client/organization name and monitored email in <code>SEC_USER_AGENT</code>.</p></div>
    <div className="methodology-block"><h2>How the TSRP Score works</h2><p>{methodologyVersionLabel(c.methodology_version)} combines business quality (50%) and financial strength (50%). Business quality uses revenue growth, operating margin and return on average equity. Financial strength uses cash/debt, reported cash-flow margin and debt/assets. Each factor has one-third of its group weight and appears in only one group. Valuation remains separate: a high operating-evidence score does not mean a stock is cheap. Analyst estimates remain visible in Expectations vs Reality and do not affect the score. Historical revenue growth informs business quality only. Saturation thresholds are declared research-policy choices, not measured peer averages or statistically calibrated cutoffs. Related financial measures can still be correlated. Weight sensitivity changes one observed factor weight by ±25% at a time; the range is not a confidence interval. Missing factors are excluded, not assigned a neutral score; observed factors are reweighted. A final score requires at least 70% of total evidence coverage and at least 70% of current score weight to be supported. Evidence coverage is separate from data confidence. Scores with different coverage should not be compared directly.</p><p>The score is a heuristic research signal on a 0–100 scale. It is not calibrated as a probability, investment rating, forecast, or expected return. Market-multiple context is not a component of the final score.</p></div>
    <div className="methodology-block"><h2>Valuation and accounting</h2><p>Score v10 separates reported operating cash flow less capital spending from the valuation cash-flow basis. Estimated pre-debt cash flow is operating profit less estimated tax on positive operating profit, plus depreciation and amortization, less capital spending, plus the cash-flow-statement working-capital movement. Stock compensation stays expensed. All inputs must share the annual period and reporting currency; a reported FCF fallback is never used to fill this bridge. Effective tax and provider working-capital classifications are explicit approximations.</p><p>The estimated weighted average cost of capital (WACC) combines a CAPM equity cost using provider beta with historical interest expense divided by average book debt, after estimated tax. Market equity and book debt determine the financing weights. The USD risk-free and equity-risk-premium reference is dated January 2026, expires after one year, and is not a live yield. Unsupported currencies or missing inputs receive no invented baseline WACC; users can enter a clearly labeled scenario assumption. Terminal growth is a policy assumption, not a sourced forecast.</p><p>The ten-year model holds revenue growth for five years, then fades to terminal growth by year ten. The estimated pre-debt cash-flow margin transitions to the modeled margin. Terminal margin assumes growth can be supported within its reinvestment budget; sustainable growth, capital intensity, taxes, leases, dilution, acquisitions and additional ownership claims require judgment. Enterprise value remains an approximation using market equity plus reported debt and identified other ownership claims, less cash. Exact, below-range, above-range and unavailable solver states are distinct. This is an assumption-dependent model sensitivity, not observed market expectations, a price target or a recommendation. See <a href="https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/free-cash-flow-valuation" target="_blank" rel="noreferrer">CFA Institute’s cash-flow valuation framework</a>.</p><p>Historical revenue growth uses consecutive dated annual periods. FX is accepted only when fresh and comparable to the market timestamp; no conversion is made without a valid rate. Financial companies and unconfigured sectors retain observed facts but do not receive a generic industrial-company score or valuation.</p></div>
    <div className="methodology-block"><h2>Privacy and service limits</h2>
      <p>Search, scenario, browser-storage, provider, and hosting data flows are described in the <Link href="/privacy">Privacy &amp; data notice</Link>. Do not enter personal or confidential information in company search.</p>
      <p>Yahoo Finance data-use and public-display rights remain uncleared for release. {c.freshness}</p>
    </div>
    <div className="methodology-block disclaimer-block"><h2>Financial information disclaimer</h2><p>{DISCLAIMER}</p></div>
  </div></details>;
}
