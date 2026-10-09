import unittest

from backend.engine import (
    choose_model_fcf_margin,
    dcf_enterprise_value,
    expectation_score,
    history_cagr,
    history_cagr_periods,
    model_rates,
    reality_score,
    score_breakdown,
    snapshot_diff,
    solve_required_growth,
    solve_required_growth_detail,
    supported_score_breakdown,
    year_growth,
)


class RateTests(unittest.TestCase):
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

    def test_invalid_discount_terminal_pair_is_unavailable(self):
        self.assertIsNone(dcf_enterprise_value(100, 0.08, 0.15, 0.03, 0.03))

    def test_missing_or_nonfinite_growth_is_unavailable(self):
        self.assertIsNone(dcf_enterprise_value(100, None, 0.15, 0.08, 0.02))
        self.assertIsNone(dcf_enterprise_value(100, float("nan"), 0.15, 0.08, 0.02))

    def test_invalid_projection_inputs_do_not_produce_numbers(self):
        self.assertIsNone(year_growth(0.10, 0.03, float("inf")))
        self.assertIsNone(dcf_enterprise_value(100, 1e308, 0.15, 0.08, 0.02))
        self.assertIsNone(dcf_enterprise_value(100, 0.08, 0.15, 0.08, 0.02, years=51))

    def test_reverse_solver_rejects_nonpositive_enterprise_value(self):
        self.assertEqual(solve_required_growth_detail(0, 100, .15, .08, .02)["status"], "unavailable")
        self.assertEqual(solve_required_growth_detail(-1, 100, .15, .08, .02)["status"], "unavailable")

    def test_round_trip_solver(self):
        target = dcf_enterprise_value(200, 0.12, 0.18, 0.09, 0.02)
        implied, hit = solve_required_growth(target, 200, 0.18, 0.09, 0.02)
        self.assertFalse(hit)
        self.assertAlmostEqual(implied, 0.12, places=2)


class HistoryAndMarginTests(unittest.TestCase):
    def test_longer_history_cagr(self):
        # newest first: 160, 140, 120, 100 over 3 years -> ~17%
        growth, years = history_cagr([160, 140, 120, 100], max_years=10)
        self.assertEqual(years, 3)
        self.assertAlmostEqual(growth, (160 / 100) ** (1 / 3) - 1, places=6)

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
        from engine import average_positive_margins
        self.assertAlmostEqual(average_positive_margins([0.10, -0.20, 0.0]), -0.10 / 3)


class ScoreTests(unittest.TestCase):
    def test_negative_sales_multiple_is_missing_evidence(self):
        model = {
            "expectation_benchmarks": {"ev_sales": 4.0, "pe": 20.0, "ev_ebitda": 12.0},
            "expectation_weights": {"required_growth": 0.40, "ev_sales": 0.20, "pe": 0.20, "ev_ebitda": 0.20},
        }
        negative = expectation_score(0.10, -1.0, 20.0, 12.0, model)
        missing = expectation_score(0.10, None, 20.0, 12.0, model)
        self.assertEqual(negative, missing)

    def test_clamped_growth_does_not_dominate_score(self):
        open_score = reality_score(70, 70, 0.08, 0.10, 0.09, growth_clamped=False)
        clamped = reality_score(70, 70, 0.08, 1.19, 0.09, growth_clamped=True)
        self.assertGreater(clamped, 40)
        self.assertLess(abs(clamped - 70 * 0.35 - 70 * 0.30 - 40 * 0.35), 1.5)
        self.assertIsInstance(open_score, float)

    def test_breakdown_weights_and_contributions_reconcile(self):
        trace = score_breakdown(80, 60, 0.08, 0.10, 0.09)
        self.assertEqual([item["weight"] for item in trace["components"]], [0.35, 0.30, 0.20, 0.15])
        self.assertAlmostEqual(sum(item["contribution"] for item in trace["components"]), trace["score"])
        self.assertAlmostEqual(trace["score"], reality_score(80, 60, 0.08, 0.10, 0.09))

    def test_score_is_bounded_and_nonfinite_inputs_are_unavailable(self):
        bounded = score_breakdown(10_000, -10_000, 0.10, 0.10, 0.10)["score"]
        self.assertTrue(0 <= bounded <= 100)
        trace = score_breakdown(float("nan"), 70, float("inf"), float("nan"), float("-inf"))
        self.assertTrue(0 <= trace["score"] <= 100)
        self.assertFalse(trace["growth_comparison_available"])
        self.assertEqual(trace["components"][2]["value"], 40)
        self.assertEqual(trace["components"][3]["value"], 40)

    def test_score_v3_does_not_impute_missing_growth_comparisons(self):
        trace = supported_score_breakdown(
            {"score": 80, "evidence_coverage": 1.0},
            {"score": 70, "evidence_coverage": 1.0},
            historical_growth=None, required_growth=0.12, consensus_growth=None,
        )
        self.assertIsNone(trace["score"])
        self.assertAlmostEqual(trace["evidence_coverage"], 0.65)
        self.assertIsNone(trace["components"][2]["value"])
        self.assertIsNone(trace["components"][3]["value"])

    def test_score_v3_rebalances_only_when_evidence_threshold_is_met(self):
        trace = supported_score_breakdown(
            {"score": 80, "evidence_coverage": 1.0},
            {"score": 70, "evidence_coverage": 1.0},
            historical_growth=0.08, required_growth=0.12, consensus_growth=None,
        )
        self.assertIsNotNone(trace["score"])
        self.assertAlmostEqual(trace["evidence_coverage"], 0.85)
        self.assertAlmostEqual(sum(item["contribution"] or 0 for item in trace["components"]), trace["score"])

    def test_score_v3_extreme_factor_overflow_is_not_a_zero_score_observation(self):
        from engine import business_quality_detail
        model = {"business_benchmarks": {"gross": .7, "operating": .3, "fcf": .2, "roe": .2}, "business_weights": {"growth": .25, "gross": .2, "operating": .15, "fcf": .2, "roe": .1, "balance": .1}}
        detail = business_quality_detail(None, 1e308, None, None, None, None, model)
        gross = next(item for item in detail["components"] if item["key"] == "gross")
        self.assertIsNone(gross["value"])


class SnapshotTests(unittest.TestCase):
    def test_comparable_snapshot_difference(self):
        previous = {"methodology_version": "score-v1", "score": 60, "revenue": 100, "solver_status": "exact"}
        current = {"methodology_version": "score-v1", "score": 65, "revenue": 110, "solver_status": "exact"}
        diff = snapshot_diff(previous, current)
        self.assertTrue(diff["comparable"])
        self.assertEqual(diff["reason"], "compatible")
        self.assertEqual({change["field"] for change in diff["changes"]}, {"score", "revenue"})
        self.assertAlmostEqual(next(change["delta"] for change in diff["changes"] if change["field"] == "score"), 5)

    def test_incompatible_snapshot_methodology_is_not_compared(self):
        diff = snapshot_diff({"methodology_version": "score-v1"}, {"methodology_version": "score-v2"})
        self.assertFalse(diff["comparable"])
        self.assertEqual(diff["reason"], "methodology_mismatch")
        self.assertEqual(diff["changes"], [])

    def test_user_assumption_change_is_tracked(self):
        previous = {"methodology_version": "score-v1", "user_assumptions": {"discount_rate": 0.10}}
        current = {"methodology_version": "score-v1", "user_assumptions": {"discount_rate": 0.12}}
        diff = snapshot_diff(previous, current)
        self.assertTrue(diff["comparable"])
        self.assertEqual(diff["changes"][0]["field"], "user_assumptions")


if __name__ == "__main__":
    unittest.main()
