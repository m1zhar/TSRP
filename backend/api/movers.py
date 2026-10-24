from api_helpers import JsonApiHandler, read_query_params, safe_error, send_json
from api_auth import require_api_access
from analysis_coalescer import coalesce_inflight
from research_service import daily_market_movers, run_with_provider_cancellation


class handler(JsonApiHandler):
    allowed_methods = "GET"

    def do_GET(self):
        if not require_api_access(self):
            return
        try:
            read_query_params(self, allowed=frozenset())
            result = coalesce_inflight(
                "daily_market_movers",
                daily_market_movers,
                cancel_event=getattr(self, "cancel_event", None),
                run_with_cancellation=run_with_provider_cancellation,
            )
            send_json(self, 200, {"data": result}, cache="private, no-store")
        except Exception as exc:
            safe_error(self, exc)

    def log_message(self, format, *args):
        return
