"""Ratios must match the owners represented by earnings and capital.

Definitions: https://xbrl.us/guidance/specific-non-controlling-interest-elements/
All amounts here are synthetic, not an issuer's reported accounts.
"""

from datetime import datetime, timezone
import math
import os
import unittest
from unittest.mock import patch

from research_service import analyze_company
from tests.test_migration_service import MockEquity


class OwnershipBasisTests(unittest.TestCase):
    def setUp(self):
        self.year = datetime.now(timezone.utc).year - 1
        self.fixture = MockEquity(self.year)
        self.fixture.financials.loc["Net Income Common Stockholders"] = [80, 75, 70, 65]
        self.fixture.financials.loc["Net Income Including Noncontrolling Interests"] = [150, 140, 130, 120]
        self.fixture.balance_sheet.loc["Total Equity Gross Minority Interest"] = [1000, 950, 900, 850]

    def analyze(self, facts=None):
        state = {"kind": "available" if facts else "unavailable", "label": "Synthetic SEC fixture"}
        with patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "true"}), patch(
            "research_service.yf.Ticker", return_value=self.fixture
        ), patch("research_service._sec_facts", return_value=(facts, state)):
            return analyze_company("AAPL")["company"]

    def sec_facts(self, values):
        result = {}
        for tag, (value, instant) in values.items():
            row = {"val": value, "end": f"{self.year}-12-31", "filed": f"{self.year + 1}-02-01",
                   "fy": self.year, "fp": "FY", "form": "10-K", "accn": "synthetic-shared-filing"}
            if not instant:
                row["start"] = f"{self.year}-01-01"
            result[tag] = {"units": {"USD": [row]}}
        return {"facts": {"us-gaap": result}}

    def test_parent_roe_and_common_shareholder_pe_are_distinct(self):
        result = self.analyze()
        self.assertAlmostEqual(result["return_on_equity"], 120 / ((700 + 650) / 2))
        self.assertAlmostEqual(result["pe"], 2500 / 80)
        self.assertEqual(result["sources"]["pe"]["earnings_basis"]["value"], 80)
        self.assertIn("parent equity", result["sources"]["return_on_equity"]["kind"])

    def test_consolidated_equity_is_not_a_parent_equity_fallback(self):
        self.fixture.balance_sheet = self.fixture.balance_sheet.drop(index="Stockholders Equity")
        result = self.analyze()
        self.assertIsNone(result["equity"])
        self.assertIsNone(result["return_on_equity"])
        self.assertEqual(result["sources"]["return_on_equity"]["source"], "Unavailable")
        self.assertTrue(any("minority owners" in item["message"] for item in result["quality_flags"]))

    def test_common_or_consolidated_income_is_not_parent_income(self):
        self.fixture.financials = self.fixture.financials.drop(index="Net Income")
        result = self.analyze()
        self.assertIsNone(result["profit_margin"])
        self.assertIsNone(result["return_on_equity"])
        self.assertAlmostEqual(result["pe"], 2500 / 80)

    def test_missing_common_earnings_are_not_replaced_by_parent_earnings(self):
        self.fixture.financials = self.fixture.financials.drop(index="Net Income Common Stockholders")
        result = self.analyze()
        self.assertIsNone(result["pe"])
        self.assertIsNotNone(result["return_on_equity"])
        self.assertTrue(any("common shareholders" in item["message"] for item in result["quality_flags"]))

    def test_negative_zero_nonfinite_and_boolean_common_earnings_do_not_make_pe(self):
        for value in (-10, 0, math.nan, math.inf, True):
            with self.subTest(value=value):
                self.fixture.financials = self.fixture.financials.astype(object)
                self.fixture.financials.loc["Net Income Common Stockholders"] = [value] * 4
                self.assertIsNone(self.analyze()["pe"])

    def test_stale_common_earnings_are_not_made_current_by_parent_period(self):
        self.fixture.financials.loc["Net Income Common Stockholders"] = [math.nan, math.nan, math.nan, 80]
        self.assertIsNone(self.analyze()["pe"])

    def test_normalized_ebitda_is_not_reported_ebitda(self):
        self.fixture.financials.loc["Normalized EBITDA"] = [400, 380, 360, 340]
        self.assertAlmostEqual(self.analyze()["ev_ebitda"], 2600 / 300)
        self.fixture.financials = self.fixture.financials.drop(index="EBITDA")
        self.assertIsNone(self.analyze()["ev_ebitda"])

    def test_sec_parent_and_consolidated_facts_are_not_conflicting_aliases(self):
        facts = self.sec_facts({"NetIncomeLoss": (120, False), "ProfitLoss": (150, False),
                                "StockholdersEquity": (700, True),
                                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": (1000, True)})
        result = self.analyze(facts)
        self.assertEqual(result["sources"]["net_income"]["tag"], "NetIncomeLoss")
        self.assertEqual(result["sources"]["equity"]["tag"], "StockholdersEquity")

    def test_sec_common_earnings_and_provenance_drive_pe(self):
        result = self.analyze(self.sec_facts({"NetIncomeLossAvailableToCommonStockholdersBasic": (90, False)}))
        self.assertAlmostEqual(result["pe"], 2500 / 90)
        basis = result["sources"]["pe"]["earnings_basis"]
        self.assertEqual(basis["tag"], "NetIncomeLossAvailableToCommonStockholdersBasic")
        self.assertEqual(basis["currency"], "USD")
        self.assertEqual(basis["value"], 90)

    def test_sec_consolidated_only_does_not_override_available_parent_values(self):
        result = self.analyze(self.sec_facts({"ProfitLoss": (150, False),
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": (1000, True)}))
        self.assertEqual(result["equity"], 700)
        self.assertEqual(result["sources"]["net_income"]["source"], "Yahoo Finance")

    def test_common_earnings_and_adjusted_ebitda_cannot_change_score(self):
        baseline = self.analyze()["score"]
        self.fixture.financials.loc["Net Income Common Stockholders"] = [1, 1, 1, 1]
        self.fixture.financials.loc["Normalized EBITDA"] = [99999] * 4
        self.assertEqual(self.analyze()["score"], baseline)
