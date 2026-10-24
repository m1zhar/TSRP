import assert from "node:assert/strict";
import test from "node:test";
import {
  MARKET_LIST_LABELS,
  MARKET_LIST_REFRESH_INTERVAL_MINUTES,
  MARKET_LIST_REFRESH_INTERVAL_MS,
  marketListEndpoint,
  shouldPollMarketList,
} from "./market-refresh.ts";

test("market-list labels distinguish movement, volume ranking, and familiar stocks", () => {
  assert.deepEqual(MARKET_LIST_LABELS, {
    movers: "Top movers",
    stocks: "Most active",
    popular: "Popular stocks",
  });
});

test("market-list refresh milliseconds match the interval disclosed to users", () => {
  assert.equal(MARKET_LIST_REFRESH_INTERVAL_MINUTES, 5);
  assert.equal(MARKET_LIST_REFRESH_INTERVAL_MS, MARKET_LIST_REFRESH_INTERVAL_MINUTES * 60_000);
});

test("selecting a market list maps only to its own provider route", () => {
  assert.equal(marketListEndpoint("movers"), "/api/movers");
  assert.equal(marketListEndpoint("stocks"), "/api/top_stocks");
  assert.equal(marketListEndpoint("popular"), "/api/top_stocks?list=popular");
});

test("market lists poll only while their page is visible", () => {
  assert.equal(shouldPollMarketList("visible"), true);
  assert.equal(shouldPollMarketList("hidden"), false);
  assert.equal(shouldPollMarketList("prerender"), false);
});
