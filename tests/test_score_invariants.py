import math
import unittest

from engine import supported_score_breakdown


class ScoreAggregationInvariantTests(unittest.TestCase):
    def test_score_and_trace_remain_consistent_over_boundary_and_invalid_inputs(self):
        scores = (None, True, float("nan"), float("inf"), -float("inf"), -5, 0, 50, 100, 105)
        coverages = (None, float("nan"), -float("inf"), -0.1, 0, 0.35, 0.5, 0.7, 1, 2)

        for business_score in scores:
            for business_coverage in coverages:
                for financial_score in scores:
                    for financial_coverage in coverages:
                        with self.subTest(
                            business=(business_score, business_coverage),
                            financial=(financial_score, financial_coverage),
                        ):
                            trace = supported_score_breakdown(
                                {"score": business_score, "evidence_coverage": business_coverage},
                                {"score": financial_score, "evidence_coverage": financial_coverage},
                                historical_growth=0.1,
                                required_growth=-0.2,
                                consensus_growth=0.9,
                            )
                            components = trace["components"]
                            coverage = trace["evidence_coverage"]
                            valid_weight = math.fsum(
                                component["weight"]
                                for component in components
                                if component["value"] is not None
                            )

                            self.assertTrue(math.isfinite(coverage))
                            self.assertGreaterEqual(coverage, 0)
                            self.assertLessEqual(coverage, 1)
                            for component in components:
                                self.assertGreaterEqual(component["evidence_coverage"], 0)
                                self.assertLessEqual(component["evidence_coverage"], 1)
                                if component["value"] is not None:
                                    self.assertTrue(math.isfinite(component["value"]))
                                    self.assertGreaterEqual(component["value"], 0)
                                    self.assertLessEqual(component["value"], 100)

                            gates_pass = (
                                coverage >= trace["minimum_evidence_coverage"]
                                and valid_weight >= trace["minimum_evidence_coverage"]
                            )
                            if not gates_pass:
                                self.assertIsNone(trace["score"])
                                self.assertTrue(all(item["contribution"] is None for item in components))
                                self.assertTrue(all(item["applied_weight"] == 0 for item in components))
                                continue

                            self.assertIsNotNone(trace["score"])
                            self.assertTrue(math.isfinite(trace["score"]))
                            self.assertGreaterEqual(trace["score"], 0)
                            self.assertLessEqual(trace["score"], 100)
                            self.assertAlmostEqual(
                                math.fsum(item["applied_weight"] for item in components), 1.0, places=14
                            )
                            self.assertAlmostEqual(
                                math.fsum(item["contribution"] or 0 for item in components),
                                trace["score"],
                                places=12,
                            )


if __name__ == "__main__":
    unittest.main()
