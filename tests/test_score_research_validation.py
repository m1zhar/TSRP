"""Synthetic implementation checks, not a historical investment backtest."""
import itertools
import math
import random
import unittest

from engine import business_quality_detail, financial_strength_detail, supported_score_breakdown
from methodology import SECTOR_MODELS, get_sector_model


def bounded(value):
    return min(100, max(0, value))


def score(sector, values):
    growth, operating, roe, cash, debt, fcf, leverage = values
    model = get_sector_model(sector)
    business = business_quality_detail(growth, None, operating, fcf, roe, leverage, model)
    financial = financial_strength_detail(cash, debt, operating, fcf, leverage, model)
    return supported_score_breakdown(business, financial, growth, .9, .8)


class ScoreResearchValidationTests(unittest.TestCase):
    def test_independent_arithmetic_oracle_for_every_configured_sector(self):
        """No oracle calls into the score implementation or its clamp helper."""
        rng = random.Random(10242026)
        for sector in SECTOR_MODELS:
            model = get_sector_model(sector)
            for _ in range(40):
                growth, operating, roe, fcf = [rng.uniform(-.2, .4) for _ in range(4)]
                cash, debt, leverage = rng.uniform(1, 100), rng.uniform(1, 100), rng.uniform(0, 1)
                expected_factors = [
                    bounded(50 + growth * 250),
                    bounded(operating / model["business_benchmarks"]["operating"] * 100),
                    bounded(roe / model["business_benchmarks"]["roe"] * 100),
                    bounded(50 + cash / debt * 35),
                    bounded(fcf / model["financial_benchmarks"]["fcf"] * 100),
                    bounded(100 - leverage / .8 * 100),
                ]
                with self.subTest(sector=sector, values=(growth, operating, roe, cash, debt, fcf, leverage)):
                    trace = score(sector, (growth, operating, roe, cash, debt, fcf, leverage))
                    self.assertAlmostEqual(trace["score"], math.fsum(expected_factors) / 6, places=11)
                    self.assertEqual(trace["evidence_coverage"], 1)

    def test_beneficial_factor_changes_never_lower_the_score(self):
        base = (.05, .1, .1, 20, 80, .05, .4)
        # Cash/debt and debt/assets are independent declared inputs here; this
        # tests each transform, not a purported coherent historical balance sheet.
        for sector in SECTOR_MODELS:
            original = score(sector, base)["score"]
            for index, change in [(0, .02), (1, .02), (2, .02), (3, 5), (4, -5), (5, .02), (6, -.05)]:
                values = list(base)
                values[index] += change
                with self.subTest(sector=sector, factor=index):
                    self.assertGreaterEqual(score(sector, values)["score"], original)

    def test_all_missing_factor_combinations_obey_declared_coverage_gates(self):
        for sector in SECTOR_MODELS:
            for mask in itertools.product((False, True), repeat=6):
                values = [.05, .1, .1, 20, 80, .05, .4]
                for present, index in zip(mask, (0, 1, 2, 3, 5, 6)):
                    if not present:
                        values[index] = None
                trace = score(sector, values)
                supported_groups = sum(group["score"] is not None for group in trace["subscores"].values())
                expected_available = sum(mask) / 6 >= .7 and supported_groups == 2
                with self.subTest(sector=sector, mask=mask):
                    self.assertEqual(trace["score"] is not None, expected_available)
                    self.assertAlmostEqual(trace["evidence_coverage"], sum(mask) / 6)
                    if expected_available:
                        self.assertGreaterEqual(trace["score"], 0)
                        self.assertLessEqual(trace["score"], 100)

    def test_nominal_currency_scaling_does_not_change_score(self):
        for sector in SECTOR_MODELS:
            baseline = score(sector, (.05, .1, .1, 20, 80, .05, .4))["score"]
            for scale in (.01, 100, 1_000_000):
                self.assertAlmostEqual(score(sector, (.05, .1, .1, 20 * scale, 80 * scale, .05, .4))["score"], baseline)

    def test_zero_and_negative_cash_flow_remain_distinct_from_missing(self):
        for sector in SECTOR_MODELS:
            zero = score(sector, (.05, .1, .1, 20, 80, 0, .4))
            negative = score(sector, (.05, .1, .1, 20, 80, -.1, .4))
            missing = score(sector, (.05, .1, .1, 20, 80, None, .4))
            self.assertEqual(zero["subscores"]["financial_strength"]["components"][1]["value"], 0)
            self.assertEqual(negative["subscores"]["financial_strength"]["components"][1]["value"], 0)
            self.assertIsNone(missing["subscores"]["financial_strength"]["components"][1]["value"])
            self.assertEqual(zero["evidence_coverage"], 1)
            self.assertLess(missing["evidence_coverage"], 1)

    def test_no_factor_is_repeated_across_groups_and_sensitivity_encloses_score(self):
        for sector in SECTOR_MODELS:
            trace = score(sector, (.05, .1, .1, 20, 80, .05, .4))
            keys = [factor["key"] for group in trace["subscores"].values() for factor in group["components"]]
            self.assertEqual(len(keys), 6)
            self.assertEqual(len(set(keys)), 6)
            self.assertLessEqual(trace["weight_sensitivity"]["low"], trace["score"])
            self.assertGreaterEqual(trace["weight_sensitivity"]["high"], trace["score"])


if __name__ == "__main__":
    unittest.main()
