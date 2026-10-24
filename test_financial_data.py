import unittest

import pandas as pd

from financial_data import (
    annual_filing_records,
    annual_filing_observations,
    compute_fcf,
    compute_fcf_detail,
    enterprise_value_from_components,
    finite_product,
    finite_quotient,
    latest_period_aligned_fcf,
    period_aligned_ttm_fcf,
    period_aligned_ratio,
    period_observations,
    period_observations_for_aliases,
    perioded_record,
    perioded_financial_value,
    records_share_period,
    sum_perioded_records,
    safe_number,
    valid_nonnegative_amounts,
    trailing_aligned_fcf_margins,
)
from engine import history_cagr_periods


def statement(rows):
    return pd.DataFrame(rows, index=["2024-12-31", "2023-12-31"]).T


class FinancialDataAlignmentTests(unittest.TestCase):
    def test_finite_product_rejects_overflowing_currency_conversion(self):
        self.assertEqual(finite_product(125, 0.8), 100)
        self.assertEqual(finite_product(-125, 0.8), -100)
        self.assertIsNone(finite_product(1e308, 1e308))
        self.assertIsNone(finite_product(12, float("inf")))

    def test_finite_quotient_rejects_zero_denominator_and_overflowing_fx_rate(self):
        self.assertEqual(finite_quotient(1, 2), 0.5)
        self.assertIsNone(finite_quotient(1, 0))
        self.assertIsNone(finite_quotient(1, 5e-324))

    def test_boolean_values_are_not_coerced_into_financial_amounts(self):
        pandas_boolean = pd.Series([True]).iloc[0]
        for value in (True, False, pandas_boolean):
            with self.subTest(value=repr(value)):
                self.assertIsNone(safe_number(value))
                self.assertIsNone(perioded_financial_value(value, "2025-12-31", as_of="2025-12-31")[0])
        self.assertIsNone(compute_fcf(True, 10))
        self.assertEqual(safe_number(12.5), 12.5)

    def test_financial_value_requires_a_verifiable_period_end_date(self):
        self.assertEqual(
            perioded_financial_value(125.5, "2025-12-31", as_of="2026-10-10"),
            (125.5, "2025-12-31"),
        )
        self.assertEqual(
            perioded_financial_value(0, pd.Timestamp("2025-12-31"), as_of="2025-12-31"),
            (0.0, "2025-12-31"),
        )
        for period in (None, "2025", "Latest annual", "not-a-date"):
            with self.subTest(period=period):
                self.assertEqual(perioded_financial_value(125.5, period, as_of="2025-12-31"), (None, None))
        self.assertEqual(perioded_financial_value(125.5, "2025-12-31"), (None, None))
        self.assertEqual(perioded_financial_value(float("nan"), "2025-12-31", as_of="2025-12-31"), (None, None))
        self.assertEqual(perioded_financial_value(125.5, "2025-12-31", as_of=pd.NaT), (None, None))
        self.assertEqual(perioded_financial_value(125.5, "2025-12-31", as_of="not-a-date"), (None, None))
        self.assertEqual(perioded_financial_value(125.5, "2026-01-01", as_of="2025-12-31"), (None, None))
        self.assertEqual(perioded_financial_value(125.5, "2025-12-31", as_of="2025-12-31"), (125.5, "2025-12-31"))

    def test_financial_value_obeys_statement_freshness_boundary(self):
        as_of = "2025-01-01"
        self.assertEqual(perioded_financial_value(10, "2023-07-01", as_of=as_of), (10.0, "2023-07-01"))
        self.assertEqual(perioded_financial_value(10, "2023-06-30", as_of=as_of), (None, None))

    def test_perioded_records_reject_stale_or_undated_values(self):
        fresh = {"value": 125, "period": "2025-12-31", "unit": "USD"}
        self.assertEqual(
            perioded_record(fresh, as_of="2026-10-10"),
            {"value": 125.0, "period": "2025-12-31", "unit": "USD"},
        )
        self.assertIsNone(perioded_record({**fresh, "period": "2024-04-29"}, as_of="2026-10-10"))
        self.assertIsNone(perioded_record({**fresh, "period": "2026-10-11"}, as_of="2026-10-10"))
        self.assertIsNone(perioded_record({"value": 125, "unit": "USD"}, as_of="2026-10-10"))

    def test_balance_sheet_components_sum_only_when_period_unit_and_freshness_match(self):
        current = {"value": 20, "period": "2025-12-31", "unit": "USD"}
        noncurrent = {"value": 80, "period": "2025-12-31", "unit": "USD"}
        combined = sum_perioded_records(current, noncurrent, as_of="2026-10-10")
        self.assertEqual(combined["value"], 100)
        self.assertEqual(combined["period"], "2025-12-31")
        self.assertEqual(len(combined["components"]), 2)
        self.assertIsNone(sum_perioded_records(current, {**noncurrent, "period": "2025-09-30"}, as_of="2026-10-10"))
        self.assertIsNone(sum_perioded_records(current, {**noncurrent, "unit": "EUR"}, as_of="2026-10-10"))
        self.assertIsNone(sum_perioded_records(current, {**noncurrent, "period": "2024-04-29"}, as_of="2026-10-10"))
        self.assertIsNone(sum_perioded_records(
            {**current, "value": 1e308}, {**noncurrent, "value": 1e308}, as_of="2026-10-10"
        ))

    def test_balance_sheet_comparisons_require_the_same_complete_period(self):
        cash = {"period": "2025-12-31"}
        debt = {"period": "2025-12-31"}
        self.assertTrue(records_share_period(cash, debt))
        self.assertFalse(records_share_period(cash, {"period": "2025-09-30"}))
        self.assertFalse(records_share_period(cash, {"period": "2025"}))
        self.assertFalse(records_share_period(cash, {}))

    def test_balance_arithmetic_rejects_negative_and_nonfinite_values(self):
        self.assertTrue(valid_nonnegative_amounts(0, 12.5))
        for values in ((-1, 12), (1, float("nan")), (1, float("inf")), (True, 12), ()):
            with self.subTest(values=values):
                self.assertFalse(valid_nonnegative_amounts(*values))

    def test_enterprise_value_uses_only_finite_nonnegative_components(self):
        self.assertEqual(enterprise_value_from_components(100, 25, 10), 115)
        self.assertEqual(enterprise_value_from_components(10, 0, 20), -10)
        for values in (
            (None, 25, 10),
            (100, -1, 10),
            (100, 25, float("inf")),
            (True, 25, 10),
            (1e308, 1e308, 0),
        ):
            with self.subTest(values=values):
                self.assertIsNone(enterprise_value_from_components(*values))

    def test_ratios_require_matching_full_periods_and_positive_denominators(self):
        self.assertEqual(period_aligned_ratio(25, "2025-12-31", 100, "2025-12-31"), 0.25)
        self.assertEqual(period_aligned_ratio(-25, "2025-12-31", 100, "2025-12-31"), -0.25)
        for values in (
            (25, "2025-12-31", 100, "2025-09-30"),
            (25, "2025", 100, "2025-01-01"),
            (25, "2025-12-31", 0, "2025-12-31"),
            (25, "2025-12-31", -100, "2025-12-31"),
            (True, "2025-12-31", 100, "2025-12-31"),
            (25, "2025-12-31", float("inf"), "2025-12-31"),
        ):
            with self.subTest(values=values):
                self.assertIsNone(period_aligned_ratio(*values))

    def test_annual_sec_facts_keep_periods_and_prefer_latest_amendment(self):
        rows = [
            {"form": "10-K", "fy": 2024, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-15", "val": 120},
            {"form": "10-K/A", "fy": 2024, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-03-01", "val": 110},
            {"form": "10-K", "fy": 2023, "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-15", "val": 100},
            {"form": "10-Q", "fy": 2024, "end": "2024-09-30", "filed": "2024-11-01", "val": 105},
            {"form": "10-K", "fy": 2022, "end": "not-a-date", "filed": "2023-02-15", "val": 90},
        ]

        latest_records = annual_filing_records(rows)
        observations = annual_filing_observations(rows)

        self.assertEqual(latest_records[0]["form"], "10-K/A")
        self.assertEqual(latest_records[0]["filed"], "2025-03-01")
        self.assertEqual(latest_records[0]["val"], 110)
        self.assertEqual(observations, [("2024-12-31", 110.0), ("2023-12-31", 100.0)])
        self.assertEqual((latest_records[0]["end"], float(latest_records[0]["val"])), observations[0])

    def test_sec_duration_filter_excludes_q4_value_with_same_end_as_fiscal_year(self):
        rows = [
            {"form": "10-K", "fy": 2024, "fp": "Q4", "start": "2024-10-01", "end": "2024-12-31", "filed": "2025-02-01", "accn": "same", "val": 30},
            {"form": "10-K", "fy": 2024, "fp": "FY", "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01", "accn": "same", "val": 100},
            {"form": "10-K/A", "fy": 2024, "fp": "FY", "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-03-01", "accn": "amended", "val": 105},
            {"form": "10-K", "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "accn": "prior", "val": 90},
        ]

        records = annual_filing_records(rows, kind="duration")
        observations = annual_filing_observations(rows)

        self.assertEqual(records[0]["val"], 105)
        self.assertEqual(observations, [("2024-12-31", 105.0), ("2023-12-31", 90.0)])

    def test_sec_instant_filter_excludes_duration_facts(self):
        rows = [
            {"form": "10-K", "fy": 2024, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-01", "accn": "same", "val": 100},
            {"form": "10-K", "fy": 2024, "end": "2024-12-31", "filed": "2025-02-01", "accn": "same", "val": 120},
        ]

        records = annual_filing_records(rows, kind="instant")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["val"], 120)

    def test_annual_sec_history_does_not_bridge_missing_fiscal_year(self):
        observations = annual_filing_observations([
            {"form": "10-K", "fy": 2024, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-15", "val": 140},
            {"form": "10-K", "fy": 2022, "start": "2022-01-01", "end": "2022-12-31", "filed": "2023-02-15", "val": 100},
        ])

        growth, elapsed_years, start, end = history_cagr_periods(observations)

        self.assertIsNone(growth)
        self.assertEqual(elapsed_years, 0.0)
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_sec_annual_rows_require_complete_period_end_and_filing_dates(self):
        rows = [
            {"form": "10-K", "fy": 2024, "start": "2024-01-01", "end": "2024-12-31", "filed": "2025-02-15", "val": 100},
            {"form": "10-K", "fy": 2025, "end": "2025", "filed": "2026-02-15", "val": 120},
            {"form": "10-K/A", "fy": 2024, "end": "2024-12-31", "filed": "2026", "val": 130},
        ]

        self.assertEqual(annual_filing_records(rows), [rows[0]])
        self.assertEqual(annual_filing_observations(rows), [("2024-12-31", 100.0)])

    def test_fcf_uses_matching_revenue_period(self):
        revenue = statement({"Total Revenue": [120, 100]})
        cashflow = statement({
            "Operating Cash Flow": [30, 25],
            "Capital Expenditure": [-10, -8],
        })
        value, period = latest_period_aligned_fcf(cashflow, revenue, ["Total Revenue"])
        self.assertEqual(value, 20)
        self.assertEqual(str(period)[:10], "2024-12-31")

    def test_fcf_uses_newer_period_from_an_equivalent_cashflow_alias(self):
        revenue = pd.DataFrame(
            [[120, 100]], index=["Total Revenue"], columns=["2025-12-31", "2024-12-31"]
        )
        cashflow = pd.DataFrame(
            [[25, None], [None, 45], [-8, None], [None, -15]],
            index=[
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
                "Capital Expenditure",
                "Capital Expenditures",
            ],
            columns=["2024-12-31", "2025-12-31"],
        )

        self.assertEqual(
            latest_period_aligned_fcf(cashflow, revenue, ["Total Revenue"]),
            (30, "2025-12-31"),
        )

    def test_equivalent_cashflow_aliases_reject_conflicting_duplicate_periods(self):
        cashflow = pd.DataFrame(
            [[25, None, None], [None, 20, None], [None, None, 18]],
            index=["Operating Cash Flow", "Total Cash From Operating Activities", "Operating Cash Flow"],
            columns=["2025-12-31", "2025-12-31", "2024-12-31"],
        )

        self.assertEqual(
            period_observations_for_aliases(
                cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]
            ),
            [("2024-12-31", 18.0)],
        )

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

    def test_statement_observations_require_complete_dates_and_sort_valid_periods(self):
        frame = pd.DataFrame(
            [[999, 202, 303, 404]],
            index=["Total Revenue"],
            columns=["2026", "2024-12-31", "not-a-date", "2025-12-31"],
        )

        self.assertEqual(
            period_observations(frame, ["Total Revenue"]),
            [("2025-12-31", 404.0), ("2024-12-31", 202.0)],
        )

    def test_year_only_statement_labels_cannot_create_aligned_fcf(self):
        revenue = pd.DataFrame([[120]], index=["Total Revenue"], columns=["2026"])
        cashflow = pd.DataFrame(
            [[30], [-10]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=["2026"],
        )

        self.assertEqual(latest_period_aligned_fcf(cashflow, revenue, ["Total Revenue"]), (None, None))

    def test_conflicting_duplicate_statement_period_is_unavailable(self):
        frame = pd.DataFrame(
            [[120, 150, 100, 100]],
            index=["Total Revenue"],
            columns=["2025-12-31", "2025-12-31", "2024-12-31", "2024-12-31"],
        )

        self.assertEqual(
            period_observations(frame, ["Total Revenue"]),
            [("2024-12-31", 100.0)],
        )

    def test_duplicate_statement_rows_with_same_value_are_collapsed(self):
        frame = pd.DataFrame(
            [[40, 30], [40, 30]],
            index=["Gross Profit", "Gross Profit"],
            columns=["2025-12-31", "2024-12-31"],
        )

        self.assertEqual(
            period_observations(frame, ["Gross Profit"]),
            [("2025-12-31", 40.0), ("2024-12-31", 30.0)],
        )

    def test_conflicting_duplicate_statement_rows_exclude_only_conflicted_period(self):
        frame = pd.DataFrame(
            [[45, 30], [40, 30]],
            index=["Gross Profit", "Gross Profit"],
            columns=["2025-12-31", "2024-12-31"],
        )

        expected = [("2024-12-31", 30.0)]
        self.assertEqual(period_observations(frame, ["Gross Profit"]), expected)
        self.assertEqual(period_observations(frame.iloc[::-1], ["Gross Profit"]), expected)

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

    def test_ttm_merges_equivalent_cashflow_aliases_by_period(self):
        periods = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
        cashflow = pd.DataFrame(
            [
                [30, None, 26, None],
                [None, 28, None, 24],
                [-10, None, -8, None],
                [None, -9, None, -7],
            ],
            index=[
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
                "Capital Expenditure",
                "Capital Expenditures",
            ],
            columns=periods,
        )
        starts = {
            "2024-12-31": "2024-10-01",
            "2024-09-30": "2024-07-01",
            "2024-06-30": "2024-04-01",
            "2024-03-31": "2024-01-01",
        }

        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=starts), (74, periods[0]))

    def test_ttm_rejects_a_quarter_with_conflicting_cashflow_aliases(self):
        periods = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
        cashflow = pd.DataFrame(
            [
                [30, 28, 26, 24],
                [29, None, None, None],
                [-10, -9, -8, -7],
            ],
            index=[
                "Operating Cash Flow",
                "Total Cash From Operating Activities",
                "Capital Expenditure",
            ],
            columns=periods,
        )
        starts = {
            "2024-12-31": "2024-10-01",
            "2024-09-30": "2024-07-01",
            "2024-06-30": "2024-04-01",
            "2024-03-31": "2024-01-01",
        }

        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=starts), (None, None))

    def test_ttm_rejects_unverified_and_year_to_date_durations(self):
        periods = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
        cashflow = pd.DataFrame([[30, 28, 26, 24], [-10, -9, -8, -7]], index=["Operating Cash Flow", "Capital Expenditure"], columns=periods)
        self.assertEqual(period_aligned_ttm_fcf(cashflow), (None, None))
        ytd_starts = {period: "2024-01-01" for period in periods}
        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=ytd_starts), (None, None))

    def test_ttm_rejects_overlapping_or_discontinuous_quarters(self):
        periods = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
        cashflow = pd.DataFrame(
            [[30, 28, 26, 24], [-10, -9, -8, -7]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=periods,
        )
        starts = {
            "2024-12-31": "2024-10-01",
            "2024-09-30": "2024-07-01",
            "2024-06-30": "2024-04-01",
            "2024-03-31": "2024-01-01",
        }

        overlapping = {**starts, "2024-12-31": "2024-09-20"}
        discontinuous = {**starts, "2024-12-31": "2024-10-03"}
        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=overlapping), (None, None))
        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=discontinuous), (None, None))

    def test_ttm_rejects_non_four_period_request(self):
        self.assertEqual(period_aligned_ttm_fcf(pd.DataFrame(), periods=5, period_starts={}), (None, None))

    def test_zero_is_a_valid_cash_flow_value(self):
        self.assertEqual(compute_fcf(0, 0, 99), 0)

    def test_negative_fcf_is_preserved_for_either_capex_sign_convention(self):
        calculation_method = "calculated: operating cash flow less capital spending"
        self.assertEqual(compute_fcf_detail(-25, -5, 999), (-30, calculation_method))
        self.assertEqual(compute_fcf_detail(-25, 5, 999), (-30, calculation_method))
        self.assertEqual(
            compute_fcf_detail(None, None, -12),
            (-12.0, "provider-reported free cash flow; OCF or capex unavailable"),
        )

    def test_fcf_component_arithmetic_rejects_finite_inputs_that_overflow(self):
        self.assertEqual(compute_fcf_detail(-1e308, 1e308), (None, None))

    def test_fcf_source_identifies_reported_fallback(self):
        self.assertEqual(compute_fcf_detail(None, -5, 12), (12.0, "provider-reported free cash flow; OCF or capex unavailable"))

    def test_trailing_fcf_margin_rejects_nonfinite_ratio(self):
        revenue = pd.DataFrame([[1e-308]], index=["Total Revenue"], columns=["2024-12-31"])
        cashflow = pd.DataFrame(
            [[1e308], [0.0]],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=["2024-12-31"],
        )
        self.assertEqual(trailing_aligned_fcf_margins(cashflow, revenue, ["Total Revenue"]), [])

    def test_ttm_fcf_rejects_sum_overflow(self):
        periods = ["2024-12-31", "2024-09-30", "2024-06-30", "2024-03-31"]
        cashflow = pd.DataFrame(
            [[1e308] * 4, [0.0] * 4],
            index=["Operating Cash Flow", "Capital Expenditure"],
            columns=periods,
        )
        starts = {
            "2024-12-31": "2024-10-01", "2024-09-30": "2024-07-01",
            "2024-06-30": "2024-04-01", "2024-03-31": "2024-01-01",
        }
        self.assertEqual(period_aligned_ttm_fcf(cashflow, period_starts=starts), (None, None))

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
