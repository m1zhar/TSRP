"""Exercise the development API through real HTTP without contacting providers."""

from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
import socket
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_api import Router
from tests.auth_fixtures import API_TEST_SECRET, test_api_token
import os
from unittest.mock import patch


class LocalApiHttpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET})
        cls.environment.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Router)
        cls.server.daemon_threads = True
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.environment.stop()

    def request(self, method, path, *, origin=None, authenticate=True):
        headers = {"Origin": origin} if origin else {}
        if method != "OPTIONS" and authenticate:
            headers["Authorization"] = f"Bearer {test_api_token()}"
        request = Request(f"{self.base_url}{path}", method=method, headers=headers)
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, response.headers, body

    def test_allowed_local_origin_reaches_handler_with_safe_validation_response(self):
        status, headers, body = self.request(
            "GET", "/api/analyze?ticker=%5EGSPC", origin="http://localhost:3000"
        )

        self.assertEqual(status, 400)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_ticker")

    def test_untrusted_origin_is_rejected_before_handler_dispatch(self):
        status, headers, body = self.request(
            "GET", "/api/analyze?ticker=%5EGSPC", origin="https://untrusted.example"
        )

        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(json.loads(body)["error"]["code"], "origin_not_allowed")

    def test_options_preflight_is_limited_to_the_local_web_origins(self):
        allowed_status, allowed_headers, _ = self.request(
            "OPTIONS", "/api/search", origin="http://127.0.0.1:3000"
        )
        denied_status, denied_headers, denied_body = self.request(
            "OPTIONS", "/api/search", origin="https://untrusted.example"
        )

        self.assertEqual(allowed_status, 204)
        self.assertEqual(allowed_headers["Access-Control-Allow-Origin"], "http://127.0.0.1:3000")
        self.assertEqual(allowed_headers["Access-Control-Allow-Methods"], "GET, POST, OPTIONS")
        self.assertIn("Authorization", allowed_headers["Access-Control-Allow-Headers"])
        self.assertEqual(denied_status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", denied_headers)
        self.assertEqual(json.loads(denied_body)["error"]["code"], "origin_not_allowed")

    def test_local_api_rejects_missing_bearer_access(self):
        status, headers, body = self.request("GET", "/api/analyze?ticker=AAPL", origin="http://localhost:3000", authenticate=False)
        self.assertEqual(status, 401)
        self.assertEqual(headers["WWW-Authenticate"], 'Bearer realm="tsrp-api"')
        self.assertEqual(json.loads(body)["error"]["code"], "authentication_required")

    def test_route_method_mismatch_keeps_allow_header_and_no_store(self):
        status, headers, body = self.request(
            "GET", "/api/search", origin="http://localhost:3000"
        )

        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(body)["error"]["code"], "method_not_allowed")

    def test_head_method_mismatch_has_no_response_body(self):
        status, headers, body = self.request("HEAD", "/api/analyze", origin="http://localhost:3000")

        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET")
        self.assertEqual(headers["Content-Length"], "0")
        self.assertEqual(body, b"")

    def test_stalled_local_post_body_returns_bounded_timeout(self):
        request = (
            "POST /api/search HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.server.server_port}\r\n"
            "Origin: http://localhost:3000\r\n"
            f"Authorization: Bearer {test_api_token()}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 100\r\n"
            "Connection: close\r\n\r\n"
        )
        with patch.object(Router, "timeout", 0.1):
            with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2) as client:
                client.settimeout(2)
                client.sendall(request.encode("ascii"))
                chunks = []
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)

        response = b"".join(chunks)
        self.assertIn(b" 408 ", response.split(b"\r\n", 1)[0])
        self.assertIn(b'"code":"request_timeout"', response)


if __name__ == "__main__":
    unittest.main()
