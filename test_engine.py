import json
import math
import unittest
from itertools import product

from engine import (
    CURRENCY_PROFILES,
    SECTOR_SPREAD,
    can_solve_reverse_dcf,
    choose_model_fcf_margin,
    currency_profile_is_supported,
    currency_pair_uses_compatible_units,
    dcf_enterprise_value,
    expectation_score,
    expectation_score_detail,
    financial_strength_detail,
    history_cagr,
    history_cagr_periods,
    model_rates,
    reality_score,
    score_breakdown,
    snapshot_diff,
    solve_required_growth,
    solve_required_growth_detail,
    solve_supported_required_growth,
    normalize_company_currencies,
    normalize_market_currency,
    supported_score_breakdown,
    year_growth,
)
from methodology import DEFAULT_SECTOR_MODEL, SECTOR_MODELS, get_sector_model, has_sector_calibration, normalize_sector_label


class RateTests(unittest.TestCase):
    def test_sector_score_calibrations_have_normalized_weights_and_no_valuation_rate_copy(self):
        models = list(SECTOR_MODELS.values()) + [DEFAULT_SECTOR_MODEL]
        for model in models:
            with self.subTest(model=model):
                self.assertNotIn("discount_rate", model)
                for field in ("business_weights", "expectation_weights", "financial_weights"):
                    self.assertAlmostEqual(sum(model[field].values()), 1.0, places=12)
                    self.assertTrue(all(value >= 0 for value in model[field].values()))
                for field in ("business_benchmarks", "expectation_benchmarks", "financial_benchmarks"):
                    self.assertTrue(all(value > 0 for value in model[field].values()))

        self.assertTrue(all(has_sector_calibration(sector) for sector in SECTOR_MODELS))
        self.assertFalse(has_sector_calibration("Unconfigured sector"))
        self.assertEqual(get_sector_model("Unconfigured sector"), DEFAULT_SECTOR_MODEL)

    def test_sector_model_access_does_not_expose_mutable_global_calibration(self):
        exposed_model = get_sector_model("Technology")
        exposed_model["business_weights"]["growth"] = 0.0
        exposed_model["business_benchmarks"]["gross"] = 0.0

        fresh_model = get_sector_model("Technology")
        self.assertEqual(fresh_model["business_weights"]["growth"], 1/3)
        self.assertEqual(fresh_model["business_benchmarks"]["gross"], 0.70)

    def test_malformed_sector_metadata_is_safe_and_unconfigured(self):
        for sector in (None, [], ["Technology"], {"sector": "Technology"}, 42, " "):
            with self.subTest(sector=sector):
                self.assertEqual(normalize_sector_label(sector), "Unknown sector")
                self.assertFalse(has_sector_calibration(sector))
                self.assertEqual(get_sector_model(sector), DEFAULT_SECTOR_MODEL)
        self.assertEqual(normalize_sector_label(" Technology "), "Technology")

    def test_currency_and_sector_rate_formula_is_the_only_valuation_rate_source(self):
        for currency, profile in CURRENCY_PROFILES.items():
            for sector, spread in SECTOR_SPREAD.items():
                rates = model_rates(currency, sector)
                expected = min(
                    max(profile["risk_free"] + profile["erp"] + spread, profile["terminal_growth"] + 0.015),
                    0.22,
                )
                with self.subTest(currency=currency, sector=sector):
                    self.assertAlmostEqual(rates["discount_rate"], round(expected, 4))
                    self.assertEqual(rates["terminal_growth"], profile["terminal_growth"])
                    self.assertIn("not a live yield or issuer-specific WACC", rates["calibration_basis"])
                    self.assertIn("cash-flow/discount-rate matching is not independently validated", rates["calibration_basis"])

    def test_jpy_utility_cheaper_than_usd_tech(self):
        jpy = model_rates("JPY", "Utilities")
        usd = model_rates("USD", "Technology")
        self.assertLess(jpy["discount_rate"], usd["discount_rate"])
        self.assertLess(jpy["terminal_growth"], usd["terminal_growth"])
        self.assertGreater(jpy["discount_rate"], jpy["terminal_growth"])

    def test_unknown_currency_falls_back_to_usd_world(self):
        rates = model_rates("XYZ", "Technology")
        self.assertTrue(rates["used_fallback_currency"])
        self.assertEqual(rates["currency"], "USD")

    def test_only_explicit_reporting_currency_profiles_are_supported(self):
        self.assertTrue(currency_profile_is_supported("USD"))
        self.assertTrue(currency_profile_is_supported("gbp"))
        self.assertFalse(currency_profile_is_supported("XYZ"))
        self.assertFalse(currency_profile_is_supported("GBp"))
        self.assertFalse(currency_profile_is_supported(None))

    def test_missing_currency_metadata_is_not_defaulted_to_usd(self):
        self.assertEqual(normalize_market_currency(None), "")
        self.assertEqual(normalize_company_currencies(None, None), ("N/A", "N/A"))
        self.assertEqual(normalize_company_currencies("USD", None), ("USD", "N/A"))
        self.assertEqual(normalize_company_currencies(None, "EUR"), ("N/A", "EUR"))
        self.assertTrue(model_rates(None, "Technology")["used_fallback_currency"])
        self.assertFalse(can_solve_reverse_dcf(None, True, 0.12))
        self.assertFalse(currency_pair_uses_compatible_units("N/A", "N/A"))

    def test_reverse_dcf_requires_supported_currency_sector_and_positive_margin(self):
        self.assertTrue(can_solve_reverse_dcf("USD", True, 0.12))
        self.assertFalse(can_solve_reverse_dcf("XYZ", True, 0.12))
        self.assertFalse(can_solve_reverse_dcf("USD", False, 0.12))
        self.assertFalse(can_solve_reverse_dcf("USD", True, 0))
        self.assertFalse(can_solve_reverse_dcf("USD", True, float("nan")))

    def test_quote_subunits_are_preserved_and_not_cross_converted(self):
        self.assertEqual(normalize_market_currency("GBp"), "GBp")
        self.assertEqual(normalize_company_currencies("gbp", "GBp"), ("GBP", "GBp"))
        self.assertTrue(currency_pair_uses_compatible_units("GBp", "GBp"))
        self.assertFalse(currency_pair_uses_compatible_units("GBp", "GBP"))
        self.assertFalse(currency_pair_uses_compatible_units("ZAc", "ZAR"))

    def test_guarded_reverse_solver_requires_supported_financial_basis(self):
        assumptions = (100, 0.10, 0.15, 0.09, 0.02)
        target = dcf_enterprise_value(*assumptions)
        valid = solve_supported_required_growth("USD", True, target, assumptions[0], *assumptions[2:])
        self.assertEqual(valid["status"], "exact")
        self.assertAlmostEqual(valid["growth"], 0.10, places=8)

        for currency, sector_ok, margin in (("XYZ", True, 0.15), ("GBp", True, 0.15), ("USD", False, 0.15), ("USD", True, 0)):
            blocked = solve_supported_required_growth(currency, sector_ok, target, 100, margin, 0.09, 0.02)
            self.assertEqual(blocked, {"growth": None, "status": "unavailable", "low": None, "high": None})


class DcfTests(unittest.TestCase):
    def test_higher_discount_lowers_value(self):
        low = dcf_enterprise_value(100, 0.08, 0.15, 0.08, 0.02)
        high = dcf_enterprise_value(100, 0.08, 0.15, 0.12, 0.02)
        self.assertGreater(low, high)

    def test_fade_growth_between_start_and_terminal(self):
        self.assertEqual(year_growth(0.20, 0.03, 1), 0.20)
        faded = year_growth(0.20, 0.03, 8)
        self.assertLess(faded, 0.20)
        self.assertGreater(faded, 0.03)

    def test_negative_margin_does_not_solve(self):
        growth, hit = solve_required_growth(1000, 100, -0.05, 0.10, 0.03)
        self.assertIsNone(growth)
        self.assertFalse(hit)

    def test_bound_hit_is_flagged(self):
        growth, hit = solve_required_growth(10_000_000, 1, 0.01, 0.10, 0.03)
        self.assertTrue(hit)
        self.assertIsNotNone(growth)

    def test_solver_classifies_below_and_above_range(self):
        low_target = dcf_enterprise_value(200, -0.40, 0.18, 0.09, 0.02)
        high_target = dcf_enterprise_value(200, 1.20, 0.18, 0.09, 0.02)
        self.assertEqual(solve_required_growth_detail(low_target * 0.5, 200, 0.18, 0.09, 0.02)["status"], "below_range")
        self.assertEqual(solve_required_growth_detail(high_target * 2, 200, 0.18, 0.09, 0.02)["status"], "above_range")

    def test_reverse_solver_range_classification_is_scale_invariant(self):
        for revenue in (1e-15, 200.0, 1e15):
            with self.subTest(revenue=revenue):
                low_value = dcf_enterprise_value(revenue, -0.40, 0.18, 0.09, 0.02)
                high_value = dcf_enterprise_value(revenue, 1.20, 0.18, 0.09, 0.02)
                self.assertEqual(solve_required_growth_detail(low_value, revenue, 0.18, 0.09, 0.02)["status"], "exact")
                self.assertEqual(solve_required_growth_detail(high_value, revenue, 0.18, 0.09, 0.02)["status"], "exact")
                self.assertEqual(solve_required_growth_detail(low_value * 0.5, revenue, 0.18, 0.09, 0.02)["status"], "below_range")
                self.assertEqual(solve_required_growth_detail(high_value * 2, revenue, 0.18, 0.09, 0.02)["status"], "above_range")

    def test_invalid_discount_terminal_pair_is_unavailable(self):
        self.assertIsNone(dcf_enterprise_value(100, 0.08, 0.15, 0.03, 0.03))

    def test_missing_or_nonfinite_growth_is_unavailable(self):
        self.assertIsNone(dcf_enterprise_value(100, None, 0.15, 0.08, 0.02))
        self.assertIsNone(dcf_enterprise_value(100, float("nan"), 0.15, 0.08, 0.02))

    def test_invalid_projection_inputs_do_not_produce_numbers(self):
        self.assertIsNone(year_growth(0.10, 0.03, float("inf")))
        self.assertIsNone(dcf_enterprise_value(100, 1e308, 0.15, 0.08, 0.02))
        self.assertIsNone(dcf_enterprise_value(100, 0.08, 0.15, 0.08, 0.02, years=51))
        self.assertIsNone(dcf_enterprise_value(True, 0.10, 0.20, 0.10, 0.02))

    def test_fractional_forecast_controls_are_not_silently_truncated(self):
        inputs = (100, 0.08, 0.15, 0.10, 0.02)
        valid = dcf_enterprise_value(*inputs, years=3, fade_start=1)
        self.assertIsNotNone(valid)

        for controls in ({"years": 3.9, "fade_start": 1}, {"years": 3, "fade_start": 1.9}):
            with self.subTest(controls=controls):
                self.assertIsNone(dcf_enterprise_value(*inputs, **controls))
                result = solve_required_growth_detail(valid, *inputs[:1], *inputs[2:], **controls)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["growth"])

    def test_invalid_explicit_start_margin_is_not_silently_replaced(self):
        for invalid in (True, False, float("nan"), float("inf"), "not-a-number"):
            with self.subTest(start_margin=invalid):
                self.assertIsNone(dcf_enterprise_value(100, 0.08, 0.15, 0.10, 0.02, start_margin=invalid))
        self.assertIsNotNone(dcf_enterprise_value(100, 0.08, 0.15, 0.10, 0.02))

    def test_reverse_solver_rejects_nonpositive_enterprise_value(self):
        self.assertEqual(solve_required_growth_detail(0, 100, .15, .08, .02)["status"], "unavailable")
        self.assertEqual(solve_required_growth_detail(-1, 100, .15, .08, .02)["status"], "unavailable")

    def test_round_trip_solver(self):
        target = dcf_enterprise_value(200, 0.12, 0.18, 0.09, 0.02)
        implied, hit = solve_required_growth(target, 200, 0.18, 0.09, 0.02)
        self.assertFalse(hit)
        self.assertAlmostEqual(implied, 0.12, places=2)

    def test_reverse_dcf_round_trips_across_supported_boundaries_and_margins(self):
        cases = product(
            (100.0, 1_000_000.0),
            (-0.40, -0.20, 0.0, 0.12, 0.60, 1.20),
            (0.02, 0.20),
            (0.07, 0.10, 0.18),
            (0.02, 0.05),
            (None, 0.005, 0.60),
            (5, 10),
        )
        for revenue, growth, margin, discount, terminal, start_margin, years in cases:
            with self.subTest(revenue=revenue, growth=growth, margin=margin, discount=discount, terminal=terminal, start_margin=start_margin, years=years):
                target = dcf_enterprise_value(
                    revenue, growth, margin, discount, terminal,
                    years=years, start_margin=start_margin,
                )
                self.assertIsNotNone(target)
                result = solve_required_growth_detail(
                    target, revenue, margin, discount, terminal,
                    years=years, start_margin=start_margin,
                )
                self.assertEqual(result["status"], "exact")
                self.assertAlmostEqual(result["growth"], growth, places=7)

    def test_dcf_value_is_monotonic_in_starting_growth_across_supported_inputs(self):
        growth_points = (-0.40, -0.20, 0.0, 0.20, 0.60, 1.20)
        cases = (
            (100.0, 0.02, 0.07, 0.02, None, 5, 2),
            (1_000_000.0, 0.20, 0.10, 0.05, 0.005, 10, 5),
            (50_000.0, 0.50, 0.18, 0.02, 0.60, 10, 0),
        )
        for revenue, margin, discount, terminal, start_margin, years, fade_start in cases:
            with self.subTest(revenue=revenue, margin=margin, start_margin=start_margin, years=years):
                values = [
                    dcf_enterprise_value(
                        revenue, growth, margin, discount, terminal,
                        years=years, fade_start=fade_start, start_margin=start_margin,
                    )
                    for growth in growth_points
                ]
                self.assertTrue(all(value is not None for value in values))
                self.assertTrue(
                    all(lower < upper for lower, upper in zip(values, values[1:])),
                    msg=f"DCF value must rise with starting growth: {values}",
                )

    def test_dcf_matches_independent_constant_cash_flow_solution(self):
        revenue = 100.0
        annual_fcf = revenue * 0.20
        discount_rate = 0.10
        terminal_growth = 0.02
        years = 3
        terminal_value = annual_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
        expected_value = sum(annual_fcf / (1 + discount_rate) ** year for year in range(1, years + 1))
        expected_value += terminal_value / (1 + discount_rate) ** years

        actual_value = dcf_enterprise_value(
            revenue, 0.0, 0.20, discount_rate, terminal_growth,
            years=years, fade_start=years, start_margin=0.20,
        )
        self.assertAlmostEqual(actual_value, expected_value, places=10)
        inverse = solve_required_growth_detail(
            expected_value, revenue, 0.20, discount_rate, terminal_growth,
            years=years, fade_start=years, start_margin=0.20,
        )
        self.assertEqual(inverse["status"], "exact")
        self.assertAlmostEqual(inverse["growth"], 0.0, places=8)

    def test_dcf_growth_and_margin_fades_match_independent_projection(self):
        # Independent three-year projection: revenue is 110, 116.6, 118.932;
        # margins are 2/15, 1/6, 1/5; FCF is 14.6667, 19.4333, 23.7864.
        # EV = sum(FCF_y / 1.1**y) + (23.7864 * 1.02 / 0.08) / 1.1**3.
        expected_value = 275.1212121212121
        actual_value = dcf_enterprise_value(
            100.0, 0.10, 0.20, 0.10, 0.02,
            years=3, fade_start=1, start_margin=0.10,
        )

        self.assertAlmostEqual(actual_value, expected_value, places=10)
        inverse = solve_required_growth_detail(
            expected_value, 100.0, 0.20, 0.10, 0.02,
            years=3, fade_start=1, start_margin=0.10,
        )
        self.assertEqual(inverse["status"], "exact")
        self.assertAlmostEqual(inverse["growth"], 0.10, places=8)


class HistoryAndMarginTests(unittest.TestCase):
    def test_longer_history_cagr(self):
        # newest first: 160, 140, 120, 100 over 3 years -> ~17%
        growth, years = history_cagr([160, 140, 120, 100], max_years=10)
        self.assertEqual(years, 3)
        self.assertAlmostEqual(growth, (160 / 100) ** (1 / 3) - 1, places=6)

    def test_undated_history_cagr_fails_closed_on_gaps_and_invalid_windows(self):
        self.assertEqual(history_cagr([160, None, 100]), (None, 0))
        self.assertEqual(history_cagr([160, 0, 100]), (None, 0))
        for invalid_window in (0, -1, 1.5, None, True):
            with self.subTest(max_years=invalid_window):
                self.assertEqual(history_cagr([160, 120, 100], invalid_window), (None, 0))

        growth, years = history_cagr([160, 140, 120, 100], max_years=2)
        self.assertEqual(years, 2)
        self.assertAlmostEqual(growth, (160 / 120) ** 0.5 - 1, places=6)

    def test_dated_cagr_uses_elapsed_days_not_row_count(self):
        growth, years, start, end = history_cagr_periods([
            ("2024-12-31", 120), ("2023-12-30", 110), ("2022-12-31", 100),
        ])
        self.assertAlmostEqual(growth, (120 / 100) ** (1 / years) - 1)
        self.assertAlmostEqual(years, ( __import__("datetime").date.fromisoformat(end) - __import__("datetime").date.fromisoformat(start)).days / 365.2425)

    def test_dated_cagr_does_not_bridge_a_missing_year(self):
        growth, years, _, _ = history_cagr_periods([("2024-12-31", 140), ("2022-12-31", 100)])
        self.assertIsNone(growth)
        self.assertEqual(years, 0)

    def test_dated_cagr_rejects_conflicting_duplicate_periods_independent_of_order(self):
        observations = [
            ("2025-12-31", 150),
            ("2024-12-31", 110),
            ("2024-12-31", 100),
            ("2023-12-31", 90),
        ]
        for order in (observations, [observations[0], observations[2], observations[1], observations[3]]):
            with self.subTest(observations=order):
                self.assertEqual(history_cagr_periods(order), (None, 0.0, None, None))

    def test_dated_cagr_collapses_identical_duplicate_periods(self):
        observations = [("2025-12-31", 150), ("2024-12-31", 100), ("2024-12-31", 100)]
        self.assertEqual(history_cagr_periods(observations), history_cagr_periods(observations[:2]))

    def test_dated_cagr_rejects_malformed_period_suffixes(self):
        for malformed_period in ("2023-12-31garbage", "2023-12-31T99:99:99"):
            with self.subTest(period=malformed_period):
                growth, years, start, end = history_cagr_periods([
                    ("2024-12-31", 120), (malformed_period, 110), ("2022-12-31", 100),
                ])
                self.assertIsNone(growth)
                self.assertEqual(years, 0)
                self.assertIsNone(start)
                self.assertIsNone(end)

        valid_timestamp = history_cagr_periods([
            ("2024-12-31T00:00:00Z", 120), ("2023-12-31 00:00:00", 100),
        ])
        self.assertIsNotNone(valid_timestamp[0])

    def test_dated_cagr_ignores_malformed_rows_and_validates_interval_limit(self):
        valid = [("2024-12-31", 120), ("2023-12-31", 100)]
        expected = history_cagr_periods(valid)
        self.assertEqual(
            history_cagr_periods([valid[0], ("malformed row",), valid[1], ("extra", "columns", "ignored")]),
            expected,
        )
        for invalid_limit in (0, -1, 1.5, None, True):
            with self.subTest(max_intervals=invalid_limit):
                self.assertEqual(history_cagr_periods(valid, invalid_limit), (None, 0.0, None, None))
        for malformed_collection in (None, 42):
            with self.subTest(observations=malformed_collection):
                self.assertEqual(history_cagr_periods(malformed_collection), (None, 0.0, None, None))

    def test_cagr_extreme_finite_inputs_do_not_overflow(self):
        from engine import cagr
        self.assertIsNone(cagr(1e-308, 1e308, 0.01))

    def test_refuses_negative_latest_without_positive_history(self):
        margin, refused, _ = choose_model_fcf_margin(-0.04, [-0.02, -0.01], 0.12)
        self.assertTrue(refused)
        self.assertIsNone(margin)

    def test_refuses_negative_latest_even_with_positive_history(self):
        margin, refused, note = choose_model_fcf_margin(-0.04, [0.12, 0.10], 0.12)
        self.assertTrue(refused)
        self.assertIsNone(margin)
        self.assertIn("non-positive", note)

    def test_does_not_invent_mature_margin(self):
        margin, refused, _ = choose_model_fcf_margin(None, [], 0.12)
        self.assertTrue(refused)
        self.assertIsNone(margin)

    def test_nonpositive_historical_average_is_unavailable_without_latest_margin(self):
        margin, refused, _ = choose_model_fcf_margin(None, [-0.10, 0.0], 0.12)
        self.assertTrue(refused)
        self.assertIsNone(margin)

    def test_historical_mean_keeps_negative_observations(self):
        from engine import average_finite_margins
        self.assertAlmostEqual(average_finite_margins([0.10, -0.20, 0.0]), -0.10 / 3)

    def test_extreme_finite_margins_do_not_overflow_the_model_average(self):
        from engine import average_finite_margins

        observations = [1e308, 1e308]
        self.assertEqual(average_finite_margins(observations), 1e308)

        selected, refused, _ = choose_model_fcf_margin(1e308, observations)
        self.assertFalse(refused)
        self.assertEqual(selected, 1e308)
        self.assertTrue(math.isfinite(selected))


class ScoreTests(unittest.TestCase):
    def test_technology_score_matches_independent_raw_input_calculation(self):
        from engine import business_quality_detail

        model = get_sector_model("Technology")
        business = business_quality_detail(
            revenue_growth=0.10,
            gross_margin=0.70,
            operating_margin=0.30,
            fcf_margin=0.25,
            roe=0.25,
            debt_to_assets=0.40,
            sector_model=model,
        )
        financial = financial_strength_detail(
            cash=100,
            debt=200,
            operating_margin=0.28,
            fcf_margin=0.22,
            debt_to_assets=0.40,
            sector_model=model,
        )

        # Independently calculated from the documented Technology weights:
        # business = 75*.25 + 100*.20 + 100*.15 + 100*.20 + 100*.10 + 50*.10
        # financial = 67.5*.30 + 100*.20 + 100*.30 + 50*.20
        expected_business = (75 + 100 + 100) / 3
        expected_financial = (67.5 + 100 + 50) / 3
        expected_total = (expected_business + expected_financial) / 2
        trace = score_breakdown(business, financial, 0.10, 0.12)

        self.assertAlmostEqual(business["score"], expected_business)
        self.assertAlmostEqual(financial["score"], expected_financial)
        self.assertAlmostEqual(trace["score"], expected_total)
        self.assertAlmostEqual(trace["evidence_coverage"], 1.0)
        self.assertAlmostEqual(sum(item["contribution"] for item in trace["components"]), expected_total)

    def test_zero_cash_and_zero_debt_do_not_score_an_undefined_ratio_as_strong(self):
        model = get_sector_model("Technology")
        detail = financial_strength_detail(0, 0, 0.28, 0.22, 0.40, model)
        cash_debt = next(item for item in detail["components"] if item["key"] == "cash_debt")

        self.assertIsNone(cash_debt["value"])
        self.assertAlmostEqual(detail["evidence_coverage"], 2/3)
        self.assertAlmostEqual(detail["score"], (100 + 50) / 2)
        trace = supported_score_breakdown(
            {"score": 80, "evidence_coverage": 1.0}, detail,
            historical_growth=0.10, required_growth=0.10,
        )
        financial_component = next(item for item in trace["components"] if item["key"] == "financial_strength")
        self.assertAlmostEqual(financial_component["value"], detail["score"])
        self.assertAlmostEqual(financial_component["evidence_coverage"], 2/3)
        self.assertAlmostEqual(trace["score"], (80 + detail["score"]) / 2)

    def test_zero_debt_with_positive_cash_remains_a_supported_best_case(self):
        detail = financial_strength_detail(10, 0, None, None, None, get_sector_model("Technology"))
        cash_debt = next(item for item in detail["components"] if item["key"] == "cash_debt")

        self.assertEqual(cash_debt["value"], 100)
        self.assertAlmostEqual(detail["evidence_coverage"], 1/3)

    def test_negative_sales_multiple_is_missing_evidence(self):
        model = {
            "expectation_benchmarks": {"ev_sales": 4.0, "pe": 20.0, "ev_ebitda": 12.0},
            "expectation_weights": {"required_growth": 0.40, "ev_sales": 0.20, "pe": 0.20, "ev_ebitda": 0.20},
        }
        negative = expectation_score(0.10, -1.0, 20.0, 12.0, model)
        missing = expectation_score(0.10, None, 20.0, 12.0, model)
        self.assertEqual(negative, missing)

    def test_expectation_score_withholds_insufficient_evidence_and_excludes_invalid_multiples(self):
        model = {
            "expectation_benchmarks": {"ev_sales": 4.0, "pe": 20.0, "ev_ebitda": 12.0},
            "expectation_weights": {"required_growth": 0.40, "ev_sales": 0.20, "pe": 0.20, "ev_ebitda": 0.20},
        }
        empty = expectation_score_detail(None, None, None, None, model)
        self.assertIsNone(empty["score"])
        self.assertEqual(empty["evidence_coverage"], 0.0)

        partial = expectation_score_detail(0.10, 8.0, -5.0, None, model)
        self.assertAlmostEqual(partial["evidence_coverage"], 0.60)
        self.assertAlmostEqual(partial["score"], ((50 + 0.10 * 180) * 0.40 + 100 * 0.20) / 0.60)
        self.assertAlmostEqual(expectation_score(0.10, 8.0, -5.0, None, model), partial["score"])

    def test_missing_score_label_is_unavailable(self):
        from engine import score_label
        self.assertEqual(score_label(None), "Unavailable")

    def test_clamped_growth_does_not_dominate_score(self):
        open_score = reality_score(70, 70, 0.08, 0.10, 0.09, growth_clamped=False)
        clamped = reality_score(70, 70, 0.08, 1.19, 0.09, growth_clamped=True)
        without_history = reality_score(70, 70, None, 0.10, None)
        self.assertIsNotNone(clamped)
        self.assertAlmostEqual(open_score, without_history)
        self.assertAlmostEqual(clamped, without_history)
        self.assertIsInstance(open_score, float)

    def test_breakdown_weights_and_contributions_reconcile(self):
        trace = score_breakdown(80, 60, 0.08, 0.10, 0.09)
        self.assertEqual([item["key"] for item in trace["components"]], ["business_quality", "financial_strength"])
        self.assertAlmostEqual(sum(item["weight"] for item in trace["components"]), 1.0)
        self.assertAlmostEqual(sum(item["contribution"] for item in trace["components"]), trace["score"])
        self.assertAlmostEqual(trace["score"], reality_score(80, 60, 0.08, 0.10, 0.09))

    def test_score_v8_excludes_unvalidated_valuation_sensitivity_and_preserves_relative_weights(self):
        # Captured from score-v4 before the method change: 35/30/20/15 weights
        # gave 71.37 for these fixed inputs, including next-year consensus fit.
        score_v4_parity = 71.37
        score_v4_recomputed = 0.35 * 80 + 0.30 * 60 + 0.20 * 71.8 + 0.15 * 73.4

        baseline = score_breakdown(80, 60, 0.08, 0.10, 0.09)
        changed_consensus = score_breakdown(80, 60, 0.08, 0.10, 0.50)
        expected_v8 = (80 + 60) / 2

        self.assertAlmostEqual(score_v4_parity, score_v4_recomputed)
        self.assertAlmostEqual(baseline["score"], expected_v8)
        self.assertAlmostEqual(changed_consensus["score"], expected_v8)
        self.assertNotAlmostEqual(baseline["score"], score_v4_parity)
        self.assertNotIn("consensus_fit", {item["key"] for item in baseline["components"]})
        self.assertFalse(baseline["growth_comparison_available"])
        self.assertEqual(baseline["growth_comparison_inputs"], {
            "historical_growth": None,
            "required_growth": None,
            "growth_clamped": False,
        })
        self.assertAlmostEqual(baseline["components"][0]["weight"], .5)
        self.assertAlmostEqual(baseline["components"][1]["weight"], .5)

    def test_score_v8_never_claims_a_growth_comparison_from_valuation_or_consensus(self):
        trace = supported_score_breakdown(
            {"score": 80, "evidence_coverage": 0.0},
            {"score": 70, "evidence_coverage": 0.0},
            historical_growth=None, required_growth=0.12, consensus_growth=0.20,
        )
        self.assertFalse(trace["growth_comparison_available"])
        self.assertIsNone(trace["growth_comparison_inputs"]["required_growth"])
        self.assertFalse(any(item["key"] == "consensus_fit" for item in trace["components"]))

    def test_score_is_bounded_and_nonfinite_inputs_are_unavailable(self):
        bounded = score_breakdown(10_000, -10_000, 0.10, 0.10, 0.10)["score"]
        self.assertTrue(0 <= bounded <= 100)
        trace = score_breakdown(float("nan"), 70, float("inf"), float("nan"), float("-inf"))
        self.assertIsNone(trace["score"])
        self.assertFalse(trace["growth_comparison_available"])
        self.assertIsNone(trace["components"][0]["value"])

    def test_score_v8_uses_only_supported_business_and_financial_evidence(self):
        trace = supported_score_breakdown(
            {"score": 80, "evidence_coverage": 1.0},
            {"score": 70, "evidence_coverage": 1.0},
            historical_growth=None, required_growth=0.12, consensus_growth=None,
        )
        self.assertIsNotNone(trace["score"])
        self.assertAlmostEqual(trace["evidence_coverage"], 1.0)

    def test_score_v8_full_business_and_financial_evidence_has_full_coverage(self):
        trace = supported_score_breakdown(
            {"score": 80, "evidence_coverage": 1.0},
            {"score": 70, "evidence_coverage": 1.0},
            historical_growth=0.08, required_growth=0.12, consensus_growth=None,
        )
        self.assertIsNotNone(trace["score"])
        self.assertAlmostEqual(trace["evidence_coverage"], 1.0)
        self.assertAlmostEqual(sum(item["contribution"] or 0 for item in trace["components"]), trace["score"])

    def test_score_v8_evidence_gate_uses_the_two_scored_components(self):
        boundary = score_breakdown(
            {"score": 80, "evidence_coverage": 1.0},
            {"score": 60, "evidence_coverage": 0.40},
            historical_growth=0.12, required_growth=0.50,
        )
        below = score_breakdown(
            {"score": 80, "evidence_coverage": 1.0},
            {"score": 60, "evidence_coverage": 0.34},
            historical_growth=0.12, required_growth=0.50,
        )
        self.assertAlmostEqual(boundary["evidence_coverage"], 0.70)
        self.assertIsNotNone(boundary["score"])
        self.assertLess(below["evidence_coverage"], 0.70)
        self.assertIsNone(below["score"])

    def test_score_extreme_factor_overflow_is_not_a_zero_score_observation(self):
        from engine import business_quality_detail
        model = {"business_benchmarks": {"gross": .7, "operating": .3, "fcf": .2, "roe": .2}, "business_weights": {"growth": .25, "gross": .2, "operating": .15, "fcf": .2, "roe": .1, "balance": .1}}
        detail = business_quality_detail(None, None, 1e308, None, None, None, model)
        operating = next(item for item in detail["components"] if item["key"] == "operating")
        self.assertIsNone(operating["value"])

    def test_score_helpers_never_replace_missing_inputs_with_a_fixed_score(self):
        from engine import business_quality_score, financial_strength_score, growth_reality_score
        from methodology import get_sector_model
        model = get_sector_model("Technology")
        self.assertIsNone(growth_reality_score(None, 0.10))
        self.assertIsNone(business_quality_score(None, None, None, None, None, None, model))
        self.assertIsNone(financial_strength_score(None, None, None, None, None, model))
        self.assertIsNotNone(reality_score(80, 70, None, 0.10, None))
        self.assertFalse(score_breakdown(80, 70, None, 0.10, None)["growth_comparison_available"])
        self.assertIsNone(reality_score(None, None, None, 0.10, None))

    def test_score_breakdown_preserves_partial_subscore_coverage(self):
        trace = score_breakdown(
            {"score": 80, "evidence_coverage": 0.5},
            {"score": 70, "evidence_coverage": 1.0},
            historical_growth=0.08,
            required_growth=0.12,
            consensus_growth=None,
        )
        self.assertIsNotNone(trace["score"])
        self.assertAlmostEqual(trace["evidence_coverage"], .75)

    def test_score_breakdown_clamps_subscores_before_reconciling_contributions(self):
        trace = supported_score_breakdown(
            {"score": 120, "evidence_coverage": 1.0},
            {"score": 120, "evidence_coverage": 1.0},
            historical_growth=0.10,
            required_growth=0.10,
        )

        self.assertEqual([item["value"] for item in trace["components"]], [100, 100])
        self.assertLessEqual(trace["score"], 100)
        self.assertAlmostEqual(trace["score"], 100)
        self.assertAlmostEqual(
            sum(item["contribution"] for item in trace["components"]), trace["score"]
        )

        below_range = supported_score_breakdown(
            {"score": -20, "evidence_coverage": 1.0},
            {"score": -20, "evidence_coverage": 1.0},
            historical_growth=0.10,
            required_growth=0.10,
        )
        self.assertEqual([item["value"] for item in below_range["components"]], [0, 0])
        self.assertAlmostEqual(below_range["score"], 0)

    def test_score_breakdown_bounds_reported_evidence_coverage(self):
        trace = supported_score_breakdown(
            {"score": 80, "evidence_coverage": 2.0},
            {"score": 70, "evidence_coverage": -1.0},
            historical_growth=0.10,
            required_growth=0.10,
        )

        self.assertEqual([item["evidence_coverage"] for item in trace["components"]], [1.0, 0.0])
        self.assertAlmostEqual(trace["evidence_coverage"], .5)
        self.assertIsNone(trace["score"], "Out-of-range evidence metadata must not inflate the score threshold.")


class SnapshotTests(unittest.TestCase):
    def test_comparable_snapshot_difference(self):
        previous = {"methodology_version": "score-v1", "score": 60, "revenue": 100, "solver_status": "exact"}
        current = {"methodology_version": "score-v1", "score": 65, "revenue": 110, "solver_status": "exact"}
        diff = snapshot_diff(previous, current)
        self.assertTrue(diff["comparable"])
        self.assertEqual(diff["reason"], "compatible")
        self.assertEqual({change["field"] for change in diff["changes"]}, {"score", "revenue"})
        self.assertAlmostEqual(next(change["delta"] for change in diff["changes"] if change["field"] == "score"), 5)

    def test_snapshot_delta_comparison_is_scale_invariant(self):
        for revenue in (1e-15, 1.0, 1e15):
            with self.subTest(revenue=revenue):
                diff = snapshot_diff(
                    {"methodology_version": "score-v5", "revenue": revenue},
                    {"methodology_version": "score-v5", "revenue": revenue * 2},
                )
                self.assertEqual(len(diff["changes"]), 1)
                self.assertEqual(diff["changes"][0]["field"], "revenue")
                self.assertEqual(diff["changes"][0]["delta"], revenue)

        negligible = snapshot_diff(
            {"methodology_version": "score-v5", "revenue": 1e15},
            {"methodology_version": "score-v5", "revenue": 1e15 + 1},
        )
        self.assertEqual(negligible["changes"], [])

    def test_snapshot_delta_overflow_is_unavailable_and_strict_json_safe(self):
        diff = snapshot_diff(
            {"methodology_version": "score-v1", "revenue": -1e308},
            {"methodology_version": "score-v1", "revenue": 1e308},
        )

        self.assertTrue(diff["comparable"])
        self.assertEqual(diff["changes"], [{
            "field": "revenue",
            "before": -1e308,
            "after": 1e308,
            "delta": None,
        }])
        json.dumps(diff, allow_nan=False)

    def test_incompatible_snapshot_methodology_is_not_compared(self):
        diff = snapshot_diff({"methodology_version": "score-v1"}, {"methodology_version": "score-v2"})
        self.assertFalse(diff["comparable"])
        self.assertEqual(diff["reason"], "methodology_mismatch")

    def test_score_v4_through_v7_snapshots_are_incompatible_with_score_v8(self):
        diff = snapshot_diff(
            {"methodology_version": "score-v4", "score": 71.37},
            {"methodology_version": "score-v8", "score": 71.01},
        )
        self.assertFalse(diff["comparable"])
        self.assertEqual(diff["reason"], "methodology_mismatch")
        self.assertEqual(diff["changes"], [])

        prior_v5 = snapshot_diff(
            {"methodology_version": "score-v5", "score": 71.01},
            {"methodology_version": "score-v8", "score": 71.01},
        )
        self.assertFalse(prior_v5["comparable"])
        self.assertEqual(prior_v5["reason"], "methodology_mismatch")

        prior_v6 = snapshot_diff(
            {"methodology_version": "score-v6", "score": 71.01},
            {"methodology_version": "score-v8", "score": 71.01},
        )
        self.assertFalse(prior_v6["comparable"])
        self.assertEqual(prior_v6["reason"], "methodology_mismatch")

    def test_user_assumption_change_is_tracked(self):
        previous = {"methodology_version": "score-v1", "user_assumptions": {"discount_rate": 0.10}}
        current = {"methodology_version": "score-v1", "user_assumptions": {"discount_rate": 0.12}}
        diff = snapshot_diff(previous, current)
        self.assertTrue(diff["comparable"])
        self.assertEqual(diff["changes"][0]["field"], "user_assumptions")


if __name__ == "__main__":
    unittest.main()
