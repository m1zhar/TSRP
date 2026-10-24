from api_helpers import JsonApiHandler, method_not_allowed, read_json_body, safe_error, send_json
from api_auth import require_api_access
from research_service import ResearchError, analyze_scenario, validate_ticker
from scenario_context import verify_scenario_context


class handler(JsonApiHandler):
    allowed_methods = "POST"

    def do_POST(self):
        if not require_api_access(self):
            return
        try:
            body = read_json_body(self)
            if body.keys() - {"ticker", "assumptions", "context_token"}:
                raise ResearchError("invalid_request", "The request body contains unsupported fields.")
            ticker = validate_ticker(body.get("ticker"))
            basis = verify_scenario_context(body.get("context_token"), ticker)
            if basis is None:
                raise ResearchError("scenario_context_invalid", "The company facts could not be verified. Refresh this analysis and try again.", 422)
            result = analyze_scenario(ticker, body.get("assumptions"), basis)
            send_json(self, 200, {"data": result})
        except Exception as exc:
            safe_error(self, exc)

    def do_GET(self):
        method_not_allowed(self, allow="POST")

    def log_message(self, format, *args):
        return
