import assert from "node:assert/strict";
import test from "node:test";
import { createServerClockAnchor, currentClockMs, isMarketDataStale, MARKET_DATA_FUTURE_TOLERANCE_MS, MARKET_DATA_MAX_AGE_MS } from "./freshness.ts";

test("server-clock freshness ignores an incorrect or later-changing device wall clock", () => {
  const serverEpochMs = Date.parse("2026-10-10T12:00:00Z");
  const anchor = createServerClockAnchor("Sat, 10 Oct 2026 12:00:00 GMT", 250);
  assert.deepEqual(anchor, { serverEpochMs, monotonicMs: 250 });
  assert.equal(currentClockMs(anchor, serverEpochMs - 3 * 60 * 60 * 1000, 1_250), serverEpochMs + 1_000);
  assert.equal(currentClockMs(anchor, serverEpochMs + 3 * 60 * 60 * 1000, 2_250), serverEpochMs + 2_000);
});

test("invalid or unavailable response dates retain a safe local-clock fallback", () => {
  assert.equal(createServerClockAnchor(null, 1), null);
  assert.equal(createServerClockAnchor("not a date", 1), null);
  assert.equal(createServerClockAnchor("Sat, 10 Oct 2026 12:00:00 GMT", Number.NaN), null);
  assert.equal(currentClockMs(null, 123, 456), 123);
});

test("missing and malformed market timestamps are stale, not current", () => {
  assert.equal(isMarketDataStale(null, 1_000_000), true);
  assert.equal(isMarketDataStale(undefined, 1_000_000), true);
  assert.equal(isMarketDataStale("not-a-timestamp", 1_000_000), true);
  assert.equal(isMarketDataStale("2026-10-10T00:00:00Z", Number.NaN), true);
});

test("market freshness uses the 48-hour and five-minute future boundaries", () => {
  const now = Date.parse("2026-10-10T12:00:00Z");
  assert.equal(isMarketDataStale(new Date(now - MARKET_DATA_MAX_AGE_MS).toISOString(), now), false);
  assert.equal(isMarketDataStale(new Date(now - MARKET_DATA_MAX_AGE_MS - 1).toISOString(), now), true);
  assert.equal(isMarketDataStale(new Date(now + MARKET_DATA_FUTURE_TOLERANCE_MS).toISOString(), now), false);
  assert.equal(isMarketDataStale(new Date(now + MARKET_DATA_FUTURE_TOLERANCE_MS + 1).toISOString(), now), true);
});
