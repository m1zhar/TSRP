import json
import io
import logging
import math
import os
from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError

from engine import (
    SOLVER_LOW,
    business_quality_score,
    dcf_enterprise_value,
    business_quality_detail,
    financial_strength_score,
    history_cagr_periods,
    model_rates,
    score_breakdown,
    solve_required_growth_detail,
)
from methodology import get_sector_model, has_sector_calibration
from financial_data import latest_period_aligned_fcf_detail, perioded_financial_value
from research_service import (
    MAX_FX_CROSS_OBSERVATION_GAP_SECONDS,
    MAX_SEC_COMPANY_FACTS_BYTES,
    MAX_SEC_TICKER_MAP_BYTES,
    MAX_SCENARIO_FINANCIAL_AMOUNT,
    _YAHOO_SESSION,
    ResearchError,
    SEC_TAGS,
    _average_equity_roe,
    _bounded_provider_calls,
    _chart_price_history,
    _chronological_history,
    _convert,
    _fx_rate,
    _fx_rate_detail,
    _estimate_growth,
    _fx_detail_aligned_to_market_timestamp,
    _issue_snapshot_proof,
    _fetch_info,
    _fetch_sec_company_facts,
    _rate_to_usd_detail,
    _period_ratio,
    _period_age_days,
    _exclude_future_statement_periods,
    _quote_timestamp,
    _sec_candidates,
    _sec_facts,
    _sec_fcf,
    _sec_fcf_margin_history,
    _sec_identity,
    _sec_names_match,
    _sec_ticker_map,
    _sec_value,
    analyze_company,
    analyze_scenario,
    compare_snapshots,
    _yahoo_total_debt_detail,
    finite,
    search_companies,
    validate_assumptions,
    validate_search,
    validate_ticker,
)


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "score-parity.json").read_text())


class StreamingResponse:
    headers = {}

    def iter_content(self):
        yield json.dumps(self.json()).encode("utf-8")

    def close(self):
        return None


def compatible_snapshot(**changes):
    business = {
        "score": 75, "evidence_coverage": 1.0,
        "components": [
            {"key": key, "value": 75, "weight": weight}
            for key, weight in (("growth", .25), ("gross", .20), ("operating", .15), ("fcf", .20), ("roe", .10), ("balance", .10))
        ],
    }
    financial = {
        "score": 70, "evidence_coverage": 1.0,
        "components": [
            {"key": key, "value": 70, "weight": weight}
            for key, weight in (("cash_debt", .30), ("operating", .20), ("fcf", .30), ("leverage", .20))
        ],
    }
    trace = {
        "score": (75 * 7 + 70 * 6) / 13,
        "growth_comparison_inputs": {
            "historical_growth": None,
            "required_growth": None,
            "growth_clamped": False,
        },
        "components": [
            {"key": "business_quality", "value": 75, "evidence_coverage": 1.0},
            {"key": "financial_strength", "value": 70, "evidence_coverage": 1.0},
        ],
        "growth_comparison_available": False,
        "subscores": {"business_quality": business, "financial_strength": financial},
        "evidence_coverage": 1.0,
    }
    result = {
        "ticker": "AAPL", "methodology_version": "score-v9", "analysis_schema_version": "analysis-v4", "sector": "Technology",
        "reporting_currency": "USD", "retrieved_at": "2026-10-07T12:00:00+00:00", "saved_at": "2026-10-07T12:01:00+00:00",
        "score": trace["score"], "score_trace": trace, "business_quality": 75, "financial_strength": 70,
        "market_expectations": 61.0, "historical_growth": 0.1, "consensus_growth": 0.12,
        "analyst_estimate_period": "Next-year analyst revenue estimate", "required_growth": 0.1328125,
        "revenue": 100, "revenue_period": "2025-12-31", "revenue_source": "Yahoo Finance",
        "free_cash_flow": 20, "fcf_period": "2025-12-31", "fcf_source": "Yahoo Finance",
        "enterprise_value": 1000, "model_fcf_margin": 0.12, "discount_rate": 0.09,
        "terminal_growth": 0.02, "solver_status": "exact", "user_assumptions": None,
        "sources": {
            "revenue": {"source": "Yahoo Finance", "period": "2025-12-31", "currency": "USD", "kind": "reported"},
            "free_cash_flow": {"source": "Yahoo Finance", "period": "2025-12-31", "currency": "USD", "kind": "calculated: operating cash flow less capital spending"},
        },
    }
    result.update(changes)
    result["snapshot_proof"] = _issue_snapshot_proof(result)
    return result


def resign_snapshot(value):
    value["snapshot_proof"] = _issue_snapshot_proof(value)
    return value


def scenario_basis(**changes):
    period = f"{datetime.now(timezone.utc).year - 1}-12-31"
    result = {
        "ticker": "AAPL", "sector": "Technology", "reporting_currency": "USD",
        "revenue": 1000.0, "revenue_period": period, "revenue_currency": "USD",
        "market_cap_reporting": 2500.0, "enterprise_value": 2600.0,
        "market_at": datetime.now(timezone.utc).isoformat(),
        "cash": 100.0, "cash_period": period, "cash_currency": "USD",
        "debt": 200.0, "debt_period": period, "debt_currency": "USD",
        "starting_fcf_margin": None,
        "methodology_version": "score-v9", "analysis_schema_version": "analysis-v4",
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
        return pd.DataFrame({"Close": [118.0, 120.0], "Volume": [1000, 1200]}, index=[now - pd.Timedelta(1, unit="D"), now])


class MigrationParityTests(unittest.TestCase):
    def test_latest_fcf_uses_newest_period_across_revenue_aliases(self):
        revenue = pd.DataFrame(
            [[100.0, None], [None, 120.0]],
            index=["Total Revenue", "Operating Revenue"],
            columns=["2024-12-31", "2025-12-31"],
        )
        cashflow = pd.DataFrame(
            [[25.0, 30.0], [-5.0, -6.0]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=["2024-12-31", "2025-12-31"],
        )

        self.assertEqual(
            latest_period_aligned_fcf_detail(
                cashflow, revenue, ["Total Revenue", "Operating Revenue"]
            ),
            (24.0, "2025-12-31", "calculated: operating cash flow less capital spending"),
        )

    def test_yahoo_fcf_normalizes_both_capex_signs_and_labels_reported_fallback(self):
        period = "2025-12-31"
        revenue = pd.DataFrame([[100.0]], index=["Total Revenue"], columns=[period])

        for capex in (6.0, -6.0):
            with self.subTest(capex=capex):
                cashflow = pd.DataFrame(
                    [[4.0], [capex], [99.0]],
                    index=["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"],
                    columns=[period],
                )
                self.assertEqual(
                    latest_period_aligned_fcf_detail(cashflow, revenue, ["Total Revenue"]),
                    (-2.0, period, "calculated: operating cash flow less capital spending"),
                )

        reported_only = pd.DataFrame([[8.0]], index=["Free Cash Flow"], columns=[period])
        self.assertEqual(
            latest_period_aligned_fcf_detail(reported_only, revenue, ["Total Revenue"]),
            (8.0, period, "provider-reported free cash flow; OCF or capex unavailable"),
        )

    def test_conflicting_revenue_aliases_exclude_only_the_ambiguous_period(self):
        revenue = pd.DataFrame(
            [[110.0, 100.0], [999.0, None]],
            index=["Total Revenue", "Operating Revenue"],
            columns=["2025-12-31", "2024-12-31"],
        )
        cashflow = pd.DataFrame(
            [[30.0, 25.0], [-6.0, -5.0]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=["2025-12-31", "2024-12-31"],
        )

        self.assertEqual(
            latest_period_aligned_fcf_detail(
                cashflow, revenue, ["Total Revenue", "Operating Revenue"]
            ),
            (20.0, "2024-12-31", "calculated: operating cash flow less capital spending"),
        )

    def test_provider_numeric_parser_rejects_boolean_values(self):
        self.assertIsNone(finite(True))
        self.assertIsNone(finite(pd.Series([True]).iloc[0]))
        self.assertEqual(finite(12.5), 12.5)

    def test_yahoo_total_debt_is_preferred_to_component_sum(self):
        period = "2025-12-31"
        balance = pd.DataFrame(
            [[270.0], [50.0], [200.0]],
            index=["Total Debt", "Current Debt", "Long Term Debt"],
            columns=[period],
        )

        self.assertEqual(_yahoo_total_debt_detail(balance), (270.0, period, "reported", []))

    def test_yahoo_debt_components_are_not_mixed_across_periods(self):
        balance = pd.DataFrame(
            [[50.0, None], [None, 200.0]],
            index=["Current Debt", "Long Term Debt"],
            columns=["2025-12-31", "2024-12-31"],
        )

        value, period, kind, components = _yahoo_total_debt_detail(balance)
        self.assertIsNone(value)
        self.assertIsNone(period)
        self.assertTrue(kind.startswith("unavailable:"))
        self.assertEqual(components, [])

    def test_yahoo_negative_debt_component_is_not_silently_summed(self):
        period = "2025-12-31"
        balance = pd.DataFrame(
            [[-5.0], [200.0]],
            index=["Current Debt", "Long Term Debt"],
            columns=[period],
        )

        value, returned_period, kind, components = _yahoo_total_debt_detail(balance)
        self.assertIsNone(value)
        self.assertIsNone(returned_period)
        self.assertTrue(kind.startswith("unavailable:"))
        self.assertEqual(components, [])

    def test_negative_reported_total_debt_is_unavailable_or_replaced_by_valid_components(self):
        period = "2025-12-31"
        negative_total = pd.DataFrame([[-20.0]], index=["Total Debt"], columns=[period])
        self.assertIsNone(_yahoo_total_debt_detail(negative_total)[0])

        with_components = pd.DataFrame(
            [[-20.0], [50.0], [200.0]],
            index=["Total Debt", "Current Debt", "Long Term Debt"],
            columns=[period],
        )
        self.assertEqual(
            _yahoo_total_debt_detail(with_components),
            (
                250.0,
                period,
                "calculated: Yahoo Finance current debt + long-term debt",
                [("Current Debt", 50.0), ("Long Term Debt", 200.0)],
            ),
        )

    def test_currency_and_sector_calibration_parity(self):
        inputs = FIXTURE["rates"]
        actual = model_rates(inputs["currency"], inputs["sector"])
        for key, expected in inputs["expected"].items():
            self.assertEqual(actual[key], expected)

    def test_cross_currency_sector_rate_reference_cases(self):
        # Expected rates are independently summed from the fixed profile and
        # spread inputs: max(risk-free + ERP + sector spread, terminal + 1.5%),
        # capped at 22%. These verify implementation consistency only; the
        # underlying static assumptions remain illustrative, not market-validated.
        cases = (
            ("EUR", "Utilities", 0.056, 0.018, -0.018),
            ("JPY", "Industrials", 0.060, 0.007, -0.002),
            ("INR", "Energy", 0.141, 0.050, 0.016),
            ("CHF", "Real Estate", 0.048, 0.010, -0.008),
        )
        for currency, sector, expected_discount, expected_terminal, expected_spread in cases:
            with self.subTest(currency=currency, sector=sector):
                actual = model_rates(currency, sector)
                self.assertEqual(actual["currency"], currency)
                self.assertFalse(actual["used_fallback_currency"])
                self.assertEqual(actual["discount_rate"], expected_discount)
                self.assertEqual(actual["terminal_growth"], expected_terminal)
                self.assertEqual(actual["sector_spread"], expected_spread)

    def test_score_and_weight_parity(self):
        values = FIXTURE["score"]["inputs"]
        trace = score_breakdown(**values)
        business_fit = 75 - (values["required_growth"] - values["historical_growth"]) * 160
        consensus_fit = 75 - (values["required_growth"] - values["consensus_growth"]) * 160
        score_v4_reference = (
            values["business_quality"] * 0.35
            + values["financial_strength"] * 0.30
            + business_fit * 0.20
            + consensus_fit * 0.15
        )
        # Keep the former v7 value as a migration reference, then independently
        # calculate v8 from business quality and financial strength only.
        historical_fit = 75 - (values["required_growth"] - values["historical_growth"]) * 160
        score_v7_reference = (
            values["business_quality"] * 0.35
            + values["financial_strength"] * 0.30
            + historical_fit * 0.20
        ) / 0.85
        score_v8_reference = (
            values["business_quality"] * 0.35 + values["financial_strength"] * 0.30
        ) / 0.65
        self.assertEqual(FIXTURE["score"]["methodology_version"], "score-v8")
        self.assertAlmostEqual(score_v4_reference, FIXTURE["score"]["score_v4_reference"], places=10)
        self.assertAlmostEqual(score_v7_reference, FIXTURE["score"]["score_v7_reference"], places=10)
        self.assertAlmostEqual(trace["score"], score_v8_reference, places=10)
        self.assertAlmostEqual(trace["score"], FIXTURE["score"]["expected_score"], places=10)
        for item, expected_weight in zip(trace["components"], FIXTURE["score"]["expected_weights"]):
            self.assertAlmostEqual(item["weight"], expected_weight, places=14)
        self.assertAlmostEqual(sum(item["contribution"] for item in trace["components"]), trace["score"], places=10)

    def test_score_evidence_gate_passes_at_seventy_percent_and_fails_below(self):
        business = {"score": 80, "evidence_coverage": 1.0}
        financial = {"score": 60, "evidence_coverage": 0.35}
        at_boundary = score_breakdown(business, financial, None, None)

        self.assertAlmostEqual(at_boundary["evidence_coverage"], 0.70, places=14)
        self.assertIsNotNone(at_boundary["score"])
        self.assertAlmostEqual(
            sum(item["contribution"] for item in at_boundary["components"] if item["contribution"] is not None),
            at_boundary["score"],
            places=12,
        )
        changed_consensus = score_breakdown(business, financial, None, None, consensus_growth=0.99)
        self.assertEqual(changed_consensus["score"], at_boundary["score"])

        below_boundary = score_breakdown(
            business,
            {"score": 60, "evidence_coverage": 0.35 - 1e-6},
            None,
            None,
        )
        self.assertLess(below_boundary["evidence_coverage"], 0.70)
        self.assertIsNone(below_boundary["score"])
        self.assertTrue(all(item["contribution"] is None for item in below_boundary["components"]))

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
    def setUp(self):
        self._previous_score_secret = os.environ.get("TSRP_API_TOKEN_SECRET")
        os.environ["TSRP_API_TOKEN_SECRET"] = "migration-test-secret-that-is-not-used-outside-tests"

    def tearDown(self):
        if self._previous_score_secret is None:
            os.environ.pop("TSRP_API_TOKEN_SECRET", None)
        else:
            os.environ["TSRP_API_TOKEN_SECRET"] = self._previous_score_secret

    def test_estimate_growth_preserves_current_year_fallback_horizon(self):
        current = pd.DataFrame({"growth": [0.09]}, index=["0y"])
        both = pd.DataFrame({"growth": [0.09, 0.12]}, index=["0y", "+1y"])

        self.assertEqual(_estimate_growth(current), (0.09, "Current-year analyst revenue estimate"))
        self.assertEqual(_estimate_growth(both), (0.12, "Next-year analyst revenue estimate"))

    def test_scenario_assumptions_do_not_change_the_baseline_score(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        assumptions_sets = (
            {"starting_growth": 0.08, "fcf_margin": 0.12, "discount_rate": 0.08, "terminal_growth": 0.02},
            {"starting_growth": 0.20, "fcf_margin": 0.24, "discount_rate": 0.15, "terminal_growth": 0.04},
        )

        def analysis(assumptions=None):
            with patch("research_service.yf.Ticker", return_value=MockEquity(fiscal_year)), patch(
                "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
            ):
                return analyze_company("AAPL", assumptions)["company"]

        baseline = analysis()
        scenarios = [analysis(assumptions) for assumptions in assumptions_sets]

        for scenario in scenarios:
            self.assertEqual(scenario["score"], baseline["score"])
            self.assertEqual(scenario["score_trace"], baseline["score_trace"])
        self.assertNotEqual(scenarios[0]["scenario"]["enterprise_value"], scenarios[1]["scenario"]["enterprise_value"])

    def test_enterprise_value_just_below_reverse_dcf_range_is_excluded_from_score(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        rates = model_rates("USD", "Technology")
        minimum_value = dcf_enterprise_value(
            1000.0, SOLVER_LOW, 0.18, rates["discount_rate"], rates["terminal_growth"]
        )
        self.assertIsNotNone(minimum_value)
        # Market capitalization + debt - cash produces an EV just below the
        # low solver wall, within the old `isclose` tolerance.
        instrument.fast_info["marketCap"] = minimum_value * (1 - 5e-13) - 100.0
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service.choose_model_fcf_margin", return_value=(0.18, False, "fixture margin")
        ), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["solver_status"], "below_range")
        self.assertTrue(company["growth_clamped"])
        self.assertEqual(company["required_growth"], SOLVER_LOW)
        self.assertFalse(company["score_trace"]["growth_comparison_available"])
        self.assertEqual(
            {component["key"] for component in company["score_trace"]["components"]},
            {"business_quality", "financial_strength"},
        )

    def test_embedded_and_interactive_scenarios_use_the_same_reported_starting_margin(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        assumptions = {"starting_growth": 0.12, "fcf_margin": 0.30, "discount_rate": 0.09, "terminal_growth": 0.02}
        with patch("research_service.yf.Ticker", return_value=MockEquity(fiscal_year)), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL", assumptions)["company"]

        basis = scenario_basis(
            revenue=company["revenue"],
            market_cap_reporting=company["market_cap_reporting"],
            enterprise_value=company["enterprise_value"],
            cash=company["cash"],
            debt=company["debt"],
            starting_fcf_margin=company["fcf_margin"],
        )
        interactive_scenario = analyze_scenario("AAPL", assumptions, basis)

        self.assertAlmostEqual(company["fcf_margin"], 0.18, places=10)
        self.assertAlmostEqual(
            company["scenario"]["enterprise_value"],
            interactive_scenario["enterprise_value"],
            places=9,
        )

    def assert_score_has_no_price_comparison(self, company):
        self.assertTrue(company["score_available"])
        self.assertIsNotNone(company["score"])
        trace = company["score_trace"]
        self.assertIsNotNone(trace)
        self.assertAlmostEqual(company["score"], trace["score"])
        self.assertFalse(trace["growth_comparison_available"])
        self.assertGreaterEqual(trace["evidence_coverage"], trace["minimum_evidence_coverage"])
        self.assertAlmostEqual(trace["evidence_coverage"], 1.0)
        self.assertEqual({component["key"] for component in trace["components"]}, {"business_quality", "financial_strength"})

    def test_chart_price_history_is_valid_ordered_and_omits_ambiguous_duplicate_sessions(self):
        history = pd.DataFrame(
            {"Close": [120.0, 999.0, 118.0, 119.0, 121.0, float("nan")], "Volume": [1200, 9999, 1000, 1100, 1210, 500]},
            index=["2026-10-10", "not-a-date", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"],
        )
        self.assertEqual(_chart_price_history(history), [
            {"date": "2026-10-08", "close": 118.0, "volume": 1000.0},
            {"date": "2026-10-09", "close": 119.0, "volume": 1100.0},
        ])

    def test_chart_price_history_omits_nonpositive_closes_and_unavailable_negative_volume(self):
        day = pd.Timestamp("2026-10-09")
        history = pd.DataFrame(
            {"Close": [10.0, 0.0, -1.0], "Volume": [100, -1, -2]},
            index=[day - pd.Timedelta(2, unit="D"), day - pd.Timedelta(1, unit="D"), day],
        )

        self.assertEqual(
            _chart_price_history(history),
            [{"date": "2026-10-07", "close": 10.0, "volume": 100.0}],
        )

    def test_legacy_info_property_is_used_only_when_method_is_absent(self):
        class LegacyInstrument:
            info = {"quoteType": "EQUITY"}

        self.assertEqual(_fetch_info(LegacyInstrument()), {"quoteType": "EQUITY"})

    def test_ticker_validation_rejects_indices_and_injection(self):
        for ticker in (
            "^GSPC", "AAPL/../", "AAPL;DROP", "BTC-USD", "14155550134",
            "415-555-0134", "123-45-6789", "A..B", "A--B", "A-.B", "A.", "A-",
            123, True, None,
        ):
            with self.subTest(ticker=ticker), self.assertRaises(ResearchError):
                validate_ticker(ticker)
        self.assertEqual(validate_ticker("brk.b"), "BRK.B")
        self.assertEqual(validate_ticker("rds-a"), "RDS-A")
        self.assertEqual(validate_ticker("700"), "700")
        self.assertEqual(validate_ticker("0700.HK"), "0700.HK")

    def test_company_analysis_rejects_contact_like_tickers_before_provider_access(self):
        for ticker in ("14155550134", "415-555-0134", "123-45-6789"):
            with self.subTest(ticker=ticker), patch("research_service.yf.Ticker") as provider:
                with self.assertRaises(ResearchError) as raised:
                    analyze_company(ticker)
                self.assertEqual(raised.exception.code, "personal_data_not_allowed")
                provider.assert_not_called()

    def test_empty_or_malformed_yahoo_company_metadata_is_retryable_not_not_found(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        for metadata in (None, [], {}, {"unexpected": "schema"}):
            instrument = MockEquity(fiscal_year)
            instrument.get_info = lambda metadata=metadata: metadata
            with self.subTest(metadata=metadata):
                with patch("research_service.yf.Ticker", return_value=instrument), patch(
                    "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
                ), self.assertRaises(ResearchError) as raised:
                    analyze_company("AAPL")
                self.assertEqual(raised.exception.code, "provider_unavailable")
                self.assertEqual(raised.exception.status, 503)

    def test_yahoo_throttle_is_retryable_and_does_not_leak_provider_details(self):
        class ThrottledEquity(MockEquity):
            def __init__(self, fiscal_year):
                super().__init__(fiscal_year)
                self.fallback_info_reads = 0

            def get_info(self):
                raise RuntimeError("429 Too Many Requests private-provider-detail")

            @property
            def info(self):
                self.fallback_info_reads += 1
                return super().get_info()

        instrument = ThrottledEquity(datetime.now(timezone.utc).year - 1)
        with patch("research_service.yf.Ticker", return_value=instrument), self.assertRaises(ResearchError) as raised:
            analyze_company("AAPL")
        self.assertEqual(raised.exception.code, "provider_unavailable")
        self.assertEqual(raised.exception.status, 503)
        self.assertNotIn("private-provider-detail", raised.exception.message)
        self.assertEqual(instrument.fallback_info_reads, 0)

    def test_malformed_optional_yahoo_statements_degrade_to_honest_partial_analysis(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.financials = {"unexpected": "schema"}
        instrument.balance_sheet = ["malformed"]
        instrument.cashflow = None
        instrument.revenue_estimate = {"wrong": "shape"}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")

        company = result["company"]
        self.assertEqual(company["price"], 120.0)
        self.assertIsNone(company["revenue"])
        self.assertIsNone(company["free_cash_flow"])
        self.assertIsNone(company["enterprise_value"])
        self.assertFalse(company["score_available"])
        self.assertTrue(any(
            flag["message"].startswith("TSRP Score is withheld: evidence coverage 0%")
            and "supported component weight 0%" in flag["message"]
            for flag in company["quality_flags"]
        ))

    def test_optional_yahoo_statement_failure_is_disclosed_separately_from_missing_data(self):
        class ThrottledIncomeEquity(MockEquity):
            @property
            def financials(self):
                raise RuntimeError("429 Too Many Requests private-provider-detail")

            @financials.setter
            def financials(self, value):
                self._financials = value

        instrument = ThrottledIncomeEquity(datetime.now(timezone.utc).year - 1)
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            result = analyze_company("AAPL")

        company = result["company"]
        self.assertIsNone(company["revenue"])
        self.assertTrue(any("income statements" in flag["message"] and "failed" in flag["message"] for flag in company["quality_flags"]))
        self.assertFalse(any("private-provider-detail" in flag["message"] for flag in company["quality_flags"]))

    def test_sec_facts_flow_through_active_analysis_with_amendment_provenance(self):
        import math

        fiscal_year = 2025
        as_of = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        instrument = MockEquity(fiscal_year)
        base_info = instrument.get_info()
        instrument.get_info = lambda: {**base_info, "regularMarketTime": as_of.timestamp()}

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return as_of if tz else as_of.replace(tzinfo=None)

        def annual_row(value, year, filed, *, start=True, form="10-K"):
            row = {
                "form": form,
                "fy": year,
                "end": f"{year}-12-31",
                "filed": filed,
                "val": value,
            }
            if start:
                row["start"] = f"{year}-01-01"
            return row

        def unit_facts(rows):
            return {"units": {"USD": rows}}

        sec_facts = {
            "entityName": "Example Corporation",
            "facts": {
                "us-gaap": {
                    "Revenues": unit_facts([
                        annual_row(800, 2023, "2024-02-15"),
                        annual_row(1000, 2024, "2025-02-15"),
                        annual_row(1150, 2025, "2026-02-15"),
                        annual_row(1200, 2025, "2026-03-01", form="10-K/A"),
                    ]),
                    "NetIncomeLoss": unit_facts([annual_row(100, 2025, "2026-02-15")]),
                    "NetCashProvidedByUsedInOperatingActivities": unit_facts([
                        annual_row(320, 2023, "2024-02-15"),
                        annual_row(400, 2024, "2025-02-15"),
                        annual_row(180, 2025, "2026-02-15"),
                    ]),
                    "PaymentsToAcquirePropertyPlantAndEquipment": unit_facts([
                        annual_row(-40, 2023, "2024-02-15"),
                        annual_row(-60, 2024, "2025-02-15"),
                        annual_row(-30, 2025, "2026-02-15"),
                    ]),
                    "Assets": unit_facts([annual_row(1000, 2025, "2026-02-15", start=False)]),
                    "StockholdersEquity": unit_facts([
                        annual_row(600, 2025, "2026-02-15", start=False),
                        annual_row(500, 2024, "2025-02-15", start=False),
                    ]),
                    "CashAndCashEquivalentsAtCarryingValue": unit_facts([annual_row(300, 2025, "2026-02-15", start=False)]),
                    "DebtCurrent": unit_facts([annual_row(100, 2025, "2026-02-15", start=False)]),
                    "LongTermDebtNoncurrent": unit_facts([annual_row(200, 2025, "2026-02-15", start=False)]),
                }
            },
        }
        current_debt = _sec_value(sec_facts, SEC_TAGS["debt_current"], ["USD"], kind="instant")
        long_debt = _sec_value(sec_facts, SEC_TAGS["debt_long"], ["USD"], kind="instant")
        self.assertEqual(current_debt["value"], 100.0)
        self.assertEqual(long_debt["value"], 200.0)
        self.assertEqual((current_debt["period"], current_debt["unit"]), (long_debt["period"], long_debt["unit"]))

        with patch("research_service.datetime", FrozenDateTime), patch(
            "research_service.yf.Ticker", return_value=instrument
        ), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available", "company": "Example Corporation", "cik": "0000000001"}),
        ) as sec_fetch:
            response = analyze_company("AAPL")

        sec_fetch.assert_called_once_with("AAPL", "N/A", "N/A")
        company = response["company"]
        self.assertEqual(company["sec_status"]["kind"], "available")
        self.assertEqual(company["revenue"], 1200.0)
        self.assertEqual(company["revenue_source"], "SEC EDGAR")
        self.assertEqual(company["revenue_period"], "2025-12-31")
        self.assertEqual(company["sources"]["revenue"]["filing_date"], "2026-03-01")
        self.assertEqual(company["sources"]["revenue"]["filing_form"], "10-K/A")
        self.assertEqual(company["sources"]["revenue"]["tag"], "Revenues")
        self.assertNotIn("series_details", company["sources"]["revenue"])

        elapsed_years = (datetime.fromisoformat("2025-12-31").date() - datetime.fromisoformat("2023-12-31").date()).days / 365.2425
        independent_growth = math.pow(1200 / 800, 1 / elapsed_years) - 1
        self.assertAlmostEqual(company["historical_growth"], independent_growth, places=10)
        self.assertEqual(company["revenue_growth_source"], "SEC EDGAR")
        history_provenance = company["sources"]["historical_growth"]
        self.assertEqual(history_provenance["source"], "SEC EDGAR")
        self.assertEqual(history_provenance["period"], "2023-12-31 to 2025-12-31")
        self.assertAlmostEqual(company["historical_growth_years"], elapsed_years)
        score_history = company["score_trace"]["growth_comparison_inputs"]
        self.assertIsNone(score_history["historical_growth"])
        self.assertIsNone(score_history["required_growth"])
        self.assertFalse(company["score_trace"]["growth_comparison_available"])
        self.assertEqual(company["methodology_version"], "score-v9")

        self.assertEqual(company["free_cash_flow"], 150.0)
        self.assertEqual(company["fcf_source"], "SEC EDGAR")
        self.assertEqual(company["fcf_period"], "2025-12-31")
        aligned_margins = [(320 - 40) / 800, (400 - 60) / 1000, (180 - 30) / 1200]
        independent_modeled_margin = 0.5 * aligned_margins[-1] + 0.5 * (sum(aligned_margins) / len(aligned_margins))
        self.assertAlmostEqual(company["fcf_margin"], aligned_margins[-1], places=12)
        self.assertAlmostEqual(company["model_fcf_margin"], independent_modeled_margin, places=12)
        self.assertEqual(company["model_fcf_note"], "blend of latest and multi-year average")
        self.assertEqual(company["sources"]["free_cash_flow"]["kind"], "calculated: annual SEC operating cash flow less capital spending")
        self.assertEqual(company["cash"], 300.0)
        self.assertEqual(company["debt"], 300.0)
        self.assertEqual(company["sources"]["debt"]["tag"], "current + noncurrent debt")
        self.assertEqual(
            [
                (item["tag"], item["value"], item["source_unit"], item["period"], item["filing_date"], item["filing_form"], item["taxonomy"])
                for item in company["sources"]["debt"]["components"]
            ],
            [
                ("DebtCurrent", 100.0, "USD", "2025-12-31", "2026-02-15", "10-K", "us-gaap"),
                ("LongTermDebtNoncurrent", 200.0, "USD", "2025-12-31", "2026-02-15", "10-K", "us-gaap"),
            ],
        )
        self.assertEqual(company["enterprise_value"], 2500.0)
        self.assertTrue(company["score_available"])
        self.assertIsNotNone(company["score_trace"])

    def test_unrepresentable_sec_debt_sum_falls_back_with_yahoo_period_and_source(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)

        def debt_fact(value):
            return {"units": {"USD": [{
                "form": "10-K",
                "fy": fiscal_year - 1,
                "end": f"{fiscal_year - 1}-12-31",
                "filed": f"{fiscal_year}-02-15",
                "val": value,
            }]}}

        sec_facts = {"facts": {"us-gaap": {
            "DebtCurrent": debt_fact(1e308),
            "LongTermDebtNoncurrent": debt_fact(1e308),
        }}}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
        ):
            result = analyze_company("AAPL")

        company = result["company"]
        self.assertEqual(company["debt"], 200.0)
        self.assertEqual(company["sources"]["debt"]["source"], "Yahoo Finance")
        self.assertEqual(company["sources"]["debt"]["period"], f"{fiscal_year}-12-31")
        self.assertIsNotNone(company["enterprise_value"])
        json.dumps(result, allow_nan=False)

    def test_sec_combined_cash_aggregate_including_restricted_cash_is_not_ev_cash(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        combined_cash = {
            "form": "10-K", "fy": fiscal_year, "fp": "FY",
            "end": f"{fiscal_year}-12-31", "filed": f"{fiscal_year + 1}-02-15", "val": 500,
        }
        sec_facts = {"facts": {"us-gaap": {
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": {
                "units": {"USD": [combined_cash]},
            },
        }}}

        self.assertIsNone(_sec_value(sec_facts, SEC_TAGS["cash"], ["USD"], kind="instant"))
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["cash"], 100.0)
        self.assertEqual(company["sources"]["cash"]["source"], "Yahoo Finance")
        # Independent: use Yahoo's unrestricted cash ($100) and total debt ($200),
        # not the SEC combined restricted-cash balance ($500): 2,500 + 200 - 100.
        self.assertEqual(company["enterprise_value"], 2600.0)

    def test_sec_long_term_only_fact_is_not_misused_as_total_debt(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        debt_fact = {
            "form": "10-K", "fy": fiscal_year, "fp": "FY",
            "end": f"{fiscal_year}-12-31", "filed": f"{fiscal_year + 1}-02-15", "val": 50,
        }

        for tag in ("LongTermDebt", "LongTermDebtAndFinanceLeaseObligations"):
            with self.subTest(tag=tag):
                sec_facts = {"facts": {"us-gaap": {tag: {"units": {"USD": [debt_fact]}}}}}
                with patch("research_service.yf.Ticker", return_value=MockEquity(fiscal_year)), patch(
                    "research_service._sec_facts",
                    return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
                ):
                    company = analyze_company("AAPL")["company"]

                self.assertEqual(company["debt"], 200.0)
                self.assertEqual(company["sources"]["debt"]["source"], "Yahoo Finance")
                # SEC reported a long-term debt measure only; use Yahoo's
                # explicit total debt of $200 for 2,500 + 200 - 100 = $2,600.
                self.assertEqual(company["enterprise_value"], 2600.0)

    def test_yahoo_long_term_debt_is_not_used_as_total_debt(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        period = f"{fiscal_year}-12-31"
        instrument = MockEquity(fiscal_year)
        dates = [period, f"{fiscal_year - 1}-12-31"]
        instrument.balance_sheet = pd.DataFrame(
            [[100, 90], [50, 45], [200, 190], [1500, 1400], [700, 650]],
            index=[
                "Cash And Cash Equivalents", "Current Debt",
                "Long Term Debt And Capital Lease Obligation", "Total Assets",
                "Stockholders Equity",
            ],
            columns=dates,
        )

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        # Independent arithmetic: $50 current + $200 long-term debt; EV=$2,500 market cap + $250 debt - $100 cash.
        self.assertEqual(company["debt"], 250.0)
        self.assertEqual(company["enterprise_value"], 2650.0)
        self.assertAlmostEqual(company["debt_to_assets"], 250 / 1500)
        self.assertEqual(company["sources"]["debt"]["period"], period)
        self.assertEqual(company["sources"]["debt"]["kind"], "calculated: Yahoo Finance current debt + long-term debt")
        self.assertEqual(
            [(item["tag"], item["value"]) for item in company["sources"]["debt"]["components"]],
            [("Current Debt", 50.0), ("Long Term Debt And Capital Lease Obligation", 200.0)],
        )

    def test_yahoo_long_term_debt_without_current_debt_remains_unavailable_as_total(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        period = f"{fiscal_year}-12-31"
        instrument = MockEquity(fiscal_year)
        instrument.balance_sheet = pd.DataFrame(
            [[100, 90], [200, 190], [1500, 1400], [700, 650]],
            index=[
                "Cash And Cash Equivalents", "Long Term Debt And Capital Lease Obligation",
                "Total Assets", "Stockholders Equity",
            ],
            columns=[period, f"{fiscal_year - 1}-12-31"],
        )

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertIsNone(company["debt"])
        self.assertIsNone(company["enterprise_value"])
        self.assertEqual(company["sources"]["debt"]["source"], "Unavailable")

    def test_same_unit_sec_facts_survive_when_currency_lacks_fx_and_dcf_calibration(self):
        as_of = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        fiscal_year = as_of.year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info["currency"] = "PLN"
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "WSE",
            "financialCurrency": "PLN", "currency": "PLN", "sector": "Technology",
            "regularMarketTime": as_of.timestamp(),
        }

        def fact(value, year, *, duration=True):
            row = {
                "form": "20-F", "fy": year, "fp": "FY", "end": f"{year}-12-31",
                "filed": f"{year + 1}-03-01", "val": value,
            }
            if duration:
                row["start"] = f"{year}-01-01"
            return row

        def unit(rows):
            return {"units": {"PLN": rows}}

        current, prior = fiscal_year, fiscal_year - 1
        sec_facts = {"entityName": "Example Corporation", "facts": {"us-gaap": {
            "Revenues": unit([fact(1150, current), fact(1000, prior)]),
            "NetIncomeLoss": unit([fact(100, current)]),
            "NetCashProvidedByUsedInOperatingActivities": unit([fact(180, current)]),
            "PaymentsToAcquirePropertyPlantAndEquipment": unit([fact(-30, current)]),
            "Assets": unit([fact(1000, current, duration=False)]),
            "StockholdersEquity": unit([fact(600, current, duration=False), fact(500, prior, duration=False)]),
            "CashAndCashEquivalentsAtCarryingValue": unit([fact(300, current, duration=False)]),
            "DebtCurrent": unit([fact(100, current, duration=False)]),
            "LongTermDebtNoncurrent": unit([fact(200, current, duration=False)]),
        }}}

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return as_of if tz else as_of.replace(tzinfo=None)

        with patch("research_service.datetime", FrozenDateTime), patch(
            "research_service.yf.Ticker", return_value=instrument
        ), patch("research_service._sec_facts", return_value=(sec_facts, {"kind": "available", "label": "Company facts available"})):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["revenue"], 1150.0)
        self.assertEqual(company["sources"]["revenue"]["source_unit"], "PLN")
        self.assertEqual(company["free_cash_flow"], 150.0)
        self.assertEqual(company["sources"]["free_cash_flow"]["filing_facts"]["operating_cash_flow"]["source_unit"], "PLN")
        self.assertAlmostEqual(company["fcf_margin"], 150 / 1150)
        self.assertIsNone(company["market_cap_reporting"])
        conversions = company["sources"]["fx"]["conversions"]
        self.assertEqual(len(conversions), 1)
        self.assertEqual(conversions[0]["period"], "Same-currency identity")
        self.assertFalse(company["currency_methodology_supported"])
        self.assertIsNone(company["enterprise_value"])
        self.assertIsNone(company["required_growth"])

    def test_assumption_validation_rejects_nonfinite_and_invalid_rate_pair(self):
        valid = {"starting_growth": .12, "fcf_margin": .18, "discount_rate": .09, "terminal_growth": .02}
        self.assertEqual(validate_assumptions(valid), valid)
        for invalid in (
            {**valid, "discount_rate": float("nan")},
            {**valid, "discount_rate": .02},
            {**valid, "fcf_margin": -.1},
            {**valid, "discount_rate": True},
            {**valid, "ignored": 1},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ResearchError):
                validate_assumptions(invalid)

    def test_fx_failure_does_not_relabel_or_return_value(self):
        with patch("research_service._fx_rate_detail", return_value=None):
            self.assertIsNone(_fx_rate("EUR", "USD"))
            self.assertIsNone(_convert(100, "EUR", "USD"))
        self.assertIsNone(_fx_rate("XYZ", "XYZ"))
        self.assertIsNone(_fx_rate("N/A", "N/A"))
        self.assertIsNone(_fx_rate("GBp", "GBP"))

    def test_same_currency_rate_is_explicit_identity(self):
        self.assertEqual(_fx_rate_detail("USD", "USD"), {"rate": 1.0, "pairs": [], "period": "Same-currency identity"})

    def test_fx_cross_rate_uses_consistent_usd_basis(self):
        period = datetime.now(timezone.utc).isoformat()
        with patch("research_service._rate_to_usd_detail", side_effect=[
            {"rate": .8, "pair": "EURUSD=X", "period": period, "direction": "pair quote"},
            {"rate": 1.25, "pair": "CADUSD=X", "period": period, "direction": "pair quote"},
        ]):
            self.assertAlmostEqual(_fx_rate("EUR", "CAD"), .64)

    def test_fx_cross_rate_combines_direct_and_inverse_usd_legs(self):
        period = datetime.now(timezone.utc).isoformat()
        usd_per_eur = 0.92
        jpy_per_usd = 150.0
        with patch("research_service._rate_to_usd_detail", side_effect=[
            {"rate": usd_per_eur, "pair": "EURUSD=X", "period": period, "direction": "pair quote"},
            {"rate": 1 / jpy_per_usd, "pair": "USDJPY=X", "period": period, "direction": "inverse pair quote"},
        ]):
            detail = _fx_rate_detail("EUR", "JPY")

        self.assertAlmostEqual(detail["rate"], jpy_per_usd * usd_per_eur)
        self.assertEqual(detail["pairs"], ["EURUSD=X", "USDJPY=X"])
        self.assertEqual(detail["directions"], ["pair quote", "inverse pair quote"])

    def test_legacy_conversion_wrapper_rejects_incomparable_cross_rate_legs(self):
        current = datetime.now(timezone.utc)
        stale = (current - timedelta(days=2)).isoformat()
        invalid_cross_rate = [
            {"rate": .8, "pair": "EURUSD=X", "period": current.isoformat(), "direction": "pair quote"},
            {"rate": 1.25, "pair": "CADUSD=X", "period": stale, "direction": "pair quote"},
        ]
        with patch("research_service._rate_to_usd_detail", side_effect=invalid_cross_rate * 2):
            self.assertIsNone(_fx_rate("EUR", "CAD"))
            self.assertIsNone(_convert(100, "EUR", "CAD"))

    def test_fx_rejects_unknown_same_currency_identity(self):
        self.assertIsNone(_fx_rate_detail("XYZ", "XYZ"))
        self.assertIsNone(_fx_rate_detail("PLN", "EUR"))

    def test_fx_detail_validates_direct_and_inverse_quotes(self):
        now = pd.Timestamp.now(tz="UTC")

        class Quote:
            def __init__(self, value):
                self.value = value

            def history(self, **_kwargs):
                if self.value is None:
                    return pd.DataFrame()
                return pd.DataFrame(
                    {"Close": [self.value - 0.05, self.value]},
                    index=[now - pd.Timedelta(1, unit="D"), now],
                ).iloc[::-1]

        with patch("research_service.yf.Ticker", return_value=Quote(1.10)):
            direct = _rate_to_usd_detail("EUR")
        self.assertEqual(direct["rate"], 1.10)
        self.assertEqual(direct["direction"], "pair quote")

        with patch("research_service.yf.Ticker", side_effect=lambda symbol, **_kwargs: Quote(None) if symbol == "JPYUSD=X" else Quote(.0067)):
            inverse = _rate_to_usd_detail("JPY")
        self.assertAlmostEqual(inverse["rate"], 1 / .0067)
        self.assertEqual(inverse["direction"], "inverse pair quote")

    def test_fx_detail_rejects_an_inverse_quote_that_overflows(self):
        now = pd.Timestamp.now(tz="UTC")

        class Quote:
            def __init__(self, value):
                self.value = value

            def history(self, **_kwargs):
                if self.value is None:
                    return pd.DataFrame()
                return pd.DataFrame({"Close": [self.value]}, index=[now])

        with patch("research_service.yf.Ticker", side_effect=lambda symbol, **_kwargs: Quote(None) if symbol == "JPYUSD=X" else Quote(5e-324)):
            self.assertIsNone(_rate_to_usd_detail("JPY"))

    def test_market_history_discards_invalid_dates_and_sorts_before_latest_selection(self):
        now = pd.Timestamp.now(tz="UTC").normalize()
        raw = pd.DataFrame(
            {"Close": [120.0, 999.0, 118.0]},
            index=[now, "not-a-date", now - pd.Timedelta(1, unit="D")],
        )

        ordered = _chronological_history(raw)

        self.assertEqual(list(ordered["Close"]), [118.0, 120.0])
        self.assertEqual(_quote_timestamp({}, {}, raw), now.isoformat())

    def test_market_history_does_not_use_future_bars_for_price_or_chart(self):
        today = pd.Timestamp.now(tz="UTC").normalize()
        tomorrow = today + pd.Timedelta(1, unit="D")
        raw = pd.DataFrame(
            {"Close": [118.0, 120.0, 999.0], "Volume": [1000, 1100, 9999]},
            index=[today - pd.Timedelta(1, unit="D"), today, tomorrow],
        )

        chart = _chart_price_history(raw)
        quote_time = pd.Timestamp(_quote_timestamp({}, {}, raw))

        self.assertEqual([point["close"] for point in chart], [118.0, 120.0])
        self.assertLessEqual(quote_time, pd.Timestamp.now(tz="UTC") + pd.Timedelta(5, unit="m"))

    def test_chronological_history_preserves_exchange_local_trading_date(self):
        local_latest = pd.Timestamp.now(tz="Asia/Tokyo").normalize()
        local_previous = local_latest - pd.Timedelta(1, unit="D")
        raw = pd.DataFrame(
            {"Close": [120.0, 118.0]},
            index=[local_latest, local_previous],
        )

        ordered = _chronological_history(raw)

        self.assertEqual(list(ordered["Close"]), [118.0, 120.0])
        self.assertEqual(ordered.index[-1].date(), local_latest.date())

    def test_fx_rejects_stale_quote_and_incomparable_cross_dates(self):
        stale = pd.Timestamp.now(tz="UTC") - pd.Timedelta(4, unit="D")
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

    def test_fx_cross_rate_requires_exact_timestamp_comparability(self):
        def quotes(first_period, second_period):
            return [
                {"rate": 1.1, "pair": "EURUSD=X", "period": first_period, "direction": "pair quote"},
                {"rate": .8, "pair": "CADUSD=X", "period": second_period, "direction": "pair quote"},
            ]

        cases = (
            # Adjacent UTC dates can still be almost 48 hours apart.
            ("2026-10-08T00:00:00+00:00", "2026-10-09T23:59:00+00:00", None, None),
            # The explicit elapsed-time boundary is allowed; one second over is not.
            ("2026-10-08T00:00:00+00:00", "2026-10-09T00:00:00+00:00", 1.1 / .8, ["2026-10-08T00:00:00+00:00", "2026-10-09T00:00:00+00:00"]),
            ("2026-10-08T00:00:00+00:00", "2026-10-09T00:00:01+00:00", None, None),
            # Equivalent instants with distinct offsets remain comparable.
            ("2026-10-08T12:00:00+00:00", "2026-10-08T14:00:00+02:00", 1.1 / .8, ["2026-10-08T12:00:00+00:00", "2026-10-08T12:00:00+00:00"]),
            ("not-a-date", "2026-10-08T00:00:00+00:00", None, None),
            (None, "2026-10-08T00:00:00+00:00", None, None),
        )
        self.assertEqual(MAX_FX_CROSS_OBSERVATION_GAP_SECONDS, 24 * 60 * 60)
        for first, second, expected, expected_leg_periods in cases:
            with self.subTest(first=first, second=second), patch(
                "research_service._rate_to_usd_detail",
                side_effect=quotes(first, second),
            ):
                actual = _fx_rate_detail("EUR", "CAD")
                if expected is None:
                    self.assertIsNone(actual)
                else:
                    self.assertAlmostEqual(actual["rate"], expected)
                    self.assertEqual(actual["leg_periods"], expected_leg_periods)

    def test_market_fx_alignment_uses_the_market_quote_timestamp(self):
        market_at = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        cases = (
            (market_at - timedelta(hours=24), True),
            (market_at - timedelta(hours=24, seconds=1), False),
            (market_at + timedelta(minutes=5), True),
            (market_at + timedelta(minutes=5, seconds=1), False),
        )
        for fx_at, expected in cases:
            with self.subTest(fx_at=fx_at):
                detail = {"rate": 0.9, "leg_periods": [fx_at.isoformat()], "period": fx_at.isoformat()}
                self.assertEqual(_fx_detail_aligned_to_market_timestamp(detail, market_at.isoformat()), expected)
        self.assertFalse(_fx_detail_aligned_to_market_timestamp(
            {"rate": 0.9, "leg_periods": [None], "period": market_at.isoformat()},
            market_at.isoformat(),
        ))
        self.assertFalse(_fx_detail_aligned_to_market_timestamp(
            {"rate": 0.9, "period": market_at.isoformat()}, None,
        ))

    def test_fx_throttle_failure_produces_no_conversion_rate(self):
        with patch("research_service.yf.Ticker", side_effect=RuntimeError("429 Too Many Requests")):
            self.assertIsNone(_fx_rate_detail("EUR", "CAD"))

    def test_statement_age_has_explicit_future_and_stale_boundaries(self):
        now = datetime(2026, 10, 7, tzinfo=timezone.utc)
        self.assertEqual(_period_age_days("2025-04-05", now), 550)
        self.assertEqual(_period_age_days("2028-01-01", now), -451)
        self.assertIsNone(_period_age_days("not-a-date", now))

    def test_perioded_financial_value_includes_freshness_limit_but_rejects_older_or_future_data(self):
        as_of = datetime(2026, 10, 7, tzinfo=timezone.utc)
        self.assertEqual(
            perioded_financial_value(125.0, "2025-04-05", as_of=as_of),
            (125.0, "2025-04-05"),
        )
        self.assertEqual(
            perioded_financial_value(125.0, "2025-04-04", as_of=as_of),
            (None, None),
        )
        self.assertEqual(
            perioded_financial_value(125.0, "2026-10-08", as_of=as_of),
            (None, None),
        )

    def test_stale_statement_values_are_disclosed_but_excluded_from_current_score_and_valuation(self):
        as_of = datetime(2026, 10, 15, tzinfo=timezone.utc)
        instrument = MockEquity(2024)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return as_of if tz else as_of.replace(tzinfo=None)

        with patch("research_service.datetime", FrozenDateTime), patch(
            "research_service.yf.Ticker", return_value=instrument
        ), patch(
            "research_service._sec_facts",
            return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"}),
        ):
            company = analyze_company("AAPL")["company"]

        # Stale reported amounts may remain visible with dates, but cannot flow
        # into current margins, price-implied growth, or the TSRP Score.
        self.assertEqual((company["revenue"], company["revenue_period"]), (1000.0, "2024-12-31"))
        self.assertEqual((company["free_cash_flow"], company["fcf_period"]), (180.0, "2024-12-31"))
        self.assertIsNone(company["fcf_margin"])
        self.assertIsNone(company["required_growth"])
        self.assertIsNone(company["enterprise_value"])
        self.assertFalse(company["score_available"])
        messages = [item["message"] for item in company["quality_flags"]]
        self.assertTrue(any("annual revenue period" in message and "more than 18 months old" in message for message in messages))
        self.assertTrue(any("free-cash-flow period" in message and "more than 18 months old" in message for message in messages))
        self.assertTrue(any("balance-sheet period" in message and "more than 18 months old" in message for message in messages))

    def test_future_statement_filter_preserves_duplicate_period_columns(self):
        frame = pd.DataFrame(
            [[10, 12, 99]],
            columns=["2025-12-31", "2025-12-31", "2027-12-31"],
        )

        filtered, found_future = _exclude_future_statement_periods(
            frame, datetime(2026, 10, 14, tzinfo=timezone.utc)
        )

        self.assertTrue(found_future)
        self.assertEqual(list(filtered.columns), ["2025-12-31", "2025-12-31"])
        self.assertEqual(filtered.to_numpy().tolist(), [[10, 12]])

    def test_sec_fcf_requires_matching_period_unit_and_duration(self):
        facts = {"ocf": {"value": 20, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"},
                 "capex": {"value": -5, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"}}
        def choose_fact(_facts, tags, _currencies):
            return facts["ocf"] if tags == SEC_TAGS["ocf"] else facts["capex"]

        with patch("research_service._sec_value", side_effect=choose_fact):
            revenue = {"value": 100, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"}
            self.assertEqual(_sec_fcf({}, ["USD"], revenue), (15, "2024-12-31"))

            mismatches = (
                ("revenue", "period", "2023-12-31"),
                ("revenue", "start", "2023-01-01"),
                ("revenue", "unit", "EUR"),
                ("ocf", "start", "2023-01-01"),
                ("ocf", "unit", "EUR"),
                ("capex", "period", "2023-12-31"),
                ("capex", "start", "2023-01-01"),
                ("capex", "unit", "EUR"),
            )
            for target, field, incompatible_value in mismatches:
                with self.subTest(target=target, field=field):
                    record = revenue if target == "revenue" else facts[target]
                    original_value = record[field]
                    try:
                        record[field] = incompatible_value
                        self.assertEqual(_sec_fcf({}, ["USD"], revenue), (None, None))
                    finally:
                        record[field] = original_value

    def test_sec_fcf_margin_history_excludes_unmatched_period_starts_and_units(self):
        def annual(value, year, *, start=None, unit="USD"):
            return {
                "form": "10-K", "fy": year, "fp": "FY",
                "start": start or f"{year}-01-01", "end": f"{year}-12-31",
                "filed": f"{year + 1}-02-15", "val": value,
            }

        facts = {"facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [annual(1200, 2025), annual(1000, 2024)]}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                annual(180, 2025), annual(400, 2024, start="2024-02-01"),
            ]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {
                "USD": [annual(-30, 2025)], "EUR": [annual(-60, 2024, unit="EUR")],
            }},
        }}}
        _, _, revenue_record = _sec_candidates(facts, SEC_TAGS["revenue"], ["USD"])

        margins = _sec_fcf_margin_history(facts, revenue_record)

        self.assertEqual(margins, [0.125])

    def test_sec_fcf_rejects_finite_inputs_that_overflow_and_remains_json_safe(self):
        facts = {
            "ocf": {"value": -1e308, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"},
            "capex": {"value": 1e308, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"},
        }
        revenue = {"value": 100, "period": "2024-12-31", "start": "2024-01-01", "unit": "USD"}

        def choose_fact(_facts, tags, _currencies):
            return facts["ocf"] if tags == SEC_TAGS["ocf"] else facts["capex"]

        with patch("research_service._sec_value", side_effect=choose_fact):
            fcf, period = _sec_fcf({}, ["USD"], revenue)

        self.assertIsNone(fcf)
        self.assertIsNone(period)
        self.assertEqual(json.dumps({"free_cash_flow": fcf}, allow_nan=False), '{"free_cash_flow": null}')

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

    def test_sec_conflicting_revenue_aliases_exclude_only_ambiguous_period(self):
        current_year = datetime.now(timezone.utc).year - 1
        prior_year = current_year - 1

        def annual(value, year, accession):
            return {
                "form": "10-K", "fy": year, "fp": "FY",
                "start": f"{year}-01-01", "end": f"{year}-12-31",
                "filed": f"{year + 1}-02-15", "accn": accession, "val": value,
            }

        current_accession = f"0000000001-{current_year + 1}-000001"
        prior_accession = f"0000000001-{prior_year + 1}-000001"
        aliases = {
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                annual(120, current_year, current_accession),
                annual(100, prior_year, prior_accession),
            ],
            "SalesRevenueNet": [
                annual(80, current_year, current_accession),
                annual(100, prior_year, prior_accession),
            ],
        }

        for tags in (tuple(aliases), tuple(reversed(aliases))):
            with self.subTest(tags=tags):
                facts = {"facts": {"us-gaap": {
                    tag: {"units": {"USD": rows}} for tag, rows in aliases.items()
                }}}
                series, unit, record = _sec_candidates(facts, tags, ["USD"])

                self.assertEqual(unit, "USD")
                self.assertEqual(record["value"], 100.0)
                self.assertEqual(record["period"], f"{prior_year}-12-31")
                self.assertEqual(series, [(f"{prior_year}-12-31", 100.0)])
                self.assertNotIn("_series_records", record)

    def test_active_analysis_falls_back_when_sec_revenue_aliases_conflict(self):
        as_of = datetime(datetime.now(timezone.utc).year, 10, 1, tzinfo=timezone.utc)
        fiscal_year = as_of.year - 1
        instrument = MockEquity(fiscal_year)
        original_info = instrument.get_info()
        instrument.get_info = lambda: {**original_info, "regularMarketTime": as_of.timestamp()}
        accession = f"0000000001-{fiscal_year + 1}-000001"

        def row(value):
            return {
                "form": "10-K", "fy": fiscal_year, "fp": "FY",
                "start": f"{fiscal_year}-01-01", "end": f"{fiscal_year}-12-31",
                "filed": f"{fiscal_year + 1}-02-15", "accn": accession, "val": value,
            }

        facts = {"entityName": "Example Corporation", "facts": {"us-gaap": {
            tag: {"units": {"USD": [row(value)]}}
            for tag, value in (
                ("RevenueFromContractWithCustomerExcludingAssessedTax", 1200),
                ("SalesRevenueNet", 800),
            )
        }}}

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return as_of if tz else as_of.replace(tzinfo=None)

        with patch("research_service.datetime", FrozenDateTime), patch(
            "research_service.yf.Ticker", return_value=instrument
        ), patch(
            "research_service._sec_facts",
            return_value=(facts, {"kind": "available", "label": "Company facts available"}),
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["revenue"], 1000.0)
        self.assertEqual(company["revenue_period"], f"{fiscal_year}-12-31")
        self.assertEqual(company["revenue_source"], "Yahoo Finance")

    def test_sec_unknown_currency_unit_is_not_selected_as_financial_data(self):
        year = datetime.now(timezone.utc).year - 1
        facts = {"facts": {"us-gaap": {"Revenues": {"units": {"XXX": [{
            "form": "10-K", "fy": year, "fp": "FY",
            "start": f"{year}-01-01", "end": f"{year}-12-31",
            "filed": f"{year + 1}-02-15", "val": 1150,
        }]}}}}}

        self.assertEqual(
            _sec_candidates(facts, SEC_TAGS["revenue"], ["N/A", "USD"], kind="duration"),
            ([], None, None),
        )
        self.assertEqual(
            _sec_candidates(
                facts, SEC_TAGS["revenue"], ["XXX"], kind="duration", allowed_units={"XXX"}
            ),
            ([], None, None),
        )

    def test_sec_prefers_newer_period_over_stale_preferred_currency_unit(self):
        current_year = datetime.now(timezone.utc).year - 1
        prior_year = current_year - 1

        def annual(value, year, filed):
            return {
                "form": "10-K", "fy": year, "fp": "FY",
                "start": f"{year}-01-01", "end": f"{year}-12-31",
                "filed": filed, "val": value,
            }

        facts = {"facts": {"us-gaap": {"Revenue": {"units": {
            "EUR": [annual(90, prior_year, f"{prior_year + 1}-02-15")],
            "USD": [
                annual(100, prior_year, f"{prior_year + 1}-02-15"),
                annual(120, current_year, f"{current_year + 1}-02-15"),
            ],
        }}}}}

        series, unit, record = _sec_candidates(facts, ("Revenue",), ["EUR", "USD"], kind="duration")

        self.assertEqual(unit, "USD")
        self.assertEqual(record["value"], 120.0)
        self.assertEqual(record["period"], f"{current_year}-12-31")
        self.assertEqual(series, [
            (f"{current_year}-12-31", 120.0),
            (f"{prior_year}-12-31", 100.0),
        ])

    def test_sec_currency_priority_breaks_same_period_tie(self):
        year = datetime.now(timezone.utc).year - 1
        row = {
            "form": "10-K", "fy": year, "fp": "FY",
            "start": f"{year}-01-01", "end": f"{year}-12-31",
            "filed": f"{year + 1}-02-15",
        }
        facts = {"facts": {"us-gaap": {"Revenue": {"units": {
            "EUR": [{**row, "val": 90}],
            "USD": [{**row, "val": 100}],
        }}}}}

        _series, unit, record = _sec_candidates(facts, ("Revenue",), ["EUR", "USD"], kind="duration")

        self.assertEqual(unit, "EUR")
        self.assertEqual(record["value"], 90.0)

    def test_sec_conflicting_duplicate_accession_is_unavailable_independent_of_row_order(self):
        current_year = datetime.now(timezone.utc).year - 1
        prior_year = current_year - 1

        def annual(value, year, filed):
            return {
                "form": "10-K", "fy": year, "fp": "FY",
                "start": f"{year}-01-01", "end": f"{year}-12-31",
                "filed": filed, "accn": f"0000000001-{year + 1}-000001", "val": value,
            }

        duplicate = annual(120, current_year, f"{current_year + 1}-02-15")
        conflicting = {**duplicate, "val": 121}
        prior = annual(100, prior_year, f"{prior_year + 1}-02-15")

        for latest_rows in ([duplicate, conflicting], [conflicting, duplicate]):
            with self.subTest(order=[row["val"] for row in latest_rows]):
                facts = {"facts": {"us-gaap": {"Revenue": {"units": {
                    "USD": [*latest_rows, prior],
                }}}}}
                series, unit, record = _sec_candidates(facts, ("Revenue",), ["USD"], kind="duration")

                self.assertEqual(unit, "USD")
                self.assertEqual(series, [(f"{prior_year}-12-31", 100.0)])
                self.assertEqual(record["value"], 100.0)

    def test_sec_identical_duplicate_accession_collapses_without_losing_period(self):
        year = datetime.now(timezone.utc).year - 1
        row = {
            "form": "10-K", "fy": year, "fp": "FY",
            "start": f"{year}-01-01", "end": f"{year}-12-31",
            "filed": f"{year + 1}-02-15", "accn": f"0000000001-{year + 1}-000001", "val": 120,
        }
        facts = {"facts": {"us-gaap": {"Revenue": {"units": {"USD": [row, dict(row)]}}}}}

        series, unit, record = _sec_candidates(facts, ("Revenue",), ["USD"], kind="duration")

        self.assertEqual(unit, "USD")
        self.assertEqual(series, [(f"{year}-12-31", 120.0)])
        self.assertEqual(record["value"], 120.0)

    def test_active_analysis_does_not_convert_sec_facts_with_unaligned_spot_fx(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        prior_year = fiscal_year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info["currency"] = "EUR"
        original_info = instrument.get_info()
        instrument.get_info = lambda: {
            **original_info, "financialCurrency": "EUR", "currency": "EUR",
        }

        def annual(value, year):
            return {
                "form": "10-K", "fy": year, "fp": "FY",
                "start": f"{year}-01-01", "end": f"{year}-12-31",
                "filed": f"{year + 1}-02-15", "val": value,
            }

        def instant(value, year):
            return {
                "form": "10-K", "fy": year, "fp": "FY",
                "end": f"{year}-12-31", "filed": f"{year + 1}-02-15", "val": value,
            }

        sec_facts = {"facts": {"us-gaap": {
            "Revenues": {"units": {
                "EUR": [annual(900, prior_year)],
                "USD": [annual(1000, prior_year), annual(1200, fiscal_year)],
            }},
            "Assets": {"units": {"USD": [instant(1500, fiscal_year)]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [instant(100, fiscal_year)]}},
            "DebtCurrent": {"units": {"USD": [instant(50, fiscal_year)]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [instant(150, fiscal_year)]}},
        }}}

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
        ), patch(
            "research_service._fx_rate_detail",
            side_effect=lambda source, target, *_args, **_kwargs: {
                "rate": 0.9 if (source, target) == ("USD", "EUR") else 1.0,
                "pairs": ["USD/EUR"], "directions": ["fixture"], "period": "2026-10-01",
            },
        ) as fx_lookup:
            result = analyze_company("AAPL")

        company = result["company"]
        # The only FX quote is dated months after the annual SEC period. It may
        # convert current market capitalization, but must not convert reported
        # annual revenue. Yahoo's independently reported amount is the fallback.
        self.assertEqual(company["revenue_source"], "Yahoo Finance")
        self.assertEqual(company["revenue_period"], f"{fiscal_year}-12-31")
        self.assertEqual(company["revenue"], 1000.0)
        # Same-currency market-cap normalization may use the identity helper;
        # no USD/EUR spot lookup is valid for the historical SEC facts.
        self.assertNotIn(("USD", "EUR"), [call.args[:2] for call in fx_lookup.call_args_list])
        self.assertEqual(company["cash"], 100.0)
        self.assertEqual(company["debt"], 200.0)
        self.assertEqual(company["assets"], 1500.0)
        self.assertEqual(company["sources"]["cash"]["source"], "Yahoo Finance")
        self.assertEqual(company["sources"]["debt"]["source"], "Yahoo Finance")
        self.assertTrue(any("Period-aligned FX is unavailable" in flag["message"] for flag in company["quality_flags"]))

    def test_newer_yahoo_annual_revenue_supersedes_older_sec_period(self):
        instrument = MockEquity(datetime.now(timezone.utc).year - 1)
        older_year = datetime.now(timezone.utc).year - 2
        older_fiscal_end = f"{older_year}-12-31"
        older_filing = f"{older_year + 1}-02-15"
        old_annual = {
            "form": "10-K", "fy": older_year, "fp": "FY",
            "start": f"{older_year}-01-01", "end": older_fiscal_end,
            "filed": older_filing,
        }
        old_instant = {
            "form": "10-K", "fy": older_year, "fp": "FY",
            "end": older_fiscal_end, "filed": older_filing,
        }
        sec_facts = {
            "facts": {
                "us-gaap": {
                    "Revenues": {"units": {"EUR": [{**old_annual, "val": 700}]}},
                    "Assets": {"units": {"USD": [{**old_instant, "val": 9000}]}},
                    "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [{**old_instant, "val": 8000}]}},
                    "DebtCurrent": {"units": {"USD": [{**old_instant, "val": 3000}]}},
                    "LongTermDebtNoncurrent": {"units": {"USD": [{**old_instant, "val": 4000}]}},
                },
            },
        }

        def reject_unneeded_fx(source, target, *_args, **_kwargs):
            if source != target:
                raise AssertionError("an unused older SEC fact must not trigger cross-currency FX requests")
            return {"rate": 1.0, "pairs": [], "directions": ["identity"], "period": "Same-currency identity"}

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
        ), patch(
            "research_service._fx_rate_detail",
            side_effect=reject_unneeded_fx,
        ):
            company = analyze_company("AAPL")["company"]

        latest_yahoo_period = f"{datetime.now(timezone.utc).year - 1}-12-31"
        self.assertEqual(company["revenue"], 1000.0)
        self.assertEqual(company["revenue_source"], "Yahoo Finance")
        self.assertEqual(company["revenue_period"], latest_yahoo_period)
        self.assertEqual(company["free_cash_flow"], 180.0)
        self.assertEqual(company["fcf_period"], latest_yahoo_period)
        self.assertIn("does not configure shared edge caching", company["freshness"])
        self.assertNotIn("cached at the edge", company["freshness"])
        self.assertEqual(company["cash"], 100.0)
        self.assertEqual(company["debt"], 200.0)
        self.assertEqual(company["assets"], 1500.0)
        self.assertEqual(company["enterprise_value"], 2600.0)

    def test_identical_duplicate_yahoo_gross_profit_rows_preserve_active_margin(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        gross_profit = instrument.financials.loc["Gross Profit"].copy()
        instrument.financials = pd.concat([
            instrument.financials,
            pd.DataFrame([gross_profit.to_list()], index=["Gross Profit"], columns=instrument.financials.columns),
        ])

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertAlmostEqual(company["gross_margin"], 0.7)
        self.assertEqual(company["sources"]["gross_margin"]["period"], f"{fiscal_year}-12-31")
        self.assertEqual(company["sources"]["gross_margin"]["source"], "Yahoo Finance annual income statement")

    def test_conflicting_duplicate_yahoo_gross_profit_does_not_create_misaligned_margin(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        gross_profit = instrument.financials.loc["Gross Profit"].copy()
        conflicting = gross_profit.copy()
        conflicting.iloc[0] += 10
        instrument.financials = pd.concat([
            instrument.financials,
            pd.DataFrame([conflicting.to_list()], index=["Gross Profit"], columns=instrument.financials.columns),
        ])

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertIsNone(company["gross_margin"])
        self.assertEqual(company["sources"]["gross_margin"]["source"], "Unavailable")

    def test_future_yahoo_statement_does_not_displace_current_sec_revenue(self):
        current_year = datetime.now(timezone.utc).year - 1
        future_year = datetime.now(timezone.utc).year + 1
        instrument = MockEquity(current_year)
        instrument.financials.insert(0, f"{future_year}-12-31", instrument.financials.iloc[:, 0] * 2)
        sec_facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [{
            "form": "10-K", "fy": current_year, "fp": "FY",
            "start": f"{current_year}-01-01", "end": f"{current_year}-12-31",
            "filed": f"{current_year + 1}-02-15", "val": 1000,
        }]}}}}}

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["revenue"], 1000.0)
        self.assertEqual(company["revenue_source"], "SEC EDGAR")
        self.assertEqual(company["revenue_period"], f"{current_year}-12-31")

    def test_future_yahoo_revenue_is_excluded_from_historical_growth_and_its_freshness(self):
        current_year = datetime.now(timezone.utc).year - 1
        future_year = datetime.now(timezone.utc).year + 1
        instrument = MockEquity(current_year)
        instrument.financials.insert(0, f"{future_year}-12-31", instrument.financials.iloc[:, 0] * 2)
        instrument.balance_sheet.insert(0, f"{future_year}-12-31", instrument.balance_sheet.iloc[:, 0] * 2)
        instrument.cashflow.insert(0, f"{future_year}-12-31", instrument.cashflow.iloc[:, 0] * 2)
        expected_growth, expected_years, expected_start, expected_end = history_cagr_periods([
            (f"{current_year}-12-31", 1000.0),
            (f"{current_year - 1}-12-31", 900.0),
            (f"{current_year - 2}-12-31", 800.0),
            (f"{current_year - 3}-12-31", 700.0),
        ])

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertAlmostEqual(company["historical_growth"], expected_growth)
        self.assertAlmostEqual(company["historical_growth_years"], expected_years)
        self.assertEqual(
            company["sources"]["historical_growth"]["period"],
            f"{expected_start} to {expected_end}",
        )
        self.assertEqual(company["sources"]["historical_growth"]["period"].split(" to ")[-1], f"{current_year}-12-31")
        self.assertEqual(company["revenue"], 1000.0)
        self.assertEqual(company["revenue_period"], f"{current_year}-12-31")
        self.assertEqual(company["free_cash_flow"], 180.0)
        self.assertEqual(company["fcf_period"], f"{current_year}-12-31")
        self.assertEqual(company["cash"], 100.0)
        self.assertEqual(company["debt"], 200.0)
        self.assertTrue(any("future-dated financial statement periods" in item["message"] for item in company["quality_flags"]))

    def test_fallback_growth_history_uses_its_own_source_period_for_freshness(self):
        instrument = MockEquity(datetime.now(timezone.utc).year - 1)
        instrument.financials = instrument.financials.iloc[:, :1]
        older_year = datetime.now(timezone.utc).year - 2
        sec_facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {
                "form": "10-K", "fy": year, "fp": "FY",
                "start": f"{year}-01-01", "end": f"{year}-12-31",
                "filed": f"{year + 1}-02-15", "val": value,
            }
            for year, value in ((older_year - 1, 800), (older_year, 900))
        ]}}}}}

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["revenue_source"], "Yahoo Finance")
        self.assertEqual(company["sources"]["historical_growth"]["period"], f"{older_year - 1}-12-31 to {older_year}-12-31")
        self.assertTrue(any(
            "Historical revenue growth is stale" in item["message"]
            and "excluded from scoring" in item["message"]
            for item in company["quality_flags"]
        ))

    def test_sec_same_day_amendment_current_value_matches_latest_accession_history(self):
        rows = [
            {"form": "10-K", "fy": 2025, "fp": "FY", "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-03-01", "accn": "0000000001-26-000001", "val": 100},
            {"form": "10-K/A", "fy": 2025, "fp": "FY", "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-03-01", "accn": "0000000001-26-000002", "val": 105},
        ]

        for ordered_rows in (rows, list(reversed(rows))):
            with self.subTest(first_accession=ordered_rows[0]["accn"]):
                facts = {"facts": {"us-gaap": {"Revenue": {"units": {"USD": ordered_rows}}}}}
                series, _unit, record = _sec_candidates(facts, ("Revenue",), ["USD"], kind="duration")
                self.assertEqual(record["value"], 105.0)
                self.assertEqual(record["form"], "10-K/A")
                self.assertEqual(series[0], ("2025-12-31", 105.0))

    def test_sec_flow_rejects_future_or_impossible_filing_dates(self):
        facts = {"facts": {"us-gaap": {"Revenue": {"units": {"USD": [
            {"form": "10-K", "fy": 2024, "fp": "FY", "start": "2024-01-01", "end": "2024-12-31", "filed": "2099-02-01", "val": 100},
            {"form": "10-K", "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2023-02-01", "val": 90},
        ]}}}}}
        series, unit, record = _sec_candidates(facts, ("Revenue",), ["USD"], kind="duration")
        self.assertEqual((series, unit, record), ([], None, None))

    def test_sec_flow_rejects_next_day_period_and_filing(self):
        future_end = datetime.now(timezone.utc).date() + timedelta(days=1)
        start = future_end - timedelta(days=365)
        row = {
            "form": "10-K", "fy": future_end.year, "fp": "FY",
            "start": start.isoformat(), "end": future_end.isoformat(),
            "filed": future_end.isoformat(), "val": 100,
        }
        facts = {"facts": {"us-gaap": {"Revenue": {"units": {"USD": [row]}}}}}

        result = _sec_candidates(facts, ("Revenue",), ["USD"], kind="duration")

        self.assertEqual(result, ([], None, None))

    def test_sec_entity_names_allow_legal_suffix_variation_but_not_different_issuer(self):
        self.assertTrue(_sec_names_match("The Apple Incorporated", "Apple, Inc."))
        self.assertFalse(_sec_names_match("Apple Inc.", "Pineapple Holdings Inc."))

    def test_sec_data_flow_uses_public_ticker_map_then_company_facts_by_cik(self):
        import threading

        user_agent = "TSRP Research Team <research@example.com>"
        now = [100.0]
        request_times = []

        def monotonic():
            return now[0]

        def sleep(delay):
            now[0] += delay

        responses = []

        def sec_get(*_args, **_kwargs):
            request_times.append(now[0])
            return responses.pop(0)

        class TickerMapResponse(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                return {"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}}

        class CompanyFactsResponse(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                return {"entityName": "Apple Inc.", "facts": {}}

        responses.extend([TickerMapResponse(), CompanyFactsResponse()])
        with patch.dict("os.environ", {"SEC_USER_AGENT": user_agent}), patch(
            "research_service._SEC_TICKER_MAP_CACHE", None
        ), patch("research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_TICKER_MAP_FAILURE_KIND", None
        ), patch("research_service._SEC_TICKER_MAP_FAILURE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_FACTS_INFLIGHT", {}
        ), patch("research_service._SEC_FACTS_LOCK", threading.Lock()), patch(
            "research_service._SEC_REQUEST_PACE_LOCK", threading.Lock()
        ), patch("research_service._SEC_NEXT_REQUEST_AT", 0.0), patch(
            "research_service.time.monotonic", side_effect=monotonic
        ), patch("research_service.time.sleep", side_effect=sleep), patch(
            "research_service._SEC_SESSION.get", side_effect=sec_get
        ) as sec_get_mock:
            facts, state = _sec_facts("AAPL", "USD", "USD")

        self.assertIsNotNone(facts)
        self.assertEqual(state["kind"], "available")
        self.assertEqual(sec_get_mock.call_count, 2)
        self.assertEqual(request_times, [100.0, 100.125])
        map_call, facts_call = sec_get_mock.call_args_list
        self.assertEqual(map_call.args[0], "https://www.sec.gov/files/company_tickers.json")
        self.assertEqual(facts_call.args[0], "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
        self.assertEqual(map_call.kwargs["headers"]["User-Agent"], user_agent)
        self.assertEqual(facts_call.kwargs["headers"]["User-Agent"], user_agent)

    def test_sec_request_starts_are_paced_and_respect_provider_deadline(self):
        import threading
        import research_service
        from research_service import _sec_get

        now = [100.0]
        sleeps = []

        def monotonic():
            return now[0]

        def sleep(delay):
            sleeps.append(delay)
            now[0] += delay

        with patch("research_service._SEC_REQUEST_PACE_LOCK", threading.Lock()), patch(
            "research_service._SEC_NEXT_REQUEST_AT", 0.0
        ), patch("research_service.time.monotonic", side_effect=monotonic), patch(
            "research_service.time.sleep", side_effect=sleep
        ), patch("research_service._SEC_SESSION.get") as sec_get:
            _sec_get("https://data.sec.gov/one")
            _sec_get("https://data.sec.gov/two")
            _sec_get("https://data.sec.gov/three")
        self.assertEqual(sleeps, [0.125, 0.125])
        self.assertEqual(sec_get.call_count, 3)

        now[0] = 200.0
        sleeps.clear()
        with patch("research_service._SEC_REQUEST_PACE_LOCK", threading.Lock()), patch(
            "research_service._SEC_NEXT_REQUEST_AT", 0.0
        ), patch("research_service.time.monotonic", side_effect=monotonic), patch(
            "research_service.time.sleep", side_effect=sleep
        ), patch.object(research_service._PROVIDER_REQUEST_CONTEXT, "deadline", 200.2, create=True), patch(
            "research_service._SEC_SESSION.get"
        ) as sec_get:
            _sec_get("https://data.sec.gov/one", timeout=10)
            _sec_get("https://data.sec.gov/two", timeout=10)
            with self.assertRaises(TimeoutError):
                _sec_get("https://data.sec.gov/three", timeout=10)
        self.assertEqual(sleeps, [0.125])
        self.assertEqual(sec_get.call_count, 2)
        self.assertAlmostEqual(sec_get.call_args_list[0].kwargs["timeout"], 0.2)
        self.assertAlmostEqual(sec_get.call_args_list[1].kwargs["timeout"], 0.075)

    def test_sec_request_dispatches_do_not_burst_under_concurrency(self):
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        from research_service import _sec_get

        request_times = []
        barrier = threading.Barrier(4)

        def sec_get(url, **_kwargs):
            request_times.append((url, time.monotonic()))
            return None

        def request(number):
            barrier.wait(timeout=2)
            return _sec_get(f"https://data.sec.gov/{number}")

        with patch("research_service._SEC_REQUEST_PACE_LOCK", threading.Lock()), patch(
            "research_service._SEC_NEXT_REQUEST_AT", 0.0
        ), patch("research_service._SEC_SESSION.get", side_effect=sec_get):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(request, range(4)))

        ordered = sorted(stamp for _url, stamp in request_times)
        self.assertEqual(len(ordered), 4)
        self.assertTrue(all(right - left >= 0.12 for left, right in zip(ordered, ordered[1:])))

    def test_sec_user_agent_requires_client_identity_and_one_contact(self):
        valid = "TSRP/1.0 Research Team <research@example.com>"
        invalid = (
            "",
            "TSRP research contact without an email",
            "research@example.com",
            "<research@example.com>",
            "TSRP research@example.com trailing text",
            "TSRP\r\nresearch@example.com",
            "TSRP research@example.com second@example.com",
            "X" * 301 + " research@example.com",
        )
        with patch.dict("os.environ", {"SEC_USER_AGENT": valid}):
            self.assertEqual(_sec_identity(), valid)
        for identity in invalid:
            with self.subTest(identity=identity[:48]), patch.dict("os.environ", {"SEC_USER_AGENT": identity}):
                self.assertIsNone(_sec_identity())

    def test_sec_missing_or_invalid_identity_fails_closed_before_network_access(self):
        for identity in ("", "TSRP research contact without an email", "research@example.com"):
            with self.subTest(identity=identity), patch.dict("os.environ", {"SEC_USER_AGENT": identity}), patch(
                "research_service._sec_ticker_map"
            ) as ticker_map, patch("research_service._SEC_SESSION.get") as sec_get:
                facts, state = _sec_facts("AAPL", "USD", "USD")

            self.assertIsNone(facts)
            self.assertEqual(state["kind"], "not_configured")
            ticker_map.assert_not_called()
            sec_get.assert_not_called()

    def test_sec_company_facts_must_match_sec_ticker_map_entity(self):
        class Response(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                return {"entityName": "Different Company Inc.", "facts": {}}

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP contact research@example.com"}), patch(
            "research_service._sec_ticker_map", return_value={"AAPL": ("0000000001", "Apple Inc.")}
        ), patch("research_service._SEC_SESSION.get", return_value=Response()):
            facts, state = _sec_facts("AAPL", "USD", "USD")
        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "entity_mismatch")

    def test_sec_company_facts_invalid_json_is_reported_as_parse_error(self):
        class Response(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("invalid JSON")

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service._sec_ticker_map",
            return_value={"AAPL": ("0000000320", "Apple Inc.")},
        ), patch("research_service._SEC_SESSION.get", return_value=Response()):
            facts, state = _sec_facts("AAPL", "USD", "USD")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "parse_error")

    def test_sec_company_facts_reject_nonstandard_numbers_and_duplicate_keys(self):
        class RawResponse:
            headers = {}

            def __init__(self, body):
                self.body = body
                self.closed = False

            def raise_for_status(self):
                return None

            def iter_content(self):
                yield self.body

            def close(self):
                self.closed = True

        malformed_bodies = (
            b'{"facts":{"us-gaap":{"Revenues":NaN}}}',
            b'{"facts":{"us-gaap":{"Revenues":Infinity}}}',
            b'{"facts":{"us-gaap":{"Revenues":1e9999}}}',
            b'{"facts":{"us-gaap":{}},"facts":{"us-gaap":{}}}',
            b'{"facts":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}',
        )
        for body in malformed_bodies:
            with self.subTest(body_prefix=body[:32], body_bytes=len(body)):
                response = RawResponse(body)
                with patch("research_service._SEC_SESSION.get", return_value=response):
                    facts, state = _fetch_sec_company_facts("0000000320", "TSRP test test@example.com")
                self.assertIsNone(facts)
                self.assertEqual(state["kind"], "parse_error")
                self.assertTrue(response.closed)

    def test_sec_company_facts_deadline_expiring_during_decode_returns_timeout(self):
        class RawResponse:
            headers = {}

            def raise_for_status(self):
                return None

            def iter_content(self):
                yield b'{"facts":{}}'

            def close(self):
                self.closed = True

        response = RawResponse()
        # The stream remains inside budget; decoding finishes after the deadline.
        with patch("research_service._sec_provider_deadline_expired", side_effect=(False, False, True)), patch(
            "research_service._SEC_SESSION.get", return_value=response
        ):
            facts, state = _fetch_sec_company_facts("0000000320", "TSRP test test@example.com")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "timeout")
        self.assertTrue(response.closed)

    def test_sec_ticker_map_invalid_json_is_parse_error_and_uses_typed_cooldown(self):
        class Response(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("invalid JSON")

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service._SEC_TICKER_MAP_CACHE", None
        ), patch("research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_TICKER_MAP_FAILURE_KIND", None
        ), patch("research_service._SEC_TICKER_MAP_FAILURE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_SESSION.get", return_value=Response()
        ) as get:
            first_facts, first_state = _sec_facts("AAPL", "USD", "USD")
            second_facts, second_state = _sec_facts("AAPL", "USD", "USD")

        self.assertEqual(get.call_count, 1)
        self.assertIsNone(first_facts)
        self.assertIsNone(second_facts)
        self.assertEqual(first_state["kind"], "parse_error")
        self.assertEqual(second_state["kind"], "parse_error")

    def test_sec_ticker_map_rejects_mixed_valid_and_malformed_rows_without_caching_partial_map(self):
        class Response(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "0": {"ticker": "AAPL", "cik_str": 320, "title": "Apple Inc."},
                    "1": {"ticker": "MSFT", "cik_str": 789019},
                }

        import research_service

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service._SEC_TICKER_MAP_CACHE", None
        ), patch("research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_TICKER_MAP_FAILURE_KIND", None
        ), patch("research_service._SEC_TICKER_MAP_FAILURE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_SESSION.get", return_value=Response()
        ) as get, patch(
            "research_service._coalesced_sec_company_facts",
            return_value=(
                {"entityName": "Apple Inc.", "facts": {}},
                {"kind": "available", "label": "Company facts available"},
            ),
        ):
            facts, state = _sec_facts("AAPL", "USD", "USD")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "parse_error")
        self.assertIsNone(research_service._SEC_TICKER_MAP_CACHE)
        get.assert_called_once()

    def test_sec_company_facts_rejects_oversized_declared_body_and_closes_response(self):
        class OversizedResponse(StreamingResponse):
            headers = {"Content-Length": str(MAX_SEC_COMPANY_FACTS_BYTES + 1)}
            closed = False

            def raise_for_status(self):
                return None

            def iter_content(self):
                raise AssertionError("oversized declared body must be rejected before reading")

            def close(self):
                self.closed = True

        response = OversizedResponse()
        with patch("research_service._SEC_SESSION.get", return_value=response) as get:
            facts, state = _fetch_sec_company_facts("0000000320", "TSRP test test@example.com")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "response_too_large")
        self.assertTrue(response.closed)
        self.assertTrue(get.call_args.kwargs["stream"])

    def test_sec_company_facts_rejects_oversized_stream_without_content_length(self):
        class OversizedStream(StreamingResponse):
            headers = {}
            closed = False

            def raise_for_status(self):
                return None

            def iter_content(self):
                yield b" " * MAX_SEC_COMPANY_FACTS_BYTES
                yield b" "

            def close(self):
                self.closed = True

        response = OversizedStream()
        with patch("research_service._SEC_SESSION.get", return_value=response):
            facts, state = _fetch_sec_company_facts("0000000320", "TSRP test test@example.com")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "response_too_large")
        self.assertTrue(response.closed)

    def test_sec_ticker_map_rejects_oversized_body_with_typed_unavailable_state(self):
        class OversizedResponse(StreamingResponse):
            headers = {"Content-Length": str(MAX_SEC_TICKER_MAP_BYTES + 1)}
            closed = False

            def raise_for_status(self):
                return None

            def iter_content(self):
                raise AssertionError("oversized declared body must be rejected before reading")

            def close(self):
                self.closed = True

        response = OversizedResponse()
        with (
            patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}),
            patch("research_service._SEC_TICKER_MAP_CACHE", None),
            patch("research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", 0.0),
            patch("research_service._SEC_TICKER_MAP_FAILURE_KIND", None),
            patch("research_service._SEC_TICKER_MAP_FAILURE_EXPIRES_AT", 0.0),
            patch("research_service._SEC_SESSION.get", return_value=response) as get,
        ):
            facts, state = _sec_facts("AAPL", "USD", "USD")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "response_too_large")
        self.assertTrue(response.closed)
        self.assertEqual(get.call_count, 1)

    def test_sec_company_facts_coalesces_concurrent_requests_without_retaining_response(self):
        import threading
        from concurrent.futures import Future, ThreadPoolExecutor

        class Response(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                return {"entityName": "Apple Inc.", "facts": {"us-gaap": {}}}

        fetch_started = threading.Event()
        allow_fetch_to_finish = threading.Event()
        all_followers_waiting = threading.Event()
        waiter_lock = threading.Lock()
        follower_count = 0

        class TrackingFuture(Future):
            def result(self, timeout=None):
                nonlocal follower_count
                with waiter_lock:
                    follower_count += 1
                    if follower_count == 7:
                        all_followers_waiting.set()
                return super().result(timeout)

        def fetch_facts(*_args, **_kwargs):
            fetch_started.set()
            allow_fetch_to_finish.wait(timeout=2)
            return Response()

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service.Future", TrackingFuture
        ), patch("research_service._SEC_FACTS_INFLIGHT", {}), patch(
            "research_service._SEC_FACTS_LOCK", threading.Lock()
        ), patch(
            "research_service._sec_ticker_map",
            return_value={"AAPL": ("0000000320", "Apple Inc.")},
        ), patch("research_service._SEC_SESSION.get", side_effect=fetch_facts) as get:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_sec_facts, "AAPL", "USD", "USD")]
                self.assertTrue(fetch_started.wait(timeout=2))
                futures.extend(pool.submit(_sec_facts, "AAPL", "USD", "USD") for _ in range(7))
                self.assertTrue(all_followers_waiting.wait(timeout=2))
                allow_fetch_to_finish.set()
                results = [future.result(timeout=2) for future in futures]

        self.assertEqual(get.call_count, 1)
        self.assertTrue(all(facts == results[0][0] for facts, _state in results))
        self.assertTrue(all(state["kind"] == "available" for _facts, state in results))
        self.assertEqual(results[0][1]["cik"], "0000000320")

        # A completed response is intentionally not cached: a later analysis gets fresh facts.
        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service._SEC_FACTS_INFLIGHT", {}
        ), patch(
            "research_service._SEC_FACTS_LOCK", threading.Lock()
        ), patch(
            "research_service._sec_ticker_map",
            return_value={"AAPL": ("0000000320", "Apple Inc.")},
        ), patch("research_service._SEC_SESSION.get", return_value=Response()) as next_get:
            facts, state = _sec_facts("AAPL", "USD", "USD")
        self.assertEqual(next_get.call_count, 1)
        self.assertEqual(state["kind"], "available")
        self.assertEqual(facts["entityName"], "Apple Inc.")

    def test_sec_company_facts_coalesces_concurrent_rate_limits(self):
        import threading
        from concurrent.futures import Future, ThreadPoolExecutor
        class Response(StreamingResponse):
            status_code = 429

            def raise_for_status(self):
                raise CurlHTTPError("rate limited", response=self)

        fetch_started = threading.Event()
        allow_fetch_to_finish = threading.Event()
        all_followers_waiting = threading.Event()
        waiter_lock = threading.Lock()
        follower_count = 0

        class TrackingFuture(Future):
            def result(self, timeout=None):
                nonlocal follower_count
                with waiter_lock:
                    follower_count += 1
                    if follower_count == 7:
                        all_followers_waiting.set()
                return super().result(timeout)

        def rate_limited(*_args, **_kwargs):
            fetch_started.set()
            allow_fetch_to_finish.wait(timeout=2)
            return Response()

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service.Future", TrackingFuture
        ), patch(
            "research_service._SEC_FACTS_INFLIGHT", {}
        ), patch("research_service._SEC_FACTS_LOCK", threading.Lock()), patch(
            "research_service._sec_ticker_map",
            return_value={"AAPL": ("0000000320", "Apple Inc.")},
        ), patch("research_service._SEC_SESSION.get", side_effect=rate_limited) as get:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_sec_facts, "AAPL", "USD", "USD")]
                self.assertTrue(fetch_started.wait(timeout=2))
                futures.extend(pool.submit(_sec_facts, "AAPL", "USD", "USD") for _ in range(7))
                self.assertTrue(all_followers_waiting.wait(timeout=2))
                allow_fetch_to_finish.set()
                results = [future.result(timeout=2) for future in futures]

        self.assertEqual(get.call_count, 1)
        self.assertTrue(all(facts is None and state["kind"] == "rate_limited" for facts, state in results))

    def test_sec_company_facts_share_fetch_but_validate_each_ticker_entity(self):
        import threading
        from concurrent.futures import Future, ThreadPoolExecutor

        class Response(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                return {"entityName": "Apple Inc.", "facts": {"us-gaap": {}}}

        fetch_started = threading.Event()
        allow_fetch_to_finish = threading.Event()
        follower_waiting = threading.Event()

        class TrackingFuture(Future):
            def result(self, timeout=None):
                follower_waiting.set()
                return super().result(timeout)

        def fetch_facts(*_args, **_kwargs):
            fetch_started.set()
            allow_fetch_to_finish.wait(timeout=2)
            return Response()

        mapping = {
            "AAPL": ("0000000320", "Apple Inc."),
            "APPLX": ("0000000320", "Unrelated Holdings Inc."),
        }
        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service.Future", TrackingFuture
        ), patch("research_service._SEC_FACTS_INFLIGHT", {}), patch(
            "research_service._SEC_FACTS_LOCK", threading.Lock()
        ), patch("research_service._sec_ticker_map", return_value=mapping), patch(
            "research_service._SEC_SESSION.get", side_effect=fetch_facts
        ) as get:
            with ThreadPoolExecutor(max_workers=2) as pool:
                apple = pool.submit(_sec_facts, "AAPL", "USD", "USD")
                self.assertTrue(fetch_started.wait(timeout=2))
                alias = pool.submit(_sec_facts, "APPLX", "USD", "USD")
                self.assertTrue(follower_waiting.wait(timeout=2))
                allow_fetch_to_finish.set()
                apple_result, alias_result = apple.result(timeout=2), alias.result(timeout=2)

        self.assertEqual(get.call_count, 1)
        self.assertIsNotNone(apple_result[0])
        self.assertEqual(apple_result[1]["kind"], "available")
        self.assertIsNone(alias_result[0])
        self.assertEqual(alias_result[1]["kind"], "entity_mismatch")

    def test_sec_ticker_map_is_cached_for_the_process_ttl_and_returned_as_a_copy(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        class Response(StreamingResponse):
            def raise_for_status(self):
                return None

            def json(self):
                return {"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}}

        fetch_started = threading.Event()
        allow_fetch_to_finish = threading.Event()

        def fetch_map(*_args, **_kwargs):
            fetch_started.set()
            allow_fetch_to_finish.wait(timeout=2)
            return Response()

        with patch("research_service._SEC_TICKER_MAP_CACHE", None), patch(
            "research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", 0.0
        ), patch("research_service._SEC_TICKER_MAP_FAILURE_KIND", None), patch(
            "research_service._SEC_TICKER_MAP_FAILURE_EXPIRES_AT", 0.0
        ), patch("research_service._SEC_SESSION.get", side_effect=fetch_map) as get:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_sec_ticker_map, "TSRP test test@example.com")]
                self.assertTrue(fetch_started.wait(timeout=2))
                futures.extend(pool.submit(_sec_ticker_map, "TSRP test test@example.com") for _ in range(7))
                allow_fetch_to_finish.set()
                results = [future.result(timeout=2) for future in futures]

        first = results[0]
        first["AAPL"] = ("9999999999", "tampered caller copy")
        second = results[1]

        self.assertEqual(get.call_count, 1)
        self.assertEqual(second, {"AAPL": ("0000320193", "Apple Inc.")})
        self.assertTrue(all(result == second for result in results[1:]))

    def test_sec_ticker_map_coalesces_concurrent_429_failures_for_short_cooldown(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        class Response(StreamingResponse):
            status_code = 429

            def raise_for_status(self):
                raise CurlHTTPError("rate limited", response=self)

        fetch_started = threading.Event()
        allow_fetch_to_finish = threading.Event()

        def rate_limited(*_args, **_kwargs):
            fetch_started.set()
            allow_fetch_to_finish.wait(timeout=2)
            return Response()

        with patch.dict("os.environ", {"SEC_USER_AGENT": "TSRP test test@example.com"}), patch(
            "research_service._SEC_TICKER_MAP_CACHE", None
        ), patch("research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_TICKER_MAP_FAILURE_KIND", None
        ), patch("research_service._SEC_TICKER_MAP_FAILURE_EXPIRES_AT", 0.0), patch(
            "research_service._SEC_SESSION.get", side_effect=rate_limited
        ) as get:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_sec_facts, "AAPL", "USD", "USD")]
                self.assertTrue(fetch_started.wait(timeout=2))
                futures.extend(pool.submit(_sec_facts, "AAPL", "USD", "USD") for _ in range(7))
                allow_fetch_to_finish.set()
                results = [future.result(timeout=2) for future in futures]

        self.assertEqual(get.call_count, 1)
        self.assertTrue(all(facts is None and state["kind"] == "rate_limited" for facts, state in results))

    def test_sec_instant_rejects_duration_facts(self):
        facts = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [
            {"form": "10-K", "fy": 2024, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01", "val": 999},
            {"form": "10-K", "fy": 2024, "end": "2024-12-31", "filed": "2025-02-01", "val": 500},
        ]}}}}}
        _, _, record = _sec_candidates(facts, ("Assets",), ["USD"], kind="instant")
        self.assertEqual(record["value"], 500)

    def test_sec_instant_rejects_year_only_period_and_filing_dates(self):
        rows = [
            {"form": "10-K", "fy": 2026, "end": "2026", "filed": "2026-02-15", "val": 600},
            {"form": "10-K/A", "fy": 2025, "end": "2025-12-31", "filed": "2026", "val": 700},
        ]
        facts = {"facts": {"us-gaap": {"Assets": {"units": {"USD": rows}}}}}

        _, _, record = _sec_candidates(facts, ("Assets",), ["USD"], kind="instant")

        self.assertIsNone(record)

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

    def test_provider_symbol_mismatch_is_rejected_before_company_data_is_returned(self):
        class MismatchedListing:
            fast_info = {}

            def get_info(self):
                return {
                    "symbol": "MSFT", "quoteType": "EQUITY", "longName": "Microsoft Corporation",
                    "financialCurrency": "USD", "currency": "USD", "sector": "Technology",
                }

        with patch("research_service.yf.Ticker", return_value=MismatchedListing()):
            with self.assertRaises(ResearchError) as result:
                analyze_company("AAPL")
        self.assertEqual(result.exception.code, "entity_mismatch")
        self.assertEqual(result.exception.status, 503)

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
        self.assert_score_has_no_price_comparison(company)
        self.assertFalse(company["currency_methodology_supported"])
        self.assertIsNone(company["discount_rate"])
        self.assertIsNone(company["terminal_growth"])
        self.assertIsNone(company["risk_free"])
        self.assertEqual(company["rate_currency"], "N/A")
        self.assertIsNone(company["assumptions"]["discount_rate"])
        self.assertIsNone(company["assumptions"]["terminal_growth"])
        calibration = company["sources"]["valuation_assumptions"]
        self.assertEqual(calibration["source"], "Unavailable")
        self.assertEqual(calibration["currency"], "N/A")
        self.assertIn("USD rates are not substituted", calibration["kind"])
        self.assertTrue(any("USD fallback is disclosed" in flag["message"] for flag in company["quality_flags"]))

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ), self.assertRaises(ResearchError) as raised:
            analyze_company("AAPL", {"starting_growth": 0.1, "fcf_margin": 0.15, "discount_rate": 0.1, "terminal_growth": 0.02})
        self.assertEqual(raised.exception.code, "unsupported_currency")

    def test_missing_currency_metadata_is_not_assumed_to_be_usd(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info.pop("currency", None)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "sector": "Technology", "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["reporting_currency"], "N/A")
        self.assertEqual(company["trading_currency"], "N/A")
        self.assertFalse(company["score_available"])
        self.assertIsNone(company["score"])
        self.assertIsNone(company["revenue"])
        self.assertIsNone(company["free_cash_flow"])
        self.assertIsNone(company["market_cap_reporting"])
        self.assertIsNone(company["enterprise_value"])
        self.assertIsNone(company["required_growth"])
        self.assertFalse(company["currency_methodology_supported"])
        self.assertIsNone(company["discount_rate"])
        self.assertIsNone(company["terminal_growth"])
        self.assertEqual(company["rate_currency"], "N/A")

    def test_quote_currency_does_not_substitute_for_missing_statement_currency(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "currency": "USD", "sector": "Technology",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["trading_currency"], "USD")
        self.assertEqual(company["reporting_currency"], "N/A")
        self.assertEqual(company["price"], 120.0)
        self.assertIsNone(company["revenue"])
        self.assertIsNone(company["revenue_period"])
        self.assertIsNone(company["historical_growth"])
        self.assertIsNone(company["market_cap_reporting"])
        self.assertIsNone(company["enterprise_value"])
        self.assertIsNone(company["required_growth"])
        self.assertFalse(company["score_available"])
        self.assertEqual(company["sources"]["revenue"]["source"], "Unavailable")
        self.assertTrue(any("statement currency" in flag["message"].lower() for flag in company["quality_flags"]))

    def test_sec_fact_unit_can_identify_reporting_currency_without_using_quote_currency(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info["currency"] = "EUR"
        instrument.fast_info.pop("marketCap", None)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "currency": "EUR", "sector": "Technology",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }

        def annual_revenue(year, value):
            return {
                "form": "10-K", "fy": year, "end": f"{year}-12-31",
                "filed": f"{year + 1}-02-15", "start": f"{year}-01-01", "val": value,
            }

        sec_facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            annual_revenue(fiscal_year - 1, 1000),
            annual_revenue(fiscal_year, 1150),
        ]}}}}}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(sec_facts, {"kind": "available", "label": "Company facts available"}),
        ):
            company = analyze_company("AAPL")["company"]
            scenario_company = analyze_company("AAPL", {
                "starting_growth": 0.1, "fcf_margin": 0.15, "discount_rate": 0.1, "terminal_growth": 0.02,
            })["company"]

        self.assertEqual(company["trading_currency"], "EUR")
        self.assertEqual(company["reporting_currency"], "USD")
        self.assertEqual(company["revenue"], 1150.0)
        self.assertEqual(company["revenue_source"], "SEC EDGAR")
        self.assertEqual(company["sources"]["revenue"]["source_unit"], "USD")
        self.assertIsNone(company["market_cap_reporting"])
        self.assertIsNone(company["enterprise_value"])
        self.assertEqual(scenario_company["reporting_currency"], "USD")

        ambiguous_facts = {"facts": {"us-gaap": {"Revenues": {"units": {
            "USD": [annual_revenue(fiscal_year, 1150)],
            "EUR": [annual_revenue(fiscal_year, 1100)],
        }}}}}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(ambiguous_facts, {"kind": "available", "label": "Company facts available"}),
        ):
            ambiguous_company = analyze_company("AAPL")["company"]
        self.assertEqual(ambiguous_company["reporting_currency"], "N/A")
        self.assertIsNone(ambiguous_company["revenue"])

    def test_provider_pool_returns_at_its_total_wait_budget(self):
        import threading
        import time
        started_event = threading.Event()
        finish = threading.Event()

        def slow_provider():
            started_event.set()
            finish.wait(timeout=1)

        call_started = time.monotonic()
        results, timed_out = _bounded_provider_calls({"slow": slow_provider}, timeout_seconds=0.01)
        elapsed = time.monotonic() - call_started
        self.assertEqual(results, {})
        self.assertEqual(timed_out, ["slow"])
        self.assertLess(elapsed, 0.15)
        self.assertTrue(started_event.wait(timeout=0.1))
        self.assertFalse(finish.is_set(), "A timed-out caller does not terminate a running provider thread")
        finish.set()

    def test_provider_exception_becomes_missing_data_not_a_success_value(self):
        results, timed_out = _bounded_provider_calls(
            {"broken": lambda: (_ for _ in ()).throw(RuntimeError("provider failed"))},
            timeout_seconds=0.2,
        )
        self.assertEqual(results, {"broken": None})
        self.assertEqual(timed_out, [])

    def test_provider_pool_caps_queued_and_running_work_and_releases_capacity(self):
        import threading
        from threading import BoundedSemaphore
        from research_service import _submit_provider_call

        gate = threading.Event()
        started = threading.Event()
        with patch("research_service._PROVIDER_SLOTS", BoundedSemaphore(1)):
            future = _submit_provider_call(lambda: (started.set(), gate.wait(timeout=1), "done")[-1])
            self.assertIsNotNone(future)
            self.assertTrue(started.wait(timeout=0.2))
            self.assertIsNone(_submit_provider_call(lambda: "must be rejected at capacity"))
            gate.set()
            self.assertEqual(future.result(timeout=1), "done")
            next_future = _submit_provider_call(lambda: "capacity released")
            self.assertIsNotNone(next_future)
            self.assertEqual(next_future.result(timeout=1), "capacity released")

    def test_market_screeners_have_an_isolated_small_pool_and_cannot_block_analysis(self):
        import threading
        from threading import BoundedSemaphore
        from research_service import (
            _cancel_market_provider_call,
            _submit_market_provider_call,
            _submit_provider_call,
        )

        gate = threading.Event()
        both_workers_started = threading.Event()
        state_lock = threading.Lock()
        started = 0

        def slow_screen():
            nonlocal started
            with state_lock:
                started += 1
                if started == 2:
                    both_workers_started.set()
            gate.wait(timeout=1)
            return "screen complete"

        with patch("research_service._MARKET_PROVIDER_SLOTS", BoundedSemaphore(3)):
            first = _submit_market_provider_call(slow_screen)
            second = _submit_market_provider_call(slow_screen)
            queued = _submit_market_provider_call(slow_screen)
            try:
                self.assertTrue(both_workers_started.wait(timeout=1))
                self.assertIsNone(_submit_market_provider_call(slow_screen), "the dedicated queue is capped")
                self.assertTrue(_cancel_market_provider_call(queued), "queued screen work can be canceled")
                replacement = _submit_market_provider_call(lambda: "replacement complete")
                self.assertIsNotNone(replacement)
                analysis = _submit_provider_call(lambda: "analysis remains available")
                self.assertIsNotNone(analysis)
                self.assertEqual(analysis.result(timeout=1), "analysis remains available")
            finally:
                gate.set()

            self.assertEqual(first.result(timeout=1), "screen complete")
            self.assertEqual(second.result(timeout=1), "screen complete")
            self.assertTrue(queued.cancelled())
            self.assertEqual(replacement.result(timeout=1), "replacement complete")

    def test_provider_capacity_rejection_is_retryable_not_a_ticker_not_found_404(self):
        from threading import BoundedSemaphore

        instrument = MockEquity(datetime.now(timezone.utc).year - 1)
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._PROVIDER_SLOTS", BoundedSemaphore(0)
        ), self.assertRaises(ResearchError) as raised:
            analyze_company("AAPL")
        self.assertEqual(raised.exception.code, "provider_unavailable")
        self.assertEqual(raised.exception.status, 503)

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

    def test_active_analysis_uses_newest_fcf_across_equivalent_yahoo_aliases(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        prior_period = f"{fiscal_year - 1}-12-31"
        current_period = f"{fiscal_year}-12-31"
        instrument.financials.loc["Total Revenue"] = [None, 100, 80, 60]
        instrument.financials.loc["Operating Revenue"] = [120, None, None, None]
        instrument.cashflow = pd.DataFrame(
            [[25, None], [None, 45], [-8, None], [None, -15]],
            index=[
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
                "Capital Expenditure",
                "Capital Expenditures",
            ],
            columns=[prior_period, current_period],
        )

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["revenue"], 120)
        self.assertEqual(company["revenue_period"], current_period)
        self.assertEqual(company["free_cash_flow"], 30)
        self.assertEqual(company["fcf_period"], current_period)
        self.assertEqual(company["fcf_margin"], 0.25)
        self.assertEqual(company["sources"]["free_cash_flow"]["period"], current_period)
        self.assertEqual(
            company["sources"]["free_cash_flow"]["kind"],
            "calculated: operating cash flow less capital spending",
        )

    def test_active_analysis_excludes_conflicting_revenue_alias_period(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        current_period = f"{fiscal_year}-12-31"
        prior_period = f"{fiscal_year - 1}-12-31"
        instrument.financials.loc["Operating Revenue"] = [1100, None, None, None]

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["revenue"], 900)
        self.assertEqual(company["revenue_period"], prior_period)
        self.assertNotEqual(company["revenue_period"], current_period)
        self.assertEqual(company["free_cash_flow"], 165)
        self.assertEqual(company["fcf_period"], prior_period)

    def test_active_analysis_marks_overflowed_yahoo_fcf_unavailable(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.cashflow = instrument.cashflow.astype(float)
        instrument.cashflow.loc["Operating Cash Flow"] = [-1e308] * 4
        instrument.cashflow.loc["Capital Expenditure"] = [1e308] * 4
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]
        self.assertIsNone(company["free_cash_flow"])
        self.assertIsNone(company["fcf_source"])
        self.assertEqual(company["sources"]["free_cash_flow"]["source"], "Unavailable")
        encoded = json.dumps(company, allow_nan=False)
        self.assertIn('"free_cash_flow": null', encoded)

    def test_active_analysis_does_not_solve_required_growth_from_negative_fcf(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.cashflow.loc["Operating Cash Flow"] = [-20, -15, -10, -5]
        instrument.cashflow.loc["Capital Expenditure"] = [-10, -10, -10, -10]

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertLess(company["free_cash_flow"], 0)
        self.assertLess(company["fcf_margin"], 0)
        self.assertTrue(company["model_fcf_refused"])
        self.assertIsNone(company["model_fcf_margin"])
        self.assertIsNone(company["required_growth"])
        self.assertIsNone(company["market_expectations"])
        self.assertTrue(any("Free cash flow is negative" in flag["message"] for flag in company["quality_flags"]))

    def test_financial_services_do_not_receive_generic_score_or_fcf_enterprise_valuation(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Financial Group", "fullExchangeName": "NYSE",
            "financialCurrency": "USD", "currency": "USD", "sector": "Financial Services",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]
        self.assertFalse(company["sector_methodology_supported"])
        self.assertEqual(company["methodology_version"], "score-v9")
        self.assertFalse(company["score_available"])
        self.assertIsNone(company["score"])
        self.assertEqual(company["score_label"], "Unavailable")
        self.assertIsNone(company["score_trace"])
        self.assertFalse(any("TSRP Score is withheld:" in flag["message"] for flag in company["quality_flags"]))
        self.assertTrue(any("No validated TSRP sector calibration" in flag["message"] for flag in company["quality_flags"]))
        self.assertIsNone(company["business_quality"])
        self.assertIsNone(company["financial_strength"])
        self.assertIsNone(company["enterprise_value"])
        self.assertIsNone(company["ev_sales"])
        self.assertIsNone(company["required_growth"])
        self.assertIsNone(company["model_fcf_margin"])
        self.assertIsNone(company["discount_rate"])
        self.assertIsNone(company["terminal_growth"])
        self.assertIsNone(company["risk_free"])
        self.assertIsNone(company["equity_risk_premium"])
        self.assertIsNone(company["sector_spread"])
        self.assertEqual(company["rate_currency"], "N/A")
        self.assertEqual(company["sources"]["valuation_assumptions"]["source"], "Unavailable")
        self.assertEqual(company["sources"]["valuation_assumptions"]["currency"], "N/A")
        self.assertIn("sector calibration", company["sources"]["valuation_assumptions"]["kind"])
        self.assertIsNone(company["assumptions"]["discount_rate"])
        self.assertIsNone(company["assumptions"]["terminal_growth"])
        self.assertEqual(company["revenue"], 1000)
        self.assertTrue(any("No validated TSRP sector calibration" in flag["message"] for flag in company["quality_flags"]))

        assumptions = {"starting_growth": 0.1, "fcf_margin": 0.15, "discount_rate": 0.1, "terminal_growth": 0.03}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ), self.assertRaises(ResearchError) as raised:
            analyze_scenario("AAPL", assumptions, scenario_basis(sector="Financial Services"))
        self.assertEqual(raised.exception.code, "unsupported_sector")

    def test_malformed_provider_sector_is_unconfigured_instead_of_crashing_analysis(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "financialCurrency": "USD", "currency": "USD", "sector": ["Technology"],
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            company = analyze_company("AAPL")["company"]

        self.assertEqual(company["sector"], "Unknown sector")
        self.assertFalse(company["sector_methodology_supported"])
        self.assertFalse(company["score_available"])
        self.assertIsNone(company["score"])
        self.assertIsNone(company["enterprise_value"])

    def test_scenario_uses_current_basis_without_fetching_providers(self):
        assumptions = {"starting_growth": 0.02, "fcf_margin": 0.18, "discount_rate": 0.09, "terminal_growth": 0.02}
        with patch("research_service.yf.Ticker") as ticker, patch("research_service._sec_facts") as sec:
            result = analyze_scenario("AAPL", assumptions, scenario_basis())
        ticker.assert_not_called()
        sec.assert_not_called()

        # Independent closed-form check: with growth equal to terminal growth
        # and a constant margin, each forecast cash flow grows by exactly 2%.
        cash_flows = [1000 * 0.18 * (1.02 ** year) for year in range(1, 11)]
        expected_ev = sum(flow / (1.09 ** year) for year, flow in enumerate(cash_flows, 1))
        expected_ev += cash_flows[-1] * 1.02 / (0.09 - 0.02) / (1.09 ** 10)
        self.assertAlmostEqual(result["enterprise_value"], expected_ev, places=8)
        self.assertAlmostEqual(result["equity_value"], expected_ev - 200 + 100, places=8)
        self.assertEqual(result["inputs"], assumptions)
        self.assertIn("source facts currently shown", result["kind"])

    def test_dcf_growth_and_margin_fade_match_independent_cash_flow_schedule(self):
        # Independently enumerate the ten annual cash flows and terminal value
        # for the documented 5-year growth hold and linear growth/margin fades.
        # This protects the engine from an off-by-one fade or discount-period bug.
        expected_enterprise_value = 2444.3618022911974
        actual = dcf_enterprise_value(
            1000.0, 0.08, 0.15, 0.10, 0.02,
            years=10, fade_start=5, start_margin=0.10,
        )
        self.assertAlmostEqual(actual, expected_enterprise_value, places=9)

    def test_default_scenario_reproduces_reverse_dcf_margin_path(self):
        revenue, latest_margin, modeled_margin = 1000.0, 0.10, 0.15
        discount, terminal, initial_growth = 0.10, 0.02, 0.08
        target_ev = dcf_enterprise_value(
            revenue, initial_growth, modeled_margin, discount, terminal,
            start_margin=latest_margin,
        )
        self.assertIsNotNone(target_ev)
        growth = solve_required_growth_detail(
            target_ev, revenue, modeled_margin, discount, terminal,
            start_margin=latest_margin,
        )["growth"]
        self.assertIsNotNone(growth)

        basis = scenario_basis(
            enterprise_value=target_ev,
            market_cap_reporting=target_ev - 100.0,
            starting_fcf_margin=latest_margin,
        )
        assumptions = {
            "starting_growth": growth,
            "fcf_margin": modeled_margin,
            "discount_rate": discount,
            "terminal_growth": terminal,
        }
        result = analyze_scenario("AAPL", assumptions, basis)

        self.assertAlmostEqual(result["enterprise_value"], target_ev, places=6)

    def test_scenario_rejects_stale_future_inconsistent_or_incomparable_basis(self):
        assumptions = {"starting_growth": 0.1, "fcf_margin": 0.15, "discount_rate": 0.1, "terminal_growth": 0.02}
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        cases = [
            (scenario_basis(market_at=old), "scenario_data_stale"),
            (scenario_basis(market_at=future), "scenario_data_stale"),
            (scenario_basis(enterprise_value=2500), "scenario_basis_inconsistent"),
            (scenario_basis(debt_period=f"{datetime.now(timezone.utc).year - 1}-09-30"), "scenario_period_mismatch"),
            (scenario_basis(cash_currency="EUR"), "scenario_currency_mismatch"),
            (scenario_basis(revenue=0), "scenario_unavailable"),
            (scenario_basis(revenue_period="2099-12-31"), "scenario_unavailable"),
            (scenario_basis(methodology_version="score-v3"), "scenario_unavailable"),
            (scenario_basis(methodology_version="score-v8"), "scenario_unavailable"),
        ]
        for basis, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ResearchError) as raised:
                    analyze_scenario("AAPL", assumptions, basis)
                self.assertEqual(raised.exception.code, code)

    def test_scenario_rejects_nonfinite_boolean_and_extra_basis_values(self):
        assumptions = {"starting_growth": 0.1, "fcf_margin": 0.15, "discount_rate": 0.1, "terminal_growth": 0.02}
        for basis in (scenario_basis(revenue=True), {**scenario_basis(), "debug": "unexpected"}):
            with self.subTest(basis=basis):
                with self.assertRaises(ResearchError) as raised:
                    analyze_scenario("AAPL", assumptions, basis)
                self.assertEqual(raised.exception.code, "scenario_unavailable")

    def test_scenario_rejects_finite_amounts_above_the_overflow_safety_limit(self):
        assumptions = {"starting_growth": 0.1, "fcf_margin": 0.15, "discount_rate": 0.1, "terminal_growth": 0.02}
        fields = ("revenue", "market_cap_reporting", "enterprise_value", "cash", "debt")
        for field in fields:
            with self.subTest(field=field):
                basis = scenario_basis(**{field: MAX_SCENARIO_FINANCIAL_AMOUNT * 1.01})
                with self.assertRaises(ResearchError) as raised:
                    analyze_scenario("AAPL", assumptions, basis)
                self.assertEqual(raised.exception.code, "scenario_unavailable")

    def test_sector_calibration_is_required_for_a_defensible_model(self):
        self.assertTrue(has_sector_calibration("Technology"))
        self.assertFalse(has_sector_calibration("Financial Services"))
        self.assertFalse(has_sector_calibration("Unknown sector"))

    def test_representative_companies_run_through_each_calibrated_sector_method(self):
        representatives = (
            ("AAPL", "Technology"),
            ("META", "Communication Services"),
            ("AMZN", "Consumer Cyclical"),
            ("COST", "Consumer Defensive"),
            ("CAT", "Industrials"),
            ("JNJ", "Healthcare"),
            ("XOM", "Energy"),
            ("LIN", "Basic Materials"),
            ("NEE", "Utilities"),
            ("PLD", "Real Estate"),
        )
        fiscal_year = datetime.now(timezone.utc).year - 1
        assumptions = {
            "starting_growth": 0.08,
            "fcf_margin": 0.15,
            "discount_rate": 0.09,
            "terminal_growth": 0.02,
        }

        for ticker, sector in representatives:
            with self.subTest(ticker=ticker, sector=sector):
                instrument = MockEquity(fiscal_year)
                instrument.get_info = lambda ticker=ticker, sector=sector: {
                    "symbol": ticker,
                    "quoteType": "EQUITY",
                    "longName": f"{ticker} deterministic sector fixture",
                    "fullExchangeName": "NYSE",
                    "financialCurrency": "USD",
                    "currency": "USD",
                    "sector": sector,
                    "regularMarketTime": datetime.now(timezone.utc).timestamp(),
                }
                with patch("research_service.yf.Ticker", return_value=instrument), patch(
                    "research_service._sec_facts",
                    return_value=(None, {"kind": "not_configured", "label": "SEC unavailable in fixture"}),
                ):
                    baseline = analyze_company(ticker)["company"]
                    scenario = analyze_company(ticker, assumptions)["company"]

                self.assertEqual(baseline["sector"], sector)
                self.assertTrue(baseline["sector_methodology_supported"])
                self.assertTrue(baseline["score_available"])
                self.assertIsInstance(baseline["score"], (int, float))
                self.assertGreaterEqual(baseline["score"], 0)
                self.assertLessEqual(baseline["score"], 100)
                self.assertIsNotNone(baseline["enterprise_value"])
                self.assertIsNotNone(baseline["revenue"])
                self.assertIsNotNone(baseline["free_cash_flow"])
                self.assertIsNotNone(scenario["scenario"]["enterprise_value"])
                self.assertAlmostEqual(scenario["score"], baseline["score"], places=10)

    def test_financial_services_company_facts_remain_visible_without_generic_score(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "symbol": "JPM",
            "quoteType": "EQUITY",
            "longName": "JPMorgan Chase deterministic sector fixture",
            "fullExchangeName": "NYSE",
            "financialCurrency": "USD",
            "currency": "USD",
            "sector": "Financial Services",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(None, {"kind": "not_configured", "label": "SEC unavailable in fixture"}),
        ):
            company = analyze_company("JPM")["company"]

        self.assertEqual(company["sector"], "Financial Services")
        self.assertIsNotNone(company["revenue"])
        self.assertFalse(company["sector_methodology_supported"])
        self.assertFalse(company["score_available"])
        self.assertIsNone(company["score"])
        self.assertIsNone(company["enterprise_value"])
        self.assertTrue(any("No validated TSRP sector calibration" in flag["message"] for flag in company["quality_flags"]))

    def test_company_search_uses_shared_provider_budget_and_filters_equities(self):
        class SearchResult:
            response = {"quotes": [
                {"symbol": "AAPL", "quoteType": "EQUITY", "longname": "Apple Inc."},
                {"symbol": "^GSPC", "quoteType": "INDEX", "longname": "S&P 500"},
            ]}
            quotes = response["quotes"]

        with patch("research_service.yf.Search", return_value=SearchResult()) as provider:
            self.assertEqual(search_companies("Apple"), [{"ticker": "AAPL", "company": "Apple Inc.", "exchange": "", "sector": ""}])
        provider.assert_called_once_with("Apple", max_results=12, timeout=10, session=_YAHOO_SESSION)

    def test_company_search_keeps_equities_among_yahoo_navigation_suggestions(self):
        class SearchResult:
            response = {"quotes": [
                {"symbol": "MSFT", "quoteType": "EQUITY", "longname": "Microsoft Corporation"},
                {"symbol": "SMSFT=F", "quoteType": "FUTURE"},
                {"symbol": "MSFY", "quoteType": "ETF"},
                {"symbol": None, "quoteType": None, "name": "Navigation suggestion"},
                {"name": "Search more results"},
                {"symbol": "MSFT.TO", "quoteType": "EQUITY", "shortname": "Microsoft CDR"},
            ]}

        with patch("research_service.yf.Search", return_value=SearchResult()):
            matches = search_companies("Microsoft")
        self.assertEqual([item["ticker"] for item in matches], ["MSFT", "MSFT.TO"])
        self.assertEqual(matches[0]["company"], "Microsoft Corporation")

    def test_company_search_preserves_valid_equities_when_another_equity_has_no_ticker(self):
        class SearchResult:
            response = {"quotes": [
                {"quoteType": "EQUITY", "longname": "Unidentified company"},
                {"symbol": "AAPL", "quoteType": "EQUITY", "longname": "Apple Inc."},
            ]}

        with patch("research_service.yf.Search", return_value=SearchResult()):
            self.assertEqual([item["ticker"] for item in search_companies("Apple")], ["AAPL"])

    def test_company_search_navigation_suggestions_alone_are_not_company_matches(self):
        class SearchResult:
            response = {"quotes": [{"name": "Search more results"}]}

        with patch("research_service.yf.Search", return_value=SearchResult()):
            self.assertEqual(search_companies("No matching company"), [])

    def test_company_search_rejects_email_phone_and_long_numbers_before_provider_access(self):
        for query in (
            "person@example.test",
            "+1 (415) 555-0134",
            "415/555/0134",
            "415‐555‐0134",
            "415‑555‑0134",
            "415–555–0134",
            "user＠example.com",
            "user@example。com",
            "user%40example%2Etest",
            "user%40example%2Etest%FF",
            "user%40example%2Etest%ZZ",
            "user%2540example%252Etest",
            "４１５／５５５／０１３４",
            "%34%31%35%2F%35%35%35%2F%30%31%33%34",
            "415\u200b555\u200b0134",
            "123-45-6789",
            "4111 1111 1111 1111",
            "١٢٣-٤٥-٦٧٨٩",
            "𝟡𝟡𝟡-𝟡𝟡-𝟡𝟡𝟡𝟡",
        ):
            with self.subTest(query=query), patch("research_service.yf.Search") as provider:
                with self.assertRaises(ResearchError) as raised:
                    search_companies(query)
                self.assertEqual(raised.exception.code, "personal_data_not_allowed")
                self.assertNotIn(query, raised.exception.message)
                provider.assert_not_called()

        self.assertEqual(validate_search("3M"), "3M")
        self.assertEqual(validate_search("8x8"), "8x8")
        self.assertEqual(validate_search("2026/10/14"), "2026/10/14")
        self.assertEqual(validate_search("C%2B%2B"), "C%2B%2B")

    def test_company_search_malformed_payload_is_unavailable_without_provider_log_data(self):
        class MalformedResponse:
            text = "{}"

            def json(self):
                raise json.JSONDecodeError("private provider payload", "{}", 0)

        capture = io.StringIO()
        root_logger = logging.getLogger()
        handler = logging.StreamHandler(capture)
        previous_level = root_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.WARNING)
        try:
            with patch("yfinance.data.YfData.cache_get", return_value=MalformedResponse()):
                with self.assertRaises(ResearchError) as raised:
                    search_companies("private query marker")
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(previous_level)

        self.assertEqual(raised.exception.code, "search_unavailable")
        self.assertEqual(raised.exception.status, 503)
        self.assertNotIn("private query marker", capture.getvalue())
        self.assertNotIn("private provider payload", capture.getvalue())

    def test_company_search_rejects_quote_rows_without_a_ticker(self):
        class SearchResult:
            response = {"quotes": [{"quoteType": "EQUITY", "longname": "Unidentified company"}]}
            quotes = []

        with patch("research_service.yf.Search", return_value=SearchResult()):
            with self.assertRaises(ResearchError) as raised:
                search_companies("Company")
        self.assertEqual(raised.exception.code, "search_unavailable")
        self.assertEqual(raised.exception.status, 503)

    def test_company_search_accepts_a_valid_empty_quote_list(self):
        class SearchResult:
            response = {"quotes": []}
            quotes = []

        with patch("research_service.yf.Search", return_value=SearchResult()):
            self.assertEqual(search_companies("No matches"), [])

    def test_company_search_reports_shared_provider_capacity_timeout(self):
        with patch("research_service._bounded_provider_calls", return_value=({}, ["search"])):
            with self.assertRaises(ResearchError) as raised:
                search_companies("Apple")
        self.assertEqual(raised.exception.code, "search_unavailable")

    def test_company_search_provider_failure_is_not_reported_as_no_matches(self):
        with patch("research_service.yf.Search", side_effect=RuntimeError("429 Too Many Requests private detail")):
            with self.assertRaises(ResearchError) as raised:
                search_companies("Apple")
        self.assertEqual(raised.exception.code, "search_unavailable")
        self.assertEqual(raised.exception.status, 503)
        self.assertNotIn("private detail", raised.exception.message)

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

    def test_analysis_uses_latest_chronological_bars_for_price_fallback(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        info = instrument.get_info()
        info.pop("regularMarketTime")
        instrument.get_info = lambda: info
        instrument.fast_info = {"marketCap": 2500.0, "currency": "USD"}
        today = pd.Timestamp.now(tz="Asia/Tokyo").normalize()
        instrument.history = lambda **_kwargs: pd.DataFrame(
            {
                "Close": [120.0, 999.0, 118.0],
                "Adj Close": [119.0, 9998.0, 117.0],
                "Volume": [1200, 9999, 1000],
            },
            index=[today, "not-a-date", today - pd.Timedelta(1, unit="D")],
        )

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            response = analyze_company("AAPL")
        result = response["company"]

        self.assertEqual(result["price"], 120.0)
        self.assertEqual(result["previous_close"], 118.0)
        chart_dates = [point["date"] for point in response["price_history"]]
        self.assertEqual(chart_dates, sorted(set(chart_dates)))
        self.assertTrue(all(datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value for value in chart_dates))
        self.assertEqual(chart_dates[-1], today.date().isoformat())
        self.assertEqual(response["price_history"][-1]["close"], 120.0)

    def test_analysis_ignores_future_bar_in_market_price_fallback(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        info = instrument.get_info()
        info.pop("regularMarketTime")
        instrument.get_info = lambda: info
        today = pd.Timestamp.now(tz="UTC").normalize()
        instrument.history = lambda **_kwargs: pd.DataFrame(
            {"Close": [118.0, 120.0, 999.0], "Adj Close": [117.0, 119.0, 998.0], "Volume": [1000, 1100, 9999]},
            index=[today - pd.Timedelta(2, unit="D"), today - pd.Timedelta(1, unit="D"), today + pd.Timedelta(1, unit="D")],
        )

        def run_fixture_provider_calls(calls, **_kwargs):
            return {name: call() for name, call in calls.items()}, []

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._bounded_provider_calls", side_effect=run_fixture_provider_calls
        ), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            response = analyze_company("AAPL")

        self.assertEqual(response["company"]["price"], 120.0)
        self.assertEqual(response["company"]["previous_close"], 118.0)
        self.assertEqual([point["close"] for point in response["price_history"]], [118.0, 120.0])

    def test_analysis_rejects_nonpositive_fallback_prices_without_poisoning_chart(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        info = instrument.get_info()
        info.pop("regularMarketTime")
        instrument.get_info = lambda: info
        instrument.fast_info = {"marketCap": 2500.0, "currency": "USD"}
        today = pd.Timestamp.now(tz="UTC").normalize()
        instrument.history = lambda **_kwargs: pd.DataFrame(
            {"Close": [118.0, -1.0], "Adj Close": [117.0, -1.0], "Volume": [1000, -5]},
            index=[today - pd.Timedelta(1, unit="D"), today],
        )

        def run_fixture_provider_calls(calls, **_kwargs):
            return {name: call() for name, call in calls.items()}, []

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._bounded_provider_calls", side_effect=run_fixture_provider_calls
        ), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            response = analyze_company("AAPL")

        self.assertIsNone(response["company"]["price"])
        self.assertEqual(response["company"]["previous_close"], 118.0)
        self.assertEqual(
            response["price_history"],
            [{"date": (today - pd.Timedelta(1, unit="D")).date().isoformat(), "close": 118.0, "volume": 1000.0}],
        )

    def test_market_cap_conversion_uses_listing_currency_not_financial_currency(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "financialCurrency": "EUR", "currency": "USD", "sector": "Technology",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        fx_stamp = datetime.now(timezone.utc).isoformat()
        fx = {"rate": 0.9, "pairs": ["USDEUR=X"], "directions": ["pair quote"], "leg_periods": [fx_stamp], "period": fx_stamp}
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
        self.assertEqual(reporting_source["conversions"][0]["leg_periods"], [fx_stamp])
        self.assertEqual(result["company"]["sources"]["fx"]["source"], "Yahoo Finance daily FX history")
        self.assertEqual(result["company"]["sources"]["fx"]["kind"], "timestamped conversion rates")
        self.assertTrue(any(call.args[0:2] == ("USD", "EUR") for call in fx_rate.call_args_list))

    def test_market_cap_fx_quote_freshness_is_checked_against_market_timestamp(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        market_at = datetime.now(timezone.utc)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "NASDAQ",
            "financialCurrency": "EUR", "currency": "USD", "sector": "Technology",
            "regularMarketTime": market_at.timestamp(),
        }
        # This FX quote is under the helper's 72-hour freshness limit, but it is
        # 36 hours older than the market quote and must not support current EV.
        fx_stamp = (market_at - timedelta(hours=36)).isoformat()
        fx = {"rate": 0.9, "pairs": ["USDEUR=X"], "directions": ["pair quote"], "leg_periods": [fx_stamp], "period": fx_stamp}
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ), patch("research_service._fx_rate_detail", return_value=fx):
            company = analyze_company("AAPL")["company"]

        self.assertFalse(company["is_stale"])
        self.assertIsNone(company["market_cap_reporting"])
        self.assertIsNone(company["enterprise_value"])
        self.assertEqual(company["sources"]["market_cap_reporting"]["source"], "Unavailable")
        market_cap_fx_flags = [flag["message"] for flag in company["quality_flags"] if "market capitalization" in flag["message"].lower()]
        self.assertEqual(len(market_cap_fx_flags), 1)
        self.assertIn("not aligned to the market timestamp", market_cap_fx_flags[0])

    def test_quote_subunit_market_cap_uses_exact_major_currency_scale(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info["currency"] = "GBp"
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "LSE",
            "financialCurrency": "GBP", "currency": "GBp", "sector": "Technology",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ), patch("research_service._fx_rate_detail") as fx_detail:
            result = analyze_company("EXM.L")

        fx_detail.assert_not_called()

        company = result["company"]
        self.assertEqual(company["price"], 120.0)
        self.assertEqual(company["trading_currency"], "GBp")
        self.assertEqual(company["sources"]["share_price"]["currency"], "GBp")
        self.assertEqual(company["market_cap_reporting"], 25.0)
        self.assertEqual(company["enterprise_value"], 125.0)
        self.assertEqual(company["sources"]["market_cap_reporting"]["source"], "Yahoo Finance + exact quote-unit scaling")
        fx_source = company["sources"]["fx"]
        self.assertEqual(fx_source["source"], "TSRP exact quote-unit scaling")
        self.assertEqual(fx_source["period"], "Fixed quote-subunit conversion")
        self.assertEqual(fx_source["kind"], "fixed quote-unit scale")
        conversion = company["sources"]["market_cap_reporting"]["conversions"][0]
        self.assertEqual((conversion["from"], conversion["to"], conversion["rate"]), ("GBp", "GBP", 0.01))
        self.assertEqual(conversion["period"], "Fixed quote-subunit conversion")
        self.assert_score_has_no_price_comparison(company)

    def test_quote_subunit_cross_currency_market_cap_scales_before_timestamped_fx(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info["currency"] = "GBp"
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "LSE",
            "financialCurrency": "EUR", "currency": "GBp", "sector": "Technology",
            "regularMarketTime": datetime.now(timezone.utc).timestamp(),
        }
        fx_stamp = datetime.now(timezone.utc).isoformat()
        gbp_eur_fx = {
            "rate": 0.9, "pairs": ["GBPEUR=X"], "directions": ["pair quote"],
            "leg_periods": [fx_stamp], "period": fx_stamp,
        }

        def fx_for_pair(source, target, *_args, **_kwargs):
            return gbp_eur_fx if (source, target) == ("GBP", "EUR") else None

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ), patch("research_service._fx_rate_detail", side_effect=fx_for_pair) as fx_detail:
            company = analyze_company("EXM.L")["company"]

        self.assertAlmostEqual(company["market_cap_reporting"], 22.5)
        self.assertTrue(any(call.args[:2] == ("GBP", "EUR") for call in fx_detail.call_args_list))
        conversions = company["sources"]["market_cap_reporting"]["conversions"]
        self.assertEqual(
            [(item["from"], item["to"], item["rate"], item["period"]) for item in conversions],
            [
                ("GBp", "GBP", 0.01, "Fixed quote-subunit conversion"),
                ("GBP", "EUR", 0.9, fx_stamp),
            ],
        )
        self.assertEqual(company["sources"]["fx"]["source"], "Yahoo Finance daily FX history + TSRP exact quote-unit scaling")

    def test_quote_subunit_cross_currency_market_cap_rejects_fx_not_aligned_to_market_time(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        instrument.fast_info["currency"] = "GBp"
        market_at = datetime.now(timezone.utc)
        instrument.get_info = lambda: {
            "quoteType": "EQUITY", "longName": "Example Corporation", "fullExchangeName": "LSE",
            "financialCurrency": "EUR", "currency": "GBp", "sector": "Technology",
            "regularMarketTime": market_at.timestamp(),
        }
        stale_fx = {
            "rate": 0.9, "pairs": ["GBPEUR=X"], "directions": ["pair quote"],
            "leg_periods": [(market_at - timedelta(hours=36)).isoformat()],
            "period": (market_at - timedelta(hours=36)).isoformat(),
        }

        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ), patch("research_service._fx_rate_detail", return_value=stale_fx):
            company = analyze_company("EXM.L")["company"]

        self.assertIsNone(company["market_cap_reporting"])
        self.assertIsNone(company["enterprise_value"])
        self.assertEqual(company["sources"]["market_cap_reporting"]["source"], "Unavailable")
        self.assertEqual(company["sources"]["fx"]["kind"], "Required currency conversion unavailable")
        self.assertEqual(company["sources"]["fx"]["period"], "No valid conversion used")
        self.assertTrue(any(
            "not aligned to the market timestamp" in flag["message"]
            for flag in company["quality_flags"]
        ))

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

    def test_stale_or_missing_market_timestamp_blocks_price_comparison_and_valuation(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        fresh_instrument = MockEquity(fiscal_year)
        with patch("research_service.yf.Ticker", return_value=fresh_instrument), patch(
            "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
        ):
            fresh_company = analyze_company("AAPL")["company"]
        self.assertFalse(fresh_company["is_stale"])
        self.assertTrue(fresh_company["score_available"])
        self.assertIsNotNone(fresh_company["enterprise_value"])

        stale_instrument = MockEquity(fiscal_year)
        stale_at = datetime.now(timezone.utc) - timedelta(hours=49)
        stale_info = stale_instrument.get_info()
        stale_info["regularMarketTime"] = stale_at.timestamp()
        stale_instrument.get_info = lambda: stale_info

        missing_time_instrument = MockEquity(fiscal_year)
        missing_time_info = missing_time_instrument.get_info()
        missing_time_info.pop("regularMarketTime")
        missing_time_instrument.get_info = lambda: missing_time_info
        missing_time_instrument.history = lambda **_kwargs: pd.DataFrame()

        for name, instrument in (
            ("stale", stale_instrument),
            ("missing timestamp", missing_time_instrument),
        ):
            with self.subTest(name=name), patch("research_service.yf.Ticker", return_value=instrument), patch(
                "research_service._sec_facts", return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"})
            ):
                company = analyze_company("AAPL")["company"]

            self.assertTrue(company["is_stale"])
            self.assertIsNone(company["enterprise_value"])
            self.assertIsNone(company["required_growth"])
            self.assert_score_has_no_price_comparison(company)
            self.assertTrue(any("stale or has no verified timestamp" in flag["message"] for flag in company["quality_flags"]))

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
        previous = compatible_snapshot()
        for version in ("score-v4", "score-v8"):
            with self.subTest(version=version):
                result = compare_snapshots(
                    resign_snapshot({**previous, "methodology_version": version}),
                    previous,
                )
                self.assertFalse(result["comparable"])
                self.assertEqual(result["reason"], "methodology_mismatch")

    def test_snapshot_diff_rejects_currency_and_schema_mismatches(self):
        base = compatible_snapshot()
        currency = compare_snapshots(base, resign_snapshot({**base, "reporting_currency": "EUR"}))
        schema = compare_snapshots(base, resign_snapshot({**base, "analysis_schema_version": "analysis-v2"}))
        self.assertEqual(currency["reason"], "currency_mismatch")
        self.assertEqual(schema["reason"], "schema_mismatch")

    def test_snapshot_comparison_exposes_only_verified_retrieval_timestamps(self):
        previous = compatible_snapshot(retrieved_at="2026-10-06T12:00:00+00:00")
        current = compatible_snapshot(retrieved_at="2026-10-07T15:30:00+00:00")

        result = compare_snapshots(previous, current)

        self.assertTrue(result["comparable"], result)
        self.assertEqual(result["previous_retrieved_at"], "2026-10-06T12:00:00+00:00")
        self.assertEqual(result["current_retrieved_at"], "2026-10-07T15:30:00+00:00")

        forged_current = {**current, "retrieved_at": "2026-10-08T15:30:00+00:00"}
        rejected = compare_snapshots(previous, forged_current)
        self.assertFalse(rejected["comparable"])
        self.assertEqual(rejected["reason"], "snapshot_unverified")

    def test_current_mock_analysis_round_trips_as_a_valid_snapshot(self):
        fiscal_year = datetime.now(timezone.utc).year - 1
        instrument = MockEquity(fiscal_year)
        with patch("research_service.yf.Ticker", return_value=instrument), patch(
            "research_service._sec_facts",
            return_value=(None, {"kind": "not_configured", "label": "SEC unavailable"}),
        ):
            company = analyze_company("AAPL")["company"]

        previous = {**company, "saved_at": datetime.now(timezone.utc).isoformat()}
        result = compare_snapshots(previous, company)

        self.assertTrue(company["score_available"])
        self.assertGreaterEqual(company["score"], 0)
        self.assertLessEqual(company["score"], 100)
        self.assertIsNotNone(company["snapshot_proof"])
        self.assertEqual(company["sources"]["fx"]["source"], "No conversion required")
        self.assertEqual(company["sources"]["fx"]["period"], "No conversion used")
        self.assertTrue(result["comparable"], result)
        self.assertEqual(result["changes"], [])

    def test_snapshot_reports_changes_to_model_valuation_inputs(self):
        previous = compatible_snapshot(
            market_expectations=61.0,
            model_fcf_margin=0.12,
            discount_rate=0.09,
            terminal_growth=0.02,
        )
        current = compatible_snapshot(
            market_expectations=68.0,
            model_fcf_margin=0.16,
            discount_rate=0.10,
            terminal_growth=0.025,
            retrieved_at="2026-10-08T12:00:00+00:00",
            saved_at="2026-10-08T12:01:00+00:00",
        )

        result = compare_snapshots(previous, current)

        self.assertTrue(result["comparable"])
        changes = {item["field"]: item for item in result["changes"]}
        self.assertEqual(
            set(changes),
            {"market_expectations", "model_fcf_margin", "discount_rate", "terminal_growth"},
        )
        self.assertAlmostEqual(changes["market_expectations"]["delta"], 7.0)
        self.assertAlmostEqual(changes["model_fcf_margin"]["delta"], 0.04)
        self.assertAlmostEqual(changes["discount_rate"]["delta"], 0.01)
        self.assertAlmostEqual(changes["terminal_growth"]["delta"], 0.005)

    def test_snapshot_rejects_nonfinite_valuation_inputs_and_invalid_score_range(self):
        base = compatible_snapshot(
            market_expectations=61.0,
            model_fcf_margin=0.12,
            discount_rate=0.09,
            terminal_growth=0.02,
        )
        invalid_values = (
            ("score", 101.0),
            ("business_quality", -0.01),
            ("financial_strength", 100.01),
            ("market_expectations", 101.0),
            ("model_fcf_margin", float("nan")),
            ("model_fcf_margin", -0.01),
            ("discount_rate", float("inf")),
            ("discount_rate", 0.02),
            ("terminal_growth", float("nan")),
            ("terminal_growth", 0.07),
        )

        for field, value in invalid_values:
            with self.subTest(field=field):
                invalid = {**base, field: value}
                if field == "score":
                    invalid["score_trace"] = {**base["score_trace"], "score": value}
                result = compare_snapshots(base, resign_snapshot(invalid))
                self.assertFalse(result["comparable"])
                expected_reason = "snapshot_unverified" if isinstance(value, float) and not math.isfinite(value) else "snapshot_schema_invalid"
                self.assertEqual(result["reason"], expected_reason)

        trace = base["score_trace"]
        invalid_trace_cases = (
            {**trace, "components": [{**trace["components"][0], "value": 101.0}]},
            {**trace, "components": [{**trace["components"][0], "evidence_coverage": 1.01}]},
            {**trace, "score": 101.0},
        )
        for invalid_trace in invalid_trace_cases:
            with self.subTest(invalid_trace=invalid_trace):
                invalid_score = invalid_trace.get("score", base["score"])
                result = compare_snapshots(
                    base, resign_snapshot({**base, "score": invalid_score, "score_trace": invalid_trace}),
                )
                self.assertFalse(result["comparable"])
                self.assertEqual(result["reason"], "snapshot_schema_invalid")

        forged_previous = resign_snapshot({
            **base,
            "score": 500.0,
            "score_trace": {**trace, "score": 500.0},
        })
        forged_diff = compare_snapshots(forged_previous, base)
        self.assertFalse(forged_diff["comparable"])
        self.assertEqual(forged_diff["reason"], "snapshot_schema_invalid")

        inverted_rates = compare_snapshots(
            base,
            resign_snapshot({**base, "discount_rate": 0.05, "terminal_growth": 0.055}),
        )
        self.assertFalse(inverted_rates["comparable"])
        self.assertEqual(inverted_rates["reason"], "snapshot_schema_invalid")

    def test_snapshot_attributes_saved_scenario_assumption_changes_by_input(self):
        previous = compatible_snapshot(user_assumptions={
            "starting_growth": 0.10, "fcf_margin": 0.15,
            "discount_rate": 0.09, "terminal_growth": 0.02,
        })
        current = compatible_snapshot(user_assumptions={
            "starting_growth": 0.12, "fcf_margin": 0.18,
            "discount_rate": 0.10, "terminal_growth": 0.025,
        })

        result = compare_snapshots(previous, current)

        self.assertTrue(result["comparable"])
        changes = {item["field"]: item for item in result["changes"]}
        self.assertEqual(set(changes), {
            "user_starting_growth", "user_fcf_margin", "user_discount_rate", "user_terminal_growth",
        })
        self.assertAlmostEqual(changes["user_starting_growth"]["delta"], 0.02)
        self.assertAlmostEqual(changes["user_fcf_margin"]["delta"], 0.03)
        self.assertAlmostEqual(changes["user_discount_rate"]["delta"], 0.01)
        self.assertAlmostEqual(changes["user_terminal_growth"]["delta"], 0.005)

        reset_to_baseline = compare_snapshots(previous, compatible_snapshot())
        reset_changes = {item["field"]: item for item in reset_to_baseline["changes"]}
        self.assertEqual(set(reset_changes), set(changes))
        self.assertTrue(all(item["after"] is None and item["delta"] is None for item in reset_changes.values()))

    def test_snapshot_rejects_invalid_saved_scenario_assumptions(self):
        valid = {
            "starting_growth": 0.10,
            "fcf_margin": 0.15,
            "discount_rate": 0.09,
            "terminal_growth": 0.02,
        }
        invalid = (
            {**valid, "starting_growth": True},
            {**valid, "starting_growth": 0.41},
            {**valid, "fcf_margin": 0.0},
            {**valid, "discount_rate": 0.02},
            {**valid, "discount_rate": 0.02, "terminal_growth": 0.03},
            {key: value for key, value in valid.items() if key != "terminal_growth"},
            {**valid, "unrecognized": 1.0},
        )

        for assumptions in invalid:
            with self.subTest(assumptions=assumptions):
                result = compare_snapshots(
                    compatible_snapshot(user_assumptions=valid),
                    compatible_snapshot(user_assumptions=assumptions),
                )
                self.assertFalse(result["comparable"])
                self.assertEqual(result["reason"], "snapshot_schema_invalid")

    def test_snapshot_omits_revenue_delta_when_periods_differ(self):
        base = compatible_snapshot()
        current = {**base, "revenue": 120, "revenue_period": "2024-12-31", "free_cash_flow": 15, "fcf_period": "2024-12-31", "sources": {"revenue": {**base["sources"]["revenue"], "period": "2024-12-31"}, "free_cash_flow": {**base["sources"]["free_cash_flow"], "period": "2024-12-31"}}}
        current = resign_snapshot(current)
        result = compare_snapshots(base, current)
        self.assertTrue(result["comparable"])
        self.assertNotIn("revenue", {item["field"] for item in result["changes"]})
        self.assertNotIn("free_cash_flow", {item["field"] for item in result["changes"]})
        self.assertEqual({item["field"] for item in result["excluded_fields"]}, {"revenue", "free_cash_flow"})

    def test_snapshot_service_keeps_extreme_fcf_change_json_safe(self):
        previous = compatible_snapshot(free_cash_flow=-1e308)
        current = compatible_snapshot(
            free_cash_flow=1e308,
            retrieved_at="2026-10-08T12:00:00+00:00",
            saved_at="2026-10-08T12:01:00+00:00",
        )

        result = compare_snapshots(previous, current)

        self.assertTrue(result["comparable"])
        change = next(item for item in result["changes"] if item["field"] == "free_cash_flow")
        self.assertEqual((change["before"], change["after"], change["delta"]), (-1e308, 1e308, None))
        json.dumps(result, allow_nan=False)

    def test_snapshot_excludes_values_when_source_method_changes(self):
        base = compatible_snapshot()
        current = resign_snapshot({**base, "fcf_source": "SEC EDGAR", "sources": {**base["sources"], "free_cash_flow": {**base["sources"]["free_cash_flow"], "source": "SEC EDGAR", "kind": "calculated: annual SEC operating cash flow less capital spending"}}})
        result = compare_snapshots(base, current)
        self.assertTrue(result["comparable"])
        self.assertIn({"field": "free_cash_flow", "reason": "source_definition_changed"}, result["excluded_fields"])

    def test_snapshot_omits_analyst_growth_delta_when_estimate_horizon_changes(self):
        base = compatible_snapshot()
        current = resign_snapshot({
            **base,
            "consensus_growth": 0.18,
            "analyst_estimate_period": "Current-year analyst revenue estimate",
        })

        result = compare_snapshots(base, current)

        self.assertTrue(result["comparable"])
        self.assertNotIn("consensus_growth", {item["field"] for item in result["changes"]})
        self.assertIn(
            {"field": "consensus_growth", "reason": "estimate_period_changed"},
            result["excluded_fields"],
        )

    def test_snapshot_keeps_analyst_growth_delta_when_estimate_horizon_matches(self):
        base = compatible_snapshot()
        current = resign_snapshot({**base, "consensus_growth": 0.18})

        result = compare_snapshots(base, current)

        self.assertTrue(result["comparable"])
        estimate_change = next(item for item in result["changes"] if item["field"] == "consensus_growth")
        self.assertAlmostEqual(estimate_change["delta"], 0.06)
        self.assertFalse(any(item["field"] == "consensus_growth" for item in result["excluded_fields"]))

    def test_snapshot_omits_analyst_growth_delta_when_legacy_estimate_period_is_missing(self):
        previous = compatible_snapshot(analyst_estimate_period=None)
        current = compatible_snapshot(analyst_estimate_period=None, consensus_growth=0.18)

        result = compare_snapshots(previous, current)

        self.assertTrue(result["comparable"])
        self.assertNotIn("consensus_growth", {item["field"] for item in result["changes"]})
        self.assertIn(
            {"field": "consensus_growth", "reason": "estimate_period_unavailable"},
            result["excluded_fields"],
        )

    def test_model_sensitivity_changes_do_not_change_score_or_its_evidence_basis(self):
        base = compatible_snapshot()
        current = compatible_snapshot(
            historical_growth=0.35,
            required_growth=0.50,
            solver_status="above_range",
            score=base["score"],
            score_trace=base["score_trace"],
        )
        result = compare_snapshots(base, current)
        self.assertTrue(result["comparable"])
        self.assertNotIn("score", {item["field"] for item in result["changes"]})

    def test_snapshot_rejects_malformed_score_trace(self):
        base = compatible_snapshot()
        result = compare_snapshots(base, resign_snapshot({**base, "score_trace": {**base["score_trace"], "score": 1}}))
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_schema_invalid")

    def test_snapshot_rejects_incomplete_aggregate_score_evidence(self):
        base = compatible_snapshot()
        components = base["score_trace"]["components"]
        malformed_traces = (
            {**base["score_trace"], "components": []},
            {
                **base["score_trace"],
                "components": [item for item in components if item["key"] != "business_quality"],
            },
            {
                **base["score_trace"],
                "components": [
                    {key: value for key, value in components[0].items() if key != "evidence_coverage"},
                    *components[1:],
                ],
            },
        )
        for trace in malformed_traces:
            with self.subTest(trace=trace):
                malformed = resign_snapshot({**base, "score_trace": trace})
                result = compare_snapshots(malformed, malformed)
                self.assertFalse(result["comparable"])
                self.assertEqual(result["reason"], "snapshot_schema_invalid")

    def test_snapshot_rejects_score_trace_that_does_not_reconcile(self):
        base = compatible_snapshot()
        trace = base["score_trace"]
        forged_score = resign_snapshot({
            **base,
            "score": 95,
            "score_trace": {**trace, "score": 95},
        })
        forged_subscore_value = 100
        forged_subscore_score = (forged_subscore_value * 7 + 70 * 6) / 13
        forged_subscore = resign_snapshot({
            **base,
            "business_quality": forged_subscore_value,
            "score": forged_subscore_score,
            "score_trace": {
                **trace,
                "score": forged_subscore_score,
                "components": [
                    {**item, "value": forged_subscore_value}
                    if item["key"] == "business_quality" else item
                    for item in trace["components"]
                ],
                "subscores": {
                    **trace["subscores"],
                    "business_quality": {
                        **trace["subscores"]["business_quality"],
                        "score": forged_subscore_value,
                    },
                },
            },
        })
        inconsistent_coverage = resign_snapshot({
            **base,
            "score_trace": {**trace, "evidence_coverage": 0.50},
        })
        injected_growth_comparison = resign_snapshot({
            **base,
            "score_trace": {
                **trace,
                "growth_comparison_inputs": {
                    **trace["growth_comparison_inputs"],
                    "historical_growth": 0.11,
                },
                "growth_comparison_available": True,
            },
        })

        for malformed in (forged_score, forged_subscore, inconsistent_coverage, injected_growth_comparison):
            with self.subTest(malformed=malformed):
                result = compare_snapshots(malformed, malformed)
                self.assertFalse(result["comparable"])
                self.assertEqual(result["reason"], "snapshot_schema_invalid")

        missing_growth_inputs = resign_snapshot({
            **base,
            "score_trace": {
                key: item for key, item in trace.items() if key != "growth_comparison_inputs"
            },
        })
        result = compare_snapshots(missing_growth_inputs, missing_growth_inputs)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_schema_invalid")

    def test_snapshot_ignores_client_forgery_and_compares_server_signed_research(self):
        base = compatible_snapshot()
        forged_score = 95
        forged = {
            **base,
            "score": forged_score,
            "score_trace": {**base["score_trace"], "score": forged_score},
        }
        result = compare_snapshots(base, forged)
        self.assertTrue(result["comparable"])
        self.assertNotIn("score", {change["field"] for change in result["changes"]})
        self.assertNotIn("score", {item["field"] for item in result["excluded_fields"]})

        forged_factor = {
            **base,
            "business_quality": 100,
            "score": (100 * 7 + 70 * 6) / 13,
        }
        result = compare_snapshots(base, forged_factor)
        self.assertTrue(result["comparable"])
        self.assertNotIn("business_quality", {change["field"] for change in result["changes"]})
        self.assertNotIn("score", {change["field"] for change in result["changes"]})

        forged_metric = {**base, "market_expectations": 95.0}
        result = compare_snapshots(base, forged_metric)
        self.assertTrue(result["comparable"])
        self.assertNotIn("market_expectations", {change["field"] for change in result["changes"]})

        unsigned = {key: value for key, value in base.items() if key != "snapshot_proof"}
        result = compare_snapshots(unsigned, base)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_unverified")

        wrong_identity = {**base, "ticker": "MSFT"}
        result = compare_snapshots(wrong_identity, base)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_unverified")

    def test_snapshot_accepts_the_minimal_frontend_score_evidence_projection(self):
        base = compatible_snapshot()
        trace = base["score_trace"]
        projected_subscores = {
            name: {
                "evidence_coverage": detail["evidence_coverage"],
                "components": [
                    {"key": item["key"], "value": item["value"], "weight": item["weight"]}
                    for item in detail["components"]
                ],
            }
            for name, detail in trace["subscores"].items()
        }
        projected = {
            **base,
            "score_trace": {
                "score": trace["score"],
                "evidence_coverage": trace["evidence_coverage"],
                "growth_comparison_inputs": trace["growth_comparison_inputs"],
                "components": [
                    {key: item[key] for key in ("key", "value", "evidence_coverage") if key in item}
                    for item in trace["components"]
                ],
                "subscores": projected_subscores,
            },
        }

        result = compare_snapshots(projected, projected)
        self.assertTrue(result["comparable"], result)
        self.assertEqual(result["changes"], [])

        incomplete_trace = {
            key: value for key, value in base["score_trace"].items() if key != "subscores"
        }
        incomplete = resign_snapshot({**base, "score_trace": incomplete_trace})
        result = compare_snapshots(incomplete, incomplete)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_schema_invalid")


if __name__ == "__main__":
    unittest.main()
