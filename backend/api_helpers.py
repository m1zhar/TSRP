"""Shared, low-detail HTTP helpers for the small Vercel Python API."""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlparse

from research_service import ResearchError


MAX_BODY_BYTES = 16_384
MAX_QUERY_LENGTH = 2_048
MAX_QUERY_FIELDS = 8
LOGGER = logging.getLogger("tsrp")
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    _log_handler = logging.StreamHandler()
    _log_handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(_log_handler)
LOGGER.propagate = False
API_ROUTES = frozenset({
    "/api/analyze",
    "/api/movers",
    "/api/scenario",
    "/api/search",
    "/api/snapshot_diff",
    "/api/top_stocks",
})


def _request_context(handler: BaseHTTPRequestHandler) -> tuple[str, str, str, float]:
    request_id = getattr(handler, "_tsrp_request_id", None)
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        request_id = uuid.uuid4().hex
        setattr(handler, "_tsrp_request_id", request_id)

    started_at = getattr(handler, "_tsrp_started_at", None)
    if not isinstance(started_at, (int, float)) or not math.isfinite(started_at):
        started_at = time.perf_counter()
        setattr(handler, "_tsrp_started_at", started_at)

    path = urlparse(str(getattr(handler, "path", ""))).path
    route = path if path in API_ROUTES else "/api/unknown"
    method = str(getattr(handler, "command", "UNKNOWN")).upper()
    if not re.fullmatch(r"[A-Z]{1,12}", method):
        method = "OTHER"
    return request_id, route, method, float(started_at)


class JsonApiHandler(BaseHTTPRequestHandler):
    allowed_methods = ""

    def __init__(self, *args, **kwargs):
        self._tsrp_request_id = uuid.uuid4().hex
        self._tsrp_started_at = time.perf_counter()
        super().__init__(*args, **kwargs)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if code == 501:
            method_not_allowed(self, allow=self.allowed_methods)
            return
        send_json(
            self,
            code,
            {"error": {"code": "invalid_request", "message": "The request could not be processed."}},
        )


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON values are not supported.")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON numbers are not supported.")
    return number


def send_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    value: Any,
    *,
    cache: str = "no-store",
    allow: str | None = None,
    error_type: str | None = None,
    bearer_challenge: bool = False,
) -> None:
    cancel_event = getattr(handler, "cancel_event", None)
    is_disconnected = getattr(cancel_event, "is_set", None)
    if callable(is_disconnected) and is_disconnected():
        return
    body = json.dumps(value, allow_nan=False, separators=(",", ":"), default=_json_default).encode("utf-8")
    response_body = b"" if getattr(handler, "command", "").upper() == "HEAD" else body
    request_id, route, method, started_at = _request_context(handler)
    error = value.get("error") if isinstance(value, dict) else None
    error_code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(error_code, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", error_code):
        error_code = None
    if not isinstance(error_type, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", error_type):
        error_type = None
    def log_outcome(outcome: str) -> None:
        LOGGER.info("api_request %s", json.dumps({
            "request_id": request_id,
            "route": route,
            "method": method,
            "status": status,
            "duration_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
            "outcome": outcome,
            "error_code": error_code,
            "error_type": error_type,
        }, separators=(",", ":")))

    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Cache-Control", cache)
        handler.send_header("X-Request-ID", request_id)
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("X-Frame-Options", "DENY")
        handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        handler.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        handler.send_header("Content-Length", str(len(response_body)))
        if allow is not None:
            handler.send_header("Allow", allow)
        if bearer_challenge:
            handler.send_header("WWW-Authenticate", 'Bearer realm="tsrp-api"')
        handler.end_headers()
        if response_body:
            handler.wfile.write(response_body)
    except (ConnectionError, TimeoutError):
        log_outcome("client_disconnected")
        return
    log_outcome("error" if status >= 400 else "success")


def method_not_allowed(handler: BaseHTTPRequestHandler, *, allow: str) -> None:
    send_json(
        handler,
        405,
        {"error": {"code": "method_not_allowed", "message": "This request method is not supported."}},
        allow=allow,
    )


def read_query_params(
    handler: BaseHTTPRequestHandler,
    *,
    allowed: frozenset[str],
) -> dict[str, str]:
    query_string = urlparse(handler.path).query
    if len(query_string) > MAX_QUERY_LENGTH:
        raise ResearchError("invalid_request", "The request query is not valid.")
    try:
        parsed = (
            parse_qs(
                query_string,
                keep_blank_values=True,
                max_num_fields=MAX_QUERY_FIELDS,
                strict_parsing=True,
            )
            if query_string
            else {}
        )
    except ValueError as exc:
        raise ResearchError("invalid_request", "The request query is not valid.") from exc
    if any(len(values) != 1 for values in parsed.values()):
        raise ResearchError("invalid_request", "The request query contains duplicate fields.")
    if parsed.keys() - allowed:
        raise ResearchError("invalid_request", "The request query contains unsupported fields.")
    return {key: values[0] for key, values in parsed.items()}


def safe_error(handler: BaseHTTPRequestHandler, error: Exception) -> None:
    if isinstance(error, ResearchError):
        send_json(handler, error.status, {"error": {"code": error.code, "message": error.message}})
    else:
        send_json(
            handler,
            503,
            {"error": {"code": "service_unavailable", "message": "Research data is temporarily unavailable. Please try again."}},
            error_type=type(error).__name__,
        )


def read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    raw_length = handler.headers.get("Content-Length")
    if not isinstance(raw_length, str) or not re.fullmatch(r"[0-9]+", raw_length):
        raise ResearchError("invalid_request", "The request could not be read.")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise ResearchError("invalid_request", "The request could not be read.") from exc
    if length < 1:
        raise ResearchError("invalid_request", "The request body must not be empty.")
    if length > MAX_BODY_BYTES:
        raise ResearchError("request_too_large", "This request is larger than allowed.", 413)
    if handler.headers.get_content_type() != "application/json":
        raise ResearchError("invalid_request", "Send this request as JSON.", 415)
    try:
        raw_body = handler.rfile.read(length)
        if len(raw_body) != length:
            raise ValueError("Request body is truncated.")
        payload = json.loads(
            raw_body,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except TimeoutError as exc:
        raise ResearchError("request_timeout", "The request body did not arrive in time. Please try again.", 408) from exc
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ResearchError("invalid_request", "The request body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ResearchError("invalid_request", "The request body must be a JSON object.")
    return payload
