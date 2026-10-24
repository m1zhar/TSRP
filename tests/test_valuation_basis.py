from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
import math
import unittest
import os
from unittest.mock import patch
import pandas as pd

from engine import business_quality_detail, financial_strength_detail, dcf_enterprise_value, solve_required_growth_detail, supported_score_breakdown
from financial_data import compute_fcf_detail
from methodology import SECTOR_MODELS, get_sector_model
from research_service import _average_equity_roe, _operating_valuation_basis, _yahoo_total_debt_detail, _sec_total_debt_record, analyze_scenario, analyze_company
from valuation_basis import capital_cost, operating_cash_flow
from tests.test_migration_service import MockEquity, scenario_basis


class OperatingCashFlowTests(unittest.TestCase):
    def test_exxon_2025_debt_matches_issuer_without_false_alias_conflict(self):
        # USD millions; official 2025 10-K, consolidated balance sheet:
        # https://investor.exxonmobil.com/sec-filings/all-sec-filings/content/0000034088-26-000045/xom-20251231.htm
        # Yahoo's loan-only and lease-inclusive rows differ by definition.
        frame = pd.DataFrame([
            [None, 41710], [9296, 4955], [9296, 4955],
            [27928, 32804], [34241, 36755],
        ], index=["Total Debt", "Current Debt", "Current Debt And Capital Lease Obligation", "Long Term Debt", "Long Term Debt And Capital Lease Obligation"], columns=["2025-12-31", "2024-12-31"])
        value, period, kind, parts = _yahoo_total_debt_detail(frame)
        self.assertEqual(value, 9296 + 34241)
        self.assertEqual(period, "2025-12-31")
        self.assertEqual([item[0] for item in parts], ["Current Debt And Capital Lease Obligation", "Long Term Debt And Capital Lease Obligation"])

    def test_current_portion_is_not_assumed_to_include_all_short_term_debt(self):
        frame = pd.DataFrame([[20], [100]], index=["Current Portion Of Long Term Debt", "Long Term Debt"], columns=["2025-12-31"])
        self.assertIsNone(_yahoo_total_debt_detail(frame)[0])

    def test_inconsistent_lease_and_loan_definitions_are_not_mixed(self):
        frame = pd.DataFrame([[20], [100]], index=["Current Debt", "Long Term Debt And Capital Lease Obligation"], columns=["2025-12-31"])
        self.assertIsNone(_yahoo_total_debt_detail(frame)[0])

    def test_sec_current_maturities_need_explicit_short_term_borrowings(self):
        def facts(values):
            return {"facts": {"us-gaap": {tag: {"units": {"USD": [{"val": value, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "fy": 2025, "fp": "FY"}]}} for tag, value in values.items()}}}
        partial = {"LongTermDebtCurrent": 20, "LongTermDebtNoncurrent": 100}
        self.assertIsNone(_sec_total_debt_record(facts(partial), ["USD"]))
        complete = _sec_total_debt_record(facts({**partial, "ShortTermBorrowings": 5}), ["USD"])
        self.assertEqual(complete["value"], 125)
        self.assertEqual(len(complete["components"]), 3)

    def test_sec_debt_families_are_not_mixed(self):
        values = {"DebtCurrent": 20, "LongTermDebtAndFinanceLeaseObligationsNoncurrent": 100}
        facts = {"facts": {"us-gaap": {tag: {"units": {"USD": [{"val": value, "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "fy": 2025, "fp": "FY"}]}} for tag, value in values.items()}}}
        self.assertIsNone(_sec_total_debt_record(facts, ["USD"]))
    def test_hand_calculated_operating_bridge_and_capex_sign(self):
        # EBIT 200 - estimated operating tax 40 + D&A 35 - capex 40 - WC 5.
        for capex in (-40, 40):
            result = operating_cash_flow(200, 150, 30, 35, capex, -5)
            self.assertEqual(result["value"], 150)
            self.assertEqual(result["tax_rate"], .2)

    def test_missing_nonfinite_boolean_inputs_never_become_zero(self):
        base = [200, 150, 30, 35, -40, -5]
        for index in range(len(base)):
            for bad in (None, True, math.nan, math.inf, -math.inf, "missing"):
                values = base.copy()
                values[index] = bad
                self.assertIsNone(operating_cash_flow(*values)["value"])

    def test_negative_cash_flow_is_preserved_and_losses_have_no_assumed_tax_credit(self):
        self.assertEqual(operating_cash_flow(-20, 150, 30, 5, -40, -5)["value"], -60)

    def test_missing_or_unusable_tax_basis_is_explicit(self):
        for pretax, taxes in ((0, 0), (-10, 0), (100, -10), (100, 51)):
            self.assertIsNone(operating_cash_flow(200, pretax, taxes, 35, -40, -5)["value"])
        self.assertEqual(operating_cash_flow(200, 100, 0, 35, -40, -5)["value"], 190)

    def test_stock_compensation_is_not_silently_added_back(self):
        # The bridge starts from EBIT after compensation, not CFO including
        # a noncash stock-compensation addback. No such addback is an input.
        self.assertEqual(set(operating_cash_flow(200, 150, 30, 35, -40, -5)["inputs"]), {
            "operating_income", "pretax_income", "tax_expense", "depreciation", "capex", "working_capital_change",
        })

    def test_microsoft_2025_reported_cash_flow_fixture(self):
        # USD millions, Microsoft FY2025 annual report, cash-flow statement:
        # https://www.microsoft.com/investor/reports/ar25/index.html
        # A reported CFO-capex cross-check, NOT a claim this is unlevered FCFF.
        result = compute_fcf_detail(136162, -64551)
        self.assertEqual(result[0], 71611)
        self.assertNotEqual(71611, operating_cash_flow(200, 150, 30, 35, -40, -5)["value"])

    def test_currency_period_and_cross_source_revenue_mismatch_block_bridge(self):
        instrument = MockEquity(2025)
        common = [instrument.financials, instrument.cashflow, instrument.balance_sheet, "2025-12-31", 1000, 200, "2025-12-31", 2500, 1, "USD", "USD", datetime(2026, 10, 24, tzinfo=timezone.utc)]
        self.assertEqual(_operating_valuation_basis(*common)["cash_flow"]["value"], 150)
        for index, value in ((3, "2025-09-30"), (4, 1200), (10, "EUR"), (11, datetime(2028, 1, 1, tzinfo=timezone.utc))):
            changed = common.copy()
            changed[index] = value
            self.assertIsNone(_operating_valuation_basis(*changed)["cash_flow"]["value"])

    def test_missing_working_capital_does_not_reuse_an_older_year(self):
        instrument = MockEquity(2025)
        instrument.cashflow.loc["Change In Working Capital", "2025-12-31"] = math.nan
        result = _operating_valuation_basis(instrument.financials, instrument.cashflow, instrument.balance_sheet, "2025-12-31", 1000, 200, "2025-12-31", 2500, 1, "USD", "USD", datetime(2026, 10, 24, tzinfo=timezone.utc))
        self.assertIsNone(result["cash_flow"]["value"])

    def test_roe_does_not_reward_negative_beginning_equity(self):
        self.assertIsNone(_average_equity_roe(10, "2025-12-31", [("2025-12-31", 100), ("2024-12-31", -10)]))


class CapitalCostTests(unittest.TestCase):
    def cost(self, **changes):
        inputs = dict(currency="USD", beta=1.2, equity_value=900, debt=100, opening_debt=100, interest_expense=5, tax_rate=.2, today=date(2026, 10, 24))
        inputs.update(changes)
        return capital_cost(**inputs)

    def test_independent_capm_and_weighted_cost_calculation(self):
        actual = self.cost()
        expected_ke = .0418 + 1.2 * .0423
        self.assertAlmostEqual(actual["cost_of_equity"], expected_ke)
        self.assertAlmostEqual(actual["value"], expected_ke * .9 + .05 * .8 * .1)
        self.assertEqual(actual["reference"]["date"], "2026-01-01")

    def test_all_equity_firm_needs_no_fictitious_cost_of_debt(self):
        actual = self.cost(debt=0, opening_debt=None, interest_expense=None)
        self.assertEqual(actual["value"], actual["cost_of_equity"])
        self.assertIsNone(actual["cost_of_debt"])

    def test_inputs_expire_and_unsupported_currencies_do_not_use_usd(self):
        for changes in ({"currency": "EUR"}, {"today": date(2025, 12, 31)}, {"today": date(2027, 1, 3)}, {"beta": None}, {"interest_expense": 0}, {"opening_debt": None}, {"equity_value": 0}, {"tax_rate": -.2}):
            self.assertIsNone(self.cost(**changes)["value"])

    def test_capital_structure_currency_scale_invariance(self):
        actual = self.cost()
        scaled = self.cost(equity_value=900e6, debt=100e6, opening_debt=100e6, interest_expense=5e6)
        self.assertAlmostEqual(actual["value"], scaled["value"])


class ValuationAndScoreIndependentTests(unittest.TestCase):
    def test_dcf_matches_decimal_growing_perpetuity_at_constant_growth_margin(self):
        with localcontext() as context:
            context.prec = 40
            expected = Decimal("100") * Decimal(".15") * Decimal("1.02") / (Decimal(".09") - Decimal(".02"))
        for horizon in (1, 5, 10, 30):
            actual = dcf_enterprise_value(100, .02, .15, .09, .02, years=horizon, fade_start=min(5, horizon))
            self.assertAlmostEqual(actual, float(expected), places=9)

    def test_reverse_roundtrip_and_dcf_scale_invariance(self):
        for growth in (-.3, -.1, 0, .1, .4, 1):
            for margin in (.03, .12, .4):
                for rate in (.07, .11, .18):
                    value = dcf_enterprise_value(100, growth, margin, rate, .02)
                    solved = solve_required_growth_detail(value, 100, margin, rate, .02)
                    self.assertEqual(solved["status"], "exact")
                    self.assertAlmostEqual(solved["growth"], growth, places=8)
                    self.assertAlmostEqual(dcf_enterprise_value(100e6, growth, margin, rate, .02) / 1e6, value, places=7)

    def test_negative_starting_cash_flow_requires_an_explicit_positive_terminal_assumption(self):
        self.assertIsNotNone(dcf_enterprise_value(100, .1, .15, .09, .02, start_margin=-.05))
        self.assertIsNone(dcf_enterprise_value(100, .1, -.05, .09, .02, start_margin=-.05))

    def test_zero_starting_margin_and_other_claims_are_preserved_in_scenario(self):
        values = {"starting_growth": .1, "fcf_margin": .15, "discount_rate": .09, "terminal_growth": .02}
        basis = scenario_basis(starting_fcf_margin=0, other_claims=50, enterprise_value=2650)
        result = analyze_scenario("AAPL", values, basis)
        expected = dcf_enterprise_value(1000, .1, .15, .09, .02, start_margin=0)
        self.assertAlmostEqual(result["enterprise_value"], expected)
        self.assertAlmostEqual(result["equity_value"], expected - 200 - 50 + 100)

    def test_known_other_equity_claims_reconcile_in_live_pipeline(self):
        fixture = MockEquity(datetime.now(timezone.utc).year - 1)
        fixture.balance_sheet.loc["Minority Interest"] = [30, 25, 20, 15]
        fixture.balance_sheet.loc["Preferred Stock Equity"] = [20, 20, 20, 20]
        with patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "true"}), patch("research_service.yf.Ticker", return_value=fixture), patch("research_service._sec_facts", return_value=(None, {"kind": "unavailable", "label": "fixture"})):
            result = analyze_company("AAPL")["company"]
        self.assertEqual(result["other_claims"], 50)
        self.assertEqual(result["enterprise_value"], 2500 + 200 + 50 - 100)

    def test_no_score_factor_is_repeated_and_sensitivity_contains_baseline(self):
        for sector in SECTOR_MODELS:
            model = get_sector_model(sector)
            business = business_quality_detail(.08, .6, .2, .15, .18, .4, model)
            financial = financial_strength_detail(50, 100, .2, .15, .4, model)
            bkeys = {item["key"] for item in business["components"]}
            fkeys = {item["key"] for item in financial["components"]}
            self.assertFalse(bkeys & fkeys)
            self.assertEqual(len(bkeys | fkeys), 6)
            trace = supported_score_breakdown(business, financial, None, None)
            self.assertAlmostEqual(trace["score"], (business["score"] + financial["score"]) / 2)
            self.assertLessEqual(trace["weight_sensitivity"]["low"], trace["score"])
            self.assertGreaterEqual(trace["weight_sensitivity"]["high"], trace["score"])


if __name__ == "__main__":
    unittest.main()
