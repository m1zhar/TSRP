import unittest

from engine import (
    choose_model_fcf_margin,
    dcf_enterprise_value,
    history_cagr,
    model_rates,
    reality_score,
    solve_required_growth,
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

    def test_refuses_negative_latest_without_positive_history(self):
        margin, refused, _ = choose_model_fcf_margin(-0.04, [-0.02, -0.01], 0.12)
        self.assertTrue(refused)
        self.assertIsNone(margin)

    def test_does_not_invent_mature_margin(self):
        margin, refused, _ = choose_model_fcf_margin(None, [], 0.12)
        self.assertTrue(refused)
        self.assertIsNone(margin)


class ScoreTests(unittest.TestCase):
    def test_clamped_growth_does_not_dominate_score(self):
        open_score = reality_score(70, 70, 0.08, 0.10, 0.09, growth_clamped=False)
        clamped = reality_score(70, 70, 0.08, 1.19, 0.09, growth_clamped=True)
        self.assertGreater(clamped, 40)
        self.assertLess(abs(clamped - 70 * 0.35 - 70 * 0.30 - 40 * 0.35), 1.5)
        self.assertIsInstance(open_score, float)


if __name__ == "__main__":
    unittest.main()
