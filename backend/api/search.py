from api_helpers import JsonApiHandler, method_not_allowed, read_json_body, safe_error, send_json
from api_auth import require_api_access
from research_service import ResearchError, search_companies


class handler(JsonApiHandler):
    allowed_methods = "POST"

    def do_GET(self):
        method_not_allowed(self, allow=self.allowed_methods)

    def do_POST(self):
        if not require_api_access(self):
            return
        try:
            body = read_json_body(self)
            if body.keys() != {"q"} or not isinstance(body["q"], str):
                raise ResearchError("invalid_request", "Send one company search query as text.")
            matches = search_companies(body["q"])
            send_json(self, 200, {"data": matches})
        except Exception as exc:
            safe_error(self, exc)

    def log_message(self, format, *args):
        return
