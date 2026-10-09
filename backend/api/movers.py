from http.server import BaseHTTPRequestHandler

from api_helpers import safe_error, send_json
from research_service import daily_market_movers


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            result = daily_market_movers()
            send_json(self, 200, {"data": result}, cache="public, s-maxage=300, stale-while-revalidate=300")
        except Exception as exc:
            safe_error(self, exc)

    def log_message(self, format, *args):
        return
