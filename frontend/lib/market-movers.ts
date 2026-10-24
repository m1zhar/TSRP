import type { MarketMover, MarketMovers, TopStocksResponse } from "./types.ts";
import { MARKET_DATA_FUTURE_TOLERANCE_MS, MARKET_DATA_MAX_AGE_MS } from "./freshness.ts";

const MAX_RESPONSE_AGE_MS = 15 * 60 * 1000;
const MAX_DATE_ONLY_MARKET_AGE_DAYS = 2;
const TICKER_PATTERN = /^[A-Z0-9][A-Z0-9.-]{0,14}$/;

type MarketMoverUpdate =
  | { kind: "movers"; value: MarketMovers; items: MarketMover[] }
  | { kind: "stocks"; value: TopStocksResponse; items: MarketMover[] }
  | { kind: "popular"; value: TopStocksResponse; items: MarketMover[] };

export function mergeMarketMovers(previous: MarketMovers | null, update: MarketMoverUpdate): MarketMovers {
  if (update.kind === "movers") {
    return {
      ...update.value,
      items: update.items,
      top_stocks: previous?.top_stocks ?? [],
      top_stocks_rank: previous?.top_stocks_rank ?? update.value.top_stocks_rank,
      top_stocks_label: previous?.top_stocks_label ?? update.value.top_stocks_label,
      top_stocks_retrieved_at: previous?.top_stocks_retrieved_at,
      top_stocks_coverage_note: previous?.top_stocks_coverage_note,
      popular_stocks: previous?.popular_stocks ?? [],
      popular_stocks_rank: previous?.popular_stocks_rank,
      popular_stocks_label: previous?.popular_stocks_label,
      popular_stocks_retrieved_at: previous?.popular_stocks_retrieved_at,
      popular_stocks_coverage_note: previous?.popular_stocks_coverage_note,
    };
  }

  if (update.kind === "stocks") return {
    ...(previous ?? {
      source: update.value.source,
      period: "Daily market movement",
      top_stocks_rank: update.value.rank,
      top_stocks_label: update.value.label,
      retrieved_at: update.value.retrieved_at,
      items: [],
      top_stocks: [],
    }),
    top_stocks_rank: update.value.rank,
    top_stocks_label: update.value.label,
    top_stocks_retrieved_at: update.value.retrieved_at,
    top_stocks: update.items,
    top_stocks_coverage_note: update.value.coverage_note,
  };

  return {
    ...(previous ?? {
      source: update.value.source,
      period: "Daily market movement",
      top_stocks_rank: "Most active US equities, ordered by trading volume",
      top_stocks_label: "Most active",
      retrieved_at: update.value.retrieved_at,
      items: [],
      top_stocks: [],
    }),
    popular_stocks_rank: update.value.rank,
    popular_stocks_label: update.value.label,
    popular_stocks_retrieved_at: update.value.retrieved_at,
    popular_stocks: update.items,
    popular_stocks_coverage_note: update.value.coverage_note,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function filterFreshMarketMovers(
  values: unknown,
  retrievedAtValue: unknown,
  now = Date.now(),
): MarketMover[] {
  if (!Array.isArray(values) || typeof retrievedAtValue !== "string" || !Number.isFinite(now)) return [];
  const retrievedAt = Date.parse(retrievedAtValue);
  if (!Number.isFinite(retrievedAt)
    || now - retrievedAt > MAX_RESPONSE_AGE_MS
    || retrievedAt - now > MARKET_DATA_FUTURE_TOLERANCE_MS) return [];

  const seen = new Set<string>();
  return values.filter((value): value is MarketMover => {
    if (!isRecord(value)
      || typeof value.ticker !== "string"
      || !TICKER_PATTERN.test(value.ticker)
      || value.currency !== "USD"
      || typeof value.change_percent !== "number"
      || !Number.isFinite(value.change_percent)
      || typeof value.absolute_change !== "number"
      || !Number.isFinite(value.absolute_change)
      || typeof value.market_at !== "string") return false;
    if (seen.has(value.ticker)
      || (value.change_percent < 0) !== (value.absolute_change < 0)
      || (value.change_percent === 0) !== (value.absolute_change === 0)) return false;
    const marketAt = Date.parse(value.market_at);
    if (!Number.isFinite(marketAt)) return false;
    if (/^\d{4}-\d{2}-\d{2}$/u.test(value.market_at)) {
      if (new Date(marketAt).toISOString().slice(0, 10) !== value.market_at) return false;
      const retrievedDay = Date.parse(`${new Date(retrievedAt).toISOString().slice(0, 10)}T00:00:00.000Z`);
      const marketDay = Date.parse(`${value.market_at}T00:00:00.000Z`);
      const ageDays = (retrievedDay - marketDay) / (24 * 60 * 60 * 1000);
      if (!Number.isInteger(ageDays) || ageDays < 0 || ageDays > MAX_DATE_ONLY_MARKET_AGE_DAYS) return false;
    } else if (now - marketAt > MARKET_DATA_MAX_AGE_MS
      || marketAt - now > MARKET_DATA_FUTURE_TOLERANCE_MS) {
      return false;
    }
    seen.add(value.ticker);
    return true;
  }).slice(0, 7);
}

/** Remove a previously displayed list before a refresh if its provider response or quotes have expired. */
export function pruneExpiredMarketMovers(
  market: MarketMovers | null,
  kind: "movers" | "stocks" | "popular",
  now = Date.now(),
): MarketMovers | null {
  if (!market) return null;

  if (kind === "movers") {
    const items = filterFreshMarketMovers(market.items, market.retrieved_at, now);
    return items.length === market.items.length
      ? market
      : { ...market, items, coverage_note: undefined };
  }

  if (kind === "stocks") {
    const topStocks = filterFreshMarketMovers(market.top_stocks, market.top_stocks_retrieved_at, now);
    return topStocks.length === market.top_stocks.length
      ? market
      : { ...market, top_stocks: topStocks, top_stocks_coverage_note: undefined };
  }

  const popularStocks = filterFreshMarketMovers(market.popular_stocks, market.popular_stocks_retrieved_at, now);
  return popularStocks.length === (market.popular_stocks ?? []).length
    ? market
    : { ...market, popular_stocks: popularStocks, popular_stocks_coverage_note: undefined };
}
