"""Tiny local adapter so the same Vercel handler functions run during dev."""

from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from api.analyze import handler as AnalyzeHandler
from api.movers import handler as MoversHandler
from api.top_stocks import handler as TopStocksHandler
from api.scenario import handler as ScenarioHandler
from api.search import handler as SearchHandler
from api.snapshot_diff import handler as SnapshotDiffHandler
from api_helpers import JsonApiHandler, method_not_allowed, send_json


HANDLERS = {
    "/api/analyze": AnalyzeHandler,
    "/api/movers": MoversHandler,
    "/api/top_stocks": TopStocksHandler,
    "/api/scenario": ScenarioHandler,
    "/api/search": SearchHandler,
    "/api/snapshot_diff": SnapshotDiffHandler,
}
LOCAL_WEB_ORIGINS = {"http://127.0.0.1:3000", "http://localhost:3000"}
LOCAL_REQUEST_TIMEOUT_SECONDS = 30


def add_local_cors_headers(handler):
    origin = handler.headers.get("Origin")
    if origin in LOCAL_WEB_ORIGINS:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Access-Control-Expose-Headers", "X-Request-ID, Date")
        handler.send_header("Vary", "Origin")


class Router(JsonApiHandler):
    allowed_methods = "GET, POST, OPTIONS"
    timeout = LOCAL_REQUEST_TIMEOUT_SECONDS

    def end_headers(self):
        add_local_cors_headers(self)
        super().end_headers()

    def _forward(self, method: str):
        origin = self.headers.get("Origin")
        if origin not in LOCAL_WEB_ORIGINS:
            send_json(self, 403, {"error": {"code": "origin_not_allowed", "message": "Use the local TSRP web app to access this development API."}})
            return
        route = HANDLERS.get(urlparse(self.path).path)
        if route is None:
            self.send_error(404)
            return
        requested_method = method.removeprefix("do_").upper()
        allowed_methods = {value.strip().upper() for value in route.allowed_methods.split(",")}
        if requested_method not in allowed_methods:
            method_not_allowed(self, allow=route.allowed_methods)
            return
        route_method = getattr(route, method, None)
        if route_method is None:
            method_not_allowed(self, allow=route.allowed_methods)
            return
        route_method(self)

    def do_GET(self):
        self._forward("do_GET")

    def do_POST(self):
        self._forward("do_POST")

    def do_HEAD(self):
        self._forward("do_HEAD")

    def do_OPTIONS(self):
        if self.headers.get("Origin") not in LOCAL_WEB_ORIGINS:
            send_json(self, 403, {"error": {"code": "origin_not_allowed", "message": "This local request origin is not allowed."}})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Router)
    print("TSRP research API available at http://127.0.0.1:8000")
    server.serve_forever()
