from api_helpers import JsonApiHandler, method_not_allowed, read_query_params, safe_error, send_json
from api_auth import require_api_access
from analysis_coalescer import coalesce_analysis
from research_service import analyze_company, run_with_provider_cancellation, scenario_context_is_eligible, validate_ticker
from scenario_context import issue_scenario_context, scenario_context_key_available, verify_scenario_context


class handler(JsonApiHandler):
    allowed_methods = "GET"

    def do_GET(self):
        if not require_api_access(self):
            return
        try:
            query = read_query_params(self, allowed=frozenset({"ticker"}))
            ticker = validate_ticker(query.get("ticker", ""))
            result = coalesce_analysis(
                ticker,
                analyze_company,
                cancel_event=getattr(self, "cancel_event", None),
                run_with_cancellation=run_with_provider_cancellation,
            )
            company = result.get("company")
            token = None
            if not scenario_context_key_available():
                context_status = "not_configured"
            elif not isinstance(company, dict) or not company.get("sector_methodology_supported") or not company.get("currency_methodology_supported"):
                context_status = "unsupported"
            else:
                candidate = issue_scenario_context(company)
                basis = verify_scenario_context(candidate, ticker)
                if candidate and basis and scenario_context_is_eligible(ticker, basis):
                    token = candidate
                    context_status = "available"
                else:
                    context_status = "unavailable"
            result["scenario_context_token"] = token
            result["scenario_context_status"] = context_status
            send_json(self, 200, {"data": result}, cache="private, no-store")
        except Exception as exc:
            safe_error(self, exc)

    def do_POST(self):
        method_not_allowed(self, allow="GET")

    def log_message(self, format, *args):
        return
