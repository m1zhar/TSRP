import unittest

from chart_utils import scale_price_values, volume_bar_colors


class PriceScalingTests(unittest.TestCase):
    def test_overflowed_or_invalid_prices_are_unavailable_not_infinite(self):
        scaled = scale_price_values([1.2, 1e308, None], 1e308)
        self.assertEqual(scaled, [1.2e308, None, None])

    def test_valid_prices_are_scaled_and_invalid_rates_are_rejected(self):
        self.assertEqual(scale_price_values([2.0, 3.0], 1.5), [3.0, 4.5])
        self.assertIsNone(scale_price_values([2.0], float("inf")))
        self.assertIsNone(scale_price_values([2.0], 0))


class VolumeBarColorTests(unittest.TestCase):
    def test_uses_direction_only_for_valid_comparable_prices(self):
        self.assertEqual(
            volume_bar_colors(
                [10, 10, 10, None, 10],
                [11, 9, 10, 12, float("inf")],
                up_color="up",
                down_color="down",
                neutral_color="neutral",
            ),
            ["up", "down", "neutral", "neutral", "neutral"],
        )

    def test_missing_or_mismatched_open_values_are_neutral(self):
        kwargs = {"up_color": "up", "down_color": "down", "neutral_color": "neutral"}
        self.assertEqual(volume_bar_colors(None, [1, 2], **kwargs), ["neutral", "neutral"])
        self.assertEqual(volume_bar_colors([1], [1, 2], **kwargs), ["neutral", "neutral"])


if __name__ == "__main__":
    unittest.main()
