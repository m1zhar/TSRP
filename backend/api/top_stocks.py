from api_helpers import JsonApiHandler, read_query_params, safe_error, send_json
from api_auth import require_api_access
from analysis_coalescer import coalesce_inflight
from research_service import ResearchError, daily_popular_stocks, daily_top_stocks, run_with_provider_cancellation


class handler(JsonApiHandler):
    allowed_methods = "GET"

    def do_GET(self):
        if not require_api_access(self):
            return
        try:
            query = read_query_params(self, allowed=frozenset({"list"}))
            selected_list = query.get("list", "active")
            if selected_list == "active":
                key = "daily_top_stocks"
                provider = daily_top_stocks
            elif selected_list == "popular":
                key = "daily_popular_stocks"
                provider = daily_popular_stocks
            else:
                raise ResearchError("invalid_request", "Choose a supported stock list.")
            result = coalesce_inflight(
                key,
                provider,
                cancel_event=getattr(self, "cancel_event", None),
                run_with_cancellation=run_with_provider_cancellation,
            )
            send_json(self, 200, {"data": result}, cache="private, no-store")
        except Exception as exc:
            safe_error(self, exc)

    def log_message(self, format, *args):
        return
