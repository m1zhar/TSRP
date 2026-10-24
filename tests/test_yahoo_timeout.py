import gzip
import inspect
import os
import time
import unittest
from concurrent.futures import TimeoutError as FutureTimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

from curl_cffi.requests import Session as CurlSession
import yfinance as yf

from research_service import (
    MAX_YAHOO_REQUEST_TIMEOUT_SECONDS,
    _PROVIDER_REQUEST_CONTEXT,
    _SEC_SESSION,
    _YAHOO_SESSION,
    _bounded_sec_timeout,
    _bounded_provider_calls,
    _bounded_yahoo_timeout,
    _cancel_market_provider_call,
    daily_market_movers,
    daily_popular_stocks,
    daily_top_stocks,
    _fetch_sec_company_facts,
    _sec_facts,
    _submit_market_provider_call,
    _YahooAccessDisabled,
    _YahooResponseTooLarge,
    ResearchError,
    analyze_company,
    search_companies,
)


class YahooTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.yahoo_enabled = patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "true"})
        self.yahoo_enabled.start()
        self.addCleanup(self.yahoo_enabled.stop)

    def test_transport_fails_closed_unless_operator_explicitly_enables_yahoo(self):
        response = SimpleNamespace(content=b"")
        with (
            patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "false"}),
            patch.object(CurlSession, "request", return_value=response) as request,
        ):
            with self.assertRaises(_YahooAccessDisabled):
                _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=1)

        request.assert_not_called()

    def test_yahoo_enable_flag_is_strict_and_whitespace_tolerant(self):
        for setting in ("", "yes", "on"):
            with self.subTest(setting=setting):
                with (
                    patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": setting}),
                    patch.object(CurlSession, "request") as request,
                ):
                    with self.assertRaises(_YahooAccessDisabled):
                        _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=1)
                request.assert_not_called()

        response = SimpleNamespace(content=b"")
        with (
            patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": " TRUE "}),
            patch.object(CurlSession, "request", return_value=response) as request,
        ):
            _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=1)
        request.assert_called_once()

    def test_company_search_fails_safely_without_a_provider_request_when_disabled(self):
        with (
            patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "false"}),
            patch.object(CurlSession, "request") as request,
        ):
            with self.assertRaises(ResearchError) as raised:
                search_companies("Apple")

        self.assertEqual(raised.exception.code, "provider_disabled")
        request.assert_not_called()

    def test_market_movers_fail_safely_without_a_provider_request_when_disabled(self):
        with (
            patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "false"}),
            patch.object(CurlSession, "request") as request,
        ):
            with self.assertRaises(ResearchError) as raised:
                daily_market_movers()

        self.assertEqual(raised.exception.code, "provider_disabled")
        request.assert_not_called()

    def test_company_analysis_fails_safely_without_a_provider_request_when_disabled(self):
        with (
            patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "false"}),
            patch.object(CurlSession, "request") as request,
            patch("research_service._sec_facts") as sec_facts,
        ):
            with self.assertRaises(ResearchError) as raised:
                analyze_company("AAPL")

        self.assertEqual(raised.exception.code, "provider_disabled")
        request.assert_not_called()
        sec_facts.assert_not_called()

    def test_top_stock_lists_fail_clearly_before_provider_work_when_disabled(self):
        with (
            patch.dict(os.environ, {"TSRP_YAHOO_FINANCE_ENABLED": "false"}),
            patch.object(CurlSession, "request") as request,
        ):
            for provider in (daily_popular_stocks, daily_top_stocks):
                with self.subTest(provider=provider.__name__):
                    with self.assertRaises(ResearchError) as raised:
                        provider()
                    self.assertEqual(raised.exception.code, "provider_disabled")

        request.assert_not_called()

    def test_caps_default_and_provider_default_timeouts(self):
        for timeout in (None, 30, 10):
            with self.subTest(timeout=timeout):
                self.assertEqual(
                    _bounded_yahoo_timeout(timeout),
                    MAX_YAHOO_REQUEST_TIMEOUT_SECONDS,
                )

    def test_preserves_short_and_connect_read_timeouts(self):
        self.assertEqual(_bounded_yahoo_timeout(3), 3)
        self.assertEqual(_bounded_yahoo_timeout((2, 20)), (2, 8))

    def test_sec_timeout_uses_remaining_absolute_budget(self):
        with patch.object(_PROVIDER_REQUEST_CONTEXT, "deadline", time.monotonic() + 0.25, create=True):
            timeout = _bounded_sec_timeout(15)

        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 0.25)

    def test_expired_sec_deadline_fails_before_a_request(self):
        with patch.object(_PROVIDER_REQUEST_CONTEXT, "deadline", time.monotonic() - 1, create=True):
            with self.assertRaisesRegex(TimeoutError, "task deadline exceeded"):
                _bounded_sec_timeout(15)

    def test_sec_total_timeout_aborts_a_slow_trickling_response(self):
        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"{" + b" " * 18 + b"}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                for byte in body:
                    try:
                        self.wfile.write(bytes((byte,)))
                        self.wfile.flush()
                        time.sleep(0.05)
                    except (BrokenPipeError, ConnectionResetError):
                        break

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started = time.monotonic()
        request = _SEC_SESSION.get
        try:
            with (
                patch.object(
                    _SEC_SESSION,
                    "get",
                    side_effect=lambda _url, **kwargs: request(
                        f"http://127.0.0.1:{server.server_port}/", **kwargs
                    ),
                ),
                patch.object(
                    _PROVIDER_REQUEST_CONTEXT,
                    "deadline",
                    time.monotonic() + 0.25,
                    create=True,
                ),
            ):
                facts, state = _fetch_sec_company_facts("0000000001", "Test contact test@example.com")

            self.assertIsNone(facts)
            self.assertEqual(state["kind"], "timeout")
            self.assertLess(time.monotonic() - started, 0.8)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_expired_sec_deadline_returns_a_typed_unavailable_state(self):
        with (
            patch.object(_PROVIDER_REQUEST_CONTEXT, "deadline", time.monotonic() - 1, create=True),
            patch("research_service._sec_identity", return_value="Test person test@example.com"),
            patch("research_service._SEC_TICKER_MAP_CACHE", {"AAPL": ("0000000001", "Apple Inc.")}),
            patch("research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", time.monotonic() + 60),
            patch.object(_SEC_SESSION, "get") as request,
        ):
            facts, state = _sec_facts("AAPL", "USD", "USD")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "timeout")
        request.assert_not_called()

    def test_expired_sec_ticker_map_deadline_is_cached_as_timeout(self):
        with (
            patch.object(_PROVIDER_REQUEST_CONTEXT, "deadline", time.monotonic() - 1, create=True),
            patch("research_service._sec_identity", return_value="Test person test@example.com"),
            patch("research_service._SEC_TICKER_MAP_CACHE", None),
            patch("research_service._SEC_TICKER_MAP_CACHE_EXPIRES_AT", 0.0),
            patch("research_service._SEC_TICKER_MAP_FAILURE_KIND", None),
            patch("research_service._SEC_TICKER_MAP_FAILURE_EXPIRES_AT", 0.0),
            patch.object(_SEC_SESSION, "get") as request,
        ):
            facts, state = _sec_facts("AAPL", "USD", "USD")

        self.assertIsNone(facts)
        self.assertEqual(state["kind"], "timeout")
        request.assert_not_called()

    def test_bounded_session_applies_cap_to_underlying_transport(self):
        response = SimpleNamespace(content=b"")
        with patch.object(CurlSession, "request", return_value=response) as request:
            result = _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=30)

        self.assertIs(result, response)
        self.assertEqual(response.content, b"")
        self.assertEqual(request.call_args.kwargs["timeout"], MAX_YAHOO_REQUEST_TIMEOUT_SECONDS)

    def test_bounded_session_preserves_response_content_for_yfinance(self):
        class JsonHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"quote": {"symbol": "AAPL"}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), JsonHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = _YAHOO_SESSION.get(
                f"http://127.0.0.1:{server.server_port}/quote", timeout=2
            )
            self.assertEqual(response.json(), {"quote": {"symbol": "AAPL"}})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_bounded_session_stops_oversized_body_without_content_length(self):
        class OversizedHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                try:
                    self.wfile.write(b"x" * 33)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), OversizedHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("research_service.MAX_YAHOO_RESPONSE_BYTES", 32):
                with self.assertRaises(_YahooResponseTooLarge):
                    _YAHOO_SESSION.get(
                        f"http://127.0.0.1:{server.server_port}/oversized", timeout=2
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_bounded_session_caps_decompressed_gzip_body(self):
        body = gzip.compress(b"x" * 33)
        self.assertLess(len(body), 32)

        class GzipHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), GzipHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch("research_service.MAX_YAHOO_RESPONSE_BYTES", 32):
                with self.assertRaises(_YahooResponseTooLarge):
                    _YAHOO_SESSION.get(
                        f"http://127.0.0.1:{server.server_port}/compressed", timeout=2
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_custom_content_callback_cannot_bypass_yahoo_body_limit(self):
        with patch.object(CurlSession, "request") as request:
            with self.assertRaisesRegex(ValueError, "callbacks are not supported"):
                _YAHOO_SESSION.get(
                    "https://finance.yahoo.com", content_callback=lambda _chunk: 0
                )

        request.assert_not_called()

    def test_pinned_yfinance_accepts_the_bounded_session_without_network_access(self):
        instrument = yf.Ticker("AAPL", session=_YAHOO_SESSION)

        self.assertIs(instrument._data._session, _YAHOO_SESSION)
        self.assertIn("session", inspect.signature(yf.Search).parameters)
        self.assertIn("session", inspect.signature(yf.screen).parameters)
        self.assertIn("session", inspect.signature(yf.download).parameters)

    def test_worker_deadline_caps_yahoo_requests_to_remaining_task_budget(self):
        response = SimpleNamespace(content=b"")
        with patch.object(CurlSession, "request", return_value=response) as request:
            result, unavailable = _bounded_provider_calls(
                {"yahoo": lambda: _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=30)},
                timeout_seconds=2,
            )

        self.assertIs(result["yahoo"], response)
        self.assertEqual(unavailable, [])
        self.assertGreater(request.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(request.call_args.kwargs["timeout"], 2)

    def test_expired_worker_deadline_prevents_a_new_provider_request(self):
        with patch.object(_PROVIDER_REQUEST_CONTEXT, "deadline", time.monotonic() - 1, create=True):
            with patch.object(CurlSession, "request") as request:
                with self.assertRaisesRegex(TimeoutError, "task deadline exceeded"):
                    _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=30)

        request.assert_not_called()

    def test_caller_timeout_cannot_start_late_yahoo_retries(self):
        started = Event()
        release = Event()
        finished = Event()

        def delayed_provider_call():
            started.set()
            release.wait(timeout=1)
            try:
                return _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=30)
            finally:
                finished.set()

        with patch.object(CurlSession, "request") as request:
            _results, unavailable = _bounded_provider_calls(
                {"slow": delayed_provider_call}, timeout_seconds=0.01
            )
            self.assertEqual(unavailable, ["slow"])
            self.assertTrue(started.wait(timeout=1))
            release.set()
            self.assertTrue(finished.wait(timeout=1))

        request.assert_not_called()

    def test_market_task_deadline_prevents_late_screener_request(self):
        started = Event()
        release = Event()
        finished = Event()

        def delayed_market_call():
            started.set()
            release.wait(timeout=1)
            try:
                return _YAHOO_SESSION.get("https://finance.yahoo.com", timeout=30)
            finally:
                finished.set()

        with patch.object(CurlSession, "request") as request:
            future = _submit_market_provider_call(delayed_market_call, timeout_seconds=0.01)
            self.assertIsNotNone(future)
            self.assertTrue(started.wait(timeout=1))
            with self.assertRaises(FutureTimeoutError):
                future.result(timeout=0.02)
            self.assertFalse(_cancel_market_provider_call(future))
            release.set()
            self.assertTrue(finished.wait(timeout=1))
            self.assertRegex(str(future.exception(timeout=1)), "task deadline exceeded")

        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
