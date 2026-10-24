"""Exercise the deployed Vercel Services ASGI adapter at its HTTP boundary."""

import asyncio
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

import api.index as api_index
import research_service
from api.index import app
from api_helpers import LOGGER, MAX_BODY_BYTES, send_json
from tests.auth_fixtures import API_TEST_SECRET, test_api_token


class AsgiAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TSRP_API_TOKEN_SECRET": API_TEST_SECRET})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_provider_runner_skips_route_when_already_disconnected(self):
        cancel_event = threading.Event()
        cancel_event.set()
        calls = []

        with self.assertRaisesRegex(InterruptedError, "disconnected before provider work"):
            research_service.run_with_provider_cancellation(cancel_event, lambda: calls.append("called"))

        self.assertEqual(calls, [])

    async def test_unauthenticated_api_request_is_rejected_before_body_read(self):
        received = 0
        sent = []

        async def receive():
            nonlocal received
            received += 1
            return {"type": "http.request", "body": b'{"q":"private"}', "more_body": False}

        async def send(message):
            sent.append(message)

        await app({
            "type": "http", "method": "POST", "path": "/api/search", "query_string": b"",
            "headers": [(b"authorization", b""), (b"content-length", b"15")],
        }, receive, send)
        response = next(message for message in sent if message["type"] == "http.response.start")
        self.assertEqual(response["status"], 401)
        self.assertEqual(received, 0)

    async def test_unauthenticated_analysis_is_rejected_before_provider_call(self):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        with patch("api.analyze.analyze_company") as analyze:
            await app({
                "type": "http", "method": "GET", "path": "/api/analyze",
                "query_string": b"ticker=AAPL", "headers": [],
            }, receive, send)

        response = next(message for message in sent if message["type"] == "http.response.start")
        body = next(message["body"] for message in sent if message["type"] == "http.response.body")
        self.assertEqual(response["status"], 401)
        self.assertEqual(json.loads(body)["error"]["code"], "authentication_required")
        analyze.assert_not_called()

    async def test_synchronous_route_does_not_block_other_asgi_requests(self):
        entered = threading.Event()
        release = threading.Event()

        def blocking_route(request):
            entered.set()
            release.wait(timeout=2)
            send_json(request, 200, {"data": "done"})

        def quick_route(request):
            send_json(request, 200, {"data": "quick"})

        with patch.dict(api_index.ROUTES, {
            "/api/test-blocking": {"GET": blocking_route},
            "/api/test-quick": {"GET": quick_route},
        }):
            blocked = asyncio.create_task(self.request("/api/test-blocking"))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1))
                started = time.perf_counter()
                response, payload, _ = await asyncio.wait_for(self.request("/api/test-quick"), timeout=0.5)
                self.assertLess(time.perf_counter() - started, 0.5)
                self.assertEqual(response["status"], 200)
                self.assertEqual(json.loads(payload["body"]), {"data": "quick"})
            finally:
                release.set()
            self.assertEqual((await blocked)[0]["status"], 200)

    async def test_disconnect_stops_followup_provider_work_but_bounds_running_call(self):
        provider_started = threading.Event()
        provider_release = threading.Event()
        provider_finished = threading.Event()
        route_finished = threading.Event()
        second_provider_started = threading.Event()
        incoming = asyncio.Queue()
        sent = []

        def first_provider_call():
            provider_started.set()
            provider_release.wait(timeout=2)
            provider_finished.set()
            return "first"

        def second_provider_call():
            second_provider_started.set()
            return "second"

        def cancellable_route(request):
            research_service._bounded_provider_calls(
                {"first": first_provider_call}, timeout_seconds=2
            )
            research_service._bounded_provider_calls(
                {"second": second_provider_call}, timeout_seconds=2
            )
            route_finished.set()
            send_json(request, 200, {"data": "completed"})

        async def receive():
            return await incoming.get()

        async def send(message):
            sent.append(message)

        with patch.dict(api_index.ROUTES, {"/api/test-cancel": {"GET": cancellable_route}}):
            route = asyncio.create_task(app({
                "type": "http", "method": "GET", "path": "/api/test-cancel",
                "query_string": b"", "headers": [(b"authorization", f"Bearer {test_api_token()}".encode())],
            }, receive, send))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(provider_started.wait), timeout=1))
                await incoming.put({"type": "http.disconnect"})
                await asyncio.wait_for(route, timeout=0.5)
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(route_finished.wait), timeout=0.5))
                self.assertFalse(second_provider_started.is_set())
                self.assertFalse(provider_finished.is_set(), "an in-flight synchronous transfer cannot be forcibly stopped")
                self.assertEqual(sent, [], "a disconnected client must not receive a synthetic response")
            finally:
                provider_release.set()
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(provider_finished.wait), timeout=1))

            recovered_provider_started = threading.Event()

            def recovered_route(request):
                research_service._bounded_provider_calls(
                    {"recovered": recovered_provider_started.set}, timeout_seconds=1
                )
                send_json(request, 200, {"data": "recovered"})

            with patch.dict(api_index.ROUTES, {"/api/test-recovered": {"GET": recovered_route}}):
                response, payload, _ = await self.request("/api/test-recovered")
            self.assertEqual(response["status"], 200)
            self.assertEqual(json.loads(payload["body"]), {"data": "recovered"})
            self.assertTrue(recovered_provider_started.is_set(), "cancellation state must not leak into later requests")

    async def test_disconnect_of_analysis_owner_does_not_cancel_connected_follower(self):
        from concurrent.futures import Future
        from analysis_coalescer import coalesce_analysis

        provider_started = threading.Event()
        provider_release = threading.Event()
        follower_waiting = threading.Event()

        class TrackingFuture(Future):
            def result(self, timeout=None):
                follower_waiting.set()
                return super().result(timeout)

        def provider_call():
            provider_started.set()
            if not provider_release.wait(timeout=2):
                raise TimeoutError("test provider was not released")
            return {"quoteType": "EQUITY"}

        def calculate(ticker):
            results, _unavailable = research_service._bounded_provider_calls(
                {"info": provider_call}, timeout_seconds=1
            )
            if not results.get("info"):
                raise RuntimeError("shared provider request was cancelled")
            return {"company": {"ticker": ticker}}

        def route(request):
            try:
                result = coalesce_analysis(
                    "TSRP-CANCEL-TEST",
                    calculate,
                    cancel_event=request.cancel_event,
                    run_with_cancellation=research_service.run_with_provider_cancellation,
                )
                send_json(request, 200, {"data": result})
            except Exception as exc:
                from api_helpers import safe_error
                safe_error(request, exc)

        owner_incoming = asyncio.Queue()
        owner_sent = []

        async def owner_receive():
            return await owner_incoming.get()

        async def owner_send(message):
            owner_sent.append(message)

        async def follower_receive():
            await asyncio.Event().wait()

        follower_sent = []

        async def follower_send(message):
            follower_sent.append(message)

        headers = [(b"authorization", f"Bearer {test_api_token()}".encode())]
        owner_scope = {
            "type": "http", "method": "GET", "path": "/api/analyze",
            "query_string": b"", "headers": headers,
        }
        follower_scope = {**owner_scope, "headers": list(headers)}
        with patch.dict(api_index.ROUTES, {"/api/analyze": {"GET": route}}), \
             patch("analysis_coalescer.Future", TrackingFuture):
            owner = asyncio.create_task(app(owner_scope, owner_receive, owner_send))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(provider_started.wait), timeout=1))
                follower = asyncio.create_task(app(follower_scope, follower_receive, follower_send))
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(follower_waiting.wait), timeout=1))
                await owner_incoming.put({"type": "http.disconnect"})
                await asyncio.wait_for(owner, timeout=0.5)
                await asyncio.sleep(0.1)
                provider_release.set()
                await asyncio.wait_for(follower, timeout=1)
            finally:
                provider_release.set()

        response = next(message for message in follower_sent if message["type"] == "http.response.start")
        payload = next(message for message in follower_sent if message["type"] == "http.response.body")
        self.assertEqual(response["status"], 200)
        self.assertEqual(json.loads(payload["body"]), {"data": {"company": {"ticker": "TSRP-CANCEL-TEST"}}})
        self.assertEqual(owner_sent, [], "a disconnected owner must not receive a synthetic response")

    async def test_disconnect_of_follower_does_not_cancel_connected_analysis_owner(self):
        from concurrent.futures import Future
        from analysis_coalescer import coalesce_analysis

        provider_started = threading.Event()
        provider_release = threading.Event()
        provider_finished = threading.Event()
        follower_waiting = threading.Event()

        class TrackingFuture(Future):
            def result(self, timeout=None):
                follower_waiting.set()
                return super().result(timeout)

        def provider_call():
            provider_started.set()
            if not provider_release.wait(timeout=2):
                raise TimeoutError("test provider was not released")
            provider_finished.set()
            return {"quoteType": "EQUITY"}

        def calculate(ticker):
            results, _unavailable = research_service._bounded_provider_calls(
                {"info": provider_call}, timeout_seconds=1
            )
            if not results.get("info"):
                raise RuntimeError("shared provider request was cancelled")
            return {"company": {"ticker": ticker}}

        def route(request):
            try:
                result = coalesce_analysis(
                    "TSRP-CANCEL-TEST-FOLLOWER",
                    calculate,
                    cancel_event=request.cancel_event,
                    run_with_cancellation=research_service.run_with_provider_cancellation,
                )
                send_json(request, 200, {"data": result})
            except Exception as exc:
                from api_helpers import safe_error
                safe_error(request, exc)

        owner_sent = []
        follower_sent = []
        follower_incoming = asyncio.Queue()

        async def owner_receive():
            await asyncio.Event().wait()

        async def owner_send(message):
            owner_sent.append(message)

        async def follower_receive():
            return await follower_incoming.get()

        async def follower_send(message):
            follower_sent.append(message)

        headers = [(b"authorization", f"Bearer {test_api_token()}".encode())]
        owner_scope = {
            "type": "http", "method": "GET", "path": "/api/analyze",
            "query_string": b"", "headers": headers,
        }
        follower_scope = {**owner_scope, "headers": list(headers)}
        with patch.dict(api_index.ROUTES, {"/api/analyze": {"GET": route}}), \
             patch("analysis_coalescer.Future", TrackingFuture):
            owner = asyncio.create_task(app(owner_scope, owner_receive, owner_send))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(provider_started.wait), timeout=1))
                follower = asyncio.create_task(app(follower_scope, follower_receive, follower_send))
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(follower_waiting.wait), timeout=1))
                await follower_incoming.put({"type": "http.disconnect"})
                await asyncio.wait_for(follower, timeout=0.5)
                await asyncio.sleep(0.1)
                self.assertFalse(provider_finished.is_set(), "the connected owner still depends on the shared provider call")
                provider_release.set()
                await asyncio.wait_for(owner, timeout=1)
            finally:
                provider_release.set()

        response = next(message for message in owner_sent if message["type"] == "http.response.start")
        payload = next(message for message in owner_sent if message["type"] == "http.response.body")
        self.assertEqual(response["status"], 200)
        self.assertEqual(json.loads(payload["body"]), {"data": {"company": {"ticker": "TSRP-CANCEL-TEST-FOLLOWER"}}})
        self.assertEqual(follower_sent, [], "a disconnected follower must not receive a synthetic response")

    async def test_asgi_request_log_duration_includes_synchronous_handler_time(self):
        def delayed_route(request):
            time.sleep(0.04)
            send_json(request, 200, {"data": "done"})

        with patch.dict(api_index.ROUTES, {"/api/test-delayed": {"GET": delayed_route}}), \
             patch.object(LOGGER, "info") as log_info:
            response, _, _ = await self.request("/api/test-delayed")

        self.assertEqual(response["status"], 200)
        logged = json.loads(log_info.call_args.args[1])
        self.assertGreaterEqual(logged["duration_ms"], 30)

    async def test_every_provider_route_authenticates_before_receiving_body(self):
        routes = (
            ("GET", "/api/analyze"),
            ("GET", "/api/movers"),
            ("GET", "/api/top_stocks"),
            ("POST", "/api/search"),
            ("POST", "/api/scenario"),
            ("POST", "/api/snapshot_diff"),
        )
        for method, path in routes:
            with self.subTest(route=path):
                received = 0
                sent = []

                async def receive():
                    nonlocal received
                    received += 1
                    return {"type": "http.request", "body": b"{}", "more_body": False}

                async def send(message):
                    sent.append(message)

                headers = [(b"authorization", b"")]
                if method == "POST":
                    headers.extend([
                        (b"content-type", b"application/json"),
                        (b"content-length", b"2"),
                    ])
                await app({
                    "type": "http", "method": method, "path": path,
                    "query_string": b"ticker=AAPL" if path == "/api/analyze" else b"",
                    "headers": headers,
                }, receive, send)

                response = next(message for message in sent if message["type"] == "http.response.start")
                self.assertEqual(response["status"], 401)
                self.assertEqual(received, 0)

    async def test_duplicate_authorization_headers_are_rejected_before_body_read(self):
        body = b'{"q":"Apple"}'
        valid = f"Bearer {test_api_token()}".encode()
        with patch("api.search.search_companies") as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"authorization", valid),
                    (b"authorization", valid),
                    (b"content-length", str(len(body)).encode()),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 400)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        self.assertEqual(len(incoming), 1, "ambiguous credentials are rejected without reading the request body")
        search.assert_not_called()

    async def test_conflicting_content_type_headers_are_rejected_before_body_read(self):
        body = b'{"q":"Apple"}'
        with patch("api.search.search_companies") as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"text/plain"),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 400)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        self.assertEqual(len(incoming), 1, "ambiguous content types are rejected without reading the request body")
        search.assert_not_called()

    async def test_transfer_encoding_with_content_length_is_rejected_before_body_read(self):
        body = b'{"q":"Apple"}'
        with patch("api.search.search_companies") as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"transfer-encoding", b"chunked"),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 400)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        self.assertEqual(len(incoming), 1, "ambiguous request framing must be rejected without reading the body")
        search.assert_not_called()

    async def test_duplicate_transfer_encoding_headers_are_rejected_before_body_read(self):
        body = b'{"q":"Apple"}'
        with patch("api.search.search_companies", return_value=[]) as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"transfer-encoding", b"chunked"),
                    (b"transfer-encoding", b"chunked"),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 400)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        self.assertEqual(len(incoming), 1, "duplicate transfer codings must be rejected without reading the body")
        search.assert_not_called()

    async def test_combined_transfer_encoding_values_are_rejected_before_body_read(self):
        body = b'{"q":"Apple"}'
        for transfer_encoding in (b"chunked, chunked", b"gzip, chunked"):
            with self.subTest(transfer_encoding=transfer_encoding):
                with patch("api.search.search_companies", return_value=[]) as search:
                    start, payload, incoming = await self.request(
                        "/api/search",
                        method="POST",
                        headers=[
                            (b"content-type", b"application/json"),
                            (b"transfer-encoding", transfer_encoding),
                        ],
                        body=body,
                    )

                self.assertEqual(start["status"], 400)
                self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
                self.assertEqual(len(incoming), 1, "unsupported or repeated transfer codings must not consume the body")
                search.assert_not_called()

    async def test_single_chunked_transfer_encoding_is_accepted(self):
        body = b'{"q":"Apple"}'
        with patch("api.search.search_companies", return_value=[]) as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"transfer-encoding", b" Chunked "),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 200)
        self.assertEqual(json.loads(payload["body"]), {"data": []})
        self.assertEqual(incoming, [])
        search.assert_called_once_with("Apple")

    async def test_unsupported_content_type_is_rejected_before_body_read(self):
        body = b'{"q":"Apple"}'
        with patch("api.search.search_companies") as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(body)).encode()),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 415)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        self.assertEqual(len(incoming), 1, "unsupported media types are rejected without reading the request body")
        search.assert_not_called()

    async def test_missing_content_type_is_rejected_before_body_read(self):
        body = b'{"q":"Apple"}'
        with patch("api.search.search_companies") as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[(b"content-length", str(len(body)).encode())],
                body=body,
            )

        self.assertEqual(start["status"], 415)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        self.assertEqual(len(incoming), 1, "missing media types are rejected without reading the request body")
        search.assert_not_called()

    async def request(self, path, *, method="GET", query=b"", headers=(), body=b""):
        request_headers = list(headers)
        if not any(name.lower() == b"authorization" for name, _ in request_headers):
            request_headers.append((b"authorization", f"Bearer {test_api_token()}".encode()))
        incoming = [{"type": "http.request", "body": body, "more_body": False}]
        sent = []
        disconnected = asyncio.Event()

        async def receive():
            if incoming:
                return incoming.pop(0)
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": method,
                "path": path,
                "query_string": query,
                "headers": request_headers,
            },
            receive,
            send,
        )
        start = next((message for message in sent if message["type"] == "http.response.start"), None)
        payload = next((message for message in sent if message["type"] == "http.response.body"), None)
        return start, payload, incoming

    async def test_post_search_reaches_the_existing_json_handler(self):
        body = json.dumps({"q": "Apple"}).encode()
        with patch(
            "api.search.search_companies",
            return_value=[{"ticker": "AAPL", "company": "Apple Inc."}],
        ) as search:
            start, payload, _ = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 200)
        response_headers = dict(start["headers"])
        self.assertEqual(response_headers[b"cache-control"], b"no-store")
        self.assertEqual(json.loads(payload["body"]), {"data": [{"ticker": "AAPL", "company": "Apple Inc."}]})
        search.assert_called_once_with("Apple")

    async def test_post_search_accepts_asgi_body_without_content_length(self):
        body = json.dumps({"q": "Apple"}).encode()
        with patch(
            "api.search.search_companies",
            return_value=[{"ticker": "AAPL", "company": "Apple Inc."}],
        ) as search:
            start, payload, _ = await self.request(
                "/api/search",
                method="POST",
                headers=[(b"content-type", b"application/json")],
                body=body,
            )

        self.assertEqual(start["status"], 200)
        self.assertEqual(json.loads(payload["body"]), {"data": [{"ticker": "AAPL", "company": "Apple Inc."}]})
        search.assert_called_once_with("Apple")

    async def test_post_search_rejects_bytes_beyond_declared_content_length(self):
        body = json.dumps({"q": "Apple"}).encode()
        with patch("api.search.search_companies", return_value=[]) as search:
            start, payload, _ = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
                body=body + b" ",
            )

        self.assertEqual(start["status"], 400)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        search.assert_not_called()

    async def test_post_rejects_duplicate_content_length_headers(self):
        body = json.dumps({"q": "Apple"}).encode()
        length = str(len(body)).encode()
        with patch("api.search.search_companies", return_value=[]) as search:
            start, payload, _ = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"content-length", length),
                    (b"content-length", length),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 400)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
        search.assert_not_called()

    async def test_post_rejects_non_decimal_content_length_before_reading_body(self):
        body = b'{"q":"Apple"}'
        malformed_lengths = (b"+12", b" 12", b"12 ", b"1_2", b"\xff")
        with patch("api.search.search_companies") as search:
            for raw_length in malformed_lengths:
                with self.subTest(content_length=raw_length):
                    start, payload, incoming = await self.request(
                        "/api/search",
                        method="POST",
                        headers=[
                            (b"content-type", b"application/json"),
                            (b"content-length", raw_length),
                        ],
                        body=body,
                    )

                    self.assertEqual(start["status"], 400)
                    self.assertEqual(json.loads(payload["body"])["error"]["code"], "invalid_request")
                    self.assertEqual(len(incoming), 1, "malformed length must be rejected before consuming body bytes")
            search.assert_not_called()

    async def test_post_normalizes_valid_zero_padded_content_length(self):
        body = b'{"q":"Apple"}'
        padded_length = b"000" + str(len(body)).encode()
        with patch("api.search.search_companies", return_value=[]) as search:
            start, payload, _ = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"content-length", padded_length),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 200)
        self.assertEqual(json.loads(payload["body"]), {"data": []})
        search.assert_called_once_with("Apple")

    async def test_enormous_decimal_content_length_is_bounded_and_rejected_as_too_large(self):
        body = b'{"q":"Apple"}'
        raw_length = b"9" * 5000
        with patch("api.search.search_companies") as search:
            start, payload, incoming = await self.request(
                "/api/search",
                method="POST",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"content-length", raw_length),
                ],
                body=body,
            )

        self.assertEqual(start["status"], 413)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "request_too_large")
        self.assertEqual(len(incoming), 1, "oversized declared lengths must be rejected before consuming body bytes")
        search.assert_not_called()

    async def test_stalled_post_body_returns_bounded_json_timeout(self):
        sent = []
        receive_calls = 0

        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        with patch("api.index.REQUEST_BODY_TIMEOUT_SECONDS", 0.01, create=True):
            await asyncio.wait_for(
                app(
                    {
                        "type": "http",
                        "method": "POST",
                        "path": "/api/scenario",
                        "query_string": b"",
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", b"2"),
                            (b"authorization", f"Bearer {test_api_token()}".encode()),
                        ],
                    },
                    receive,
                    send,
                ),
                timeout=0.2,
            )

        start = next(message for message in sent if message["type"] == "http.response.start")
        payload = next(message for message in sent if message["type"] == "http.response.body")
        response_headers = dict(start["headers"])
        self.assertEqual(receive_calls, 1)
        self.assertEqual(start["status"], 408)
        self.assertEqual(response_headers[b"content-type"], b"application/json; charset=utf-8")
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "request_timeout")

    async def test_get_analysis_preserves_query_and_response_contract(self):
        result = {"company": {"ticker": "AAPL"}, "price_history": []}
        with patch("api.analyze.analyze_company", return_value=result) as analyze:
            start, payload, _ = await self.request("/api/analyze", query=b"ticker=AAPL")

        analyze.assert_called_once_with("AAPL")
        self.assertEqual(start["status"], 200)
        self.assertEqual(json.loads(payload["body"])["data"]["company"]["ticker"], "AAPL")
        self.assertIn((b"cache-control", b"private, no-store"), start["headers"])

    async def test_unknown_route_and_disallowed_method_have_explicit_status(self):
        not_found, not_found_body, _ = await self.request("/api/not-a-route")
        method_not_allowed, method_not_allowed_body, _ = await self.request("/api/analyze", method="POST")
        search_wrong_method, search_wrong_method_body, _ = await self.request("/api/search", method="GET")
        head_response, head_body, _ = await self.request("/api/analyze", method="HEAD")

        self.assertEqual(not_found["status"], 404)
        not_found_headers = dict(not_found["headers"])
        self.assertEqual(not_found_headers[b"content-type"], b"application/json; charset=utf-8")
        self.assertEqual(not_found_headers[b"cache-control"], b"no-store")
        self.assertIn(b"x-content-type-options", not_found_headers)
        self.assertEqual(json.loads(not_found_body["body"])["error"]["code"], "not_found")
        self.assertEqual(method_not_allowed["status"], 405)
        method_headers = dict(method_not_allowed["headers"])
        self.assertEqual(method_headers[b"allow"], b"GET")
        self.assertEqual(method_headers[b"content-type"], b"application/json; charset=utf-8")
        self.assertEqual(json.loads(method_not_allowed_body["body"])["error"]["code"], "method_not_allowed")
        self.assertEqual(search_wrong_method["status"], 405)
        search_method_headers = dict(search_wrong_method["headers"])
        self.assertEqual(search_method_headers[b"allow"], b"POST")
        self.assertEqual(json.loads(search_wrong_method_body["body"])["error"]["code"], "method_not_allowed")
        self.assertEqual(head_response["status"], 405)
        self.assertEqual(dict(head_response["headers"])[b"content-length"], b"0")
        self.assertEqual(head_body["body"], b"")

    async def test_unknown_route_rejects_post_before_reading_body(self):
        body = b'{"private":"value"}'
        start, payload, incoming = await self.request(
            "/api/not-a-route",
            method="POST",
            headers=[
                (b"authorization", b""),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            body=body,
        )

        self.assertEqual(start["status"], 404)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "not_found")
        self.assertEqual(len(incoming), 1, "unknown routes must not consume request bodies")

    async def test_disallowed_method_rejects_post_before_reading_body(self):
        body = b'{"private":"value"}'
        start, payload, incoming = await self.request(
            "/api/analyze",
            method="POST",
            headers=[
                (b"authorization", b""),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            body=body,
        )

        self.assertEqual(start["status"], 405)
        self.assertEqual(dict(start["headers"])[b"allow"], b"GET")
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "method_not_allowed")
        self.assertEqual(len(incoming), 1, "unsupported methods must not consume request bodies")

    async def test_declared_oversized_body_is_rejected_before_reading(self):
        incoming = [{"type": "http.request", "body": b"", "more_body": False}]
        sent = []
        receive_calls = 0

        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            return incoming.pop(0)

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/scenario",
                "query_string": b"",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(MAX_BODY_BYTES + 1).encode()),
                    (b"authorization", f"Bearer {test_api_token()}".encode()),
                ],
            },
            receive,
            send,
        )

        start = next(message for message in sent if message["type"] == "http.response.start")
        payload = next(message for message in sent if message["type"] == "http.response.body")
        self.assertEqual(receive_calls, 0)
        self.assertEqual(start["status"], 413)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "request_too_large")

    async def test_underreported_body_is_bounded_and_rejected(self):
        body = b"{" + (b" " * MAX_BODY_BYTES)
        start, payload, _ = await self.request(
            "/api/scenario",
            method="POST",
            headers=[
                (b"content-type", b"application/json"),
                (b"content-length", b"1"),
            ],
            body=body,
        )

        self.assertEqual(start["status"], 413)
        self.assertEqual(json.loads(payload["body"])["error"]["code"], "request_too_large")


if __name__ == "__main__":
    unittest.main()
