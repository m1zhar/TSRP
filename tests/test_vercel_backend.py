from __future__ import annotations

import json
from io import BytesIO
import unittest
from unittest.mock import patch

import vercel_backend


class VercelBackendTests(unittest.TestCase):
    def request(self, path, method="GET", *, query="", payload=None, content_type=None):
        encoded = b"" if payload is None else json.dumps(payload).encode("utf-8")
        environ = {
            "PATH_INFO": path,
            "REQUEST_METHOD": method,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(encoded)),
            "CONTENT_TYPE": "application/json" if encoded else "",
            "wsgi.input": BytesIO(encoded),
        }
        if content_type is not None:
            environ["CONTENT_TYPE"] = content_type
        result = {}

        def start_response(status, headers):
            result["status"] = status
            result["headers"] = dict(headers)

        result["body"] = b"".join(vercel_backend.app(environ, start_response))
        return result

    @patch("vercel_backend.analyze_company", return_value={"ticker": "AAPL"})
    def test_analysis_route_preserves_payload_and_cache(self, analyze):
        response = self.request("/api/analyze", query="ticker=AAPL")
        self.assertEqual(response["status"], "200 OK")
        self.assertEqual(json.loads(response["body"]), {"data": {"ticker": "AAPL"}})
        self.assertEqual(response["headers"]["Cache-Control"], "public, s-maxage=300, stale-while-revalidate=900")
        analyze.assert_called_once_with("AAPL")

    @patch("vercel_backend.search_companies", return_value=[{"ticker": "AAPL"}])
    def test_search_route_keeps_short_cache_policy(self, search):
        response = self.request("/api/search", query="q=Apple")
        self.assertEqual(json.loads(response["body"]), {"data": [{"ticker": "AAPL"}]})
        self.assertEqual(response["headers"]["Cache-Control"], "public, s-maxage=30, stale-while-revalidate=60")
        search.assert_called_once_with("Apple")

    def test_invalid_ticker_returns_safe_validation_error(self):
        response = self.request("/api/analyze", query="ticker=%5EGSPC")
        payload = json.loads(response["body"])
        self.assertEqual(response["status"], "400 Bad Request")
        self.assertEqual(payload["error"]["code"], "invalid_ticker")
        self.assertNotIn("traceback", response["body"].decode("utf-8").lower())

    def test_post_requires_json_and_bounded_body(self):
        wrong_type = self.request("/api/scenario", "POST", payload={"ticker": "AAPL"}, content_type="text/plain")
        self.assertEqual(wrong_type["status"], "415 Unsupported Media Type")

        oversized_payload = json.dumps({"data": "x" * 17_000}).encode("utf-8")
        oversized = {
            "PATH_INFO": "/api/scenario",
            "REQUEST_METHOD": "POST",
            "CONTENT_LENGTH": str(len(oversized_payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": BytesIO(oversized_payload),
        }
        captured = {}
        body = b"".join(vercel_backend.app(oversized, lambda status, headers: captured.update(status=status)))
        self.assertEqual(captured["status"], "413 Request Entity Too Large")
        self.assertEqual(json.loads(body)["error"]["code"], "request_too_large")

    def test_scenario_and_snapshot_routes_forward_the_existing_payloads(self):
        scenario = {"ticker": "AAPL", "score": 71}
        previous = {"ticker": "AAPL", "saved_at": "2026-01-01T00:00:00Z"}
        current = {"ticker": "AAPL", "saved_at": "2026-02-01T00:00:00Z"}
        with patch("vercel_backend.analyze_scenario", return_value=scenario) as analyze:
            response = self.request("/api/scenario", "POST", payload={"ticker": "AAPL", "assumptions": {"discount_rate": 0.1}})
        self.assertEqual(json.loads(response["body"]), {"data": scenario})
        analyze.assert_called_once_with("AAPL", {"discount_rate": 0.1})

        diff = {"comparable": True, "changes": []}
        with patch("vercel_backend.compare_snapshots", return_value=diff) as compare:
            response = self.request("/api/snapshot_diff", "POST", payload={"previous": previous, "current": current})
        self.assertEqual(json.loads(response["body"]), {"data": diff})
        compare.assert_called_once_with(previous, current)

    def test_market_routes_forward_existing_service_results(self):
        for path, function, expected in (
            ("/api/movers", "daily_market_movers", {"items": [{"ticker": "AAPL"}]}),
            ("/api/top_stocks", "daily_top_stocks", {"items": [{"ticker": "MSFT"}]}),
        ):
            with patch.object(vercel_backend, function, return_value=expected) as source:
                response = self.request(path)
            self.assertEqual(json.loads(response["body"]), {"data": expected})
            source.assert_called_once_with()

    def test_unknown_paths_and_methods_are_not_misrouted(self):
        missing = self.request("/api/not-a-route")
        method = self.request("/api/analyze", "POST", payload={"ticker": "AAPL"})
        self.assertEqual(missing["status"], "404 Not Found")
        self.assertEqual(method["status"], "405 Method Not Allowed")


if __name__ == "__main__":
    unittest.main()
