"""One Vercel Python entrypoint dispatching the existing typed API handlers."""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

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
