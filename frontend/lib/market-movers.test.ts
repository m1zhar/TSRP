import assert from "node:assert/strict";
import test from "node:test";
import { filterFreshMarketMovers, mergeMarketMovers, pruneExpiredMarketMovers } from "./market-movers.ts";

const now = Date.parse("2026-10-10T12:00:00.000Z");
const retrievedAt = new Date(now).toISOString();

function item(ticker: string, marketAt = retrievedAt, change = 1) {
  return { ticker, change_percent: change, absolute_change: 1, currency: "USD", market_at: marketAt };
}

test("accepts fresh data and preserves the provider ranking order", () => {
  const values = [item("NVDA"), item("AAPL"), item("MSFT")];
  assert.deepEqual(filterFreshMarketMovers(values, retrievedAt, now).map(({ ticker }) => ticker), ["NVDA", "AAPL", "MSFT"]);
});

test("rejects stale or implausibly future response timestamps", () => {
  assert.deepEqual(filterFreshMarketMovers([item("AAPL")], new Date(now - 16 * 60 * 1000).toISOString(), now), []);
  assert.deepEqual(filterFreshMarketMovers([item("AAPL")], new Date(now + 6 * 60 * 1000).toISOString(), now), []);
});

test("filters malformed, non-finite, stale, and future mover entries", () => {
  const values = [
    item("AAPL"),
    item("lowercase"),
    item("NAN", retrievedAt, Number.NaN),
    item("OLD", new Date(now - 73 * 60 * 60 * 1000).toISOString()),
    item("FUTURE", new Date(now + 6 * 60 * 1000).toISOString()),
    { ...item("BADABS"), absolute_change: Number.POSITIVE_INFINITY },
  ];
  assert.deepEqual(filterFreshMarketMovers(values, retrievedAt, now).map(({ ticker }) => ticker), ["AAPL"]);
});

test("rejects non-USD or internally inconsistent changes and keeps only the first ticker occurrence", () => {
  const values = [
    item("AAPL"),
    item("USDOK", retrievedAt, 2),
    { ...item("FOREIGN"), currency: "CAD" },
    { ...item("MISMATCH"), change_percent: -1 },
    { ...item("DUPLICATE"), change_percent: 2, absolute_change: 2 },
    { ...item("DUPLICATE"), change_percent: 3, absolute_change: 3 },
    { ...item("FLAT"), change_percent: 0, absolute_change: 0 },
  ];
  assert.deepEqual(
    filterFreshMarketMovers(values, retrievedAt, now).map(({ ticker }) => ticker),
    ["AAPL", "USDOK", "DUPLICATE", "FLAT"],
  );
});

test("does not display exact-timestamp mover quotes that the company freshness policy calls stale", () => {
  const boundaryAt = new Date(now - 48 * 60 * 60 * 1000).toISOString();
  const staleAt = new Date(now - 49 * 60 * 60 * 1000).toISOString();
  assert.equal(filterFreshMarketMovers([item("BOUNDARY", boundaryAt)], retrievedAt, now).length, 1);
  assert.deepEqual(filterFreshMarketMovers([item("STALE", staleAt)], retrievedAt, now), []);
});

test("date-only history uses its retrieval date and remains stable across UTC midnight", () => {
  const retrievedAtNearMidnight = "2026-10-10T23:59:59.000Z";
  const justAfterMidnight = Date.parse("2026-10-11T00:00:01.000Z");
  const values = [
    item("AAPL", "2026-10-08"),
    item("STALE", "2026-10-07"),
    item("FUTURE", "2026-10-11"),
    item("INVALID", "2026-10-32"),
  ];
  assert.deepEqual(filterFreshMarketMovers(values, retrievedAtNearMidnight, justAfterMidnight).map(({ ticker }) => ticker), ["AAPL"]);
  assert.deepEqual(filterFreshMarketMovers([item("AAPL", "2026-10-08")], retrievedAt, now + 16 * 60 * 1000), []);
});

test("limits the row without changing the provider order", () => {
  const values = Array.from({ length: 9 }, (_, index) => item(`STK${index}`));
  assert.deepEqual(filterFreshMarketMovers(values, retrievedAt, now).map(({ ticker }) => ticker), ["STK0", "STK1", "STK2", "STK3", "STK4", "STK5", "STK6"]);
});

test("retains fresh rows and coverage warnings for failed-refresh recovery", () => {
  const market = {
    source: "Yahoo Finance", period: "Daily market movement", top_stocks_rank: "volume",
    top_stocks_label: "Top stocks", retrieved_at: retrievedAt, top_stocks_retrieved_at: retrievedAt,
    items: [item("AAPL")], top_stocks: [item("NVDA")],
    coverage_note: "movers may be incomplete", top_stocks_coverage_note: "popular-stock fallback",
  };
  assert.equal(pruneExpiredMarketMovers(market, "movers", now), market);
  assert.equal(pruneExpiredMarketMovers(market, "stocks", now), market);
  assert.equal(pruneExpiredMarketMovers(market, "movers", now)?.coverage_note, "movers may be incomplete");
  assert.equal(pruneExpiredMarketMovers(market, "stocks", now)?.top_stocks_coverage_note, "popular-stock fallback");
});

test("prunes expired or malformed movers before a delayed refresh can render them", () => {
  const market = {
    source: "Yahoo Finance", period: "Daily market movement", top_stocks_rank: "volume",
    top_stocks_label: "Top stocks", retrieved_at: new Date(now - 16 * 60 * 1000).toISOString(),
    items: [item("STALE")], top_stocks: [item("NVDA")], coverage_note: "old mover warning",
  };
  const pruned = pruneExpiredMarketMovers(market, "movers", now);
  assert.deepEqual(pruned?.items, []);
  assert.equal(pruned?.coverage_note, undefined);
  assert.deepEqual(pruned?.top_stocks.map(({ ticker }) => ticker), ["NVDA"]);
});

test("prunes stale top stocks without disturbing fresh movers", () => {
  const market = {
    source: "Yahoo Finance", period: "Daily market movement", top_stocks_rank: "volume",
    top_stocks_label: "Top stocks", retrieved_at: retrievedAt,
    top_stocks_retrieved_at: new Date(now - 16 * 60 * 1000).toISOString(),
    items: [item("AAPL")], top_stocks: [item("STALE")], top_stocks_coverage_note: "old stock warning",
  };
  const pruned = pruneExpiredMarketMovers(market, "stocks", now);
  assert.deepEqual(pruned?.top_stocks, []);
  assert.equal(pruned?.top_stocks_coverage_note, undefined);
  assert.deepEqual(pruned?.items.map(({ ticker }) => ticker), ["AAPL"]);
});

test("a later movers response preserves popular-stock fallback provenance", () => {
  const fallback = {
    source: "Yahoo Finance",
    rank: "Popular large-cap stocks; not ranked by daily trading volume",
    label: "Popular stocks",
    retrieved_at: retrievedAt,
    items: [item("AAPL")],
    coverage_note: "Yahoo Finance most-active results included incomplete quotes; this list may be incomplete.",
  };
  const movers = {
    source: "Yahoo Finance",
    period: "Daily market movement",
    top_stocks_rank: "Most active US equities, ordered by trading volume",
    top_stocks_label: "Top stocks",
    retrieved_at: retrievedAt,
    items: [item("NVDA")],
    top_stocks: [],
  };

  const state = mergeMarketMovers(
    mergeMarketMovers(null, { kind: "stocks", value: fallback, items: fallback.items }),
    { kind: "movers", value: movers, items: movers.items },
  );

  assert.equal(state.top_stocks_label, "Popular stocks");
  assert.equal(state.top_stocks_rank, fallback.rank);
  assert.equal(state.top_stocks_retrieved_at, retrievedAt);
  assert.equal(state.top_stocks_coverage_note, fallback.coverage_note);
  assert.deepEqual(state.top_stocks.map(({ ticker }) => ticker), ["AAPL"]);
  assert.deepEqual(state.items.map(({ ticker }) => ticker), ["NVDA"]);

  const partialMovers = { ...movers, coverage_note: "Partial market data: daily losers unavailable; this ranking may be incomplete." };
  const reverse = mergeMarketMovers(
    mergeMarketMovers(null, { kind: "movers", value: partialMovers, items: partialMovers.items }),
    { kind: "stocks", value: fallback, items: fallback.items },
  );
  assert.equal(reverse.coverage_note, partialMovers.coverage_note);
  assert.equal(reverse.top_stocks_rank, fallback.rank);
  assert.equal(reverse.top_stocks_retrieved_at, retrievedAt);
  assert.equal(reverse.top_stocks_coverage_note, fallback.coverage_note);
  assert.deepEqual(reverse.items.map(({ ticker }) => ticker), ["NVDA"]);
  assert.deepEqual(reverse.top_stocks.map(({ ticker }) => ticker), ["AAPL"]);
});

test("popular large-cap data stays separate from volume-ranked stocks and movers", () => {
  const movers = {
    source: "Yahoo Finance", period: "Daily market movement", top_stocks_rank: "Most active US equities, ordered by trading volume",
    top_stocks_label: "Most active", retrieved_at: retrievedAt, items: [item("CIFR")], top_stocks: [item("ONDS")],
  };
  const popular = {
    source: "Yahoo Finance", rank: "Popular large-cap stocks; not ranked by daily trading volume",
    label: "Popular stocks", retrieved_at: retrievedAt, items: [item("AAPL"), item("MSFT")],
  };
  const active = {
    source: "Yahoo Finance", rank: "Most active US equities, ordered by trading volume",
    label: "Most active", retrieved_at: retrievedAt, items: [item("ONDS")],
  };
  const state = mergeMarketMovers(
    mergeMarketMovers(
      mergeMarketMovers(null, { kind: "movers", value: movers, items: movers.items }),
      { kind: "stocks", value: active, items: active.items },
    ),
    { kind: "popular", value: popular, items: popular.items },
  );

  assert.deepEqual(state.items.map(({ ticker }) => ticker), ["CIFR"]);
  assert.deepEqual(state.top_stocks.map(({ ticker }) => ticker), ["ONDS"]);
  assert.deepEqual(state.popular_stocks?.map(({ ticker }) => ticker), ["AAPL", "MSFT"]);
  assert.equal(state.popular_stocks_rank, popular.rank);
  assert.equal(state.popular_stocks_label, popular.label);
  assert.equal(state.popular_stocks_retrieved_at, retrievedAt);
});

test("pruning an expired popular list does not alter the other market lists", () => {
  const market = {
    source: "Yahoo Finance", period: "Daily market movement", top_stocks_rank: "volume",
    top_stocks_label: "Most active", retrieved_at: retrievedAt,
    top_stocks_retrieved_at: retrievedAt,
    popular_stocks_rank: "curated", popular_stocks_label: "Popular stocks",
    popular_stocks_retrieved_at: new Date(now - 16 * 60 * 1000).toISOString(),
    items: [item("CIFR")], top_stocks: [item("ONDS")], popular_stocks: [item("AAPL")],
  };
  const pruned = pruneExpiredMarketMovers(market, "popular", now);
  assert.deepEqual(pruned?.popular_stocks, []);
  assert.deepEqual(pruned?.top_stocks.map(({ ticker }) => ticker), ["ONDS"]);
  assert.deepEqual(pruned?.items.map(({ ticker }) => ticker), ["CIFR"]);
});
