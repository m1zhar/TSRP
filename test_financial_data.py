import unittest

import pandas as pd

from financial_data import (
    compute_fcf,
    compute_fcf_detail,
    latest_period_aligned_fcf,
    period_aligned_ttm_fcf,
    trailing_aligned_fcf_margins,
)


def statement(rows):
    return pd.DataFrame(rows, index=["2024-12-31", "2023-12-31"]).T


class FinancialDataAlignmentTests(unittest.TestCase):
    def test_fcf_uses_matching_revenue_period(self):
        revenue = statement({"Total Revenue": [120, 100]})
        cashflow = statement({
            "Operating Cash Flow": [30, 25],
            "Capital Expenditure": [-10, -8],
        })
        value, period = latest_period_aligned_fcf(cashflow, revenue, ["Total Revenue"])
        self.assertEqual(value, 20)
        self.assertEqual(str(period)[:10], "2024-12-31")

    def test_timestamp_periods_are_normalized_for_provider_alignment(self):
        periods = pd.to_datetime(["2024-12-31", "2023-12-31"])
        revenue = pd.DataFrame([[120, 100]], index=["Total Revenue"], columns=periods)
        cashflow = pd.DataFrame(
            [[30, 25], [-10, -8]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=periods,
        )

        value, period = latest_period_aligned_fcf(cashflow, revenue, ["Total Revenue"])

        self.assertEqual(value, 20)
        self.assertEqual(period, "2024-12-31")

    def test_missing_cash_flow_period_is_not_paired_by_position(self):
        revenue = pd.DataFrame(
            [[120, 100]], index=["Total Revenue"], columns=["2024-12-31", "2023-12-31"]
        )
        cashflow = pd.DataFrame(
            [[25], [-8]], index=["Operating Cash Flow", "Capital Expenditure"], columns=["2023-12-31"]
        )
        value, period = latest_period_aligned_fcf(cashflow, revenue, ["Total Revenue"])
        self.assertEqual(value, 17)
        self.assertEqual(str(period)[:10], "2023-12-31")

    def test_trailing_margins_only_use_period_aligned_rows(self):
        revenue = statement({"Total Revenue": [120, 100]})
        cashflow = pd.DataFrame(
            [[30, 25], [-10, -8]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=["2024-12-31", "2022-12-31"],
        )
        self.assertEqual(trailing_aligned_fcf_margins(cashflow, revenue, ["Total Revenue"], years=4), [20 / 120])

    def test_ttm_requires_four_periods_with_usable_fcf(self):
        periods = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
        cashflow = pd.DataFrame(
            [[30, 28, 26, 24], [-10, -9, -8, -7]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=periods,
        )
        starts = {"2024-12-31": "2024-10-01", "2024-09-30": "2024-07-01", "2024-06-30": "2024-04-01", "2024-03-31": "2024-01-01"}
        value, period = period_aligned_ttm_fcf(cashflow, period_starts=starts)
        self.assertEqual(value, 74)
        self.assertEqual(str(period)[:10], periods[0])

        incomplete = cashflow.iloc[:, :3]
        self.assertEqual(period_aligned_ttm_fcf(incomplete, period_starts=starts), (None, None))

    def test_ttm_rejects_unverified_and_year_to_date_durations(self):
        periods = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
        cashflow = pd.DataFrame([[30, 28, 26, 24], [-10, -9, -8, -7]], index=["Operating Cash Flow", "Capital Expenditure"], columns=periods)
        self.assertEqual(period_aligned_ttm_fcf(cashflow), (None, None))
        ytd_starts = {period: "2024-01-01" for period in periods}
        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=ytd_starts), (None, None))

    def test_ttm_rejects_non_four_period_request(self):
        self.assertEqual(period_aligned_ttm_fcf(pd.DataFrame(), periods=5, period_starts={}), (None, None))

    def test_zero_is_a_valid_cash_flow_value(self):
        self.assertEqual(compute_fcf(0, 0, 99), 0)

    def test_fcf_source_identifies_reported_fallback(self):
        self.assertEqual(compute_fcf_detail(None, -5, 12), (12.0, "provider-reported free cash flow; OCF or capex unavailable"))

    def test_ttm_rejects_four_annual_periods(self):
        cashflow = pd.DataFrame(
            [[30, 28, 26, 24], [-10, -9, -8, -7]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"],
        )
        starts = {period: f"{int(period[:4])-1}-12-31" for period in ["2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"]}
        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=starts), (None, None))


if __name__ == "__main__":
    unittest.main()
