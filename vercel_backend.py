"""WSGI adapter exposing TSRP's existing Python research service on Vercel."""

from __future__ import annotations

from datetime import date, datetime
from http import HTTPStatus
import json
import logging
from typing import Any, Callable
from urllib.parse import parse_qs

from research_service import (
    ResearchError,
    analyze_company,
    analyze_scenario,
    compare_snapshots,
    daily_market_movers,
    daily_top_stocks,
    search_companies,
    validate_ticker,
)


MAX_BODY_BYTES = 16_384
LOGGER = logging.getLogger("tsrp")
WSGIStartResponse = Callable[[str, list[tuple[str, str]]], Any]


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _read_json_body(environ: dict[str, Any]) -> dict[str, Any]:
    raw_length = environ.get("CONTENT_LENGTH", "0") or "0"
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise ResearchError("invalid_request", "The request could not be read.") from exc
    if length < 1 or length > MAX_BODY_BYTES:
        raise ResearchError("request_too_large", "This request is larger than allowed.", 413)

    content_type = str(environ.get("CONTENT_TYPE", "")).split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ResearchError("invalid_request", "Send assumptions as JSON.", 415)

    try:
        raw = environ["wsgi.input"].read(length)
        payload = json.loads(raw)
    except (KeyError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResearchError("invalid_request", "The request body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ResearchError("invalid_request", "The request body must be a JSON object.")
    return payload


def _status_line(status: int) -> str:
    try:
        phrase = HTTPStatus(status).phrase
    except ValueError:
        phrase = "Error"
    return f"{status} {phrase}"


def _respond(start_response: WSGIStartResponse, status: int, value: Any, *, cache: str = "no-store") -> list[bytes]:
    body = json.dumps(value, allow_nan=False, separators=(",", ":"), default=_json_default).encode("utf-8")
    start_response(
        _status_line(status),
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", cache),
            ("X-Content-Type-Options", "nosniff"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


def _dispatch(path: str, method: str, query: dict[str, list[str]], body: dict[str, Any]) -> tuple[Any, str]:
    if path == "/api/analyze" and method == "GET":
        ticker = validate_ticker(query.get("ticker", [""])[0])
        return {"data": analyze_company(ticker)}, "public, s-maxage=300, stale-while-revalidate=900"
    if path == "/api/search" and method == "GET":
        return {"data": search_companies(query.get("q", [""])[0])}, "public, s-maxage=30, stale-while-revalidate=60"
    if path == "/api/scenario" and method == "POST":
        return {"data": analyze_scenario(body.get("ticker"), body.get("assumptions"))}, "no-store"
    if path == "/api/snapshot_diff" and method == "POST":
        return {"data": compare_snapshots(body.get("previous"), body.get("current"))}, "no-store"
    if path == "/api/movers" and method == "GET":
        return {"data": daily_market_movers()}, "public, s-maxage=300, stale-while-revalidate=300"
    if path == "/api/top_stocks" and method == "GET":
        return {"data": daily_top_stocks()}, "public, s-maxage=300, stale-while-revalidate=300"

    known_paths = {"/api/analyze", "/api/search", "/api/scenario", "/api/snapshot_diff", "/api/movers", "/api/top_stocks"}
    if path in known_paths:
        raise ResearchError("method_not_allowed", "This request method is not supported.", 405)
    raise ResearchError("not_found", "This research endpoint was not found.", 404)


def app(environ: dict[str, Any], start_response: WSGIStartResponse) -> list[bytes]:
    """Serve the same six JSON endpoints used by the Next.js client."""
    path = str(environ.get("PATH_INFO", "/")).rstrip("/") or "/"
    method = str(environ.get("REQUEST_METHOD", "GET")).upper()

    try:
        body = _read_json_body(environ) if method == "POST" else {}
        payload, cache = _dispatch(path, method, parse_qs(str(environ.get("QUERY_STRING", ""))), body)
        return _respond(start_response, 200, payload, cache=cache)
    except ResearchError as exc:
        LOGGER.warning("request_rejected code=%s", exc.code)
        return _respond(start_response, exc.status, {"error": {"code": exc.code, "message": exc.message}})
    except Exception as exc:
        LOGGER.error("request_failed exception_type=%s", type(exc).__name__)
        return _respond(
            start_response,
            503,
            {"error": {"code": "service_unavailable", "message": "Research data is temporarily unavailable. Please try again."}},
        )
