import math
import unittest

from methodology import DEFAULT_SECTOR_MODEL, SECTOR_MODELS, get_sector_model, has_sector_calibration


class SectorMethodologyIntegrityTests(unittest.TestCase):
    def test_every_sector_and_default_model_has_complete_normalized_weights(self):
        expected = {
            "business_weights": {"growth", "operating", "roe"},
            "expectation_weights": {"required_growth", "ev_sales", "pe", "ev_ebitda"},
            "financial_weights": {"cash_debt", "fcf", "leverage"},
        }

        for name, model in [("default", DEFAULT_SECTOR_MODEL), *SECTOR_MODELS.items()]:
            for field, expected_keys in expected.items():
                with self.subTest(model=name, weights=field):
                    weights = model[field]
                    self.assertEqual(set(weights), expected_keys)
                    self.assertTrue(all(math.isfinite(value) and value >= 0 for value in weights.values()))
                    self.assertAlmostEqual(sum(weights.values()), 1.0, places=12)

    def test_every_calibration_has_complete_positive_finite_benchmarks(self):
        expected = {
            "business_benchmarks": {"gross", "operating", "fcf", "roe"},
            "expectation_benchmarks": {"ev_sales", "pe", "ev_ebitda"},
            "financial_benchmarks": {"operating", "fcf"},
        }

        for name, model in [("default", DEFAULT_SECTOR_MODEL), *SECTOR_MODELS.items()]:
            for field, expected_keys in expected.items():
                with self.subTest(model=name, benchmarks=field):
                    benchmarks = model[field]
                    self.assertEqual(set(benchmarks), expected_keys)
                    self.assertTrue(all(math.isfinite(value) and value > 0 for value in benchmarks.values()))

    def test_sector_calibration_copies_cannot_mutate_shared_methodology(self):
        original = SECTOR_MODELS["Technology"]["business_weights"]["growth"]
        returned = get_sector_model("Technology")
        returned["business_weights"]["growth"] = 0
        self.assertEqual(SECTOR_MODELS["Technology"]["business_weights"]["growth"], original)
        self.assertEqual(get_sector_model("Technology")["business_weights"]["growth"], original)

    def test_unknown_sector_uses_default_baseline_but_is_not_claimed_as_calibrated(self):
        self.assertFalse(has_sector_calibration("Unlisted Sector"))
        self.assertEqual(get_sector_model("Unlisted Sector"), DEFAULT_SECTOR_MODEL)


if __name__ == "__main__":
    unittest.main()
