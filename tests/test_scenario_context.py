import base64
import os
import unittest
from unittest.mock import patch

from scenario_context import (
    issue_scenario_context,
    scenario_context_key_available,
    verify_scenario_context,
)


class ScenarioContextTests(unittest.TestCase):
    def setUp(self):
        self.secret = "test-key-" + "0123456789abcdef" * 4
        self.company = {
            "ticker": "AAPL",
            "sector": "Technology",
            "reporting_currency": "USD",
            "revenue": 400_000_000_000.0,
            "revenue_period": "2025-09-27",
            "market_cap_reporting": 3_000_000_000_000.0,
            "enterprise_value": 3_050_000_000_000.0,
            "market_at": "2026-10-09T20:00:00+00:00",
            "cash": 60_000_000_000.0,
            "debt": 110_000_000_000.0,
            "fcf_margin": 0.12,
            "methodology_version": "score-v9",
            "analysis_schema_version": "analysis-v4",
            "sources": {
                "revenue": {"period": "2025-09-27", "currency": "USD"},
                "cash": {"period": "2025-09-27", "currency": "USD"},
                "debt": {"period": "2025-09-27", "currency": "USD"},
            },
        }

    def test_server_signed_context_round_trips_only_company_basis(self):
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": self.secret}):
            token = issue_scenario_context(self.company)
            verified = verify_scenario_context(token, "AAPL")

        self.assertIsInstance(token, str)
        self.assertEqual(verified["ticker"], "AAPL")
        self.assertEqual(verified["revenue"], self.company["revenue"])
        self.assertEqual(verified["cash_period"], "2025-09-27")
        self.assertEqual(verified["debt_currency"], "USD")
        self.assertEqual(verified["starting_fcf_margin"], 0.12)
        self.assertNotIn(self.secret, token)
        self.assertEqual(set(verified), {
            "ticker", "sector", "reporting_currency", "revenue", "revenue_period",
            "revenue_currency", "market_cap_reporting", "enterprise_value", "market_at",
            "cash", "cash_period", "cash_currency", "debt", "debt_period", "debt_currency",
            "starting_fcf_margin",
            "methodology_version", "analysis_schema_version",
        })

    def test_tampered_facts_and_wrong_ticker_are_rejected(self):
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": self.secret}):
            token = issue_scenario_context(self.company)
            version, encoded, signature = token.split(".")
            payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            tampered_payload = payload.replace(b"400000000000.0", b"900000000000.0")
            tampered = base64.urlsafe_b64encode(tampered_payload).rstrip(b"=").decode("ascii")

            self.assertIsNone(verify_scenario_context(f"{version}.{tampered}.{signature}", "AAPL"))
            self.assertIsNone(verify_scenario_context(token, "MSFT"))

    def test_key_rotation_or_missing_short_key_disables_context(self):
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": self.secret}):
            token = issue_scenario_context(self.company)
        with patch.dict(
            os.environ,
            {"TSRP_SCENARIO_SIGNING_SECRET": "rotated-key-" + "fedcba9876543210" * 3},
        ):
            self.assertIsNone(verify_scenario_context(token, "AAPL"))
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": "too-short"}):
            self.assertIsNone(issue_scenario_context(self.company))
            self.assertIsNone(verify_scenario_context(token, "AAPL"))

    def test_weak_or_whitespace_padded_signing_keys_fail_closed(self):
        for secret in (
            "x" * 64,
            "0123456789abcdef" * 4,
            "x" * 52 + "y" * 12,
            f" {self.secret}",
        ):
            with self.subTest(secret=secret[:16]), patch.dict(
                os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": secret}
            ):
                self.assertFalse(scenario_context_key_available())
                self.assertIsNone(issue_scenario_context(self.company))
                self.assertIsNone(verify_scenario_context("v2.e30.invalid", "AAPL"))

    def test_nonfinite_or_malformed_context_is_not_signed_or_accepted(self):
        invalid_company = {**self.company, "revenue": float("nan")}
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": self.secret}):
            token = issue_scenario_context(invalid_company)
            self.assertIsNone(token)
            self.assertIsNone(verify_scenario_context("not.a.valid.signature", "AAPL"))
            self.assertIsNone(verify_scenario_context("x" * 4097, "AAPL"))


if __name__ == "__main__":
    unittest.main()
