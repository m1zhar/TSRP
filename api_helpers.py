"""Shared, low-detail HTTP helpers for the small Vercel Python API."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler
from typing import Any

from research_service import ResearchError


MAX_BODY_BYTES = 16_384
LOGGER = logging.getLogger("tsrp")


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


def send_json(handler: BaseHTTPRequestHandler, status: int, value: Any, *, cache: str = "no-store") -> None:
    body = json.dumps(value, allow_nan=False, separators=(",", ":"), default=_json_default).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", cache)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def safe_error(handler: BaseHTTPRequestHandler, error: Exception) -> None:
    if isinstance(error, ResearchError):
        LOGGER.warning("request_rejected code=%s", error.code)
        send_json(handler, error.status, {"error": {"code": error.code, "message": error.message}})
    else:
        LOGGER.error("request_failed exception_type=%s", type(error).__name__)
        send_json(handler, 503, {"error": {"code": "service_unavailable", "message": "Research data is temporarily unavailable. Please try again."}})


def read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise ResearchError("invalid_request", "The request could not be read.") from exc
    if length < 1 or length > MAX_BODY_BYTES:
        raise ResearchError("request_too_large", "This request is larger than allowed.", 413)
    if handler.headers.get_content_type() != "application/json":
        raise ResearchError("invalid_request", "Send assumptions as JSON.", 415)
    try:
        payload = json.loads(handler.rfile.read(length))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ResearchError("invalid_request", "The request body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ResearchError("invalid_request", "The request body must be a JSON object.")
    return payload
