"""Fiscal-year and estimate-basis regressions; entirely deterministic fixtures."""

import base64
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import math
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from expectations import revenue_estimate
from provider_utils import get_revenue_estimates
from research_service import _snapshot_proof_key, analyze_company, compare_snapshots
from tests.auth_fixtures import API_TEST_SECRET
from tests.test_migration_service import MockEquity, compatible_snapshot


class RevenueEstimateTests(unittest.TestCase):
    def frame(self, **changes):
        row = {"growth": .12, "currency": "USD", "numberOfAnalysts": 8}
        row.update(changes)
        frame = pd.DataFrame([row], index=["+1y"])
        frame.attrs["fiscal_periods"] = {"+1y": {"end_date": "2027-06-30"}}
        return frame

    def parse(self, frame):
        return revenue_estimate(frame, as_of=date(2026, 10, 24))

    def test_reported_fiscal_end_and_currency_survive_without_becoming_actual_results(self):
        result = self.parse(self.frame())
        self.assertEqual(result, {"growth": .12, "currency": "USD", "end_date": "2027-06-30", "label": "Next-year analyst revenue estimate"})

    def test_missing_dates_and_currencies_are_never_inferred(self):
        frame = self.frame(currency=None)
        frame.attrs.clear()
        result = self.parse(frame)
        self.assertEqual(result["growth"], .12)
        self.assertIsNone(result["end_date"])
        self.assertIsNone(result["currency"])
        for currency in ("GBp", "XXX", "N/A", True):
            self.assertIsNone(self.parse(self.frame(currency=currency))["currency"])
        frame = self.frame(endDate=math.nan)
        self.assertEqual(self.parse(frame)["growth"], .12)
        self.assertIsNone(self.parse(frame)["end_date"])

    def test_invalid_growth_and_contributor_counts_are_unavailable(self):
        for value in (None, True, math.nan, math.inf, -1.01, 1e308, [1, 2]):
            self.assertIsNone(self.parse(self.frame(growth=value))["growth"])
        for count in (0, -1, .5, True, math.inf, "unknown", [1, 2]):
            self.assertIsNone(self.parse(self.frame(numberOfAnalysts=count))["growth"])
        for count in (None, math.nan, 1, 20):
            self.assertEqual(self.parse(self.frame(numberOfAnalysts=count))["growth"], .12)
        self.assertEqual(self.parse(self.frame(growth=-1))["growth"], -1)

    def test_stale_next_year_and_implausibly_remote_periods_are_not_current_estimates(self):
        for end in ("2025-06-30", "2030-06-30", "2027-02-30", "2027", True):
            frame = self.frame()
            frame.attrs["fiscal_periods"]["+1y"]["end_date"] = end
            self.assertIsNone(self.parse(frame)["growth"], end)

    def test_current_year_can_await_reported_results_but_not_indefinitely(self):
        for end, valid in (("2026-09-30", True), ("2026-01-31", False), ("2028-06-30", False)):
            frame = pd.DataFrame({"growth": [.1]}, index=["0y"])
            frame.attrs["fiscal_periods"] = {"0y": {"end_date": end}}
            self.assertEqual(self.parse(frame)["growth"], .1 if valid else None)

    def test_invalid_next_year_can_fall_back_to_valid_current_year(self):
        frame = pd.DataFrame({"growth": [-1.2, .09], "numberOfAnalysts": [0, 4]}, index=["+1y", "0y"])
        result = self.parse(frame)
        self.assertEqual(result["growth"], .09)
        self.assertEqual(result["label"], "Current-year analyst revenue estimate")

    def test_duplicate_rows_columns_and_malformed_frames_fail_safely(self):
        for frame in (None, {}, pd.DataFrame([[.1], [.2]], columns=["growth"], index=["+1y", "+1y"]), pd.DataFrame([[.1, .2]], columns=["growth", "growth"], index=["+1y"])):
            self.assertIsNone(self.parse(frame)["growth"])

    def test_adapter_reads_only_already_fetched_metadata_and_does_not_mutate_frame(self):
        original = self.frame()
        original.attrs.clear()
        instrument = SimpleNamespace(revenue_estimate=original, _analysis=SimpleNamespace(_earnings_trend=[{"period": "+1y", "endDate": "2027-06-30"}]))
        result = get_revenue_estimates(instrument)
        self.assertEqual(self.parse(result)["end_date"], "2027-06-30")
        self.assertEqual(original.attrs, {})
        self.assertIsNot(result, original)

    def test_absent_or_conflicting_adapter_cache_does_not_invent_dates(self):
        for cache in (None, [], [{"period": "+1y", "endDate": "2027-06-30"}, {"period": "+1y", "endDate": "2028-06-30"} ]):
            original = self.frame()
            original.attrs.clear()
            instrument = SimpleNamespace(revenue_estimate=original, _analysis=SimpleNamespace(_earnings_trend=cache))
            self.assertIsNone(self.parse(get_revenue_estimates(instrument))["end_date"])


class SnapshotEstimateBasisTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_same_relative_label_with_different_fiscal_end_is_not_a_revision(self):
        before = compatible_snapshot(analyst_estimate_end_date="2027-06-30")
        after = compatible_snapshot(analyst_estimate_end_date="2028-06-30", consensus_growth=.18)
        result = compare_snapshots(before, after)
        self.assertTrue(result["comparable"])
        self.assertNotIn("consensus_growth", {item["field"] for item in result["changes"]})
        self.assertIn({"field": "consensus_growth", "reason": "estimate_period_changed"}, result["excluded_fields"])

    def test_unknown_dates_or_currency_do_not_hide_other_valid_changes(self):
        for changes in ({"analyst_estimate_end_date": None}, {"analyst_estimate_currency": None}):
            result = compare_snapshots(compatible_snapshot(**changes), compatible_snapshot(consensus_growth=.18, enterprise_value=1100))
            self.assertTrue(result["comparable"])
            self.assertEqual([item["field"] for item in result["changes"]], ["enterprise_value"])
            self.assertIn({"field": "consensus_growth", "reason": "estimate_period_unavailable"}, result["excluded_fields"])

    def test_same_fiscal_year_in_different_forecast_currencies_is_not_comparable(self):
        result = compare_snapshots(compatible_snapshot(), compatible_snapshot(analyst_estimate_currency="EUR", consensus_growth=.18))
        self.assertNotIn("consensus_growth", {item["field"] for item in result["changes"]})

    def test_matching_verified_period_and_currency_keep_growth_change(self):
        result = compare_snapshots(compatible_snapshot(), compatible_snapshot(consensus_growth=.18))
        change = next(item for item in result["changes"] if item["field"] == "consensus_growth")
        self.assertAlmostEqual(change["delta"], .06)

    def test_browser_cannot_relabel_a_different_signed_fiscal_period(self):
        before = compatible_snapshot()
        after = compatible_snapshot(analyst_estimate_end_date="2028-12-31", consensus_growth=.18)
        after["analyst_estimate_end_date"] = before["analyst_estimate_end_date"]
        result = compare_snapshots(before, after)
        self.assertNotIn("consensus_growth", {item["field"] for item in result["changes"]})

    def test_old_proof_remains_readable_but_browser_cannot_add_unsigned_period(self):
        before = compatible_snapshot()
        payload_part = before["snapshot_proof"].split(".")[0]
        payload = json.loads(base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4)))
        for field in ("analyst_estimate_end_date", "analyst_estimate_currency"):
            payload.pop(field)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(_snapshot_proof_key(), encoded, hashlib.sha256).digest()
        encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
        before["snapshot_proof"] = f"{encode(encoded)}.{encode(signature)}"
        result = compare_snapshots(before, compatible_snapshot(consensus_growth=.18))
        self.assertTrue(result["comparable"])
        self.assertIn({"field": "consensus_growth", "reason": "estimate_period_unavailable"}, result["excluded_fields"])

    def test_invalid_signed_forecast_metadata_is_rejected(self):
        for changes in ({"analyst_estimate_end_date": "2027-02-30"}, {"analyst_estimate_currency": True}, {"analyst_estimate_currency": "XXX"}):
            self.assertFalse(compare_snapshots(compatible_snapshot(), compatible_snapshot(**changes))["comparable"])

    def test_newer_snapshot_cannot_be_presented_as_the_previous_analysis(self):
        result = compare_snapshots(compatible_snapshot(retrieved_at="2026-10-08T12:00:00Z"), compatible_snapshot(retrieved_at="2026-10-07T12:00:00Z", consensus_growth=.18))
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_order_invalid")
        self.assertEqual(result["changes"], [])

    def test_ordering_uses_utc_instants_not_lexical_timezone_offsets(self):
        result = compare_snapshots(compatible_snapshot(retrieved_at="2026-10-07T13:00:00+02:00"), compatible_snapshot(retrieved_at="2026-10-07T12:00:00Z", consensus_growth=.18))
        self.assertTrue(result["comparable"])

    def test_timezone_unknown_retrieval_dates_cannot_support_change_order(self):
        result = compare_snapshots(compatible_snapshot(retrieved_at="2026-10-07T12:00:00"), compatible_snapshot())
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "snapshot_schema_invalid")


class EstimatePipelineTests(unittest.TestCase):
    def test_estimate_metadata_and_warning_survive_analysis_without_changing_score(self):
        fixture = MockEquity(datetime.now(timezone.utc).year - 1)
        with patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "true"}), patch("research_service.yf.Ticker", return_value=fixture), patch("research_service._sec_facts", return_value=(None, {"kind": "unavailable", "label": "Synthetic fixture"})):
            unknown = analyze_company("AAPL")["company"]
            end = f"{datetime.now(timezone.utc).year + 1}-12-31"
            fixture.revenue_estimate["currency"] = "USD"
            fixture._analysis = SimpleNamespace(_earnings_trend=[{"period": "+1y", "endDate": end}])
            known = analyze_company("AAPL")["company"]
        self.assertEqual(known["analyst_estimate_end_date"], end)
        self.assertEqual(known["analyst_estimate_currency"], "USD")
        self.assertEqual(known["sources"]["analyst_growth"]["period"], end)
        self.assertEqual(known["score"], unknown["score"])
        self.assertIsNone(unknown["analyst_estimate_end_date"])
        self.assertEqual(unknown["sources"]["analyst_growth"]["currency"], "N/A")
        self.assertTrue(any("no verified fiscal end date" in item["message"] for item in unknown["quality_flags"]))
