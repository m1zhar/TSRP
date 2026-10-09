import json
from io import BytesIO
import unittest
from unittest.mock import patch

from api.analyze import handler as AnalyzeHandler
from api.scenario import handler as ScenarioHandler


class Headers(dict):
    def get_content_type(self):
        return self.get("Content-Type", "").split(";", 1)[0].strip().lower()


class RequestRecorder:
    def __init__(self, path="/", body=b"", headers=None):
        self.path = path
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

    def payload(self):
        return json.loads(self.wfile.getvalue())


class ApiRouteTests(unittest.TestCase):
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
