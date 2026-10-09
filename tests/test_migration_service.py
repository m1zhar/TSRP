import json
from pathlib import Path
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from engine import (
    business_quality_score,
    dcf_enterprise_value,
    business_quality_detail,
    financial_strength_score,
    model_rates,
    score_breakdown,
)
from methodology import get_sector_model
from research_service import (
    ResearchError,
    _average_equity_roe,
    _bounded_provider_calls,
    _convert,
    _fx_rate,
    _fx_rate_detail,
    _rate_to_usd_detail,
    _period_ratio,
    _period_age_days,
    _sec_candidates,
    _sec_facts,
    _sec_fcf,
    _sec_names_match,
    analyze_company,
    compare_snapshots,
    validate_assumptions,
    validate_ticker,
)


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "score-parity.json").read_text())


def compatible_snapshot(**changes):
    business = {"score": 75, "evidence_coverage": 1.0, "components": [{"key": "growth", "value": 60, "weight": 0.2}]}
    financial = {"score": 70, "evidence_coverage": 1.0, "components": [{"key": "cash_debt", "value": 70, "weight": 0.3}]}
    trace = {"score": 72, "components": [{"key": "business_quality", "value": 75, "evidence_coverage": 1.0}], "subscores": {"business_quality": business, "financial_strength": financial}, "evidence_coverage": 1.0}
    result = {
        "ticker": "AAPL", "methodology_version": "score-v3", "analysis_schema_version": "analysis-v2",
        "reporting_currency": "USD", "retrieved_at": "2026-10-07T12:00:00+00:00", "saved_at": "2026-10-07T12:01:00+00:00",
        "score": 72, "score_trace": trace, "business_quality": 75, "financial_strength": 70,
        "historical_growth": 0.1, "consensus_growth": 0.12, "required_growth": 0.08,
        "revenue": 100, "revenue_period": "2025-12-31", "revenue_source": "Yahoo Finance",
        "free_cash_flow": 20, "fcf_period": "2025-12-31", "fcf_source": "Yahoo Finance",
        "enterprise_value": 1000, "solver_status": "exact", "user_assumptions": None,
        "sources": {
            "revenue": {"source": "Yahoo Finance", "period": "2025-12-31", "currency": "USD", "kind": "reported"},
            "free_cash_flow": {"source": "Yahoo Finance", "period": "2025-12-31", "currency": "USD", "kind": "calculated: operating cash flow less capital spending"},
        },
    }
    result.update(changes)
    return result


class MockEquity:
    def __init__(self, fiscal_year: int):
        dates = [f"{year}-12-31" for year in range(fiscal_year, fiscal_year - 4, -1)]
        self.fast_info = {"lastPrice": 120.0, "previousClose": 118.0, "marketCap": 2500.0, "currency": "USD"}
        self.financials = pd.DataFrame([
            [1000, 900, 800, 700], [700, 630, 560, 490], [200, 180, 160, 140], [120, 110, 100, 90], [300, 280, 260, 240],
        ], index=["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "EBITDA"], columns=dates)
        self.balance_sheet = pd.DataFrame([
            [100, 90, 80, 70], [200, 190, 180, 170], [1500, 1400, 1300, 1200], [700, 650, 600, 550],
        ], index=["Cash And Cash Equivalents", "Total Debt", "Total Assets", "Stockholders Equity"], columns=dates)
        self.cashflow = pd.DataFrame([[220, 200, 180, 160], [-40, -35, -30, -25]], index=["Operating Cash Flow", "Capital Expenditure"], columns=dates)
        self.revenue_estimate = pd.DataFrame({"growth": [0.12]}, index=["+1y"])

    def get_info(self):
        return {"quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ", "financialCurrency": "USD", "currency": "USD", "sector": "Technology", "regularMarketTime": datetime.now(timezone.utc).timestamp()}

    def history(self, **_kwargs):
        now = pd.Timestamp.now(tz="UTC").normalize()
        return pd.DataFrame({"Close": [118.0, 120.0], "Volume": [1000, 1200]}, index=[now - pd.Timedelta(days=1), now])


class MigrationParityTests(unittest.TestCase):
    def test_currency_and_sector_calibration_parity(self):
        inputs = FIXTURE["rates"]
        actual = model_rates(inputs["currency"], inputs["sector"])
        for key, expected in inputs["expected"].items():
            self.assertEqual(actual[key], expected)

    def test_score_and_weight_parity(self):
        values = FIXTURE["score"]["inputs"]
        trace = score_breakdown(**values)
        self.assertAlmostEqual(trace["score"], FIXTURE["score"]["expected_score"], places=10)
        self.assertEqual([item["weight"] for item in trace["components"]], FIXTURE["score"]["expected_weights"])
        self.assertAlmostEqual(sum(item["contribution"] for item in trace["components"]), trace["score"], places=10)

    def test_sector_subscores_use_the_baseline_model(self):
        model = get_sector_model("Technology")
        self.assertAlmostEqual(
            business_quality_score(.12, .68, .30, .19, .32, .15, model),
            87.75357142857143,
            places=10,
        )
        self.assertAlmostEqual(
            financial_strength_score(90, 35, .30, .19, .15, model),
            92.1590909090909,
            places=10,
        )

    def test_dcf_parity(self):
        case = FIXTURE["dcf"]
        self.assertAlmostEqual(
            dcf_enterprise_value(**case["inputs"]),
            case["expected_enterprise_value"],
            places=9,
        )


class InputAndSourceSafetyTests(unittest.TestCase):
    def test_ticker_validation_rejects_indices_and_injection(self):
        for ticker in ("^GSPC", "AAPL/../", "AAPL;DROP", "BTC-USD"):
            with self.subTest(ticker=ticker), self.assertRaises(ResearchError):
                validate_ticker(ticker)
        self.assertEqual(validate_ticker("brk.b"), "BRK.B")

    def test_assumption_validation_rejects_nonfinite_and_invalid_rate_pair(self):
        valid = {"starting_growth": .12, "fcf_margin": .18, "discount_rate": .09, "terminal_growth": .02}
        self.assertEqual(validate_assumptions(valid), valid)
        for invalid in (
            {**valid, "discount_rate": float("nan")},
            {**valid, "discount_rate": .02},
            {**valid, "fcf_margin": -.1},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ResearchError):
                validate_assumptions(invalid)

    def test_fx_failure_does_not_relabel_or_return_value(self):
        with patch("research_service._rate_to_usd", return_value=None):
            self.assertIsNone(_fx_rate("EUR", "USD"))
            self.assertIsNone(_convert(100, "EUR", "USD"))
        self.assertIsNone(_fx_rate("XYZ", "XYZ"))
        self.assertIsNone(_fx_rate("N/A", "N/A"))
        self.assertIsNone(_fx_rate("GBp", "GBP"))

    def test_same_currency_rate_is_explicit_identity(self):
        self.assertEqual(_fx_rate_detail("USD", "USD"), {"rate": 1.0, "pairs": [], "period": "Same-currency identity"})

    def test_fx_cross_rate_uses_consistent_usd_basis(self):
        with patch("research_service._rate_to_usd", side_effect=[.8, 1.25]):
            self.assertAlmostEqual(_fx_rate("EUR", "CAD"), .64)

    def test_fx_rejects_unknown_same_currency_identity(self):
        self.assertIsNone(_fx_rate_detail("XYZ", "XYZ"))

    def test_fx_detail_validates_direct_and_inverse_quotes(self):
        now = pd.Timestamp.now(tz="UTC")

        class Quote:
            def __init__(self, value):
                self.value = value

            def history(self, **_kwargs):
                return pd.DataFrame({"Close": [self.value]}, index=[now]) if self.value is not None else pd.DataFrame()

        with patch("research_service.yf.Ticker", return_value=Quote(1.10)):
            direct = _rate_to_usd_detail("EUR")
        self.assertEqual(direct["rate"], 1.10)
        self.assertEqual(direct["direction"], "pair quote")

        with patch("research_service.yf.Ticker", side_effect=lambda symbol: Quote(None) if symbol == "JPYUSD=X" else Quote(.0067)):
            inverse = _rate_to_usd_detail("JPY")
        self.assertAlmostEqual(inverse["rate"], 1 / .0067)
        self.assertEqual(inverse["direction"], "inverse pair quote")

    def test_fx_rejects_stale_quote_and_incomparable_cross_dates(self):
        stale = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=4)
        class StaleQuote:
            def history(self, **_kwargs):
                return pd.DataFrame({"Close": [1.1]}, index=[stale])
        with patch("research_service.yf.Ticker", return_value=StaleQuote()):
            self.assertIsNone(_rate_to_usd_detail("EUR"))
        with patch("research_service._rate_to_usd_detail", side_effect=[
            {"rate": 1.1, "pair": "EURUSD=X", "period": "2026-10-07T00:00:00+00:00", "direction": "pair quote"},
            {"rate": .8, "pair": "CADUSD=X", "period": "2026-10-04T00:00:00+00:00", "direction": "pair quote"},
        ]):
            self.assertIsNone(_fx_rate_detail("EUR", "CAD"))

    def test_statement_age_has_explicit_future_and_stale_boundaries(self):
        now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        self.assertEqual(_period_age_days("2025-04-05", now), 550)
        self.assertEqual(_period_age_days("2028-01-01", now), -451)
        self.assertIsNone(_period_age_days("not-a-date", now))

    def test_sec_fcf_requires_matching_period_unit_and_duration(self):
        facts = {"ocf": {"value": 20, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"},
                 "capex": {"value": -5, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"}}
        with patch("research_service._sec_value", side_effect=lambda _facts, tags, _currencies: facts["ocf"] if tags == ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "CashProvidedByUsedInOperatingActivities") else facts["capex"]):
            revenue = {"value": 100, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"}
            self.assertEqual(_sec_fcf({}, ["USD"], revenue), (15, "2024-12-31"))
            revenue["period"] = "2023-12-31"
            self.assertEqual(_sec_fcf({}, ["USD"], revenue), (None, None))

    def test_sec_flow_selects_full_year_not_a_quarter_inside_annual_filing(self):
        facts = {"facts": {"us-gaap": {"Revenue": {"units": {"USD": [
            {"form": "10-K", "fy": 2024, "fp": "Q4", "start": "2024-10-01", "end": "2024-12-31", "filed": "2025-02-01", "val": 30},
            {"form": "10-K", "fy": 2024, "fp": "FY", "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01", "val": 100},
            {"form": "10-K/A", "fy": 2024, "fp": "FY", "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-03-01", "val": 105},
            {"form": "10-K", "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "val": 90},
        ]}}}}}
        series, unit, record = _sec_candidates(facts, ("Revenue",), ["USD"], kind="duration")
        self.assertEqual(unit, "USD")
        self.assertEqual(record["value"], 105)
        self.assertEqual(series, [("2024-12-31", 105.0), ("2023-12-31", 90.0)])

    def test_sec_flow_rejects_future_or_impossible_filing_dates(self):
        facts = {"facts": {"us-gaap": {"Revenue": {"units": {"USD": [
            {"form": "10-K", "fy": 2024, "fp": "FY", "start": "2024-01-01", "end": "2024-12-31", "filed": "2099-02-01", "val": 100},
            {"form": "10-K", "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2023-02-01", "val": 90},
        ]}}}}}
        series, unit, record = _sec_candidates(facts, ("Revenue",), ["USD"], kind="duration")
        self.assertEqual((series, unit, record), ([], None, None))

    def test_sec_entity_names_allow_legal_suffix_variation_but_not_different_issuer(self):
        self.assertTrue(_sec_names_match("The Apple Incorporated", "Apple, Inc."))
        self.assertFalse(_sec_names_match("Apple Inc.", "Pineapple Holdings Inc."))

    def test_sec_company_facts_must_match_sec_ticker_map_entity(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"entityName": "Different Company Inc.", "facts": {}}

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP contact research@example.com"}), patch(
            "research_service._sec_ticker_map", return_value={"AAPL": ("0000000001", "Apple Inc.")}
        ), patch("research_service.requests.get", return_value=Response()):
            facts, state = _sec_facts("AAPL", "USD", "USD")
        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "entity_mismatch")

    def test_sec_instant_rejects_duration_facts(self):
        facts = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
            {"form": "10-K", "fy": 2024, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01", "val": 999},
            {"form": "10-K", "fy": 2024, "end": "2024-12-31", "filed": "2025-02-01", "val": 500},
        ]}}}}}
        _, _, record = _sec_candidates(facts, ("Assets",), ["USD"], kind="instant")
        self.assertEqual(record["value"], 500)

    def test_financial_ratios_require_same_period_and_positive_denominator(self):
        self.assertEqual(_period_ratio(-10, "2024-12-31", 100, "2024-12-31"), -0.1)
        self.assertIsNone(_period_ratio(10, "2024-12-31", 100, "2023-12-31"))
        self.assertIsNone(_period_ratio(10, "2024-12-31", 0, "2024-12-31"))
        self.assertIsNone(_period_ratio(10, "2024-12-31", -100, "2024-12-31"))

    def test_roe_uses_average_of_comparable_year_end_equity(self):
        value = _average_equity_roe(20, "2024-12-31", [("2024-12-31", 120), ("2023-12-31", 80)])
        self.assertAlmostEqual(value, 0.2)
        self.assertIsNone(_average_equity_roe(20, "2024-12-31", [("2024-12-31", 120)]))

    def test_business_subscore_does_not_make_missing_factors_look_observed(self):
        detail = business_quality_detail(None, None, None, None, None, None, get_sector_model("Technology"))
        self.assertIsNone(detail["score"])
        self.assertEqual(detail["evidence_coverage"], 0)

    def test_unknown_or_non_equity_listing_is_not_analyzed_as_company_stock(self):
        class Listing:
            fast_info = {}

            def get_info(self):
                return {"quoteType": "INDEX"}

        with patch("research_service.yf.Ticker", return_value=Listing()):
            with self.assertRaises(ResearchError) as result:
                analyze_company("AAPL")
        self.assertEqual(result.exception.code, "unsupported_instrument")

    def test_unknown_currency_blocks_price_valuation_instead_of_using_usd_rates(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "financialCurrency": "XYZ", "currency": "XYZ", "sector": "Technology",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")
        company = result["company"]
        self.assertIsNone(company["market_cap_reporting"])
        self.assertIsNone(company["enterprise_value"])
        self.assertIsNone(company["required_growth"])
        self.assertTrue(any("USD fallback is disclosed" in flag["message"] for flag in company["quality_flags"]))

    def test_provider_pool_returns_at_its_total_wait_budget(self):
        import time
        started = time.monotonic()
        results, timed_out = _bounded_provider_calls({"slow": lambda: time.sleep(0.20)}, timeout_seconds=0.01)
        elapsed = time.monotonic() - started
        self.assertEqual(results, {})
        self.assertEqual(timed_out, ["slow"])
        self.assertLess(elapsed, 0.15)

    def test_active_analysis_returns_source_aligned_fcf_score_and_provenance(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        with patch("research_service.yf.Ticker", return_value=MockEquity(fiscal_year)), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")
        company = result["company"]
        self.assertEqual(company["revenue_period"], f"{fiscal_year}-12-31")
        self.assertEqual(company["fcf_period"], company["revenue_period"])
        self.assertEqual(company["free_cash_flow"], 180)
        self.assertEqual(company["sources"]["free_cash_flow"]["kind"], "calculated: operating cash flow less capital spending")
        self.assertIn("annual reported revenue", company["sources"]["ev_sales"]["kind"])
        self.assertIn("not a trailing-twelve-month P/E", company["sources"]["pe"]["kind"])
        self.assertTrue(company["score_available"])
        self.assertAlmostEqual(company["score"], company["score_trace"]["score"])
        self.assertIsNone(company["market_expectations"])
        self.assertIsNotNone(company["enterprise_value"])

    def test_active_analysis_handles_yfinance_timestamp_statement_columns(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        for statement in (instrument.financials, instrument.balance_sheet, instrument.cashflow):
            statement.columns = pd.to_datetime(statement.columns)
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")

        company = result["company"]
        self.assertEqual(company["fcf_period"], company["revenue_period"])
        self.assertEqual(company["free_cash_flow"], 180)
        self.assertTrue(company["score_available"])

    def test_previous_close_is_not_substituted_for_missing_current_price(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info = {"previousClose": 118.0, "marketCap": 2500.0, "currency": "USD"}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")
        self.assertIsNone(result["company"]["price"])
        self.assertEqual(result["company"]["previous_close"], 118.0)

    def test_market_cap_conversion_uses_listing_currency_not_financial_currency(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "financialCurrency": "EUR", "currency": "USD", "sector": "Technology",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        fx = {"rate": 0.9, "pairs": ["USDEUR=X"], "directions": ["pair quote"], "period": datetime.now(timezone.utc).isoformat()}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ), patch("research_service._fx_rate_detail", return_value=fx) as fx_rate:
            result = analyze_company("AAPL")
        self.assertEqual(result["company"]["reporting_currency"], "EUR")
        self.assertEqual(result["company"]["market_cap_reporting"], 2250.0)
        self.assertEqual(result["company"]["sources"]["market_cap"]["currency"], "USD")
        reporting_source = result["company"]["sources"]["market_cap_reporting"]
        self.assertEqual(reporting_source["currency"], "EUR")
        self.assertEqual(reporting_source["conversions"][0]["from"], "USD")
        self.assertEqual(reporting_source["conversions"][0]["to"], "EUR")
        self.assertTrue(any(call.args[0:2] == ("USD", "EUR") for call in fx_rate.call_args_list))

    def test_future_market_timestamp_blocks_price_valuation(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "financialCurrency": "USD", "currency": "USD", "sector": "Technology",
            "regularMarketTime": (datetime.now(timezone.utc).timestamp() + 10 * 60),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")
        self.assertTrue(result["company"]["is_stale"])
        self.assertIsNone(result["company"]["enterprise_value"])

    def test_negative_earnings_and_ebitda_do_not_produce_standard_multiples(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.financials.loc["Net Income", :] = [-120, -110, -100, -90]
        instrument.financials.loc["EBITDA", :] = [-300, -280, -260, -240]
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")
        self.assertIsNone(result["company"]["pe"])
        self.assertIsNone(result["company"]["ev_ebitda"])

    def test_stale_statements_block_scenario_values(self):
        assumptions = {"starting_growth": 0.10, "fcf_margin": 0.15, "discount_rate": 0.10, "terminal_growth": 0.02}
        with patch("research_service.yf.Ticker", return_value=MockEquity(2000)), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL", assumptions)
        self.assertIsNone(result["company"]["enterprise_value"])
        self.assertIsNone(result["company"]["scenario"]["enterprise_value"])
        self.assertIsNone(result["company"]["scenario"]["equity_value"])

    def test_snapshot_diff_rejects_mismatched_methodology(self):
        result = compare_snapshots(
            {"ticker": "AAPL", "methodology_version": "score-v1"},
            {"ticker": "AAPL", "methodology_version": "score-v2"},
        )
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "methodology_mismatch")

    def test_snapshot_diff_rejects_currency_and_schema_mismatches(self):
        base = compatible_snapshot()
        currency = compare_snapshots(base, {**base, "reporting_currency": "EUR"})
        schema = compare_snapshots(base, {**base, "analysis_schema_version": "analysis-v3"})
        self.assertEqual(currency["reason"], "currency_mismatch")
        self.assertEqual(schema["reason"], "schema_mismatch")

    def test_snapshot_omits_revenue_delta_when_periods_differ(self):
        base = compatible_snapshot()
        current = {**base, "revenue": 120, "revenue_period": "2024-12-31", "free_cash_flow": 15, "fcf_period": "2024-12-31", "sources": {"revenue": {**base["sources"]["revenue"], "period": "2024-12-31"}, "free_cash_flow": {**base["sources"]["free_cash_flow"], "period": "2024-12-31"}}}
        result = compare_snapshots(base, current)
        self.assertTrue(result["comparable"])
        self.assertNotIn("revenue", {item["field"] for item in result["changes"]})
        self.assertNotIn("free_cash_flow", {item["field"] for item in result["changes"]})
        self.assertEqual({item["field"] for item in result["excluded_fields"]}, {"revenue", "free_cash_flow"})

    def test_snapshot_excludes_values_when_source_method_changes(self):
        base = compatible_snapshot()
        current = {**base, "fcf_source": "SEC EDGAR", "sources": {**base["sources"], "free_cash_flow": {**base["sources"]["free_cash_flow"], "source": "SEC EDGAR", "kind": "calculated: annual SEC operating cash flow less capital spending"}}}
        result = compare_snapshots(base, current)
        self.assertTrue(result["comparable"])
        self.assertIn({"field": "free_cash_flow", "reason": "source_definition_changed"}, result["excluded_fields"])

    def test_snapshot_score_is_excluded_when_evidence_basis_changes(self):
        base = compatible_snapshot()
        trace = {**base["score_trace"], "evidence_coverage": 0.85, "components": [{"key": "business_quality", "value": 75, "evidence_coverage": 0.8}]}
        current = {**base, "score": 70, "score_trace": {**trace, "score": 70}}
        result = compare_snapshots(base, current)
        self.assertTrue(result["comparable"])
        self.assertIn({"field": "score", "reason": "score_evidence_changed"}, result["excluded_fields"])
        self.assertNotIn("score", {item["field"] for item in result["changes"]})

    def test_snapshot_rejects_malformed_score_trace(self):
        base = compatible_snapshot()
        result = compare_snapshots(base, {**base, "score_trace": {**base["score_trace"], "score": 1}})
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_schema_invalid")


if __name__ == "__main__":
    unittest.main()
