from api_helpers import JsonApiHandler, method_not_allowed, read_json_body, safe_error, send_json
from api_auth import require_api_access
from research_service import ResearchError, compare_snapshots


class handler(JsonApiHandler):
    allowed_methods = "POST"

    def do_POST(self):
        if not require_api_access(self):
            return
        try:
            body = read_json_body(self)
            if body.keys() != {"previous", "current"}:
                raise ResearchError("invalid_request", "Provide exactly the two saved analyses to compare.")
            result = compare_snapshots(body.get("previous"), body.get("current"))
            send_json(self, 200, {"data": result})
        except Exception as exc:
            safe_error(self, exc)

    def do_GET(self):
        method_not_allowed(self, allow="POST")

    def log_message(self, format, *args):
        return
