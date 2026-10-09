import asyncio
import json
from io import BytesIO
import unittest
from unittest.mock import patch

from api.analyze import handler as AnalyzeHandler
from api.index import ROUTES, app as VercelApp, handler as ApiEntryHandler
from api.scenario import handler as ScenarioHandler


class Headers(dict):
    def get_content_type(self):
        return self.get("Content-Type", "").split(";", 1)[0].strip().lower()


class RequestRecorder:
    def __init__(self, path="/", body=b"", headers=None, command="GET"):
        self.path = path
        self.command = command
        self.headers = Headers(headers or {})
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

    def send_error(self, status):
        self.status = status

    def payload(self):
        return json.loads(self.wfile.getvalue())


class ApiRouteTests(unittest.TestCase):
    def run_asgi(self, path, *, method="GET", body=b"", headers=None):
        incoming = [{"type": "http.request", "body": body, "more_body": False}]
        outgoing = []

        async def receive():
            return incoming.pop(0)

        async def send(message):
            outgoing.append(message)

        scope = {
            "type": "http",
            "method": method,
            "path": path.split("?", 1)[0],
            "query_string": path.partition("?")[2].encode(),
            "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
        }
        asyncio.run(VercelApp(scope, receive, send))
        return outgoing

    def test_vercel_entrypoint_exposes_each_api_route(self):
        self.assertEqual(set(ROUTES), {
            "/api/analyze", "/api/movers", "/api/top_stocks",
            "/api/scenario", "/api/search", "/api/snapshot_diff",
        })

    def test_vercel_entrypoint_dispatches_query_requests(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        calls = []
        with patch.dict(ROUTES, {"/api/analyze": {"GET": lambda received: calls.append(received)}}):
            ApiEntryHandler._dispatch(request)
        self.assertEqual(calls, [request])

    def test_vercel_asgi_entrypoint_dispatches_api_requests(self):
        with patch("api.analyze.analyze_company", return_value={"company": {"ticker": "AAPL"}}):
            outgoing = self.run_asgi("/api/analyze?ticker=AAPL")
        self.assertEqual(outgoing[0]["status"], 200)
        self.assertEqual(json.loads(outgoing[1]["body"])["data"]["company"]["ticker"], "AAPL")

    def test_vercel_asgi_entrypoint_enforces_existing_body_limit(self):
        oversized = b"x" * (16_384 + 1)
        outgoing = self.run_asgi(
            "/api/scenario", method="POST", body=oversized,
            headers={"Content-Type": "application/json", "Content-Length": str(len(oversized))},
        )
        self.assertEqual(outgoing[0]["status"], 413)
        self.assertEqual(json.loads(outgoing[1]["body"])["error"]["code"], "request_too_large")

    def test_vercel_entrypoint_rejects_unknown_routes_and_methods(self):
        unknown = RequestRecorder("/api/not-real")
        ApiEntryHandler._dispatch(unknown)
        self.assertEqual(unknown.status, 404)

        unsupported = RequestRecorder("/api/analyze", command="POST")
        ApiEntryHandler._dispatch(unsupported)
        self.assertEqual(unsupported.status, 405)
        self.assertEqual(unsupported.response_headers["Allow"], "GET")

    def test_analyze_returns_safe_validation_error(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL%2F..%2Fsecret")
        AnalyzeHandler.do_GET(request)
        self.assertEqual(request.status, 400)
        self.assertEqual(request.payload()["error"]["code"], "invalid_ticker")
        self.assertNotIn("secret", request.wfile.getvalue().decode())

    def test_analyze_wraps_a_successful_result(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch("api.analyze.analyze_company", return_value={"company": {"ticker": "AAPL"}, "price_history": []}):
            AnalyzeHandler.do_GET(request)
        self.assertEqual(request.status, 200)
        self.assertEqual(request.payload()["data"]["company"]["ticker"], "AAPL")
        self.assertIn("s-maxage=300", request.response_headers["Cache-Control"])

    def test_analyze_masks_provider_exception_text(self):
        request = RequestRecorder("/api/analyze?ticker=AAPL")
        with patch("api.analyze.analyze_company", side_effect=RuntimeError("provider token private-value")):
            AnalyzeHandler.do_GET(request)
        response = request.wfile.getvalue().decode()
        self.assertEqual(request.status, 503)
        self.assertNotIn("private-value", response)
        self.assertEqual(request.payload()["error"]["code"], "service_unavailable")

    def test_scenario_rejects_oversized_body_before_parsing(self):
        request = RequestRecorder(
            "/api/scenario", headers={"Content-Length": "20000", "Content-Type": "application/json"}
        )
        ScenarioHandler.do_POST(request)
        self.assertEqual(request.status, 413)
        self.assertEqual(request.payload()["error"]["code"], "request_too_large")


if __name__ == "__main__":
    unittest.main()
