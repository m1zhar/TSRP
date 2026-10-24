"""Unit-scale regressions use no network and never assume price units label totals."""

from datetime import datetime, timezone
import math
import os
import unittest
from unittest.mock import patch

from market_quote import resolve_market_cap
from provider_utils import get_ticker_fast_info
from research_service import analyze_company
from tests.test_migration_service import MockEquity


class MarketCapUnitsTests(unittest.TestCase):
    def test_major_currency_paths_retain_provider_values(self):
        for currency in ("USD", "EUR", "JPY", "GBP"):
            fast = {"currency": currency, "marketCap": 1000}
            self.assertEqual(resolve_market_cap({}, fast, currency)["value"], 1000)
            self.assertEqual(resolve_market_cap(fast, {}, currency)["value"], 1000)

    def test_captured_yahoo_uk_profile_and_fast_totals_differ_by_100_not_company_value(self):
        # Read-only Yahoo observations on 2026-10-24. Not issuer-certified data.
        # FastInfo's source multiplies shares by quote units:
        # https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/quote.py
        for price, shares, profile_cap, fast_cap in (
            (638.5, 15453548807, 98670903296, 9867090913269.5),  # BP.L
            (12824.0, 1550905742, 198888144896, 19888815235408),  # AZN.L
        ):
            profile = {"currency": "GBp", "marketCap": profile_cap, "currentPrice": price, "sharesOutstanding": shares}
            fast = {"currency": "GBp", "marketCap": fast_cap, "lastPrice": price, "shares": shares}
            fast_result = resolve_market_cap(profile, fast, "GBp")
            fallback = resolve_market_cap(profile, {}, "GBp")
            self.assertAlmostEqual(fast_result["value"] / fallback["value"], 1, places=6)
            self.assertEqual(fallback["raw_currency"], "GBP")
            self.assertEqual(fallback["value"], profile_cap * 100)
            self.assertEqual(fast_result["raw_currency"], "GBp")

    def test_all_supported_subunits_resolve_both_total_scales(self):
        for quote, major in (("GBp", "GBP"), ("GBX", "GBP"), ("ZAc", "ZAR"), ("ILA", "ILS")):
            for raw, expected_unit in ((3000, quote), (30, major)):
                result = resolve_market_cap({"currency": quote, "marketCap": raw, "currentPrice": 120, "sharesOutstanding": 25}, {}, quote)
                self.assertEqual(result["value"], 3000)
                self.assertEqual(result["raw_currency"], expected_unit)

    def test_fallback_inside_fast_info_can_already_be_in_major_units(self):
        fast = {"currency": "GBp", "marketCap": 30, "lastPrice": 120, "shares": 25}
        result = resolve_market_cap({}, fast, "GBp")
        self.assertEqual(result["value"], 3000)
        self.assertEqual(result["raw_currency"], "GBP")

    def test_missing_invalid_or_incomparable_reference_is_never_a_guessed_scale(self):
        valid = {"currency": "GBp", "marketCap": 30, "currentPrice": 120, "sharesOutstanding": 25}
        for field in ("currentPrice", "sharesOutstanding"):
            for value in (None, 0, -1, True, math.inf, math.nan):
                source = {**valid, field: value}
                result = resolve_market_cap(source, {}, "GBp")
                self.assertIsNone(result["value"], (field, value))
                self.assertTrue(result["unit_warning"])
        for value in (3, 300, 30000):
            self.assertIsNone(resolve_market_cap({**valid, "marketCap": value}, {}, "GBp")["value"])
        self.assertIsNone(resolve_market_cap({**valid, "currency": "USD"}, {}, "GBp")["value"])
        self.assertIsNone(resolve_market_cap({**valid, "currency": None}, {}, "GBp")["value"])

    def test_moderate_asynchronous_changes_do_not_hide_a_100x_mismatch(self):
        for cap in (29.9, 30, 30.1):
            result = resolve_market_cap({"currency": "GBp", "marketCap": cap, "currentPrice": 120, "sharesOutstanding": 25}, {}, "GBp")
            self.assertAlmostEqual(result["value"], cap * 100)
        self.assertIsNone(resolve_market_cap({"currency": "GBp", "marketCap": 20, "currentPrice": 120, "sharesOutstanding": 25}, {}, "GBp")["value"])

    def test_bad_fast_cap_uses_independently_comparable_profile_cap(self):
        profile = {"currency": "GBp", "marketCap": 30, "currentPrice": 120, "sharesOutstanding": 25}
        fast = {"currency": "GBp", "marketCap": 999, "lastPrice": 120, "shares": 25}
        self.assertEqual(resolve_market_cap(profile, fast, "GBp")["value"], 3000)

    def test_unidentified_currency_and_invalid_totals_remain_unavailable(self):
        self.assertIsNone(resolve_market_cap({"marketCap": 1000}, {}, "N/A")["value"])
        self.assertIsNone(resolve_market_cap(None, [], "USD")["value"])
        for cap in (None, True, 0, -1, math.inf, math.nan):
            self.assertIsNone(resolve_market_cap({"marketCap": cap}, {}, "USD")["value"])

    def test_conflicting_major_currency_is_not_silently_relabelled(self):
        self.assertIsNone(resolve_market_cap({}, {"currency": "EUR", "marketCap": 1000}, "USD")["value"])
        result = resolve_market_cap({"currency": "USD", "marketCap": 800}, {"currency": "EUR", "marketCap": 1000}, "USD")
        self.assertEqual(result["value"], 800)


class FastQuoteReadTests(unittest.TestCase):
    def test_unused_properties_are_never_evaluated_and_optional_failure_is_isolated(self):
        reads = []

        class Fast:
            def __iter__(self):
                raise AssertionError("Do not eagerly iterate lazy provider data")

            def get(self, key):
                reads.append(key)
                if key in {"marketCap", "market_cap"}:
                    raise RuntimeError("Synthetic unavailable optional field")
                return {"currency": "USD", "lastPrice": 120, "previousClose": 118, "shares": 25}.get(key)

        instrument = type("Instrument", (), {"fast_info": Fast()})()
        result = get_ticker_fast_info(instrument)
        self.assertEqual(result["lastPrice"], 120)
        self.assertNotIn("marketCap", result)
        self.assertEqual(set(reads), {"currency", "lastPrice", "previousClose", "marketCap", "market_cap", "shares"})

    def test_snake_case_provider_keys_remain_supported(self):
        instrument = type("Instrument", (), {"fast_info": {"currency": "USD", "last_price": 120, "previous_close": 118, "market_cap": 3000}})()
        self.assertEqual(get_ticker_fast_info(instrument)["marketCap"], 3000)

    def test_malformed_alias_does_not_hide_an_independently_valid_alias(self):
        instrument = type("Instrument", (), {"fast_info": {"currency": "USD", "lastPrice": True, "last_price": 120, "marketCap": math.inf, "market_cap": 3000}})()
        result = get_ticker_fast_info(instrument)
        self.assertEqual(result["lastPrice"], 120)
        self.assertEqual(result["marketCap"], 3000)


class MarketQuotePipelineTests(unittest.TestCase):
    def fixture(self):
        instrument = MockEquity(datetime.now(timezone.utc).year - 1)
        instrument.fast_info = {}
        instrument.get_info = lambda: {"quoteType": "EQUITY", "currency": "GBp", "financialCurrency": "GBP",
            "sector": "Technology", "currentPrice": 120, "previousClose": 118, "marketCap": 30,
            "sharesOutstanding": 25, "regularMarketTime": datetime.now(timezone.utc).timestamp()}
        return instrument

    def analyze(self, instrument):
        with patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "true"}), patch("research_service.yf.Ticker", return_value=instrument), patch("research_service._sec_facts", return_value=(None, {"kind": "unavailable", "label": "Synthetic fixture"})):
            return analyze_company("EXM.L")["company"]

    def test_profile_fallback_does_not_shrink_equity_value_100_times(self):
        company = self.analyze(self.fixture())
        self.assertEqual(company["market_cap_reporting"], 30)
        self.assertEqual(company["market_cap"], 3000)
        self.assertEqual(company["enterprise_value"], 130)
        self.assertEqual(company["sources"]["market_cap"]["original_currency"], "GBP")
        self.assertEqual(company["sources"]["market_cap"]["original_value"], 30)

    def test_unknown_subunit_scale_blocks_valuation_without_hiding_the_score(self):
        instrument = self.fixture()
        original = instrument.get_info
        instrument.get_info = lambda: {**original(), "sharesOutstanding": None}
        company = self.analyze(instrument)
        self.assertIsNone(company["market_cap_reporting"])
        self.assertIsNone(company["enterprise_value"])
        self.assertTrue(company["score_available"])
        self.assertTrue(any("unit scale" in item["message"] for item in company["quality_flags"]))

    def test_conflicting_quote_currencies_do_not_relabel_fast_amounts(self):
        instrument = self.fixture()
        instrument.fast_info = {"currency": "USD", "lastPrice": 999, "previousClose": 998, "marketCap": 99999}
        company = self.analyze(instrument)
        self.assertEqual(company["price"], 120)
        self.assertEqual(company["previous_close"], 118)
        self.assertEqual(company["market_cap_reporting"], 30)
        self.assertTrue(any("disagree on the trading unit" in item["message"] for item in company["quality_flags"]))
