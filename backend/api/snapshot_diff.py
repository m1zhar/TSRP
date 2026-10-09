from http.server import BaseHTTPRequestHandler

from api_helpers import read_json_body, safe_error, send_json
from research_service import compare_snapshots


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            body = read_json_body(self)
            result = compare_snapshots(body.get("previous"), body.get("current"))
            send_json(self, 200, {"data": result})
        except Exception as exc:
            safe_error(self, exc)

    def log_message(self, format, *args):
        return
