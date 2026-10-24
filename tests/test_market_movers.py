from datetime import datetime, timedelta, timezone
from concurrent.futures import Future
import unittest
from unittest.mock import patch

import pandas as pd

from research_service import ResearchError, _popular_stock_history, daily_market_movers, daily_popular_stocks, daily_top_stocks


def quote(ticker, price, previous, *, age=0, currency="USD", kind="EQUITY", timestamp_offset=0):
    market_at = datetime.now(timezone.utc) - timedelta(seconds=age) + timedelta(seconds=timestamp_offset)
    return {
        "symbol": ticker,
        "quoteType": kind,
        "currency": currency,
        "regularMarketPrice": price,
        "regularMarketPreviousClose": previous,
        "regularMarketTime": market_at.timestamp(),
    }


class DailyMarketMoversTests(unittest.TestCase):
    def screen_with(self, gainers, losers, active=None):
        screens = {"day_gainers": gainers, "day_losers": losers, "most_actives": active or []}
        def screen(name, **_kwargs):
            return {"quotes": screens[name]}
        return patch("research_service.yf.screen", side_effect=screen)

    def test_popular_stocks_entry_point_uses_the_separate_large_cap_history(self):
        today = pd.Timestamp.now(tz="UTC").normalize()
        history = pd.DataFrame({
            ("Close", "AAPL"): [100.0, 101.0],
            ("Close", "MSFT"): [200.0, 198.0],
        }, index=[today - pd.Timedelta(1, unit="D"), today])
        history.columns = pd.MultiIndex.from_tuples(history.columns)
        with patch("research_service.yf.screen", side_effect=AssertionError("popular stocks are not volume-ranked")), patch(
            "research_service.yf.download", return_value=history
        ):
            result = daily_popular_stocks()
        self.assertEqual(result["label"], "Popular stocks")
        self.assertEqual(result["rank"], "Popular large-cap stocks; not ranked by daily trading volume")
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL", "MSFT"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], 1.0)

    def test_combines_and_ranks_real_equity_moves_with_comparable_daily_percentages(self):
        gainers = [quote("AAA", 110, 100), quote("BBB", 220, 200), quote("CCC", 80, 100)]
        losers = [quote("AAA", 112, 100, timestamp_offset=1)]
        with self.screen_with(gainers, losers):
            result = daily_market_movers()
        self.assertEqual([item["ticker"] for item in result["items"]], ["CCC", "AAA", "BBB"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], -20.0)
        self.assertAlmostEqual(result["items"][1]["change_percent"], 12.0)
        self.assertEqual(result["source"], "Yahoo Finance")
        self.assertEqual(result["period"], "Daily market movement")

    def test_equal_percentages_use_absolute_change_then_recency_then_ticker(self):
        rows = [quote("ZZZ", 110, 100), quote("AAA", 220, 200), quote("BBB", 110, 100, timestamp_offset=2)]
        with self.screen_with(rows, []):
            result = daily_market_movers()
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAA", "BBB", "ZZZ"])

    def test_excludes_instruments_invalid_baselines_stale_quotes_and_future_timestamps(self):
        rows = [
            quote("INDEX", 110, 100, kind="INDEX"),
            quote("BAD/QUOTE", 110, 100),
            quote("ZERO", 110, 0),
            quote("NEG", 90, -100),
            quote("OLD", 110, 100, age=49 * 60 * 60),
            quote("FUTURE", 110, 100, timestamp_offset=6 * 60),
            quote("CAD", 110, 100, currency="CAD"),
        ]
        with self.screen_with(rows, []):
            result = daily_market_movers()
        self.assertEqual(result["items"], [])

    def test_deduplicates_and_returns_up_to_seven_valid_movers(self):
        rows = [quote(f"STK{i}", 100 + i, 100) for i in range(1, 8)]
        with self.screen_with(rows, [rows[0]]):
            result = daily_market_movers()
        self.assertEqual(len(result["items"]), 7)
        self.assertEqual(len({item["ticker"] for item in result["items"]}), 7)

    def test_top_stocks_come_from_yahoos_most_active_screener_without_duplicates(self):
        movers = [quote("AAPL", 110, 100)]
        active = [quote("AAPL", 110, 100), quote("MSFT", 220, 200), quote("NVDA", 130, 125), quote("AMZN", 210, 200), quote("GOOGL", 310, 300), quote("META", 420, 400), quote("TSLA", 220, 200), quote("AVGO", 550, 500)]
        with self.screen_with(movers, [], active):
            result = daily_top_stocks()
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"])
        self.assertEqual(result["rank"], "Most active US equities, ordered by trading volume")
        self.assertAlmostEqual(result["items"][1]["change_percent"], 10.0)

    def test_invalid_duplicate_does_not_hide_later_valid_top_stock_quote(self):
        invalid = quote("AAPL", None, 100)
        valid = quote("AAPL", 101, 100)
        with self.screen_with([], [], [invalid, valid]), patch(
            "research_service._popular_stock_history", side_effect=AssertionError("unexpected fallback")
        ):
            result = daily_top_stocks()
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], 1.0)

    def test_top_stocks_uses_freshest_valid_duplicate_quote_without_reordering(self):
        first_aapl = quote("AAPL", 110, 100, age=4 * 60)
        msft = quote("MSFT", 220, 200, age=3 * 60)
        fresher_aapl = quote("AAPL", 111, 100, age=1 * 60)
        with self.screen_with([], [], [first_aapl, msft, fresher_aapl]):
            result = daily_top_stocks()
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL", "MSFT"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], 11.0)
        self.assertGreater(
            datetime.fromisoformat(result["items"][0]["market_at"]),
            datetime.fromisoformat(result["items"][1]["market_at"]),
        )

    def test_late_duplicate_refreshes_quote_after_seven_unique_stocks(self):
        rows = [quote("AAPL", 110, 100, age=4 * 60)]
        rows.extend(quote(f"STK{index}", 110, 100, age=3 * 60) for index in range(6))
        rows.append(quote("AAPL", 111, 100, age=1 * 60))
        with self.screen_with([], [], rows):
            result = daily_top_stocks()
        self.assertEqual(len(result["items"]), 7)
        self.assertEqual(result["items"][0]["ticker"], "AAPL")
        self.assertAlmostEqual(result["items"][0]["change_percent"], 11.0)

    def test_familiar_stocks_use_fresh_premarket_quote_not_future_regular_close(self):
        premarket = quote("NVDA", 239.24, 238.90, timestamp_offset=8 * 60 * 60)
        premarket.update({
            "marketState": "PRE",
            "regularMarketPrice": 239.24,
            "regularMarketTime": (datetime.now(timezone.utc) + timedelta(hours=8)).timestamp(),
            "preMarketPrice": 237.60,
            "preMarketTime": datetime.now(timezone.utc).timestamp(),
        })
        with self.screen_with([], [], [premarket]):
            result = daily_top_stocks()
        self.assertEqual(result["items"][0]["ticker"], "NVDA")
        self.assertAlmostEqual(result["items"][0]["change_percent"], (237.60 - 238.90) / 238.90 * 100)
        self.assertLess(datetime.fromisoformat(result["items"][0]["market_at"]), datetime.now(timezone.utc) + timedelta(minutes=5))

    def test_movers_use_fresh_premarket_quote_instead_of_stale_regular_quote(self):
        premarket = quote("NVDA", 239.24, 238.90, timestamp_offset=8 * 60 * 60)
        premarket.update({
            "marketState": "PRE",
            "preMarketPrice": 237.60,
            "preMarketTime": datetime.now(timezone.utc).timestamp(),
        })
        with self.screen_with([premarket], []):
            result = daily_market_movers()
        self.assertEqual([item["ticker"] for item in result["items"]], ["NVDA"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], (237.60 - 238.90) / 238.90 * 100)

    def test_movers_use_fresh_postmarket_quote_instead_of_regular_quote(self):
        postmarket = quote("NVDA", 239.24, 238.90, timestamp_offset=-8 * 60 * 60)
        postmarket.update({
            "marketState": "POST",
            "postMarketPrice": 237.60,
            "postMarketTime": datetime.now(timezone.utc).timestamp(),
        })
        with self.screen_with([postmarket], []):
            result = daily_market_movers()
        self.assertEqual([item["ticker"] for item in result["items"]], ["NVDA"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], (237.60 - 238.90) / 238.90 * 100)

    def test_top_stocks_keep_comparable_unchanged_names_and_skip_invalid_quotes(self):
        rows = [
            quote("FLAT", 100, 100),
            quote("RECENT", 110, 100, age=47 * 60 * 60 + 59 * 60),
            quote("ZEROBASE", 10, 0),
            quote("STALE", 110, 100, age=49 * 60 * 60),
            quote("FOREIGN", 110, 100, currency="CAD"),
        ]
        with self.screen_with([], [], rows):
            result = daily_top_stocks()
        self.assertEqual([item["ticker"] for item in result["items"]], ["FLAT", "RECENT"])
        self.assertEqual(result["items"][0]["change_percent"], 0)

    def test_screener_failure_uses_real_yahoo_history_and_labels_popular_stock_fallback(self):
        today = pd.Timestamp.now(tz="UTC").normalize()
        index = [today - pd.Timedelta(1, unit="D"), today]
        history = pd.DataFrame({
            ("Close", "AAPL"): [100.0, 101.0],
            ("Close", "MSFT"): [200.0, 198.0],
        }, index=index)
        history.columns = pd.MultiIndex.from_tuples(history.columns)
        with patch("research_service.yf.screen", side_effect=RuntimeError("screener unavailable")), patch("research_service.yf.download", return_value=history):
            result = daily_top_stocks()
        self.assertEqual(result["label"], "Popular stocks")
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL", "MSFT"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], 1.0)
        self.assertAlmostEqual(result["items"][1]["change_percent"], -1.0)

    def test_stale_historical_fallback_is_unavailable_instead_of_presented_as_current(self):
        today = pd.Timestamp.now(tz="UTC").normalize()
        old_dates = [today - pd.Timedelta(20, unit="D"), today - pd.Timedelta(19, unit="D")]
        history = pd.DataFrame({
            ("Close", "AAPL"): [100.0, 101.0],
            ("Close", "MSFT"): [200.0, 198.0],
        }, index=old_dates)
        history.columns = pd.MultiIndex.from_tuples(history.columns)
        with patch("research_service.yf.screen", side_effect=RuntimeError("screener unavailable")), patch(
            "research_service.yf.download", return_value=history
        ):
            with self.assertRaises(ResearchError) as error:
                daily_top_stocks()
        self.assertEqual(error.exception.code, "top_stocks_unavailable")
        self.assertEqual(error.exception.message, "Yahoo Finance top stocks are temporarily unavailable.")

    def test_history_fallback_stays_within_the_browser_date_only_freshness_limit(self):
        fetched_at = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        dates = [
            pd.Timestamp("2026-10-07", tz="UTC"),
            pd.Timestamp("2026-10-08", tz="UTC"),
            pd.Timestamp("2026-10-09", tz="UTC"),
            pd.Timestamp("2026-10-11", tz="UTC"),
        ]
        history = pd.DataFrame({
            ("Close", "AAPL"): [100.0, 101.0, None, None],
            ("Close", "MSFT"): [199.0, None, None, None],
            ("Close", "NVDA"): [None, None, None, 305.0],
        }, index=dates)
        history.columns = pd.MultiIndex.from_tuples(history.columns)

        with patch("research_service._bounded_provider_calls", return_value=({"history": history}, False)):
            result = _popular_stock_history(fetched_at)

        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL"])
        self.assertEqual(result["items"][0]["market_at"], "2026-10-08")

    def test_history_fallback_does_not_bridge_a_missing_market_session(self):
        fetched_at = datetime(2026, 10, 9, 16, tzinfo=timezone.utc)
        dates = [
            pd.Timestamp("2026-10-07", tz="UTC"),
            pd.Timestamp("2026-10-08", tz="UTC"),
            pd.Timestamp("2026-10-09", tz="UTC"),
        ]
        history = pd.DataFrame({
            ("Close", "AAPL"): [100.0, None, 110.0],
            ("Close", "MSFT"): [200.0, 202.0, 204.0],
        }, index=dates)
        history.columns = pd.MultiIndex.from_tuples(history.columns)

        with patch("research_service._bounded_provider_calls", return_value=({"history": history}, False)):
            result = _popular_stock_history(fetched_at)

        self.assertEqual([item["ticker"] for item in result["items"]], ["MSFT"])
        self.assertAlmostEqual(result["items"][0]["change_percent"], 100 * (204 - 202) / 202)

    def test_a_failed_screener_does_not_hide_successful_groups(self):
        def screen(name, **_kwargs):
            if name == "day_gainers":
                raise RuntimeError("temporary Yahoo error")
            return {"quotes": [quote("LOSER", 90, 100)] if name == "day_losers" else [quote("AAPL", 110, 100)]}
        with patch("research_service.yf.screen", side_effect=screen):
            result = daily_market_movers()
            top_stocks = daily_top_stocks()
        self.assertEqual(result["items"][0]["ticker"], "LOSER")
        self.assertEqual(result["coverage_note"], "Partial market data: daily gainers unavailable; this ranking may be incomplete.")
        self.assertEqual(top_stocks["items"][0]["ticker"], "AAPL")

    def test_both_mover_screens_return_complete_coverage(self):
        with self.screen_with([quote("WIN", 110, 100)], [quote("DROP", 90, 100)]):
            result = daily_market_movers()
        self.assertEqual({item["ticker"] for item in result["items"]}, {"WIN", "DROP"})
        self.assertNotIn("coverage_note", result)

    def test_malformed_equity_quote_discloses_incomplete_mover_ranking(self):
        malformed = {"symbol": "BROKEN", "quoteType": "EQUITY", "currency": "USD"}
        with self.screen_with([malformed], [quote("DROP", 90, 100)]):
            result = daily_market_movers()
        self.assertEqual([item["ticker"] for item in result["items"]], ["DROP"])
        self.assertEqual(
            result["coverage_note"],
            "Partial market data: daily gainers contained unusable or stale quotes; this ranking may be incomplete.",
        )

    def test_malformed_top_stock_quote_discloses_partial_list(self):
        malformed = {"symbol": "BROKEN", "quoteType": "EQUITY", "currency": "USD"}
        with self.screen_with([], [], [quote("AAPL", 110, 100), malformed]):
            result = daily_top_stocks()
        self.assertEqual([item["ticker"] for item in result["items"]], ["AAPL"])
        self.assertEqual(
            result["coverage_note"],
            "Yahoo Finance most-active results contained unusable or stale quotes; this list may be incomplete.",
        )

    def test_provider_capacity_rejection_is_disclosed_as_partial_coverage(self):
        successful = Future()
        successful.set_result({"quotes": [quote("WIN", 110, 100)]})
        with patch("research_service._submit_market_provider_call", side_effect=[successful, None]):
            result = daily_market_movers()
        self.assertEqual([item["ticker"] for item in result["items"]], ["WIN"])
        self.assertEqual(result["coverage_note"], "Partial market data: daily losers unavailable; this ranking may be incomplete.")

    def test_pending_loser_screener_keeps_completed_gainers_and_discloses_partial_coverage(self):
        successful = Future()
        successful.set_result({"quotes": [quote("WIN", 110, 100)]})
        pending = Future()
        with patch("research_service._submit_market_provider_call", side_effect=[successful, pending]), patch(
            "research_service.wait", return_value=({successful}, {pending})
        ), patch("research_service._cancel_market_provider_call") as cancel:
            result = daily_market_movers()

        self.assertEqual([item["ticker"] for item in result["items"]], ["WIN"])
        self.assertEqual(result["coverage_note"], "Partial market data: daily losers unavailable; this ranking may be incomplete.")
        cancel.assert_called_once_with(pending)

    def test_pending_top_stocks_screener_uses_labeled_fallback(self):
        pending = Future()
        fallback = {"label": "Popular stocks", "items": [], "source": "Yahoo Finance", "period": "Daily market movement"}
        with patch("research_service._submit_market_provider_call", return_value=pending), patch(
            "research_service.wait", return_value=(set(), {pending})
        ), patch("research_service._cancel_market_provider_call") as cancel, patch(
            "research_service._popular_stock_history", return_value=fallback
        ) as history:
            result = daily_top_stocks()

        self.assertEqual(result, fallback)
        cancel.assert_called_once_with(pending)
        history.assert_called_once()

    def test_provider_failure_returns_a_safe_unavailable_error(self):
        with patch("research_service.yf.screen", side_effect=RuntimeError("private provider details")):
            with self.assertRaises(ResearchError) as error:
                daily_market_movers()
        self.assertEqual(error.exception.code, "market_movers_unavailable")
        self.assertNotIn("private provider details", str(error.exception))

    def test_top_stock_provider_failure_returns_a_safe_error(self):
        with patch("research_service.yf.screen", side_effect=RuntimeError("private provider details")), patch("research_service.yf.download", side_effect=RuntimeError("private provider details")):
            with self.assertRaises(ResearchError) as error:
                daily_top_stocks()
        self.assertEqual(error.exception.code, "top_stocks_unavailable")
        self.assertNotIn("private provider details", str(error.exception))


if __name__ == "__main__":
    unittest.main()
