import json
import logging
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import unittest
from unittest.mock import patch

from api.analyze import handler as AnalyzeHandler
from api.movers import handler as MoversHandler
from api.search import handler as SearchHandler
from api.scenario import handler as ScenarioHandler
from api.snapshot_diff import handler as SnapshotDiffHandler
from api.top_stocks import handler as TopStocksHandler
from engine import dcf_enterprise_value
from local_api import Router, add_local_cors_headers
from research_service import ResearchError, run_with_provider_cancellation
from scenario_context import issue_scenario_context, verify_scenario_context
from tests.auth_fixtures import API_TEST_SECRET, test_api_token


class Headers(dict):
    def get_content_type(self):
        return self.get("Content-Type", "").split(";", 1)[0].strip().lower()


class RequestRecorder:
    def __init__(self, path="/", body=b"", headers=None, method="GET"):
        self.path = path
        self.command = method
        request_headers = dict(headers or {})
        request_headers.setdefault("Authorization", f"Bearer {test_api_token()}")
        self.headers = Headers(request_headers)
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.status = None
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        return None

    def payload(self):
        return json.loads(self.wfile.getvalue())


class ApiRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tsrp_logger = logging.getLogger("tsrp")
        cls._previous_log_level = cls._tsrp_logger.level
        cls._tsrp_logger.setLevel(logging.CRITICAL)

    def setUp(self):
        auth_environment = patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET})
        auth_environment.start()
        self.addCleanup(auth_environment.stop)

    @classmethod
    def tearDownClass(cls):
        cls._tsrp_logger.setLevel(cls._previous_log_level)

    def test_analyze_returns_safe_validation_error(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL%2F..%2Fsecret")
        AnalyzeHandler.do_GET(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_ticker")
        self.assertNotIn("secret", request.wfile.getvalue().decode())

    def test_analyze_rejects_malformed_ticker_separators_before_provider_access(self):
        for ticker in ("A..B", "A--B", "A-.B", "A.", "A-"):
            request = RequestRecorder(f"/api/analyze?ticker={ticker}")
            with self.subTest(ticker=ticker), patch("api.analyze.analyze_company") as provider:
                AnalyzeHandler.do_GET(request)
            self.assertEqual(request.status, 400)
            self.assertEqual(request.payload()["error"]["code"], "invalid_ticker")
            provider.assert_not_called()

    def test_analyze_returns_safe_retryable_error_for_provider_entity_mismatch(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        mismatch = ResearchError(
            "entity_mismatch",
            "The market-data provider returned a different company for this ticker. Verify the symbol and retry.",
            503,
        )
        with patch("api.analyze.analyze_company", side_effect=mismatch):
            AnalyzeHandler.do_GET(request)
        payload = request.payload()["error"]
        self.assertEqual(request.status, 503)
        self.assertEqual(payload["code"], "entity_mismatch")
        self.assertNotIn("stack", payload)
        self.assertNotIn("MSFT", request.wfile.getvalue().decode())

    def test_analyze_rejects_phone_like_tickers_before_provider_access(self):
        for ticker in ("14155550134", "415-555-0134"):
            request = RequestRecorder(f"/api/analyze?ticker={ticker}")
            with self.subTest(ticker=ticker), patch("research_service.yf.Ticker") as provider:
                AnalyzeHandler.do_GET(request)
            self.assertEqual(request.status, 400)
            self.assertEqual(request.payload()["error"]["code"], "personal_data_not_allowed")
            self.assertNotIn(ticker, request.wfile.getvalue().decode())
            provider.assert_not_called()

    def test_api_handlers_do_not_log_request_lines_or_query_values(self):
        handlers = (
            AnalyzeHandler,
            MoversHandler,
            ScenarioHandler,
            SearchHandler,
            SnapshotDiffHandler,
            TopStocksHandler,
            Router,
        )
        for handler_type in handlers:
            with self.subTest(handler=handler_type.__module__):
                handler = handler_type.__new__(handler_type)
                handler.requestline = "POST /api/search HTTP/1.1"
                handler.address_string = lambda: "127.0.0.1"
                handler.log_date_time_string = lambda: "test-time"
                with patch("sys.stderr", new_callable=StringIO) as stderr:
                    handler.log_request(200, 12)
                self.assertEqual(stderr.getvalue(), "")

    def test_analyze_wraps_a_successful_result(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": ""}), patch(
            "api.analyze.analyze_company", return_value={"company": {"ticker": "AAPL"}, "price_history": []}
        ):
            AnalyzeHandler.do_GET(request)
        self.assertEqual(request.status, 200)
        self.assertEqual(request.payload()["data"]["company"]["ticker"], "AAPL")
        self.assertIsNone(request.payload()["data"]["scenario_context_token"])
        self.assertEqual(request.payload()["data"]["scenario_context_status"], "not_configured")
        self.assertEqual(request.response_headers["Cache-Control"], "private, no-store")

    def test_concurrent_analysis_route_requests_share_one_calculation(self):
        import threading
        from concurrent.futures import Future, ThreadPoolExecutor

        waiter_count = 7
        waiters_ready = threading.Event()
        started = threading.Event()
        release = threading.Event()
        waiting = 0
        lock = threading.Lock()

        class TrackingFuture(Future):
            def result(self, timeout=None):
                nonlocal waiting
                with lock:
                    waiting += 1
                    if waiting == waiter_count:
                        waiters_ready.set()
                return super().result(timeout)

        def slow_analysis(ticker):
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test analysis was not released")
            return {"company": {"ticker": ticker}, "price_history": []}

        requests = [RequestRecorder("/api/analyze?ticker=AAPL") for _ in range(waiter_count + 1)]
        with patch("api.analyze.analyze_company", side_effect=slow_analysis) as analysis, patch(
            "api.analyze.scenario_context_key_available", return_value=False
        ), patch("analysis_coalescer.Future", TrackingFuture):
            with ThreadPoolExecutor(max_workers=waiter_count + 1) as pool:
                leader = pool.submit(AnalyzeHandler.do_GET, requests[0])
                self.assertTrue(started.wait(timeout=1))
                followers = [pool.submit(AnalyzeHandler.do_GET, request) for request in requests[1:]]
                try:
                    self.assertTrue(waiters_ready.wait(timeout=1))
                    analysis.assert_called_once_with("AAPL")
                finally:
                    release.set()
                leader.result(timeout=1)
                for follower in followers:
                    follower.result(timeout=1)

        self.assertEqual([request.status for request in requests], [200] * len(requests))
        self.assertTrue(all(request.payload()["data"]["company"]["ticker"] == "AAPL" for request in requests))

    def test_analyze_returns_signed_scenario_context_without_the_key(self):
        secret = "analysis-route-test-" + "0123456789abcdef" * 3
        market_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        company = {
            "ticker": "AAPL",
            "sector": "Technology",
            "sector_methodology_supported": True,
            "reporting_currency": "USD",
            "currency_methodology_supported": True,
            "revenue": 400_000_000_000.0,
            "revenue_period": "2025-09-27",
            "market_cap_reporting": 3_000_000_000_000.0,
            "enterprise_value": 3_050_000_000_000.0, "other_claims": 0.0,
            "market_at": market_at,
            "cash": 60_000_000_000.0,
            "debt": 110_000_000_000.0,
            "fcf_margin": 0.12, "unlevered_fcf_margin": 0.12,
            "methodology_version": "score-v10",
            "analysis_schema_version": "analysis-v5",
            "sources": {
                "revenue": {"period": "2025-09-27", "currency": "USD"},
                "cash": {"period": "2025-09-27", "currency": "USD"},
                "debt": {"period": "2025-09-27", "currency": "USD"},
            },
        }
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": secret}), patch(
            "api.analyze.analyze_company", return_value={"company": company, "price_history": []}
        ):
            AnalyzeHandler.do_GET(request)
            token = request.payload()["data"]["scenario_context_token"]
            self.assertEqual(request.payload()["data"]["scenario_context_status"], "available")
            verified = verify_scenario_context(token, "AAPL")
            self.assertEqual(verified["revenue"], 400_000_000_000.0)
            self.assertNotIn(secret, request.wfile.getvalue().decode())

    def test_analyze_does_not_advertise_scenarios_when_signed_facts_are_stale(self):
        secret = "analysis-route-stale-test-" + "0123456789abcdef" * 3
        stale_at = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        company = {
            "ticker": "AAPL", "sector": "Technology", "sector_methodology_supported": True,
            "reporting_currency": "USD", "currency_methodology_supported": True,
            "revenue": 1000.0, "revenue_period": f"{datetime.now(timezone.utc).year - 1}-12-31",
            "market_cap_reporting": 2500.0, "enterprise_value": 2600.0, "market_at": stale_at,
            "cash": 100.0, "debt": 200.0, "methodology_version": "score-v10",
            "analysis_schema_version": "analysis-v5",
            "sources": {
                "revenue": {"period": f"{datetime.now(timezone.utc).year - 1}-12-31", "currency": "USD"},
                "cash": {"period": f"{datetime.now(timezone.utc).year - 1}-12-31", "currency": "USD"},
                "debt": {"period": f"{datetime.now(timezone.utc).year - 1}-12-31", "currency": "USD"},
            },
        }
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": secret}), patch(
            "api.analyze.analyze_company", return_value={"company": company, "price_history": []}
        ):
            AnalyzeHandler.do_GET(request)

        data = request.payload()["data"]
        self.assertIsNone(data["scenario_context_token"])
        self.assertEqual(data["scenario_context_status"], "unavailable")

    def test_analyze_withholds_scenario_context_for_unsupported_methodology(self):
        company = {
            "ticker": "AAPL", "sector_methodology_supported": False,
            "currency_methodology_supported": False,
        }
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": "scenario-route-test-0123456789abcdef0123456789abcdef"}), patch(
            "api.analyze.analyze_company", return_value={"company": company, "price_history": []}
        ):
            AnalyzeHandler.do_GET(request)
        data = request.payload()["data"]
        self.assertIsNone(data["scenario_context_token"])
        self.assertEqual(data["scenario_context_status"], "unsupported")

    def test_analyze_rejects_duplicate_ticker_parameter(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL&ticker=MSFT")
        with patch("api.analyze.analyze_company") as analyze:
            AnalyzeHandler.do_GET(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        analyze.assert_not_called()

    def test_search_sends_query_in_body_and_never_caches_it(self):
        query = "personal search phrase"
        body = json.dumps({"q": query}).encode()
        request = RequestRecorder(
            "/api/search",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            method="POST",
        )
        result = [{"ticker": "AAPL", "company": "Apple Inc.", "exchange": "Nasdaq", "sector": "Technology"}]
        with self.assertLogs("tsrp", level="INFO") as captured, patch(
            "api.search.search_companies", return_value=result
        ) as search:
            SearchHandler.do_POST(request)
        self.assertEqual(request.status, 200)
        self.assertEqual(request.path, "/api/search")
        self.assertEqual(request.response_headers["Cache-Control"], "no-store")
        self.assertEqual(request.payload()["data"], result)
        search.assert_called_once_with(query)
        self.assertNotIn(query, captured.output[0])
        self.assertEqual(json.loads(captured.output[0].split("api_request ", 1)[1])["method"], "POST")

    def test_search_rejects_unexpected_or_non_text_body_fields(self):
        for payload in ({"q": "Apple", "debug": True}, {"q": ["Apple"]}, {}):
            with self.subTest(payload=payload):
                body = json.dumps(payload).encode()
                request = RequestRecorder(
                    "/api/search",
                    body=body,
                    headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
                    method="POST",
                )
                with patch("api.search.search_companies") as search:
                    SearchHandler.do_POST(request)
                self.assertEqual(request.status, 400)
                self.assertEqual(request.payload()["error"]["code"], "invalid_request")
                search.assert_not_called()

    def test_search_rejects_overlong_query_before_provider_request(self):
        body = json.dumps({"q": "A" * 81}).encode()
        request = RequestRecorder(
            "/api/search",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            method="POST",
        )
        with patch("research_service.yf.Search") as provider_search:
            SearchHandler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "query_too_long")
        provider_search.assert_not_called()

    def test_search_rejects_contact_or_identifier_text_without_logging_or_provider_access(self):
        for query in (
            "person@example.test",
            "+1 (415) 555-0134",
            "415/555/0134",
            "415‐555‐0134",
            "user＠example.com",
            "user%40example%2Etest",
            "user%40example%2Etest%FF",
            "user%40example%2Etest%ZZ",
            "user%2540example%252Etest",
            "４１５／５５５／０１３４",
            "%34%31%35%2F%35%35%35%2F%30%31%33%34",
            "415\u200b555\u200b0134",
            "4111 1111 1111 1111",
        ):
            body = json.dumps({"q": query}).encode()
            request = RequestRecorder(
                "/api/search",
                body=body,
                headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
                method="POST",
            )
            with self.subTest(query=query), self.assertLogs("tsrp", level="INFO") as captured, patch(
                "research_service.yf.Search"
            ) as provider_search:
                SearchHandler.do_POST(request)
            self.assertEqual(request.status, 400)
            self.assertEqual(request.payload()["error"]["code"], "personal_data_not_allowed")
            self.assertNotIn(query, request.wfile.getvalue().decode())
            self.assertNotIn(query, "\n".join(captured.output))
            provider_search.assert_not_called()

    def test_search_provider_failure_returns_retryable_safe_error_not_empty_matches(self):
        body = json.dumps({"q": "Apple"}).encode()
        request = RequestRecorder(
            "/api/search",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            method="POST",
        )
        with patch("research_service.yf.Search", side_effect=RuntimeError("429 Too Many Requests private detail")):
            SearchHandler.do_POST(request)
        self.assertEqual(request.status, 503)
        self.assertEqual(request.payload()["error"]["code"], "search_unavailable")
        self.assertNotIn("private detail", request.wfile.getvalue().decode())

    def test_search_provider_failure_does_not_log_query_or_provider_diagnostics(self):
        query = "private-query-marker"
        body = json.dumps({"q": query}).encode()
        request = RequestRecorder(
            "/api/search",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            method="POST",
        )
        provider_logger = logging.getLogger("yfinance")
        previous_level = provider_logger.level
        previous_propagation = provider_logger.propagate
        self.addCleanup(setattr, provider_logger, "level", previous_level)
        self.addCleanup(setattr, provider_logger, "propagate", previous_propagation)
        provider_messages = []

        class CaptureProviderLog(logging.Handler):
            def emit(self, record):
                provider_messages.append(record.getMessage())

        capture = CaptureProviderLog()
        provider_logger.addHandler(capture)
        self.addCleanup(provider_logger.removeHandler, capture)

        def fail_with_diagnostic(*_args, **_kwargs):
            provider_logger.error("search failed for %s", query)
            raise RuntimeError(f"upstream diagnostic includes {query}")

        with self.assertLogs("tsrp", level="INFO") as captured, patch(
            "research_service.yf.Search", side_effect=fail_with_diagnostic
        ):
            SearchHandler.do_POST(request)

        response = request.wfile.getvalue().decode()
        self.assertEqual(request.status, 503)
        self.assertEqual(request.payload()["error"]["code"], "search_unavailable")
        self.assertNotIn(query, response)
        self.assertNotIn(query, "\n".join(captured.output))
        self.assertEqual(provider_messages, [])

    def test_analyze_masks_provider_exception_text(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch("api.analyze.analyze_company", side_effect=RuntimeError("provider token private-value")):
            AnalyzeHandler.do_GET(request)
        response = request.wfile.getvalue().decode()
        self.assertEqual(request.status, 503)
        self.assertEqual(request.response_headers["Cache-Control"], "no-store")
        self.assertNotIn("private-value", response)
        self.assertEqual(request.payload()["error"]["code"], "service_unavailable")

    def test_request_log_correlates_without_recording_query_or_personal_text(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL&note=mazhar%40example.com")
        with self.assertLogs("tsrp", level="INFO") as captured:
            AnalyzeHandler.do_GET(request)
        record = json.loads(captured.output[0].split("api_request ", 1)[1])
        self.assertEqual(record["route"], "/api/analyze")
        self.assertEqual(record["method"], "GET")
        self.assertEqual(record["status"], 400)
        self.assertEqual(record["outcome"], "error")
        self.assertEqual(record["error_code"], "invalid_request")
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertEqual(request.response_headers["X-Request-ID"], record["request_id"])
        self.assertNotIn("?ticker=", captured.output[0])
        self.assertNotIn("mazhar", captured.output[0])

    def test_request_log_classifies_unexpected_error_without_raw_exception(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with self.assertLogs("tsrp", level="INFO") as captured:
            with patch("api.analyze.analyze_company", side_effect=RuntimeError("private provider token")):
                AnalyzeHandler.do_GET(request)
        record = json.loads(captured.output[0].split("api_request ", 1)[1])
        self.assertEqual(record["route"], "/api/analyze")
        self.assertEqual(record["status"], 503)
        self.assertEqual(record["error_code"], "service_unavailable")
        self.assertEqual(record["error_type"], "RuntimeError")
        self.assertNotIn("private provider token", captured.output[0])

    def test_analyze_capacity_saturation_is_retryable_service_unavailable(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch("api.analyze.analyze_company", side_effect=ResearchError(
            "provider_unavailable", "Company data is temporarily unavailable. Please retry shortly.", 503
        )):
            AnalyzeHandler.do_GET(request)
        self.assertEqual(request.status, 503)
        self.assertEqual(request.payload()["error"]["code"], "provider_unavailable")

    def test_scenario_rejects_oversized_body_before_parsing(self):
        request = RequestRecorder(
            "/api/scenario", headers={"Content-Length": "20000", "Content-Type": "application/json"}
        )
        ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 413)
        self.assertEqual(request.payload()["error"]["code"], "request_too_large")

    def test_scenario_rejects_missing_or_invalid_content_length_as_bad_request(self):
        for label, raw_length in (("missing", None), ("zero", "0"), ("negative", "-1"), ("malformed", "abc")):
            with self.subTest(content_length=label):
                headers = {"Content-Type": "application/json"}
                if raw_length is not None:
                    headers["Content-Length"] = raw_length
                request = RequestRecorder("/api/scenario", headers=headers, method="POST")
                with patch("api.scenario.analyze_scenario") as analyze:
                    ScenarioHandler.do_POST(request)
                self.assertEqual(request.status, 400)
                self.assertEqual(request.payload()["error"]["code"], "invalid_request")
                analyze.assert_not_called()

    def test_scenario_accepts_a_valid_typed_request(self):
        basis = {"ticker": "AAPL", "market_at": "recent", "reporting_currency": "USD"}
        body = json.dumps({
            "ticker": "AAPL",
            "assumptions": {
                "starting_growth": 0.12,
                "fcf_margin": 0.18,
                "discount_rate": 0.09,
                "terminal_growth": 0.02,
            },
            "context_token": "signed-company-context",
        }).encode()
        request = RequestRecorder(
            "/api/scenario",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            method="POST",
        )
        with patch("api.scenario.verify_scenario_context", return_value=basis) as verify, patch(
            "api.scenario.analyze_scenario", return_value={"company": {"ticker": "AAPL"}}
        ) as analyze:
            ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 200)
        self.assertEqual(request.response_headers["Cache-Control"], "no-store")
        self.assertEqual(request.payload()["data"]["company"]["ticker"], "AAPL")
        verify.assert_called_once_with("signed-company-context", "AAPL")
        analyze.assert_called_once_with("AAPL", {
            "starting_growth": 0.12,
            "fcf_margin": 0.18,
            "discount_rate": 0.09,
            "terminal_growth": 0.02,
        }, basis)

    def test_scenario_rejects_client_supplied_basis_facts(self):
        body = json.dumps({
            "ticker": "AAPL",
            "assumptions": {},
            "basis": {"ticker": "AAPL", "enterprise_value": 1},
        }).encode()
        request = RequestRecorder(
            "/api/scenario",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            method="POST",
        )
        with patch("api.scenario.verify_scenario_context") as verify, patch("api.scenario.analyze_scenario") as analyze:
            ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        verify.assert_not_called()
        analyze.assert_not_called()

    def test_scenario_rejects_missing_or_invalid_signed_context(self):
        body = json.dumps({
            "ticker": "AAPL",
            "assumptions": {},
            "context_token": "invalid",
        }).encode()
        request = RequestRecorder(
            "/api/scenario",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
            method="POST",
        )
        with patch("api.scenario.verify_scenario_context", return_value=None) as verify, patch(
            "api.scenario.analyze_scenario"
        ) as analyze:
            ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 422)
        self.assertEqual(request.payload()["error"]["code"], "scenario_context_invalid")
        verify.assert_called_once_with("invalid", "AAPL")
        analyze.assert_not_called()

    def test_signed_context_flows_from_analysis_into_real_scenario_calculation(self):
        secret = "scenario-integration-test-" + "0123456789abcdef" * 3
        now = datetime.now(timezone.utc)
        period = (now - timedelta(days=120)).date().isoformat()
        market_at = now.isoformat()
        company = {
            "ticker": "AAPL",
            "sector": "Technology",
            "sector_methodology_supported": True,
            "reporting_currency": "USD",
            "currency_methodology_supported": True,
            "revenue": 400_000_000_000.0,
            "revenue_period": period,
            "market_cap_reporting": 3_000_000_000_000.0,
            "enterprise_value": 3_050_000_000_000.0, "other_claims": 0.0,
            "market_at": market_at,
            "cash": 60_000_000_000.0,
            "debt": 110_000_000_000.0,
            "fcf_margin": 0.12, "unlevered_fcf_margin": 0.12,
            "methodology_version": "score-v10",
            "analysis_schema_version": "analysis-v5",
            "sources": {
                "revenue": {"period": period, "currency": "USD"},
                "cash": {"period": period, "currency": "USD"},
                "debt": {"period": period, "currency": "USD"},
            },
        }
        analysis_request = RequestRecorder("/api/analyze?ticker=AAPL")
        assumptions = {
            "starting_growth": 0.12,
            "fcf_margin": 0.18,
            "discount_rate": 0.09,
            "terminal_growth": 0.02,
        }
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": secret}), patch(
            "api.analyze.analyze_company", return_value={"company": company, "price_history": []}
        ):
            AnalyzeHandler.do_GET(analysis_request)
            token = analysis_request.payload()["data"]["scenario_context_token"]
            scenario_body = json.dumps({
                "ticker": "aapl",
                "assumptions": assumptions,
                "context_token": token,
            }).encode()
            scenario_request = RequestRecorder(
                "/api/scenario",
                body=scenario_body,
                headers={"Content-Length": str(len(scenario_body)), "Content-Type": "application/json"},
                method="POST",
            )
            ScenarioHandler.do_POST(scenario_request)

        self.assertEqual(analysis_request.status, 200)
        self.assertEqual(scenario_request.status, 200)
        result = scenario_request.payload()["data"]
        self.assertGreater(result["enterprise_value"], 0)
        self.assertEqual(result["inputs"], assumptions)
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": secret}):
            signed_basis = verify_scenario_context(token, "AAPL")
        self.assertEqual(signed_basis["starting_fcf_margin"], 0.12)
        expected_enterprise_value = dcf_enterprise_value(
            company["revenue"], assumptions["starting_growth"], assumptions["fcf_margin"],
            assumptions["discount_rate"], assumptions["terminal_growth"],
            start_margin=company["fcf_margin"],
        )
        self.assertAlmostEqual(result["enterprise_value"], expected_enterprise_value, places=8)

    def test_scenario_rejects_replayed_expired_signed_context(self):
        secret = "scenario-expired-token-" + "0123456789abcdef" * 3
        now = datetime.now(timezone.utc)
        period = (now - timedelta(days=120)).date().isoformat()
        company = {
            "ticker": "AAPL",
            "sector": "Technology",
            "reporting_currency": "USD",
            "revenue": 400_000_000_000.0,
            "revenue_period": period,
            "market_cap_reporting": 3_000_000_000_000.0,
            "enterprise_value": 3_050_000_000_000.0, "other_claims": 0.0,
            "market_at": (now - timedelta(days=3)).isoformat(),
            "cash": 60_000_000_000.0,
            "debt": 110_000_000_000.0,
            "fcf_margin": 0.12, "unlevered_fcf_margin": 0.12,
            "methodology_version": "score-v10",
            "analysis_schema_version": "analysis-v5",
            "sources": {
                "revenue": {"period": period, "currency": "USD"},
                "cash": {"period": period, "currency": "USD"},
                "debt": {"period": period, "currency": "USD"},
            },
        }
        assumptions = {
            "starting_growth": 0.12,
            "fcf_margin": 0.18,
            "discount_rate": 0.09,
            "terminal_growth": 0.02,
        }
        with patch.dict(os.environ, {"TSRP_SCENARIO_SIGNING_SECRET": secret}):
            token = issue_scenario_context(company)
            body = json.dumps({
                "ticker": "AAPL",
                "assumptions": assumptions,
                "context_token": token,
            }).encode()
            request = RequestRecorder(
                "/api/scenario",
                body=body,
                headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
                method="POST",
            )
            ScenarioHandler.do_POST(request)

        self.assertIsInstance(token, str)
        self.assertEqual(request.status, 422)
        self.assertEqual(request.payload()["error"]["code"], "scenario_data_stale")

    def test_scenario_rejects_non_string_ticker_before_context_verification(self):
        for ticker in (123, True):
            with self.subTest(ticker=ticker):
                body = json.dumps({"ticker": ticker, "assumptions": {}, "context_token": "signed-context"}).encode()
                request = RequestRecorder(
                    "/api/scenario",
                    body=body,
                    headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
                    method="POST",
                )
                with patch("api.scenario.verify_scenario_context") as verify:
                    ScenarioHandler.do_POST(request)
                self.assertEqual(request.status, 400)
                self.assertEqual(request.payload()["error"]["code"], "invalid_ticker")
                verify.assert_not_called()

    def test_scenario_rejects_duplicate_json_keys(self):
        body = b'{"ticker":"AAPL","ticker":"MSFT","assumptions":{}}'
        request = RequestRecorder(
            "/api/scenario",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
        )
        with patch("api.scenario.analyze_scenario") as analyze:
            ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        analyze.assert_not_called()

    def test_scenario_rejects_non_finite_json_numbers(self):
        for number in (b"NaN", b"Infinity", b"1e999"):
            with self.subTest(number=number):
                body = b'{"ticker":"AAPL","assumptions":{"discount_rate":' + number + b"}}"
                request = RequestRecorder(
                    "/api/scenario",
                    body=body,
                    headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
                )
                ScenarioHandler.do_POST(request)
                self.assertEqual(request.status, 400)
                self.assertEqual(request.payload()["error"]["code"], "invalid_request")

    def test_scenario_rejects_truncated_json_body(self):
        request = RequestRecorder(
            "/api/scenario",
            body=b"{}",
            headers={"Content-Length": "8", "Content-Type": "application/json"},
        )
        ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")

    def test_scenario_rejects_unsupported_fields(self):
        body = b'{"ticker":"AAPL","assumptions":{},"debug":true}'
        request = RequestRecorder(
            "/api/scenario",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
        )
        with patch("api.scenario.analyze_scenario") as analyze:
            ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        analyze.assert_not_called()

    def test_routes_return_consistent_json_for_unsupported_methods(self):
        for route, method, allow in (
            (AnalyzeHandler, "do_POST", "GET"),
            (SearchHandler, "do_GET", "POST"),
            (ScenarioHandler, "do_GET", "POST"),
        ):
            with self.subTest(route=route.__module__, method=method):
                request = RequestRecorder()
                request.allowed_methods = route.allowed_methods
                getattr(route, method)(request)
                self.assertEqual(request.status, 405)
                self.assertEqual(request.response_headers["Allow"], allow)
                self.assertEqual(request.response_headers["Cache-Control"], "no-store")
                self.assertEqual(request.payload()["error"]["code"], "method_not_allowed")

    def test_unimplemented_http_verb_uses_safe_json_method_error(self):
        request = RequestRecorder()
        request.allowed_methods = "GET"
        AnalyzeHandler.send_error(request, 501, "private server details")
        self.assertEqual(request.status, 405)
        self.assertEqual(request.response_headers["Allow"], "GET")
        self.assertEqual(request.response_headers["Cache-Control"], "no-store")
        self.assertEqual(request.payload()["error"]["code"], "method_not_allowed")
        self.assertNotIn("private server details", request.wfile.getvalue().decode())

    def test_every_vercel_api_route_uses_safe_json_for_unimplemented_methods(self):
        for route, allow in (
            (AnalyzeHandler, "GET"),
            (SearchHandler, "POST"),
            (ScenarioHandler, "POST"),
            (MoversHandler, "GET"),
            (TopStocksHandler, "GET"),
            (SnapshotDiffHandler, "POST"),
        ):
            with self.subTest(route=route.__module__):
                request = RequestRecorder()
                request.allowed_methods = allow
                route.send_error(request, 501, "private server details")
                self.assertEqual(request.status, 405)
                self.assertEqual(request.response_headers["Allow"], allow)
                self.assertEqual(request.response_headers["Cache-Control"], "no-store")
                self.assertEqual(request.payload()["error"]["code"], "method_not_allowed")
                self.assertNotIn("private server details", request.wfile.getvalue().decode())

    def test_local_router_returns_405_instead_of_crashing_on_known_route_wrong_method(self):
        request = RequestRecorder("/api/movers")
        request.headers["Origin"] = "http://localhost:3000"
        Router._forward(request, "do_POST")
        self.assertEqual(request.status, 405)
        self.assertEqual(request.response_headers["Allow"], "GET")
        self.assertEqual(request.payload()["error"]["code"], "method_not_allowed")

    def test_local_router_rejects_untrusted_and_missing_origins(self):
        for origin in ("https://untrusted.example", None):
            with self.subTest(origin=origin):
                headers = {"Origin": origin} if origin is not None else {}
                request = RequestRecorder("/api/analyze?ticker=AAPL", headers=headers)
                with patch("api.analyze.analyze_company") as analyze:
                    Router._forward(request, "do_GET")
                self.assertEqual(request.status, 403)
                self.assertEqual(request.payload()["error"]["code"], "origin_not_allowed")
                analyze.assert_not_called()

    def test_local_router_exposes_reference_and_clock_headers_only_to_allowed_origins(self):
        allowed = RequestRecorder(headers={"Origin": "http://localhost:3000"})
        add_local_cors_headers(allowed)
        self.assertEqual(allowed.response_headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertEqual(allowed.response_headers["Access-Control-Expose-Headers"], "X-Request-ID, Date")
        denied = RequestRecorder(headers={"Origin": "https://untrusted.example"})
        add_local_cors_headers(denied)
        self.assertNotIn("Access-Control-Expose-Headers", denied.response_headers)

    def test_movers_and_top_stocks_reject_unused_query_parameters(self):
        for route, path, provider in (
            (MoversHandler, "/api/movers?debug=true", "research_service.daily_market_movers"),
            (TopStocksHandler, "/api/top_stocks?debug=true", "research_service.daily_top_stocks"),
        ):
            with self.subTest(route=route.__module__):
                request = RequestRecorder(path)
                with patch(provider) as call:
                    route.do_GET(request)
                self.assertEqual(request.status, 400)
                self.assertEqual(request.payload()["error"]["code"], "invalid_request")
                call.assert_not_called()

    def test_public_market_routes_use_separate_inflight_coalescing_keys(self):
        for route, path, key, provider in (
            (MoversHandler, "/api/movers", "daily_market_movers", "api.movers.daily_market_movers"),
            (TopStocksHandler, "/api/top_stocks", "daily_top_stocks", "api.top_stocks.daily_top_stocks"),
        ):
            with self.subTest(route=route.__module__):
                request = RequestRecorder(path)
                with patch(provider) as calculate, patch(
                    f"{route.__module__}.coalesce_inflight", return_value={"items": []}
                ) as coalesce:
                    route.do_GET(request)
                self.assertEqual(request.status, 200)
                self.assertEqual(request.response_headers["Cache-Control"], "private, no-store")
                coalesce.assert_called_once_with(
                    key,
                    calculate,
                    cancel_event=None,
                    run_with_cancellation=run_with_provider_cancellation,
                )

    def test_top_stocks_popular_mode_uses_separate_coalescing_key_and_provider(self):
        request = RequestRecorder("/api/top_stocks?list=popular")
        with patch("api.top_stocks.daily_popular_stocks", return_value={"label": "Popular stocks"}) as calculate, patch(
            "api.top_stocks.coalesce_inflight", side_effect=lambda _key, provider, **_kwargs: provider()
        ) as coalesce:
            TopStocksHandler.do_GET(request)
        self.assertEqual(request.status, 200)
        self.assertEqual(request.payload()["data"]["label"], "Popular stocks")
        coalesce.assert_called_once_with(
            "daily_popular_stocks",
            calculate,
            cancel_event=None,
            run_with_cancellation=run_with_provider_cancellation,
        )

    def test_top_stocks_rejects_unknown_list_mode_without_provider_work(self):
        request = RequestRecorder("/api/top_stocks?list=all")
        with patch("api.top_stocks.daily_top_stocks") as active, patch("api.top_stocks.daily_popular_stocks") as popular:
            TopStocksHandler.do_GET(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        active.assert_not_called()
        popular.assert_not_called()

    def test_snapshot_comparison_rejects_unexpected_body_fields(self):
        body = b'{"previous":{},"current":{},"debug":true}'
        request = RequestRecorder(
            "/api/snapshot_diff",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
        )
        with patch("api.snapshot_diff.compare_snapshots") as compare:
            SnapshotDiffHandler.do_POST(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_request")
        compare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
