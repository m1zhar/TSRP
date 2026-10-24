import math
import unittest
from datetime import date

from engine import (
    SOLVER_HIGH,
    SOLVER_LOW,
    SOLVER_MIN_RELATIVE_VALUE_SPREAD,
    choose_model_fcf_margin,
    dcf_enterprise_value,
    fixed_quote_currency_conversion_rate,
    history_cagr_periods,
    solve_required_growth_detail,
    supported_score_breakdown,
)


class ReverseDcfRangeTests(unittest.TestCase):
    def test_latest_nonpositive_fcf_margin_blocks_reverse_dcf_despite_positive_history(self):
        for latest in (0.0, -0.03):
            with self.subTest(latest=latest):
                modeled, refused, explanation = choose_model_fcf_margin(
                    latest, [0.10, 0.08, 0.06]
                )
                self.assertIsNone(modeled)
                self.assertTrue(refused)
                self.assertIn("non-positive", explanation)

    def test_positive_latest_fcf_margin_is_used_without_a_positive_history_average(self):
        modeled, refused, explanation = choose_model_fcf_margin(
            0.06, [-0.10, -0.05]
        )
        self.assertEqual(modeled, 0.06)
        self.assertFalse(refused)
        self.assertIn("latest year", explanation)

    def test_modeled_margin_average_includes_finite_negative_and_zero_years(self):
        modeled, refused, explanation = choose_model_fcf_margin(
            0.06, [0.12, -0.03, 0.0, float("nan"), float("inf")]
        )
        # Finite-history mean is 3%; blend equally with the latest 6% margin.
        self.assertAlmostEqual(modeled, 0.045)
        self.assertFalse(refused)
        self.assertEqual(explanation, "blend of latest and multi-year average")

    def test_one_year_dcf_matches_an_independent_hand_calculation(self):
        # With $100 revenue, 10% growth, and a 20% margin, year-one FCF is $22.
        # Discounted FCF is $20; terminal value is $280.50, or $255 present value.
        # Total enterprise value is therefore $275 before calling the solver.
        value = dcf_enterprise_value(
            100, 0.10, 0.20, 0.10, 0.02, years=1, fade_start=1, start_margin=0.20
        )
        self.assertAlmostEqual(value, 275.0, places=10)

        result = solve_required_growth_detail(
            275.0, 100, 0.20, 0.10, 0.02, years=1, fade_start=1, start_margin=0.20
        )
        self.assertEqual(result["status"], "exact")
        self.assertAlmostEqual(result["growth"], 0.10, places=10)

    def test_one_year_dcf_and_reverse_solver_match_closed_form_across_margins(self):
        cases = (
            (500.0, -0.25, 0.10, 0.08, 0.02, 0.35),
            (1_200.0, 0.32, 0.18, 0.11, 0.03, 0.05),
            (80.0, 0.95, 0.45, 0.16, 0.04, 0.15),
        )
        for revenue, growth, mature_margin, discount, terminal, start_margin in cases:
            with self.subTest(growth=growth, start_margin=start_margin, mature_margin=mature_margin):
                # At a one-year horizon the margin has reached mature_margin;
                # explicit FCF plus its discounted terminal value simplify to
                # revenue * (1 + growth) * mature_margin / (discount - terminal).
                expected_value = revenue * (1 + growth) * mature_margin / (discount - terminal)
                value = dcf_enterprise_value(
                    revenue,
                    growth,
                    mature_margin,
                    discount,
                    terminal,
                    years=1,
                    fade_start=1,
                    start_margin=start_margin,
                )
                self.assertAlmostEqual(value, expected_value, places=10)

                result = solve_required_growth_detail(
                    expected_value,
                    revenue,
                    mature_margin,
                    discount,
                    terminal,
                    years=1,
                    fade_start=1,
                    start_margin=start_margin,
                )
                self.assertEqual(result["status"], "exact")
                self.assertAlmostEqual(result["growth"], growth, places=9)

    def test_ten_year_constant_growth_case_matches_closed_form(self):
        # With fade_start equal to the horizon and a constant margin, the
        # explicit forecast is a geometric series. This closed form is
        # independent of the engine's year-by-year projection loop.
        revenue, growth, margin = 1_000.0, 0.10, 0.20
        discount, terminal_growth, years = 0.10, 0.02, 10
        ratio = (1 + growth) / (1 + discount)
        if math.isclose(ratio, 1.0):
            forecast_pv = revenue * margin * years
        else:
            forecast_pv = revenue * margin * ratio * (1 - ratio**years) / (1 - ratio)
        final_fcf = revenue * (1 + growth) ** years * margin
        terminal_pv = (
            final_fcf * (1 + terminal_growth)
            / (discount - terminal_growth)
            / (1 + discount) ** years
        )
        independently_expected = forecast_pv + terminal_pv
        self.assertAlmostEqual(forecast_pv, 2_000.0, places=8)
        self.assertAlmostEqual(terminal_pv, 2_550.0, places=8)
        self.assertAlmostEqual(independently_expected, 4_550.0, places=8)

        value = dcf_enterprise_value(
            revenue,
            growth,
            margin,
            discount,
            terminal_growth,
            years=years,
            fade_start=years,
            start_margin=margin,
        )

        self.assertAlmostEqual(value, independently_expected, places=8)

    def test_quote_subunit_scaling_is_exact_and_never_substitutes_for_fx(self):
        cases = (
            ("GBp", "GBP", 0.01),
            ("GBP", "GBp", 100.0),
            ("GBX", "GBp", 1.0),
            ("ZAc", "ZAR", 0.01),
            ("ILA", "ILS", 0.01),
        )
        for source, target, expected in cases:
            with self.subTest(source=source, target=target):
                self.assertEqual(fixed_quote_currency_conversion_rate(source, target), expected)

        self.assertIsNone(fixed_quote_currency_conversion_rate("GBp", "EUR"))
        self.assertIsNone(fixed_quote_currency_conversion_rate("USD", "USD"))

    def test_exact_solver_bound_returns_the_bound_growth(self):
        for boundary in (SOLVER_LOW, SOLVER_HIGH):
            with self.subTest(boundary=boundary):
                target = dcf_enterprise_value(200, boundary, 0.18, 0.09, 0.02)
                result = solve_required_growth_detail(target, 200, 0.18, 0.09, 0.02)
                self.assertEqual(result["status"], "exact")
                self.assertEqual(result["growth"], boundary)

    def test_reverse_dcf_does_not_claim_exact_growth_when_model_value_is_flat(self):
        target = dcf_enterprise_value(
            200, 0.35, 0.18, 0.09, 0.02, years=1, fade_start=0, start_margin=0.12
        )
        result = solve_required_growth_detail(
            target, 200, 0.18, 0.09, 0.02, years=1, fade_start=0, start_margin=0.12
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["growth"])

    def test_reverse_dcf_round_trips_across_horizons_fade_boundaries_and_margin_paths(self):
        for years in (1, 2, 10, 50):
            for fade_start in sorted({0, years // 2, years}):
                for discount_rate, terminal_growth in ((0.04, 0.03), (0.09, 0.02)):
                    for start_margin, mature_margin in ((0.02, 0.25), (0.25, 0.02)):
                        low_value = dcf_enterprise_value(
                            1_000, SOLVER_LOW, mature_margin, discount_rate,
                            terminal_growth, years, fade_start, start_margin,
                        )
                        high_value = dcf_enterprise_value(
                            1_000, SOLVER_HIGH, mature_margin, discount_rate,
                            terminal_growth, years, fade_start, start_margin,
                        )
                        for growth in (SOLVER_LOW, -0.10, 0.0, 0.4, SOLVER_HIGH):
                            with self.subTest(
                                years=years, fade_start=fade_start,
                                discount_rate=discount_rate, start_margin=start_margin,
                                mature_margin=mature_margin, growth=growth,
                            ):
                                target = dcf_enterprise_value(
                                    1_000, growth, mature_margin, discount_rate,
                                    terminal_growth, years, fade_start, start_margin,
                                )
                                result = solve_required_growth_detail(
                                    target, 1_000, mature_margin, discount_rate,
                                    terminal_growth, years, fade_start, start_margin,
                                )
                                unidentifiable = math.isclose(
                                    low_value, high_value,
                                    rel_tol=SOLVER_MIN_RELATIVE_VALUE_SPREAD,
                                    abs_tol=0.0,
                                )
                                if unidentifiable:
                                    self.assertEqual(result["status"], "unavailable")
                                    self.assertIsNone(result["growth"])
                                else:
                                    self.assertEqual(result["status"], "exact")
                                    self.assertAlmostEqual(result["growth"], growth, places=8)

    def test_out_of_range_targets_return_the_nearest_growth_bound(self):
        low_value = dcf_enterprise_value(200, SOLVER_LOW, 0.18, 0.09, 0.02)
        high_value = dcf_enterprise_value(200, SOLVER_HIGH, 0.18, 0.09, 0.02)
        below = solve_required_growth_detail(low_value * 0.5, 200, 0.18, 0.09, 0.02)
        above = solve_required_growth_detail(high_value * 2, 200, 0.18, 0.09, 0.02)

        self.assertEqual((below["status"], below["growth"]), ("below_range", SOLVER_LOW))
        self.assertEqual((above["status"], above["growth"]), ("above_range", SOLVER_HIGH))

    def test_targets_just_outside_solver_bounds_are_clamped_and_excluded_from_score(self):
        low_value = dcf_enterprise_value(200, SOLVER_LOW, 0.18, 0.09, 0.02)
        high_value = dcf_enterprise_value(200, SOLVER_HIGH, 0.18, 0.09, 0.02)
        cases = (
            (low_value * (1 - 5e-13), "below_range", SOLVER_LOW),
            (high_value * (1 + 5e-13), "above_range", SOLVER_HIGH),
        )
        for target, expected_status, expected_growth in cases:
            with self.subTest(expected_status=expected_status):
                result = solve_required_growth_detail(target, 200, 0.18, 0.09, 0.02)
                self.assertEqual((result["status"], result["growth"]), (expected_status, expected_growth))

                clamped = result["status"] in {"below_range", "above_range"}
                trace = supported_score_breakdown(
                    {"score": 70, "evidence_coverage": 1.0},
                    {"score": 70, "evidence_coverage": 1.0},
                    historical_growth=0.08,
                    required_growth=result["growth"],
                    growth_clamped=clamped,
                )
                self.assertFalse(trace["growth_comparison_available"])
                self.assertEqual(
                    {item["key"] for item in trace["components"]},
                    {"business_quality", "financial_strength"},
                )

    def test_enterprise_value_is_monotonic_across_supported_growth_bounds(self):
        values = [
            dcf_enterprise_value(1_000, growth, 0.15, 0.09, 0.02)
            for growth in (SOLVER_LOW, 0.02, SOLVER_HIGH)
        ]

        self.assertIsNotNone(values[0])
        self.assertIsNotNone(values[1])
        self.assertIsNotNone(values[2])
        self.assertLess(values[0], values[1])
        self.assertLess(values[1], values[2])

    def test_reverse_solver_round_trips_growth_across_supported_domain_and_margin_paths(self):
        cases = (
            (SOLVER_LOW, 0.08, 0.12),
            (-0.10, 0.05, 0.20),
            (0.0, 0.03, 0.10),
            (0.024, 0.20, 0.20),
            (0.15, 0.10, 0.30),
            (0.40, 0.25, 0.35),
            (SOLVER_HIGH, 0.40, 0.50),
        )
        for growth, start_margin, mature_margin in cases:
            with self.subTest(growth=growth, start_margin=start_margin, mature_margin=mature_margin):
                target = dcf_enterprise_value(
                    1_000, growth, mature_margin, 0.11, 0.02,
                    start_margin=start_margin,
                )
                result = solve_required_growth_detail(
                    target, 1_000, mature_margin, 0.11, 0.02,
                    start_margin=start_margin,
                )

                self.assertIsNotNone(target)
                self.assertEqual(result["status"], "exact")
                self.assertAlmostEqual(result["growth"], growth, places=9)

    def test_reverse_solver_is_invariant_to_consistent_currency_unit_scaling(self):
        revenue = 1_000.0
        growth = 0.17
        enterprise_value = dcf_enterprise_value(revenue, growth, 0.18, 0.10, 0.02)

        for scale in (1e-9, 1.0, 1e9):
            with self.subTest(scale=scale):
                result = solve_required_growth_detail(
                    enterprise_value * scale,
                    revenue * scale,
                    0.18,
                    0.10,
                    0.02,
                )
                self.assertEqual(result["status"], "exact")
                self.assertAlmostEqual(result["growth"], growth, places=9)

    def test_dcf_and_reverse_solver_reject_boolean_nonfinite_and_invalid_parameters(self):
        base = {
            "revenue": 1_000.0,
            "growth": 0.10,
            "fcf_margin": 0.15,
            "discount_rate": 0.10,
            "terminal_growth": 0.02,
            "years": 10,
        }
        invalid_cases = (
            ("revenue", True),
            ("growth", float("nan")),
            ("fcf_margin", float("inf")),
            ("discount_rate", float("nan")),
            ("terminal_growth", 0.10),
            ("years", True),
            ("years", 0),
            ("growth", -1.0),
        )
        for field, value in invalid_cases:
            with self.subTest(field=field, value=value):
                inputs = {**base, field: value}
                self.assertIsNone(
                    dcf_enterprise_value(
                        inputs["revenue"], inputs["growth"], inputs["fcf_margin"],
                        inputs["discount_rate"], inputs["terminal_growth"], inputs["years"],
                    )
                )
                if field != "growth":
                    result = solve_required_growth_detail(
                        2_000.0,
                        inputs["revenue"],
                        inputs["fcf_margin"],
                        inputs["discount_rate"],
                        inputs["terminal_growth"],
                        inputs["years"],
                    )
                    self.assertEqual(result["status"], "unavailable")
                    self.assertIsNone(result["growth"])

        for target in (True, float("nan"), float("inf"), 0.0, -1.0):
            with self.subTest(target=target):
                result = solve_required_growth_detail(
                    target, base["revenue"], base["fcf_margin"], base["discount_rate"],
                    base["terminal_growth"], base["years"],
                )
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["growth"])


class HistoricalRevenueCagrPeriodTests(unittest.TestCase):
    def test_dated_revenue_cagr_sorts_periods_and_uses_actual_elapsed_days(self):
        observations = [
            ("2025-12-31", 121.0),
            ("2024-12-31", 110.0),
            ("2023-12-31", 100.0),
        ]
        growth, years, start, end = history_cagr_periods(observations)
        elapsed_days = (date(2025, 12, 31) - date(2023, 12, 31)).days
        elapsed_years = elapsed_days / 365.2425
        self.assertAlmostEqual(growth, (121.0 / 100.0) ** (1 / elapsed_years) - 1)
        self.assertAlmostEqual(years, elapsed_years)
        self.assertEqual((start, end), ("2023-12-31", "2025-12-31"))

    def test_conflicting_duplicate_period_is_unavailable_independent_of_row_order(self):
        rows = [
            ("2025-12-31", 121.0),
            ("2024-12-31", 110.0),
            ("2024-12-31", 111.0),
            ("2023-12-31", 100.0),
        ]
        for values in (rows, list(reversed(rows))):
            with self.subTest(values=values):
                self.assertEqual(history_cagr_periods(values), (None, 0.0, None, None))

    def test_gap_stops_history_instead_of_skipping_a_missing_annual_period(self):
        observations = [
            ("2025-12-31", 110.0),
            ("2024-12-31", 100.0),
            ("2023-01-01", 80.0),
            ("2022-01-01", 60.0),
        ]
        growth, years, start, end = history_cagr_periods(observations)
        elapsed_years = (date(2025, 12, 31) - date(2024, 12, 31)).days / 365.2425
        self.assertAlmostEqual(growth, (110.0 / 100.0) ** (1 / elapsed_years) - 1)
        self.assertAlmostEqual(years, elapsed_years)
        self.assertEqual((start, end), ("2024-12-31", "2025-12-31"))

    def test_nonpositive_revenue_within_selected_history_withholds_cagr(self):
        observations = [("2025-12-31", 110.0), ("2024-12-31", 0.0)]
        self.assertEqual(
            history_cagr_periods(observations),
            (None, 0.0, "2024-12-31", "2025-12-31"),
        )

    def test_invalid_or_ambiguous_period_values_do_not_enter_the_cagr(self):
        cases = (
            None,
            [],
            [("2025-13-31", 110.0), ("2024-12-31", 100.0)],
            [("2025-12-31", float("nan")), ("2024-12-31", 100.0)],
        )
        for observations in cases:
            with self.subTest(observations=observations):
                self.assertEqual(history_cagr_periods(observations), (None, 0.0, None, None))


if __name__ == "__main__":
    unittest.main()
