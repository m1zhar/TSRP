"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PriceChart } from "@/components/price-chart";
import { dateLabel, finite, money, number, pct } from "@/lib/format";
import { toCsv } from "@/lib/csv";
import { toSnapshotComparisonPayload } from "@/lib/snapshot-comparison";
import { SECTIONS, type Analysis, type Assumptions, type Company, type CompanyMatch, type MarketMover, type MarketMovers, type SectionName, type Snapshot, type TopStocksResponse } from "@/lib/types";

type ApiError = { error?: { message?: string } };
type ApiEnvelope<T> = { data?: T } & ApiError;
type Diff = { comparable: boolean; reason: string; changes: { field: string; before: unknown; after: unknown; delta: number | null }[]; excluded_fields?: { field: string; reason: string }[] };
type ScenarioOutput = NonNullable<Company["scenario"]>;
type DraftAssumptions = Record<keyof Assumptions, string>;

const STORAGE_KEY = "tsrp.research-snapshots.v1";
const MAX_SNAPSHOTS_PER_TICKER = 8;
const MARKET_TICKER_LIMIT = 7;
const HOME_RESEARCH_SECTIONS: { section: SectionName; description: string }[] = [
  { section: "TSRP Score", description: "See the overall signal, its drivers, and data confidence." },
  { section: "Expectations vs Reality", description: "Review reported results beside analyst estimates and model-implied growth." },
  { section: "Valuation", description: "Inspect the growth and assumptions implied by the current company value." },
  { section: "Catalysts & Risks", description: "Review data-backed supports, risks, and what to check next." },
  { section: "What Changed?", description: "Compare analyses saved in this browser for the same company." },
];
const DISCLAIMER = "TSRP is an independent research tool. Its score and analysis are educational, are not a probability or price target, and are not personalized investment advice. Data may be incomplete, delayed, revised, or unavailable. Verify material information against company filings and other primary sources.";
const API_BASE = process.env.NODE_ENV === "development" ? "http://127.0.0.1:8000" : "";

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, { ...init, headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
  let payload: ApiEnvelope<T>;
  try { payload = await response.json() as ApiEnvelope<T>; }
  catch { throw new Error("The research service returned an unreadable response. Try again shortly."); }
  if (!response.ok || payload.data === undefined) throw new Error(payload.error?.message || "Research data is temporarily unavailable.");
  return payload.data;
}

function sectionFrom(value: string | null): SectionName {
  return SECTIONS.includes(value as SectionName) ? value as SectionName : "TSRP Score";
}

function isSnapshot(value: unknown): value is Snapshot {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Snapshot>;
  return typeof item.ticker === "string"
    && /^[A-Z0-9][A-Z0-9.-]{0,14}$/u.test(item.ticker)
    && typeof item.saved_at === "string" && Number.isFinite(Date.parse(item.saved_at))
    && typeof item.retrieved_at === "string" && Number.isFinite(Date.parse(item.retrieved_at))
    && typeof item.methodology_version === "string"
    && typeof item.analysis_schema_version === "string"
    && typeof item.reporting_currency === "string"
    && Boolean(item.sources && typeof item.sources === "object")
    && (item.score === null || (typeof item.score === "number" && Number.isFinite(item.score)))
    && (item.score === null || Boolean(item.score_trace && Number.isFinite(item.score_trace.score) && Array.isArray(item.score_trace.components)));
}

function readSnapshotStore(): Snapshot[] {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return [];
  const value: unknown = JSON.parse(raw);
  if (!Array.isArray(value) || !value.every(isSnapshot)) throw new Error("Saved research is malformed. It has not been overwritten.");
  return value;
}

function readSnapshots(ticker: string): Snapshot[] {
  return readSnapshotStore().filter((item) => item.ticker === ticker);
}

function writeSnapshots(ticker: string, values: Snapshot[]) {
  const all = readSnapshotStore().filter((item) => item.ticker !== ticker);
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...all, ...values.slice(-MAX_SNAPSHOTS_PER_TICKER)]));
}

function sourceLine(company: Company, key: string) {
  const value = company.sources[key];
  if (!value || !("source" in value)) return "Source unavailable";
  if (key === "fx" && "conversions" in value) {
    const conversions = (value.conversions ?? []).filter((item) => item.period !== "Same-currency identity");
    return conversions.length
      ? conversions.map((item) => `${item.from} → ${item.to}: ${item.rate === null ? "N/A" : number(item.rate)} · ${item.pairs.join(", ")} · ${item.period ?? "timestamp unavailable"}`).join("; ")
      : "No currency conversion required";
  }
  const base = `${value.source} · ${value.period}${value.currency && value.currency !== "N/A" ? ` · ${value.currency}` : ""}`;
  const conversions = "conversions" in value ? value.conversions ?? [] : [];
  return conversions.length
    ? `${base} · FX ${conversions.map((item) => `${item.from} → ${item.to}: ${item.rate === null ? "N/A" : number(item.rate)} · ${item.pairs.join(", ")} · ${item.period ?? "timestamp unavailable"}`).join("; ")}`
    : base;
}

function setUrl(ticker: string | null, section: SectionName) {
  const params = new URLSearchParams();
  if (ticker) params.set("ticker", ticker);
  if (section !== "TSRP Score") params.set("section", section);
  const query = params.toString();
  return query ? `/?${query}` : "/";
}

function ResearchNavigation({ ticker, section }: { ticker: string; section: SectionName }) {
  return <div className="research-nav-wrap">
    <p className="research-nav-label">Research questions</p>
    <nav className="research-nav" aria-label="Research sections">
      {SECTIONS.map((name) => <Link key={name} href={setUrl(ticker, name)} scroll={false} aria-current={section === name ? "page" : undefined} className={`research-nav-link ${section === name ? "is-active" : ""}`}>{name}</Link>)}
    </nav>
  </div>;
}

function MarketMoversTicker({ onSelect }: { onSelect: (ticker: string) => void }) {
  const [market, setMarket] = useState<MarketMovers | null>(null);
  const [moversStatus, setMoversStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [stocksStatus, setStocksStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [activeList, setActiveList] = useState<"movers" | "stocks">("movers");

  useEffect(() => {
    let active = true;
    const validItems = (values: MarketMover[], retrievedAtValue: string) => {
      const retrievedAt = Date.parse(retrievedAtValue);
      if (!Number.isFinite(retrievedAt) || Date.now() - retrievedAt > 15 * 60 * 1000) return [];
      return (Array.isArray(values) ? values : []).filter((item): item is MarketMover => {
        const marketAt = Date.parse(item.market_at);
        return /^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(item.ticker)
          && Number.isFinite(item.change_percent)
          && Number.isFinite(marketAt)
          && Date.now() - marketAt <= 72 * 60 * 60 * 1000
          && marketAt - Date.now() <= 5 * 60 * 1000;
      }).slice(0, MARKET_TICKER_LIMIT);
    };
    const loadMovers = async () => {
      try {
        const result = await api<MarketMovers>("/api/movers");
        const items = validItems(result.items, result.retrieved_at);
        if (active) {
          setMarket((previous) => ({ ...result, items, top_stocks: previous?.top_stocks ?? [] }));
          setMoversStatus(items.length ? "ready" : "unavailable");
        }
      } catch {
        if (active) {
          setMarket((previous) => previous ? { ...previous, items: [] } : null);
          setMoversStatus("unavailable");
        }
      }
    };
    const loadTopStocks = async () => {
      try {
        const result = await api<TopStocksResponse>("/api/top_stocks");
        const topStocks = validItems(result.items, result.retrieved_at);
        if (active) {
          setMarket((previous) => ({
            source: result.source,
            period: previous?.period ?? "Daily market movement",
            top_stocks_rank: result.rank,
            top_stocks_label: result.label,
            retrieved_at: previous?.retrieved_at ?? result.retrieved_at,
            items: previous?.items ?? [],
            top_stocks: topStocks,
          }));
          setStocksStatus(topStocks.length ? "ready" : "unavailable");
        }
      } catch {
        if (active) {
          setMarket((previous) => previous ? { ...previous, top_stocks: [] } : null);
          setStocksStatus("unavailable");
        }
      }
    };
    const load = () => { void loadMovers(); void loadTopStocks(); };
    load();
    const timer = window.setInterval(load, 5 * 60 * 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

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
  const activeItems = activeList === "movers" ? market?.items ?? [] : market?.top_stocks ?? [];
  const activeStatus = activeList === "movers" ? moversStatus : stocksStatus;
  const activeLabel = activeList === "movers" ? "Top movers" : market?.top_stocks_label || "Top stocks";

  return <section
    className="market-movers"
    aria-label={`${activeLabel}. ${activeList === "movers" ? "Ranked by daily percentage movement." : market?.top_stocks_rank ?? "Yahoo Finance most-active equities."} Daily changes from Yahoo Finance.`}
  >
    <div className="market-movers-tabs" role="group" aria-label="Choose ticker list">
      <button type="button" className={`market-movers-tab ${activeList === "movers" ? "is-active" : ""}`} aria-pressed={activeList === "movers"} onClick={() => setActiveList("movers")}>Top movers</button>
      <button type="button" className={`market-movers-tab ${activeList === "stocks" ? "is-active" : ""}`} aria-pressed={activeList === "stocks"} onClick={() => setActiveList("stocks")}>{market?.top_stocks_label || "Top stocks"}</button>
    </div>
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
  const [lookup, setLookup] = useState<{ query: string; matches: CompanyMatch[]; status: "loading" | "done" | "failed" }>({ query: "", matches: [], status: "done" });
  const [formError, setFormError] = useState("");
  const query = value.trim();
  const currentLookup = lookup.query === query;
  const matches = currentLookup ? lookup.matches : [];
  const searchLoading = currentLookup && lookup.status === "loading";

  const chooseTicker = (ticker: string) => {
    setUserEdited(false);
    setLookup({ query, matches: [], status: "done" });
    onAnalyze(ticker);
  };

  useEffect(() => {
    if (!userEdited || query.length < 2) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLookup({ query, matches: [], status: "loading" });
      void api<CompanyMatch[]>(`/api/search?q=${encodeURIComponent(query)}`, { signal: controller.signal })
        .then((items) => { if (!controller.signal.aborted) setLookup({ query, matches: items, status: "done" }); })
        .catch(() => { if (!controller.signal.aborted) setLookup({ query, matches: [], status: "failed" }); });
    }, 240);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, userEdited]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const chosen = matches.find((match) => match.ticker.toUpperCase() === query.toUpperCase());
    const directTicker = /^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(query) && query === query.toUpperCase();
    const ticker = chosen?.ticker || (directTicker ? query : matches[0]?.ticker);
    if (!ticker) {
      setFormError(query.length < 1 ? "Enter a company name or ticker to begin." : "Choose a listed company from the suggestions, or enter its ticker symbol.");
      return;
    }
    setFormError("");
    chooseTicker(ticker);
  };

  return <section className="search-region" aria-label="Company search">
    <form className="search-form" onSubmit={submit}>
      <label className="visually-hidden" htmlFor="company-search">Company name or ticker</label>
      <div className="search-input-wrap">
        <span className="search-icon" aria-hidden="true"><svg viewBox="0 0 20 20" focusable="false"><circle cx="8.7" cy="8.7" r="5.7" /><path d="m13 13 4 4" /></svg></span>
        <input id="company-search" type="search" role="combobox" autoComplete="off" spellCheck={false} value={value} onChange={(event) => { setValue(event.target.value); setUserEdited(true); setFormError(""); }} placeholder="Search a listed company or ticker" aria-autocomplete="list" aria-controls="company-suggestions" aria-expanded={matches.length > 0} />
        {searchLoading && <span className="search-status" aria-label="Searching">Searching</span>}
      </div>
      <button className="button button-primary analyze-button" type="submit" disabled={loading && loadingTicker === query.toUpperCase()}>
        {loading && loadingTicker === query.toUpperCase() ? <><span className="spinner" aria-hidden="true" />Analyzing</> : "Analyze company"}
      </button>
      {matches.length > 0 && <div className="suggestions" id="company-suggestions" role="listbox" aria-label="Company matches">
        {matches.map((match) => <button className="suggestion" type="button" role="option" aria-selected={false} key={match.ticker} onClick={() => chooseTicker(match.ticker)}><span><strong>{match.company}</strong><small>{match.exchange || "Listed company"}{match.sector ? ` · ${match.sector}` : ""}</small></span><b>{match.ticker}</b></button>)}
      </div>}
    </form>
    <p className="search-help">Search by company name or ticker. Analysis uses public market data and may be delayed.</p>
    {(formError || (currentLookup && lookup.status === "failed")) && <p className="form-error" role="alert">{formError || "Company search is temporarily unavailable. You can still enter a ticker symbol."}</p>}
    {currentLookup && lookup.status === "done" && query.length >= 2 && matches.length === 0 && !/^[A-Z0-9][A-Z0-9.-]{0,14}$/.test(query) && <p className="search-empty" role="status">No listed-company matches for that search.</p>}
  </section>;
}

export function ResearchWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const tickerParam = (searchParams.get("ticker") || "").trim().toUpperCase();
  const section = sectionFrom(searchParams.get("section"));
  const requestId = useRef(0);
  const scenarioRequestId = useRef(0);
  const skipFirstScroll = useRef(true);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingTicker, setLoadingTicker] = useState("");
  const [error, setError] = useState("");
  const [scenario, setScenario] = useState<ScenarioOutput | null>(null);
  const [scenarioError, setScenarioError] = useState("");
  const [scenarioLoading, setScenarioLoading] = useState(false);
  const [draft, setDraft] = useState<DraftAssumptions>({ starting_growth: "", fcf_margin: "", discount_rate: "", terminal_growth: "" });
  const [copyState, setCopyState] = useState("");
  const [snapshotCount, setSnapshotCount] = useState(0);
  const [snapshotState, setSnapshotState] = useState<"idle" | "loading" | "saved" | "empty" | "incompatible" | "error">("idle");
  const [diff, setDiff] = useState<Diff | null>(null);
  const [snapshotMessage, setSnapshotMessage] = useState("");
  const pendingMoverScroll = useRef(false);

  const loadAnalysis = useCallback(async (ticker: string) => {
    const id = ++requestId.current;
    scenarioRequestId.current += 1;
    setLoading(true);
    setLoadingTicker(ticker);
    setError("");
    setScenario(null);
    setScenarioError("");
    setScenarioLoading(false);
    try {
      const result = await api<Analysis>(`/api/analyze?ticker=${encodeURIComponent(ticker)}`);
      if (id === requestId.current) {
        setAnalysis(result);
        setDraft({
          starting_growth: finite(result.company.required_growth) && result.company.solver_status === "exact" ? (result.company.required_growth * 100).toFixed(1) : "",
          fcf_margin: finite(result.company.model_fcf_margin) ? (result.company.model_fcf_margin * 100).toFixed(1) : "",
          discount_rate: String(result.company.discount_rate * 100),
          terminal_growth: String(result.company.terminal_growth * 100),
        });
      }
    } catch (reason) {
      if (id === requestId.current) setError(reason instanceof Error ? reason.message : "Research data is temporarily unavailable.");
    } finally {
      if (id === requestId.current) { setLoading(false); setLoadingTicker(""); }
    }
  }, []);

  useEffect(() => {
    if (tickerParam && analysis?.company.ticker !== tickerParam) queueMicrotask(() => void loadAnalysis(tickerParam));
  }, [tickerParam, analysis?.company.ticker, loadAnalysis]);

  useEffect(() => {
    if (skipFirstScroll.current) { skipFirstScroll.current = false; return; }
    document.getElementById("research-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [section]);

  useEffect(() => {
    if (!pendingMoverScroll.current || !tickerParam || analysis?.company.ticker !== tickerParam) return;
    pendingMoverScroll.current = false;
    const heading = document.getElementById("section-title");
    heading?.scrollIntoView({ behavior: "smooth", block: "start" });
    heading?.focus({ preventScroll: true });
  }, [tickerParam, analysis?.company.ticker]);

  const company = analysis?.company.ticker === tickerParam ? analysis.company : null;
  const currentAnalysis = company && analysis ? analysis : null;

  const navigateSection = (next: SectionName) => {
    router.push(setUrl(tickerParam || null, next), { scroll: false });
  };

  const openMover = (ticker: string) => {
    if (ticker === tickerParam) {
      if (!company) {
        pendingMoverScroll.current = true;
        if (!loading) void loadAnalysis(ticker);
        return;
      }
      const heading = document.getElementById("section-title");
      heading?.scrollIntoView({ behavior: "smooth", block: "start" });
      heading?.focus({ preventScroll: true });
      return;
    }
    pendingMoverScroll.current = true;
    router.push(setUrl(ticker, section), { scroll: false });
  };

  const beginAnalysis = (ticker: string) => {
    const normalized = ticker.trim().toUpperCase();
    setError("");
    router.push(setUrl(normalized, "TSRP Score"), { scroll: false });
    if (tickerParam === normalized && company?.ticker === normalized) void loadAnalysis(normalized);
  };

  const openShare = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopyState("Link copied");
    } catch { setCopyState("Copy is unavailable in this browser"); }
    window.setTimeout(() => setCopyState(""), 2400);
  };

  const exportResearch = (format: "json" | "csv") => {
    if (!company) return;
    const enterpriseValueSource = company.sources.enterprise_value;
    const enterpriseValuePeriod = enterpriseValueSource && "period" in enterpriseValueSource ? enterpriseValueSource.period : "N/A";
    const historicalGrowthSource = company.sources.historical_growth;
    const historicalGrowthPeriod = historicalGrowthSource && "period" in historicalGrowthSource ? historicalGrowthSource.period : "N/A";
    const payload = {
      company: {
        ticker: company.ticker, name: company.company, exchange: company.exchange,
        retrieved_at: company.retrieved_at, market_at: company.market_at,
        reporting_currency: company.reporting_currency, trading_currency: company.trading_currency,
        score: company.score, score_label: company.score_label, score_trace: company.score_trace,
        price: company.price, market_cap_reporting: company.market_cap_reporting,
        market_cap_quote_currency: company.market_cap,
        enterprise_value: company.enterprise_value, revenue: company.revenue,
        free_cash_flow: company.free_cash_flow, fcf_period: company.fcf_period,
        historical_growth: company.historical_growth, consensus_growth: company.consensus_growth,
        required_growth: company.required_growth, assumptions: company.assumptions,
        scenario, sources: company.sources, quality_flags: company.quality_flags,
        confidence: company.confidence, is_stale: company.is_stale, freshness: company.freshness,
        methodology_version: company.methodology_version, analysis_schema_version: company.analysis_schema_version,
      },
      disclaimer: DISCLAIMER,
      limitations: company.quality_flags.map((flag) => flag.message),
    };
    const csvRows = [
      ["Metric", "Value", "Currency / units", "Source", "Period"],
      ["TSRP Score", company.score ?? "N/A", "0–100 research signal", "TSRP engine", company.methodology_version],
      ["Share price", company.price ?? "N/A", company.trading_currency, "Yahoo Finance", company.market_at ?? "N/A"],
      ["Market capitalization in reporting currency", company.market_cap_reporting ?? "N/A", company.reporting_currency, sourceLine(company, "market_cap_reporting"), company.market_at ?? "N/A"],
      ["Enterprise value", company.enterprise_value ?? "N/A", company.reporting_currency, enterpriseValueSource && "source" in enterpriseValueSource ? enterpriseValueSource.source : "Unavailable", enterpriseValuePeriod],
      ["Revenue", company.revenue ?? "N/A", company.reporting_currency, company.revenue_source ?? "Unavailable", company.revenue_period ?? "N/A"],
      ["Free cash flow", company.free_cash_flow ?? "N/A", company.reporting_currency, company.fcf_source ?? "Unavailable", company.fcf_period ?? "N/A"],
      ["Historical revenue growth", company.historical_growth ?? "N/A", "annualized", company.revenue_growth_source ?? "Unavailable", historicalGrowthPeriod],
      ["Next-year analyst revenue estimate", company.consensus_growth ?? "N/A", "growth rate", "Yahoo Finance analyst estimates", company.analyst_estimate_period ?? "N/A"],
      ["Growth required by current price", company.required_growth ?? "N/A", "annualized model input", "TSRP reverse DCF", company.solver_status],
      ["FCF margin assumption", company.model_fcf_margin ?? "N/A", "ratio", "TSRP baseline assumption", company.fcf_period ?? "N/A"],
      ["Discount-rate assumption", company.discount_rate, "ratio", sourceLine(company, "valuation_assumptions"), company.methodology_version],
      ["Terminal-growth assumption", company.terminal_growth, "ratio", sourceLine(company, "valuation_assumptions"), company.methodology_version],
      ["Scenario starting-growth assumption", scenario?.inputs.starting_growth ?? "N/A", "ratio", "User-entered scenario", scenario ? "Scenario assumptions" : "Not calculated"],
      ["Scenario FCF-margin assumption", scenario?.inputs.fcf_margin ?? "N/A", "ratio", "User-entered scenario", scenario ? "Scenario assumptions" : "Not calculated"],
      ["Scenario discount-rate assumption", scenario?.inputs.discount_rate ?? "N/A", "ratio", "User-entered scenario", scenario ? "Scenario assumptions" : "Not calculated"],
      ["Scenario terminal-growth assumption", scenario?.inputs.terminal_growth ?? "N/A", "ratio", "User-entered scenario", scenario ? "Scenario assumptions" : "Not calculated"],
      ["Scenario enterprise value", scenario?.enterprise_value ?? "N/A", company.reporting_currency, "User-entered scenario", scenario ? "Scenario assumptions" : "Not calculated"],
      ["Scenario equity value", scenario?.equity_value ?? "N/A", company.reporting_currency, "Scenario less reported debt plus cash", scenario ? "Scenario assumptions" : "Not calculated"],
      ["Analysis retrieved at", company.retrieved_at, "UTC", "TSRP", company.retrieved_at],
      ["Market observation as of", company.market_at ?? "N/A", "UTC", "Yahoo Finance", company.market_at ?? "N/A"],
      ["Limitations", company.quality_flags.map((flag) => flag.message).join("; ") || "See methodology and disclaimer", "", "TSRP quality flags", company.methodology_version],
    ];
    const csv = toCsv([...csvRows, [], ["Disclaimer", DISCLAIMER]]);
    const blob = new Blob([format === "json" ? JSON.stringify(payload, null, 2) : csv], { type: format === "json" ? "application/json" : "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `tsrp-${company.ticker.toLowerCase()}-research.${format}`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    if (!company) return;
    let active = true;
    const timer = window.setTimeout(() => {
      try {
        const saved = readSnapshots(company.ticker);
        setSnapshotCount(saved.length);
        setDiff(null);
        setSnapshotMessage("");
        if (!saved.length) { setSnapshotState("empty"); return; }
        if (section !== "What Changed?") { setSnapshotState("saved"); return; }
        setSnapshotState("loading");
        const previous = saved[saved.length - 1];
        void api<Diff>("/api/snapshot_diff", { method: "POST", body: JSON.stringify({ previous: toSnapshotComparisonPayload(previous, previous.saved_at), current: toSnapshotComparisonPayload(company) }) })
          .then((value) => { if (active) { setDiff(value); setSnapshotState(value.comparable ? "saved" : "incompatible"); } })
          .catch(() => { if (active) setSnapshotState("error"); });
      } catch { setSnapshotState("error"); }
    }, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [company, section]);

  const saveSnapshot = async () => {
    if (!company) return;
    const snapshot = { ...company, saved_at: new Date().toISOString() } as Snapshot;
    try {
      const existing = readSnapshots(company.ticker);
      const previous = existing.at(-1);
      const updated = [...existing, snapshot].slice(-MAX_SNAPSHOTS_PER_TICKER);
      writeSnapshots(company.ticker, updated);
      setSnapshotCount(updated.length);
      setSnapshotMessage("Saved in this browser only.");
      if (!previous) { setDiff(null); setSnapshotState("empty"); return; }
      setSnapshotState("loading");
      const result = await api<Diff>("/api/snapshot_diff", { method: "POST", body: JSON.stringify({ previous: toSnapshotComparisonPayload(previous, previous.saved_at), current: toSnapshotComparisonPayload(company) }) });
      setDiff(result);
      setSnapshotState(result.comparable ? "saved" : "incompatible");
    } catch { setSnapshotState("error"); setSnapshotMessage("Storage or comparison is unavailable in this browser."); }
  };

  const clearSnapshots = () => {
    if (!company) return;
    try { writeSnapshots(company.ticker, []); setSnapshotCount(0); setDiff(null); setSnapshotState("empty"); setSnapshotMessage("Saved research removed from this browser."); }
    catch { setSnapshotState("error"); setSnapshotMessage("Saved research could not be removed."); }
  };

  const setScenarioField = (field: keyof Assumptions, value: string) => setDraft((current) => ({ ...current, [field]: value }));
  const runScenario = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!company) return;
    const converted = Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, value.trim() === "" ? null : Number(value) / 100])) as Assumptions;
    if (Object.values(converted).some((value) => !finite(value))) { setScenarioError("Enter all four assumptions as percentages to calculate this scenario."); return; }
    const id = ++scenarioRequestId.current;
    setScenarioLoading(true); setScenarioError("");
    try {
      const result = await api<Analysis>("/api/scenario", { method: "POST", body: JSON.stringify({ ticker: company.ticker, assumptions: converted }) });
      if (id === scenarioRequestId.current) {
        setScenario(result.company.scenario);
        if (!result.company.scenario) setScenarioError("The available inputs do not support a scenario value.");
      }
    } catch (reason) { if (id === scenarioRequestId.current) setScenarioError(reason instanceof Error ? reason.message : "Scenario calculation is temporarily unavailable."); }
    finally { if (id === scenarioRequestId.current) setScenarioLoading(false); }
  };

  const brandHeader = <header className="masthead">
    <Link href="/" className="brand" aria-label="TSRP home" onClick={(event) => { event.preventDefault(); setAnalysis(null); setError(""); router.push("/"); }}>
      <span className="brand-copy"><strong>TSRP</strong><small>The Saleh Research Project</small></span>
    </Link>
  </header>;
  const searchForm = <CompanySearch key={tickerParam || "home"} initialTicker={tickerParam} loading={loading} loadingTicker={loadingTicker} onAnalyze={beginAnalysis} />;

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

      {loading && !company && <section className="loading-state" aria-live="polite" aria-busy="true"><span className="spinner spinner-large" aria-hidden="true" /><div><strong>Researching {loadingTicker}</strong><p>Checking company identity, market data, and available financial statements.</p></div></section>}

      {error && tickerParam && !company && <section className="error-state" role="alert"><span className="error-symbol" aria-hidden="true">!</span><div><h1>Research could not be completed</h1><p>{error}</p><button className="button button-secondary" type="button" onClick={() => void loadAnalysis(tickerParam)}>Try again</button></div></section>}
      {tickerParam && !company && <ResearchNavigation ticker={tickerParam} section={section} />}

      {company && currentAnalysis && <>
        <section className="company-header" aria-label={`${company.company} company information`}>
          <div className="company-identification"><h1>{company.company}</h1><span className="ticker-pill">{company.ticker}</span></div>
          <div className="company-meta"><span>{company.exchange}</span><span>{company.sector}</span><span>Reporting currency {company.reporting_currency}</span></div>
          <div className="company-header-tools"><span className={`freshness-badge ${company.is_stale ? "is-warning" : ""}`}><span className="status-dot" />{company.is_stale ? "Market data may be stale" : "Data retrieved"} · {dateLabel(company.retrieved_at, true)} UTC</span><button type="button" className="text-action" onClick={() => void loadAnalysis(company.ticker)} aria-label={`Refresh ${company.ticker} research data`}>Refresh data</button></div>
        </section>

        {(company.is_stale || company.quality_flags.length > 0) && <section className="data-notes" aria-label="Data quality and limitations">
          {company.is_stale && <p className="warning-note">The latest available market observation is older than 48 hours. Confirm the quote before relying on it.</p>}
          {company.quality_flags.map((flag, index) => <p className={`quality-note severity-${flag.severity}`} key={`${flag.message}-${index}`}>{flag.message}</p>)}
        </section>}

        <ResearchNavigation ticker={company.ticker} section={section} />

        <section className="research-section" id="research-detail" aria-labelledby="section-title">
          <div className="section-title-row"><div><h1 id="section-title" tabIndex={-1}>{section}</h1></div><div className="section-actions"><button type="button" className="button button-quiet" onClick={() => void openShare()}>{copyState || "Copy link"}</button><button type="button" className="button button-quiet" onClick={() => exportResearch("csv")}>Export CSV</button><button type="button" className="button button-quiet" onClick={() => exportResearch("json")}>Export JSON</button></div></div>
          <div className="research-view-transition" key={section}>
            {section === "TSRP Score" && <ScoreView analysis={currentAnalysis} onOpen={navigateSection} snapshotCount={snapshotCount} />}
            {section === "Expectations vs Reality" && <ExpectationsView company={company} />}
            {section === "Valuation" && <ValuationView company={company} draft={draft} setDraftField={setScenarioField} onSubmit={runScenario} result={scenario} loading={scenarioLoading} error={scenarioError} />}
            {section === "Catalysts & Risks" && <CatalystsView company={company} />}
            {section === "What Changed?" && <ChangesView company={company} count={snapshotCount} state={snapshotState} diff={diff} message={snapshotMessage} onSave={saveSnapshot} onClear={clearSnapshots} />}
          </div>
          <Methodology company={company} />
        </section>
      </>}

      <footer className="site-footer"><span>TSRP · Saleh Research Project</span><span>Independent research, not investment advice.</span></footer>
    </main>
  );
}

function ScoreView({ analysis, onOpen, snapshotCount }: { analysis: Analysis; onOpen: (section: SectionName) => void; snapshotCount: number }) {
  const c = analysis.company;
  const drivers = c.score_trace?.components ?? [];
  return <>
    <section className="score-hero" aria-label="TSRP research score">
      <div className="score-main"><span className="eyebrow">Research signal · 0 to 100</span><div className="score-number">{finite(c.score) ? c.score.toFixed(1) : "N/A"}</div><div className="score-rating">{c.score_label}</div><p>{finite(c.score) ? "A heuristic summary of business quality, financial strength, and growth evidence against market expectations. It is not a forecast or probability." : "The available evidence does not support a defensible TSRP Score."}</p></div>
      <div className="score-drivers"><div className="panel-heading"><h2>What drives this score</h2><span className="confidence-label">Data confidence: {c.confidence}</span></div>{drivers.length ? drivers.map((driver) => <div className="driver-row" key={driver.key}><div className="driver-label"><span>{driver.label}</span><small>{Math.round(driver.weight * 100)}% base weight{c.score !== null && driver.value !== null ? ` · ${Math.round(driver.applied_weight * 100)}% applied` : " · not included"}</small></div><div className="driver-track" aria-hidden="true"><span style={{ width: `${driver.value === null ? 0 : Math.max(0, Math.min(100, driver.value))}%` }} /></div><strong className="tabular">{driver.value === null ? "N/A" : driver.value.toFixed(0)}</strong></div>) : <p className="empty-inline">Score drivers are unavailable because core inputs are missing.</p>}
        <p className="score-explain">{c.score_trace ? `Evidence coverage: ${Math.round(c.score_trace.evidence_coverage * 100)}%. Missing evidence is excluded, not assigned a substitute score. Remaining weights are rebalanced; scores with different coverage are not directly comparable.` : "Score evidence is unavailable."}</p>
      </div>
    </section>

    <section className="metric-strip" aria-label="Key company metrics">
      <Metric label="Share price" value={money(c.price, c.trading_currency)} source={sourceLine(c, "share_price")} />
      <Metric label="Market capitalization" value={money(c.market_cap_reporting, c.reporting_currency, true)} source={sourceLine(c, "market_cap_reporting")} />
      <Metric label="Enterprise value" value={money(c.enterprise_value, c.reporting_currency, true)} source={sourceLine(c, "enterprise_value")} />
      <Metric label="Revenue" value={money(c.revenue, c.reporting_currency, true)} source={sourceLine(c, "revenue")} />
    </section>

    <div className="main-research-grid">
      <PriceChart points={analysis.price_history} currency={c.trading_currency} ticker={c.ticker} />
      <section className="summary-panel">
        <div className="panel-heading"><div><h2>Valuation at a glance</h2><p>Model output, not a price target</p></div></div>
        <div className="summary-stat"><span>Growth required by current price</span><strong className="tabular">{pct(c.required_growth)}</strong><small>{solverDescription(c)}</small></div>
        <div className="summary-stat"><span>Historical revenue growth</span><strong className="tabular">{pct(c.historical_growth)}</strong><small>{c.historical_growth_years ? `${c.historical_growth_years.toFixed(1)} years · ${c.revenue_growth_source || "Source unavailable"}` : "Comparable annual history unavailable"}</small></div>
        <div className="summary-stat"><span>Next-year analyst estimate</span><strong className="tabular">{pct(c.consensus_growth)}</strong><small>{c.analyst_estimate_period || "Yahoo Finance estimate unavailable"}</small></div>
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
        <EvidenceRow label="Next-year analyst estimate" value={pct(c.consensus_growth)} note={c.consensus_growth === null ? "Yahoo Finance does not provide a usable next-year growth estimate." : "Provider-supplied analyst revenue estimate; not company guidance or reported performance."} source={sourceLine(c, "analyst_growth")} />
        <EvidenceRow label="Growth required by current price" value={pct(c.required_growth)} note={`${solverDescription(c)} This is a 10-year reverse-DCF model output, not a one-year consensus estimate.`} source={`TSRP calculation · ${c.reporting_currency} assumptions · ${c.methodology_version}`} />
      </div>
      <div className="comparison-note"><strong>Periods are not interchangeable.</strong><span>Historical CAGR, next-year analyst estimates, and a 10-year market-implied growth rate use different horizons. TSRP does not label the company as beating or missing expectations from these figures alone.</span></div>
    </section>
    <section className="content-panel"><div className="panel-heading"><div><h2>Reported financial evidence</h2><p>Latest available annual observations. Values retain their reporting currency and period.</p></div></div>
      <div className="evidence-grid two-columns">
        <EvidenceRow label="Revenue" value={money(c.revenue, c.reporting_currency, true)} note={c.revenue_period ? `Latest annual period ending ${dateLabel(c.revenue_period)}.` : "Reporting period is unavailable; treat this value cautiously."} source={sourceLine(c, "revenue")} />
        <EvidenceRow label="Free cash flow" value={money(c.free_cash_flow, c.reporting_currency, true)} note={c.fcf_margin === null ? "Not calculated because a period-aligned reported input is unavailable." : `Cash flow margin ${pct(c.fcf_margin)}. Operating cash flow less capital spending for the same period.`} source={sourceLine(c, "free_cash_flow")} />
      </div>
    </section>
  </div>;
}

function solverDescription(c: Company): string {
  if (c.solver_status === "exact" && finite(c.required_growth)) return "Exact model solution within tested bounds.";
  if (c.solver_status === "below_range") return `Below the tested range (${pct(c.solver_low)} to ${pct(c.solver_high)}); no precise rate is shown.`;
  if (c.solver_status === "above_range") return `Above the tested range (${pct(c.solver_low)} to ${pct(c.solver_high)}); no precise rate is shown.`;
  return c.model_fcf_refused ? "Unavailable: current cash-flow evidence is non-positive or insufficient." : "Unavailable: required market value, revenue, or cash-flow inputs are missing.";
}

function expectationsSummary(c: Company) {
  if (finite(c.historical_growth) && finite(c.consensus_growth)) return `Historical revenue growth ${pct(c.historical_growth)}; next-year analyst estimate ${pct(c.consensus_growth)}. Different periods.`;
  if (finite(c.required_growth)) return `The current price implies ${pct(c.required_growth)} annualized growth in the 10-year model.`;
  return "Reported history or comparable expectations are incomplete.";
}

function ValuationView({ company: c, draft, setDraftField, onSubmit, result, loading, error }: {
  company: Company; draft: DraftAssumptions; setDraftField: (field: keyof Assumptions, value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void; result: ScenarioOutput | null; loading: boolean; error: string;
}) {
  const baseCurrency = c.reporting_currency;
  const fieldLabels: [keyof Assumptions, string, string][] = [
    ["starting_growth", "Starting revenue growth", "User assumption for the first forecast years"],
    ["fcf_margin", "Free-cash-flow margin", "Free cash flow divided by revenue"],
    ["discount_rate", "Discount rate", "Return required to discount future cash flows"],
    ["terminal_growth", "Long-run growth", "Assumed growth beyond the forecast period"],
  ];
  return <div className="view-stack">
    <section className="content-panel"><div className="panel-heading"><div><h2>What must the company achieve to support its current value?</h2><p>Reverse DCF translates enterprise value into an annualized revenue-growth requirement using explicit cash-flow and discount assumptions.</p></div></div>
      <div className="valuation-overview"><div><span className="eyebrow">Enterprise value</span><strong className="valuation-value tabular">{money(c.enterprise_value, baseCurrency, true)}</strong><small>{sourceLine(c, "enterprise_value")}</small></div><div><span className="eyebrow">Growth required by current price</span><strong className="valuation-value tabular">{c.solver_status === "exact" ? pct(c.required_growth) : c.solver_status === "unavailable" ? "N/A" : "Outside range"}</strong><small>{solverDescription(c)}</small></div><div><span className="eyebrow">FCF margin used</span><strong className="valuation-value tabular">{pct(c.model_fcf_margin)}</strong><small>{c.model_fcf_note || sourceLine(c, "free_cash_flow")}</small></div></div>
      <div className="comparison-note"><strong>How to read this:</strong><span>The required-growth result is a model output, not a fair value, price target, or forecast. It can be unavailable when financial inputs are missing, cash flow is negative, currencies differ, or the result falls outside the engine’s supported range.</span></div>
      <div className="rate-grid"><div><span>Discount rate</span><strong className="tabular">{pct(c.discount_rate)}</strong></div><div><span>Long-run growth</span><strong className="tabular">{pct(c.terminal_growth)}</strong></div><div><span>EV / annual revenue</span><strong className="tabular">{finite(c.ev_sales) ? `${number(c.ev_sales)}×` : "N/A"}</strong></div><div><span>Debt / assets</span><strong className="tabular">{pct(c.debt_to_assets)}</strong></div></div>
    </section>
    <section className="content-panel"><div className="panel-heading"><div><h2>Test your own assumptions</h2><p>Scenario values are deterministic model outputs, not predictions. The baseline TSRP analysis remains unchanged.</p></div></div>
      <form className="assumption-form" onSubmit={onSubmit}>
        {fieldLabels.map(([field, label, help]) => <label className="assumption-field" key={field}><span>{label}</span><div className="percent-input"><input type="number" min={field === "starting_growth" ? -10 : 0} max={field === "starting_growth" ? 40 : field === "fcf_margin" ? 50 : field === "discount_rate" ? 20 : 6} step="0.1" value={draft[field]} onChange={(event) => setDraftField(field, event.target.value)} aria-describedby={`${field}-help`} /><b>%</b></div><small id={`${field}-help`}>{help}</small></label>)}
        <div className="assumption-actions"><button className="button button-primary" type="submit" disabled={loading}>{loading && <span className="spinner" aria-hidden="true" />}{loading ? "Calculating" : "Calculate scenario"}</button><span>Forecast horizon: 10 years</span></div>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      {result && <div className="scenario-result" role="status"><div><span className="eyebrow">Scenario enterprise value</span><strong className="tabular">{money(result.enterprise_value, baseCurrency, true)}</strong><small>Calculated from the assumptions above · Not a target price</small></div><div><span className="eyebrow">Scenario equity value</span><strong className="tabular">{money(result.equity_value, baseCurrency, true)}</strong><small>{result.equity_value === null ? "Unavailable because cash or debt is missing." : "Enterprise value less reported debt plus reported cash."}</small></div></div>}
    </section>
  </div>;
}

type EvidenceItem = { title: string; description: string; checkpoint: string; source: string; tone: "positive" | "risk" | "neutral" };

function deriveCatalystEvidence(c: Company) {
  const positive: EvidenceItem[] = [];
  const risks: EvidenceItem[] = [];
  if (finite(c.historical_growth) && c.historical_growth > 0) positive.push({ title: "Positive reported revenue trend", description: `Revenue grew at ${pct(c.historical_growth)} annualized over approximately ${c.historical_growth_years.toFixed(1)} years. This is historical evidence, not a forecast.`, checkpoint: "Confirm against the next comparable annual revenue filing; a sustained decline would weaken this signal.", source: sourceLine(c, "historical_growth"), tone: "positive" });
  if (finite(c.fcf_margin) && c.fcf_margin > 0) positive.push({ title: "Positive cash conversion", description: `Period-aligned free cash flow was ${pct(c.fcf_margin)} of revenue.`, checkpoint: "Recheck operating cash flow and capital spending in the next same-period filing; a negative margin would weaken this signal.", source: sourceLine(c, "free_cash_flow"), tone: "positive" });
  if (finite(c.consensus_growth) && c.consensus_growth > 0) positive.push({ title: "Analyst revenue growth estimate", description: `Yahoo Finance reports a next-year revenue growth estimate of ${pct(c.consensus_growth)}. This is an estimate, not company guidance.`, checkpoint: "Compare with reported revenue for the matching fiscal period when available.", source: sourceLine(c, "analyst_growth"), tone: "neutral" });
  if (finite(c.historical_growth) && c.historical_growth < 0) risks.push({ title: "Historical revenue contraction", description: `Revenue declined at ${pct(c.historical_growth)} annualized over approximately ${c.historical_growth_years.toFixed(1)} years.`, checkpoint: "Check the next comparable annual filing; a return to positive growth would reduce this concern.", source: sourceLine(c, "historical_growth"), tone: "risk" });
  if (finite(c.fcf_margin) && c.fcf_margin <= 0) risks.push({ title: "Negative or zero free cash flow", description: `Period-aligned free-cash-flow margin is ${pct(c.fcf_margin)}. TSRP does not reverse-solve growth from non-positive cash flow.`, checkpoint: "Review operating cash flow and capital spending in the next same-period filing.", source: sourceLine(c, "free_cash_flow"), tone: "risk" });
  if (c.cash === null || c.debt === null) risks.push({ title: "Incomplete balance-sheet evidence", description: "Cash or debt could not be verified from the available provider data; leverage conclusions are limited.", checkpoint: "Confirm cash and debt from the next company filing.", source: "SEC EDGAR or Yahoo Finance · see source status", tone: "risk" });
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
  score: "TSRP Score", business_quality: "Business quality", financial_strength: "Financial strength",
  historical_growth: "Historical revenue growth", consensus_growth: "Next-year analyst estimate", required_growth: "Growth required by price",
  revenue: "Revenue", revenue_period: "Revenue period", free_cash_flow: "Free cash flow", fcf_period: "Free-cash-flow period", enterprise_value: "Enterprise value",
  model_fcf_margin: "Cash-flow margin used", discount_rate: "Discount rate", terminal_growth: "Long-run growth", solver_status: "Valuation status", user_assumptions: "User assumptions",
};

function ChangesView({ company, count, state, diff, message, onSave, onClear }: { company: Company; count: number; state: string; diff: Diff | null; message: string; onSave: () => void; onClear: () => void }) {
  const renderValue = (field: string, value: unknown) => {
    if (typeof value === "number") {
      if (field.includes("growth") || field.includes("margin") || field.includes("rate") || field === "terminal_growth") return pct(value);
      if (["score", "business_quality", "financial_strength", "market_expectations"].includes(field)) return value.toFixed(1);
      if (["revenue", "free_cash_flow", "enterprise_value"].includes(field)) return money(value, company.reporting_currency, true);
      return number(value);
    }
    if (value && typeof value === "object") return "Assumptions updated";
    return value === null || value === undefined ? "N/A" : String(value);
  };
  return <div className="view-stack">
    <section className="content-panel"><div className="panel-heading"><div><h2>What is different from the last saved analysis?</h2><p>Snapshots are stored only in this browser. Comparisons require the same ticker, methodology, analysis format, and reporting currency.</p></div></div>
      <div className="snapshot-toolbar"><div><strong>{count} saved {count === 1 ? "snapshot" : "snapshots"}</strong><span>Up to {MAX_SNAPSHOTS_PER_TICKER} are retained on this browser for {company.ticker}.</span></div><div className="button-pair"><button className="button button-primary" type="button" onClick={onSave}>Save current analysis</button><button className="button button-quiet" type="button" onClick={onClear} disabled={count === 0}>Remove saved research</button></div></div>
      {message && <p className="form-success" role="status">{message}</p>}
      {state === "loading" && <p className="empty-inline" role="status">Comparing compatible saved research…</p>}
      {state === "error" && <p className="form-error" role="alert">Saved research could not be read or compared. Your current analysis is unchanged.</p>}
      {state === "incompatible" && <p className="comparison-note"><strong>These analyses cannot be compared.</strong><span>{diff?.reason === "methodology_mismatch" ? "The saved research uses a different TSRP methodology." : diff?.reason === "schema_mismatch" ? "The saved research uses a different analysis format." : diff?.reason === "currency_mismatch" ? "The analyses use different reporting currencies." : diff?.reason === "snapshot_schema_invalid" ? "A saved analysis is incomplete or malformed." : "The saved research is missing metadata required for a safe comparison."} Save a new snapshot to begin a comparable series. Older snapshots remain in this browser until you remove them.</span></p>}
      {state === "empty" && <div className="empty-state"><strong>No comparable saved analysis yet</strong><p>Save this result. The next saved analysis for {company.ticker} can be compared with it.</p></div>}
      {state === "saved" && diff?.comparable && <div className="change-table-wrap"><p className="snapshot-date">Compared with the latest saved analysis · {company.methodology_version}</p>{diff.excluded_fields?.length ? <p className="comparison-note">Some amounts or score changes are omitted because their reporting periods, source definitions, or score evidence changed.</p> : null}{diff.changes.length ? <div className="change-table" role="table" aria-label="Research changes"><div className="change-table-head" role="row"><span role="columnheader">Measure</span><span role="columnheader">Previous</span><span role="columnheader">Current</span><span role="columnheader">Change</span></div>{diff.changes.map((change, index) => <div className="change-table-row" role="row" key={`${change.field}-${index}`}><strong role="cell">{CHANGE_LABELS[change.field] || change.field}</strong><span role="cell" className="tabular">{renderValue(change.field, change.before)}</span><span role="cell" className="tabular">{renderValue(change.field, change.after)}</span><span role="cell" className={`tabular ${change.delta === null ? "" : change.delta > 0 ? "positive-text" : change.delta < 0 ? "negative-text" : ""}`}>{change.delta === null ? "Updated" : change.delta > 0 ? `+${renderValue(change.field, change.delta)}` : renderValue(change.field, change.delta)}</span></div>)}</div> : <div className="empty-state"><strong>No measured changes detected</strong><p>The comparable fields match the latest saved analysis.</p></div>}</div>}
    </section>
    <p className="snapshot-privacy">Browser storage is not synced, backed up, or shared across devices. Clearing this browser’s site data may remove snapshots.</p>
  </div>;
}

function Methodology({ company: c }: { company: Company }) {
  const sourceEntries = [
    ["Share price", sourceLine(c, "share_price")],
    ["Market capitalization in reporting currency", sourceLine(c, "market_cap_reporting")],
    ["Enterprise value", sourceLine(c, "enterprise_value")],
    ["EV / annual revenue", sourceLine(c, "ev_sales")],
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
    <div className="methodology-block"><h2>Sources and as-of dates</h2><p>Analysis retrieved {dateLabel(c.retrieved_at, true)} UTC. Latest market observation: {dateLabel(c.market_at, true)} UTC. The market provider may delay, revise, or omit data.</p><dl className="source-list">{sourceEntries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p>SEC EDGAR status: {c.sec_status.label}. SEC filings are not inferred from Yahoo values. SEC requests require a real monitored contact identity in <code>SEC_USER_AGENT</code>.</p></div>
    <div className="methodology-block"><h2>How the TSRP Score works</h2><p>Version {c.methodology_version} combines business quality (35%), financial strength (30%), historical growth fit against growth required by price (20%), and analyst-consensus fit against growth required by price (15%). Subscores use static sector benchmarks. Missing factors are excluded, not assigned a neutral score; observed factors are reweighted. A final score requires at least 70% of the total evidence weight and at least 70% of the original component weight to be supported. The shown evidence coverage is separate from data confidence. Scores with different coverage should not be compared directly.</p><p>The score is a heuristic research signal on a 0–100 scale. It is not calibrated as a probability, investment rating, forecast, or expected return. Market-multiple context is not a component of the final score.</p></div>
    <div className="methodology-block"><h2>Valuation and accounting</h2><p>Reverse DCF uses current enterprise value (market capitalization plus reported debt less cash, when currency, date, and quote freshness align), annual reported revenue, a positive free-cash-flow margin, discount rate, and terminal growth. It projects ten years: growth holds for five years then fades to terminal growth; the cash-flow margin moves from the latest reported margin toward the modeled margin. Exact, outside-range, and unavailable states are distinct. Enterprise value excludes claims TSRP does not separately capture, such as minority interests or preferred equity, so it is an approximation.</p><p>Free cash flow is annual operating cash flow less capital spending only when periods align; provider-reported free cash flow is a labeled fallback. Historical growth uses actual dated annual reporting periods and does not bridge a missing year. Yahoo Finance daily FX history is accepted only when fresh and date-comparable; the exact pair, rate, and timestamp are included in source metadata. No rate means no conversion.</p><p>Discount-rate, terminal-growth, and sector values are static TSRP assumptions copied from the prior baseline. Their current market calibration and source basis have not been independently validated; they are not live yields or forecasts.</p></div>
    <div className="methodology-block"><h2>Privacy and service limits</h2><p>Company tickers and name-search text are sent server-side to Yahoo Finance. The top ticker requests Yahoo Finance daily gainers, losers, and most-active US stocks, then displays ticker symbols and daily percentage moves. These requests do not include personal information or company-search text. When configured, TSRP sends a monitored contact User-Agent and company identifier requests to SEC EDGAR. Assumptions are sent to TSRP’s own calculation endpoint. For What Changed?, only the fields needed to compare the current analysis with the selected saved snapshot are sent to TSRP’s comparison endpoint for processing; snapshots are retained in this browser, not a TSRP database, and can be removed from What Changed?. No account, analytics, or tracking is added.</p><p>Yahoo/yfinance screening access and public display or redistribution rights have not been cleared for release. {c.freshness}</p></div>
    <div className="methodology-block disclaimer-block"><h2>Financial information disclaimer</h2><p>{DISCLAIMER}</p></div>
  </div></details>;
}
