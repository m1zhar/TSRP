"""One Vercel Python entrypoint dispatching the existing typed API handlers."""

import asyncio
import time
from threading import Event
from io import BytesIO
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from api_helpers import MAX_BODY_BYTES, method_not_allowed, send_json
from api_auth import require_api_access
from research_service import run_with_provider_cancellation
from api.analyze import handler as AnalyzeHandler
from api.movers import handler as MoversHandler
from api.top_stocks import handler as TopStocksHandler
from api.scenario import handler as ScenarioHandler
from api.search import handler as SearchHandler
from api.snapshot_diff import handler as SnapshotDiffHandler

REQUEST_BODY_TIMEOUT_SECONDS = 30.0


ROUTES = {
    "/api/analyze": {"GET": AnalyzeHandler.do_GET},
    "/api/movers": {"GET": MoversHandler.do_GET},
    "/api/top_stocks": {"GET": TopStocksHandler.do_GET},
    "/api/scenario": {"POST": ScenarioHandler.do_POST},
    "/api/search": {"POST": SearchHandler.do_POST},
    "/api/snapshot_diff": {"POST": SnapshotDiffHandler.do_POST},
}


class handler(BaseHTTPRequestHandler):
    def _dispatch(self):
        methods = ROUTES.get(urlparse(self.path).path)
        if methods is None:
            send_json(
                self,
                404,
                {"error": {"code": "not_found", "message": "This API route does not exist."}},
            )
            return
        callback = methods.get(self.command)
        if callback is None:
            method_not_allowed(self, allow=", ".join(methods))
            return
        callback(self)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def log_message(self, format, *args):
        return


class _Headers(dict):
    def get_content_type(self):
        return self.get("Content-Type", "").split(";", 1)[0].strip().lower()


class _Request:
    """Bridge ASGI requests to the existing BaseHTTPRequestHandler routes."""

    def __init__(self, scope, body, *, started_at=None, cancel_event=None):
        headers = {
            key.decode("latin-1").title(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        self.path = scope.get("path", "/")
        query = scope.get("query_string", b"").decode("latin-1")
        if query:
            self.path = f"{self.path}?{query}"
        self.command = scope.get("method", "GET").upper()
        self.headers = _Headers(headers)
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.status = None
        self.response_headers = {}
        self.cancel_event = cancel_event if cancel_event is not None else Event()
        self._tsrp_started_at = started_at if started_at is not None else time.perf_counter()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        return None

    def send_error(self, status):
        self.status = status
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.wfile.write(f"{status} {HTTPStatus(status).phrase}".encode())


async def app(scope, receive, send):
    """ASGI entrypoint used by Vercel Services; API logic stays in route handlers."""
    if scope.get("type") != "http":
        return
    started_at = time.perf_counter()

    raw_headers = scope.get("headers", [])
    route_path = urlparse(scope.get("path", "/")).path
    route_methods = ROUTES.get(route_path, {})
    method = scope.get("method", "GET").upper()
    if not route_methods:
        request = _Request(scope, b"", started_at=started_at)
        send_json(
            request,
            404,
            {"error": {"code": "not_found", "message": "This API route does not exist."}},
        )
        await send({
            "type": "http.response.start",
            "status": request.status,
            "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in request.response_headers.items()],
        })
        await send({"type": "http.response.body", "body": request.wfile.getvalue(), "more_body": False})
        return
    if method not in route_methods:
        request = _Request(scope, b"", started_at=started_at)
        method_not_allowed(request, allow=", ".join(route_methods))
        await send({
            "type": "http.response.start",
            "status": request.status,
            "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in request.response_headers.items()],
        })
        await send({"type": "http.response.body", "body": request.wfile.getvalue(), "more_body": False})
        return
    ambiguous_singleton_header = any(
        sum(key.lower() == name for key, _ in raw_headers) > 1
        for name in (b"authorization", b"content-type", b"transfer-encoding")
    )
    if ambiguous_singleton_header and method in route_methods:
        request = _Request(scope, b"", started_at=started_at)
        send_json(
            request,
            400,
            {"error": {"code": "invalid_request", "message": "The request could not be read."}},
        )
        await send({
            "type": "http.response.start",
            "status": request.status,
            "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in request.response_headers.items()],
        })
        await send({"type": "http.response.body", "body": request.wfile.getvalue(), "more_body": False})
        return

    headers = {
        key.decode("latin-1").title(): value.decode("latin-1")
        for key, value in raw_headers
    }
    if method in route_methods:
        auth_request = _Request(scope, b"", started_at=started_at)
        auth_request.headers.update(headers)
        if not require_api_access(auth_request):
            await send({
                "type": "http.response.start",
                "status": auth_request.status or 401,
                "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in auth_request.response_headers.items()],
            })
            await send({"type": "http.response.body", "body": auth_request.wfile.getvalue(), "more_body": False})
            return
    if method == "POST" and method in route_methods:
        content_types = [value.decode("latin-1") for key, value in raw_headers if key.lower() == b"content-type"]
        media_type = content_types[0].split(";", 1)[0].strip().lower() if content_types else ""
        if media_type != "application/json":
            request = _Request(scope, b"", started_at=started_at)
            send_json(
                request,
                415,
                {"error": {"code": "invalid_request", "message": "Send this request as JSON."}},
            )
            await send({
                "type": "http.response.start",
                "status": request.status,
                "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in request.response_headers.items()],
            })
            await send({"type": "http.response.body", "body": request.wfile.getvalue(), "more_body": False})
            return
    duplicate_content_length = sum(key.lower() == b"content-length" for key, _ in raw_headers) > 1
    transfer_encoding_values = [
        value.strip().lower()
        for key, value in raw_headers
        if key.lower() == b"transfer-encoding"
    ]
    has_transfer_encoding = bool(transfer_encoding_values)
    unsupported_transfer_encoding = has_transfer_encoding and (
        len(transfer_encoding_values) != 1 or transfer_encoding_values[0] != b"chunked"
    )
    raw_length = headers.get("Content-Length")
    has_content_length = raw_length is not None
    # Conflicting HTTP framing must not reach the route handler or consume a body.
    ambiguous_framing = has_content_length and has_transfer_encoding
    declared_length = None
    invalid_content_length = has_content_length and (
        not isinstance(raw_length, str)
        or not raw_length.isascii()
        or not raw_length.isdigit()
    )
    if has_content_length and not invalid_content_length:
        normalized_length = raw_length.lstrip("0") or "0"
        if len(normalized_length) > len(str(MAX_BODY_BYTES)):
            declared_length = MAX_BODY_BYTES + 1
        else:
            declared_length = int(normalized_length)
        headers["Content-Length"] = str(declared_length)

    body = bytearray()
    should_read_body = (
        method == "POST"
        and not duplicate_content_length
        and not ambiguous_framing
        and not unsupported_transfer_encoding
        and not invalid_content_length
        and (
            not has_content_length
            or (declared_length is not None and 0 < declared_length <= MAX_BODY_BYTES)
        )
    )
    body_timed_out = False
    if should_read_body:
        try:
            async with asyncio.timeout(REQUEST_BODY_TIMEOUT_SECONDS):
                while True:
                    message = await receive()
                    if message.get("type") == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        declared_length = MAX_BODY_BYTES + 1
                        body.clear()
                        break
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            body_timed_out = True
    body_length_mismatch = (
        has_content_length
        and declared_length is not None
        and 0 < declared_length <= MAX_BODY_BYTES
        and len(body) != declared_length
        and not body_timed_out
    )

    if declared_length is not None and declared_length > MAX_BODY_BYTES:
        headers["Content-Length"] = str(declared_length)
    elif method == "POST" and not has_content_length and not body_timed_out:
        headers["Content-Length"] = str(len(body))

    cancel_event = Event()
    request = _Request(
        scope,
        b"" if body_timed_out else bytes(body),
        started_at=started_at,
        cancel_event=cancel_event,
    )
    request.headers.update(headers)
    if unsupported_transfer_encoding:
        send_json(
            request,
            400,
            {"error": {"code": "invalid_request", "message": "The request transfer encoding is not supported."}},
        )
    elif ambiguous_framing:
        send_json(
            request,
            400,
            {"error": {"code": "invalid_request", "message": "The request framing is ambiguous."}},
        )
    elif duplicate_content_length:
        send_json(
            request,
            400,
            {"error": {"code": "invalid_request", "message": "The request could not be read."}},
        )
    elif invalid_content_length:
        send_json(
            request,
            400,
            {"error": {"code": "invalid_request", "message": "The request could not be read."}},
        )
    elif declared_length is not None and declared_length > MAX_BODY_BYTES:
        send_json(
            request,
            413,
            {"error": {"code": "request_too_large", "message": "This request is larger than allowed."}},
        )
    elif body_length_mismatch:
        send_json(
            request,
            400,
            {"error": {"code": "invalid_request", "message": "The request body length did not match its declared size."}},
        )
    elif body_timed_out:
        send_json(
            request,
            408,
            {"error": {"code": "request_timeout", "message": "The request body did not arrive in time. Please try again."}},
        )
    else:
        # Existing route handlers perform synchronous provider I/O. Keep that
        # work off the ASGI event loop so concurrent requests can still progress.
        route_task = asyncio.create_task(asyncio.to_thread(
            run_with_provider_cancellation, cancel_event, handler._dispatch, request
        ))
        disconnect_task = asyncio.create_task(_wait_for_disconnect(receive))
        try:
            done, _pending = await asyncio.wait(
                (route_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            cancel_event.set()
            route_task.cancel()
            disconnect_task.cancel()
            await asyncio.gather(route_task, disconnect_task, return_exceptions=True)
            raise
        if disconnect_task in done:
            cancel_event.set()
            if not route_task.done():
                route_task.cancel()
            await asyncio.gather(route_task, return_exceptions=True)
            return
        disconnect_task.cancel()
        try:
            await disconnect_task
        except asyncio.CancelledError:
            pass
        await route_task
    response_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in request.response_headers.items()
    ]
    await send({
        "type": "http.response.start",
        "status": request.status or 200,
        "headers": response_headers,
    })
    await send({
        "type": "http.response.body",
        "body": request.wfile.getvalue(),
        "more_body": False,
    })


async def _wait_for_disconnect(receive):
    """Observe disconnects while a synchronous route runs in a worker thread."""
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return
