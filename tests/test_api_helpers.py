import json
import threading
import unittest
from io import BytesIO

import numpy as np
import pandas as pd

from api_helpers import _json_default, safe_error
from research_service import ResearchError


class ResponseRecorder:
    def __init__(self):
        self.path = "/api/analyze?ticker=AAPL"
        self.command = "GET"
        self.wfile = BytesIO()
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        return None


class DisconnectedStream:
    def write(self, _body):
        raise BrokenPipeError("client disconnected secret-value")


class TimedOutStream:
    def write(self, _body):
        raise TimeoutError("client write deadline secret-value")


class ApiJsonSerializationTests(unittest.TestCase):
    def test_pandas_timestamp_serializes_as_iso_8601(self):
        stamp = pd.Timestamp("2026-10-07T12:30:00Z")
        result = json.dumps({"observed_at": stamp}, default=_json_default, allow_nan=False)
        self.assertEqual(json.loads(result)["observed_at"], "2026-10-07T12:30:00+00:00")

    def test_numpy_scalar_serializes_as_native_value(self):
        result = json.dumps({"close": np.float64(123.45)}, default=_json_default, allow_nan=False)
        self.assertEqual(json.loads(result)["close"], 123.45)


class ApiErrorPrivacyTests(unittest.TestCase):
    def test_disconnected_asgi_request_is_not_buffered_or_logged_as_a_response(self):
        request = ResponseRecorder()
        request.cancel_event = threading.Event()
        request.cancel_event.set()

        with self.assertNoLogs("tsrp", level="INFO"):
            safe_error(request, ResearchError("invalid_request", "request is already disconnected"))

        self.assertFalse(hasattr(request, "status"))
        self.assertEqual(request.response_headers, {})
        self.assertEqual(request.wfile.getvalue(), b"")

    def test_safe_error_logs_category_without_user_or_provider_message(self):
        request = ResponseRecorder()
        with self.assertLogs("tsrp", level="INFO") as captured:
            safe_error(request, ResearchError("invalid_request", "private query example@invalid.test"))
        record = json.loads(captured.output[0].split("api_request ", 1)[1])
        self.assertEqual(request.status, 400)
        self.assertEqual(record["error_code"], "invalid_request")
        self.assertEqual(request.response_headers["X-Request-ID"], record["request_id"])
        self.assertEqual(request.response_headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(request.response_headers["X-Frame-Options"], "DENY")
        self.assertEqual(request.response_headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(request.response_headers["Permissions-Policy"], "camera=(), microphone=(), geolocation=()")
        self.assertNotIn("private query", captured.output[0])
        self.assertNotIn("example@invalid.test", captured.output[0])
        self.assertNotIn("ticker=AAPL", captured.output[0])

    def test_unexpected_error_does_not_log_exception_text(self):
        request = ResponseRecorder()
        with self.assertLogs("tsrp", level="INFO") as captured:
            safe_error(request, ValueError("provider token secret-value"))
        record = json.loads(captured.output[0].split("api_request ", 1)[1])
        self.assertEqual(request.status, 503)
        self.assertEqual(record["error_code"], "service_unavailable")
        self.assertEqual(record["error_type"], "ValueError")
        self.assertNotIn("secret-value", captured.output[0])
        self.assertNotIn("provider token", captured.output[0])
        response = json.loads(request.wfile.getvalue())
        self.assertNotIn("secret-value", response["error"]["message"])

    def test_disconnected_client_does_not_escape_the_response_boundary(self):
        request = ResponseRecorder()
        request.wfile = DisconnectedStream()

        with self.assertLogs("tsrp", level="INFO") as captured:
            safe_error(request, ResearchError("invalid_request", "Invalid request."))

        record = json.loads(captured.output[0].split("api_request ", 1)[1])
        self.assertEqual(record["outcome"], "client_disconnected")
        self.assertEqual(record["error_code"], "invalid_request")
        self.assertEqual(request.response_headers["X-Request-ID"], record["request_id"])
        self.assertNotIn("secret-value", captured.output[0])

    def test_client_write_timeout_does_not_escape_the_response_boundary(self):
        request = ResponseRecorder()
        request.wfile = TimedOutStream()

        with self.assertLogs("tsrp", level="INFO") as captured:
            safe_error(request, ResearchError("invalid_request", "Invalid request."))

        record = json.loads(captured.output[0].split("api_request ", 1)[1])
        self.assertEqual(record["outcome"], "client_disconnected")
        self.assertEqual(record["error_code"], "invalid_request")
        self.assertEqual(request.response_headers["X-Request-ID"], record["request_id"])
        self.assertNotIn("secret-value", captured.output[0])


if __name__ == "__main__":
    unittest.main()
