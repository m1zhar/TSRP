from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from api_helpers import safe_error, send_json
from research_service import analyze_company, validate_ticker


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        try:
            ticker = validate_ticker(query.get("ticker", [""])[0])
            result = analyze_company(ticker)
            send_json(self, 200, {"data": result}, cache="public, s-maxage=300, stale-while-revalidate=900")
        except Exception as exc:
            safe_error(self, exc)

    def log_message(self, format, *args):
        return
