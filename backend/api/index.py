"""One Vercel Python entrypoint dispatching the existing typed API handlers."""

from io import BytesIO
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from api_helpers import MAX_BODY_BYTES
from api.analyze import handler as AnalyzeHandler
from api.movers import handler as MoversHandler
from api.top_stocks import handler as TopStocksHandler
from api.scenario import handler as ScenarioHandler
from api.search import handler as SearchHandler
from api.snapshot_diff import handler as SnapshotDiffHandler


ROUTES = {
    "/api/analyze": {"GET": AnalyzeHandler.do_GET},
    "/api/movers": {"GET": MoversHandler.do_GET},
    "/api/top_stocks": {"GET": TopStocksHandler.do_GET},
    "/api/scenario": {"POST": ScenarioHandler.do_POST},
    "/api/search": {"GET": SearchHandler.do_GET},
    "/api/snapshot_diff": {"POST": SnapshotDiffHandler.do_POST},
}


class handler(BaseHTTPRequestHandler):
    def _dispatch(self):
        methods = ROUTES.get(urlparse(self.path).path)
        if methods is None:
            self.send_error(404)
            return
        callback = methods.get(self.command)
        if callback is None:
            self.send_response(405)
            self.send_header("Allow", ", ".join(methods))
            self.end_headers()
            return
        callback(self)

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def log_message(self, format, *args):
        return


class _Headers(dict):
    def get_content_type(self):
        return self.get("Content-Type", "").split(";", 1)[0].strip().lower()


class _Request:
    """Bridge ASGI requests to the existing BaseHTTPRequestHandler routes."""

    def __init__(self, scope, body):
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

    headers = {
        key.decode("latin-1").title(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    raw_length = headers.get("Content-Length", "0")
    try:
        declared_length = int(raw_length)
    except ValueError:
        declared_length = 0

    body = bytearray()
    if scope.get("method", "GET").upper() == "POST" and declared_length <= MAX_BODY_BYTES:
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                declared_length = MAX_BODY_BYTES + 1
                body.clear()
                break
            if not message.get("more_body", False):
                break

    if declared_length > MAX_BODY_BYTES:
        headers["Content-Length"] = str(declared_length)

    request = _Request(scope, bytes(body))
    request.headers.update(headers)
    handler._dispatch(request)
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
