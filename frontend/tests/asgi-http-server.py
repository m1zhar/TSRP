"""Tiny test-only HTTP bridge for exercising the production ASGI API entrypoint."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import pandas as pd


BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
TESTS_ROOT = Path(__file__).resolve().parents[2] / "tests"
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(TESTS_ROOT))
from api.index import app  # noqa: E402
from api_helpers import MAX_BODY_BYTES  # noqa: E402
import research_service  # noqa: E402
import api.analyze as analyze_route  # noqa: E402
import api.scenario as scenario_route  # noqa: E402
import api.movers as movers_route  # noqa: E402
import api.search as search_route  # noqa: E402
import api.top_stocks as top_stocks_route  # noqa: E402
import api.snapshot_diff as snapshot_diff_route  # noqa: E402
from test_migration_service import MockEquity  # noqa: E402
from research_service import ResearchError  # noqa: E402


def _reject_external_provider_request(*_args, **_kwargs):
    raise AssertionError("Unexpected external provider request in synthetic ASGI tests.")


research_service._YAHOO_SESSION.request = _reject_external_provider_request
research_service._SEC_SESSION.get = _reject_external_provider_request
research_service._SEC_SESSION.request = _reject_external_provider_request


# The production smoke must never contact public market providers. Exercise the
# real analysis engine with the same deterministic synthetic company used by
# backend tests, and make SEC explicitly unavailable for this harness.
_fixture_equity = MockEquity(datetime.now(timezone.utc).year - 1)
_fixture_equity.revenue_estimate["currency"] = "USD"
_fixture_equity.revenue_estimate["endDate"] = f"{datetime.now(timezone.utc).year + 1}-12-31"
if os.environ.get("TSRP_E2E_FUTURE_PRICE_BAR") == "1":
    _fixture_history = _fixture_equity.history

    def _future_price_bar_fixture(**kwargs):
        history = _fixture_history(**kwargs)
        future_day = pd.Timestamp.now(tz="UTC").normalize() + pd.Timedelta(1, unit="D")
        injected = pd.concat([
            history,
            pd.DataFrame({"Close": [999.0], "Volume": [9999]}, index=[future_day]),
        ])
        assert injected["Close"].iloc[-1] == 999.0
        print("TSRP_E2E_FUTURE_PRICE_BAR injected", flush=True)
        return injected

    _fixture_equity.history = _future_price_bar_fixture
if os.environ.get("TSRP_E2E_UNSUPPORTED_SECTOR") == "1":
    _fixture_get_info = _fixture_equity.get_info

    def _financial_services_fixture():
        info = _fixture_get_info()
        info.update(longName="Synthetic QA · Example Financial Group", sector="Financial Services")
        return info

    _fixture_equity.get_info = _financial_services_fixture
_uk_fixture_equity = MockEquity(datetime.now(timezone.utc).year - 1)
_uk_fixture_equity.fast_info = {"currency": "GBp", "lastPrice": 120, "previousClose": 118}
_uk_fixture_info = _uk_fixture_equity.get_info
_uk_fixture_equity.get_info = lambda: {
    **_uk_fixture_info(), "currency": "GBp", "financialCurrency": "GBP",
    "currentPrice": 120, "previousClose": 118, "marketCap": 30, "sharesOutstanding": 25,
}
research_service.yf.Ticker = lambda _symbol, session=None: _uk_fixture_equity if _symbol == "EXM.L" else _fixture_equity
research_service._sec_facts = lambda *_args: (
    None,
    {"kind": "not_configured", "label": "SEC unavailable in synthetic production smoke"},
)


def _market_feed_unavailable():
    raise ResearchError("provider_unavailable", "Market feeds are intentionally disabled in local synthetic QA.", 503)


def _popular_stocks_fixture():
    retrieved_at = datetime.now(timezone.utc)
    market_day = retrieved_at.date().isoformat()
    return {
        "source": "Synthetic local QA fixture",
        "rank": "Popular large-cap stocks; not ranked by daily trading volume",
        "label": "Popular stocks",
        "retrieved_at": retrieved_at.isoformat(),
        "items": [
            {"ticker": "AAPL", "change_percent": 1.25, "absolute_change": 2.5, "currency": "USD", "market_at": market_day},
            {"ticker": "MSFT", "change_percent": -0.5, "absolute_change": -2.0, "currency": "USD", "market_at": market_day},
        ],
    }


def _company_search_unavailable(query):
    research_service.validate_search(query)
    raise ResearchError("provider_unavailable", "Company search is intentionally disabled in local synthetic QA.", 503)


def _company_search_provider_disabled(query):
    research_service.validate_search(query)
    raise ResearchError("provider_disabled", "Market-data access is not enabled for this deployment. Contact its operator.", 503)


def _company_search_race_fixture(query):
    research_service.validate_search(query)
    normalized = query.strip().lower()
    if normalized.startswith("slow"):
        print("TSRP_SEARCH_RACE slow_started", flush=True)
        time.sleep(1.8)
        print("TSRP_SEARCH_RACE slow_completed", flush=True)
        return [{"ticker": "SLOW", "company": "Slow Search Fixture", "exchange": "Test", "sector": "Fixture"}]
    if normalized.startswith("fast"):
        print("TSRP_SEARCH_RACE fast_started", flush=True)
        time.sleep(0.05)
        print("TSRP_SEARCH_RACE fast_completed", flush=True)
        return [{"ticker": "FAST", "company": "Fast Search Fixture", "exchange": "Test", "sector": "Fixture"}]
    return []


def _analysis_race_fixture(ticker):
    normalized = str(ticker).strip().upper()
    slow_delay = float(os.environ.get("TSRP_E2E_ANALYSIS_DELAY_SECONDS", "1.8"))
    delay, label = {"SLOW": (slow_delay, "slow"), "FAST": (0.05, "fast")}.get(normalized, (0.0, ""))
    if delay > 0:
        print(f"TSRP_ANALYSIS_RACE {label}_started", flush=True)
        time.sleep(delay)
        print(f"TSRP_ANALYSIS_RACE {label}_completed", flush=True)
    return _original_analyze(ticker)


def _scenario_race_fixture(symbol, assumptions, basis):
    print("TSRP_SCENARIO_RACE calculation_started", flush=True)
    time.sleep(1.8)
    print("TSRP_SCENARIO_RACE calculation_completed", flush=True)
    return _original_scenario(symbol, assumptions, basis)


def _mark_synthetic(value):
    if isinstance(value, dict):
        return {
            key: "Synthetic QA · Example Corporation" if key == "name" and item == "Example Corporation"
            else _mark_synthetic(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mark_synthetic(item) for item in value]
    if isinstance(value, str):
        return value.replace("Example Corporation", "Synthetic QA · Example Corporation").replace("Yahoo Finance", "Synthetic local QA fixture")
    return value


movers_route.daily_market_movers = _market_feed_unavailable
top_stocks_route.daily_top_stocks = _market_feed_unavailable
top_stocks_route.daily_popular_stocks = _popular_stocks_fixture
search_route.search_companies = (
    _company_search_race_fixture if os.environ.get("TSRP_E2E_SEARCH_RACE") == "1"
    else _company_search_provider_disabled if os.environ.get("TSRP_E2E_PROVIDER_DISABLED") == "1"
    else _company_search_unavailable
)
_original_analyze = analyze_route.analyze_company
if os.environ.get("TSRP_E2E_PROVIDER_DISABLED") == "1":
    def _analysis_provider_disabled(ticker):
        research_service.validate_ticker(ticker)
        raise ResearchError("provider_disabled", "Market-data access is not enabled for this deployment. Contact its operator.", 503)

    analyze_route.analyze_company = _analysis_provider_disabled
elif os.environ.get("TSRP_E2E_ANALYSIS_FAILURE") == "1":
    def _analysis_failure_fixture(ticker):
        print("TSRP_ANALYSIS_FAILURE attempt", flush=True)
        raise ResearchError("provider_unavailable", "Synthetic provider failure for local retry-loop QA.", 503)

    analyze_route.analyze_company = _analysis_failure_fixture
elif os.environ.get("TSRP_E2E_ANALYSIS_RECOVERY") == "1":
    _analysis_recovery_attempts = 0

    def _analysis_recovery_fixture(ticker):
        global _analysis_recovery_attempts
        normalized = str(ticker).strip().upper()
        if normalized == "RETRY" and _analysis_recovery_attempts == 0:
            _analysis_recovery_attempts += 1
            print("TSRP_ANALYSIS_RECOVERY initial_failure", flush=True)
            raise ResearchError("provider_unavailable", "Synthetic provider failure for local retry QA.", 503)
        if normalized == "RETRY":
            print("TSRP_ANALYSIS_RECOVERY recovered", flush=True)
        if os.environ.get("TSRP_E2E_ANALYSIS_RACE") == "1":
            return _analysis_race_fixture(ticker)
        return _original_analyze(ticker)

    analyze_route.analyze_company = _analysis_recovery_fixture
elif os.environ.get("TSRP_E2E_ANALYSIS_RACE") == "1":
    analyze_route.analyze_company = _analysis_race_fixture
if os.environ.get("TSRP_E2E_ESTIMATE_REVISIONS") == "1":
    _estimate_revision_count = 0
    _analysis_before_revision = analyze_route.analyze_company

    def _estimate_revision_fixture(ticker):
        global _estimate_revision_count
        if ticker.upper() == "QAEST":
            _fixture_equity.revenue_estimate.loc["+1y", "growth"] = .12 if _estimate_revision_count == 0 else .18
            _estimate_revision_count += 1
        return _analysis_before_revision(ticker)

    analyze_route.analyze_company = _estimate_revision_fixture
if os.environ.get("TSRP_E2E_SINGLE_CHART_POINT") == "1":
    _analysis_with_single_chart_point = analyze_route.analyze_company

    def _single_chart_point_fixture(ticker):
        result = _analysis_with_single_chart_point(ticker)
        history = result.get("price_history")
        if isinstance(history, list) and history:
            result["price_history"] = history[-1:]
        return result

    analyze_route.analyze_company = _single_chart_point_fixture
_original_scenario = scenario_route.analyze_scenario
if os.environ.get("TSRP_E2E_SCENARIO_RACE") == "1":
    scenario_route.analyze_scenario = _scenario_race_fixture


_original_snapshot_diff = snapshot_diff_route.compare_snapshots
if os.environ.get("TSRP_E2E_SNAPSHOT_RACE") == "1":
    def _snapshot_diff_race_fixture(previous, current):
        print("TSRP_SNAPSHOT_RACE comparison_started", flush=True)
        time.sleep(1.8)
        print("TSRP_SNAPSHOT_RACE comparison_completed", flush=True)
        return _original_snapshot_diff(previous, current)

    snapshot_diff_route.compare_snapshots = _snapshot_diff_race_fixture


class AsgiHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps({"status": "ready"}).encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self._run_asgi_request()

    def do_POST(self):
        self._run_asgi_request()

    def _run_asgi_request(self):
        parsed = urlsplit(self.path)
        sent = []
        received = False
        try:
            declared_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            declared_length = 0
        body = self.rfile.read(min(max(declared_length, 0), MAX_BODY_BYTES + 1))

        async def receive():
            nonlocal received
            if received:
                # An open HTTP connection has no second request-body message;
                # do not report a disconnect just because the route is still
                # running. Unit tests send a real ASGI disconnect explicitly.
                await asyncio.Future()
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": self.command,
                "scheme": "http",
                "path": parsed.path,
                "raw_path": parsed.path.encode("ascii"),
                "query_string": parsed.query.encode("ascii"),
                "root_path": "",
                "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in self.headers.raw_items()],
                "server": ("127.0.0.1", self.server.server_port),
                "client": self.client_address,
            },
            receive,
            send,
        ))

        start = next(message for message in sent if message["type"] == "http.response.start")
        body = next(message for message in sent if message["type"] == "http.response.body")["body"]
        if parsed.path == "/api/analyze" and start["status"] == 200:
            body = json.dumps(_mark_synthetic(json.loads(body))).encode("utf-8")
            start["headers"] = [
                (name, value) for name, value in start.get("headers", []) if name.lower() != b"content-length"
            ] + [(b"content-length", str(len(body)).encode("ascii"))]
        self.send_response(start["status"])
        for name, value in start.get("headers", []):
            self.send_header(name.decode("latin-1"), value.decode("latin-1"))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return



if __name__ == "__main__":
    port = int(sys.argv[1])
    server = ThreadingHTTPServer(("127.0.0.1", port), AsgiHttpHandler)
    print("TSRP_ASGI_TEST_SERVER_READY", flush=True)
    server.serve_forever()
