"""Regression tests for the server-to-server API bearer boundary."""

import os
import unittest
from unittest.mock import patch

from api_auth import api_token_secret, require_api_access, verify_api_access_token
from api.analyze import handler as AnalyzeHandler
from api.movers import handler as MoversHandler
from api.scenario import handler as ScenarioHandler
from api.search import handler as SearchHandler
from api.snapshot_diff import handler as SnapshotDiffHandler
from api.top_stocks import handler as TopStocksHandler
from tests.auth_fixtures import API_TEST_SECRET, test_api_token
from tests.test_api_routes import RequestRecorder


class ApiAuthTests(unittest.TestCase):
    def test_valid_short_lived_token_is_accepted(self):
        with patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET}):
            self.assertTrue(verify_api_access_token(test_api_token()))

    def test_invalid_claims_signature_and_expiry_are_rejected(self):
        now = 2_000_000_000
        with patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET}):
            self.assertFalse(verify_api_access_token(test_api_token(now=now, subject="bad"), now=now))
            self.assertFalse(verify_api_access_token(test_api_token(now=now - 100), now=now))
            self.assertFalse(verify_api_access_token("not.a.token", now=now))

    def test_expiry_and_clock_skew_boundaries_are_explicit(self):
        now = 2_000_000_000
        with patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET}):
            self.assertTrue(verify_api_access_token(
                test_api_token(now=now, claims={"iat": now - 30, "exp": now + 60}), now=now
            ))
            self.assertTrue(verify_api_access_token(
                test_api_token(now=now, claims={"iat": now + 30, "exp": now + 120}), now=now
            ))
            self.assertFalse(verify_api_access_token(
                test_api_token(now=now, claims={"iat": now + 31, "exp": now + 121}), now=now
            ))
            self.assertFalse(verify_api_access_token(
                test_api_token(now=now, claims={"iat": now - 1, "exp": now}), now=now
            ))
            self.assertFalse(verify_api_access_token(
                test_api_token(now=now, claims={"iat": now - 1, "exp": now + 90}), now=now
            ))

    def test_non_integer_or_unexpected_claims_are_rejected(self):
        now = 2_000_000_000
        invalid_claims = (
            {"iat": True},
            {"exp": True},
            {"iat": float(now)},
            {"exp": float(now + 60)},
            {"role": "admin"},
        )
        with patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET}):
            for claims in invalid_claims:
                with self.subTest(claims=claims):
                    self.assertFalse(verify_api_access_token(
                        test_api_token(now=now, claims=claims), now=now
                    ))

    def test_missing_token_returns_safe_bearer_challenge(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL", headers={"Authorization": ""})
        with patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET}):
            self.assertFalse(require_api_access(request))
        self.assertEqual(request.status, 401)
        self.assertEqual(request.response_headers["WWW-Authenticate"], 'Bearer realm="tsrp-api"')
        self.assertEqual(request.payload()["error"]["code"], "authentication_required")

    def test_missing_secret_fails_closed(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": "short"}):
            self.assertFalse(require_api_access(request))
        self.assertEqual(request.status, 503)
        self.assertEqual(request.payload()["error"]["code"], "auth_not_configured")

    def test_trivially_repeated_signing_secrets_fail_closed(self):
        for secret in ("x" * 32, "0123456789abcdef" * 2, "x" * 20 + "y" * 12, f" {API_TEST_SECRET}"):
            with self.subTest(secret=secret[:16]), patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": secret}):
                self.assertIsNone(api_token_secret())
                self.assertFalse(verify_api_access_token(test_api_token()))

    def test_every_provider_route_rejects_missing_token_before_work(self):
        routes = (
            (AnalyzeHandler, "GET", "/api/analyze?ticker=AAPL", b"", "api.analyze.analyze_company"),
            (MoversHandler, "GET", "/api/movers", b"", "api.movers.daily_market_movers"),
            (TopStocksHandler, "GET", "/api/top_stocks", b"", "api.top_stocks.daily_top_stocks"),
            (TopStocksHandler, "GET", "/api/top_stocks?list=popular", b"", "api.top_stocks.daily_popular_stocks"),
            (SearchHandler, "POST", "/api/search", b'{"q":"Apple"}', "api.search.search_companies"),
            (ScenarioHandler, "POST", "/api/scenario", b"{}", "api.scenario.analyze_scenario"),
            (SnapshotDiffHandler, "POST", "/api/snapshot_diff", b"{}", "api.snapshot_diff.compare_snapshots"),
        )
        for handler_type, method, path, body, provider in routes:
            with self.subTest(route=path), patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET}), patch(provider) as work:
                request = RequestRecorder(path, body=body, headers={"Authorization": ""}, method=method)
                getattr(handler_type, f"do_{method}")(request)
                self.assertEqual(request.status, 401)
                work.assert_not_called()


if __name__ == "__main__":
    unittest.main()
