"""Tiny local adapter so the same Vercel handler functions run during dev."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from api.analyze import handler as AnalyzeHandler
from api.movers import handler as MoversHandler
from api.top_stocks import handler as TopStocksHandler
from api.scenario import handler as ScenarioHandler
from api.search import handler as SearchHandler
from api.snapshot_diff import handler as SnapshotDiffHandler


HANDLERS = {
    "/api/analyze": AnalyzeHandler,
    "/api/movers": MoversHandler,
    "/api/top_stocks": TopStocksHandler,
    "/api/scenario": ScenarioHandler,
    "/api/search": SearchHandler,
    "/api/snapshot_diff": SnapshotDiffHandler,
}
LOCAL_WEB_ORIGINS = {"http://127.0.0.1:3000", "http://localhost:3000"}


class Router(BaseHTTPRequestHandler):
    def end_headers(self):
        origin = self.headers.get("Origin")
        if origin in LOCAL_WEB_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def _forward(self, method: str):
        route = HANDLERS.get(urlparse(self.path).path)
        if route is None:
            self.send_error(404)
            return
        route.__dict__[method](self)

    def do_GET(self):
        self._forward("do_GET")

    def do_POST(self):
        self._forward("do_POST")

    def do_OPTIONS(self):
        if self.headers.get("Origin") not in LOCAL_WEB_ORIGINS:
            self.send_error(403)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Router)
    print("TSRP research API available at http://127.0.0.1:8000")
    server.serve_forever()
