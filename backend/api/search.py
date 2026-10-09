from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from api_helpers import safe_error, send_json
from research_service import search_companies


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        try:
            matches = search_companies(query.get("q", [""])[0])
            send_json(self, 200, {"data": matches}, cache="public, s-maxage=30, stale-while-revalidate=60")
        except Exception as exc:
            safe_error(self, exc)

    def log_message(self, format, *args):
        return
