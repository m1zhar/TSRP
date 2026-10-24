"""Short-lived same-origin bearer-token verification for API routes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any

from api_helpers import send_json

API_TOKEN_SECRET_ENV = "TSRP_API_TOKEN_SECRET"
API_TOKEN_ISSUER = "tsrp-web"
API_TOKEN_AUDIENCE = "tsrp-api"
API_TOKEN_MAX_LIFETIME_SECONDS = 90
API_TOKEN_CLOCK_SKEW_SECONDS = 30
_TOKEN_PART_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_SUBJECT_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def api_token_secret() -> bytes | None:
    raw = os.environ.get(API_TOKEN_SECRET_ENV, "")
    if raw != raw.strip():
        return None
    value = raw.encode("utf-8")
    if len(value) < 32 or len(set(value)) < 12:
        return None
    if any(len(value) % size == 0 and value == value[:size] * (len(value) // size) for size in range(1, 17)):
        return None
    return value


def _decode_part(value: str) -> bytes:
    if not _TOKEN_PART_PATTERN.fullmatch(value):
        raise ValueError("Invalid token encoding.")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _json_object(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid token claims.")
    return value


def verify_api_access_token(value: Any, *, now: float | None = None) -> bool:
    secret = api_token_secret()
    if secret is None or not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        header_text, claims_text, signature_text = value.split(".")
        signed = f"{header_text}.{claims_text}".encode("ascii")
        supplied = _decode_part(signature_text)
        expected = hmac.new(secret, signed, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, supplied):
            return False
        header = _json_object(_decode_part(header_text))
        claims = _json_object(_decode_part(claims_text))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return False
    if header != {"alg": "HS256", "typ": "JWT"} or set(claims) != {"iss", "aud", "sub", "iat", "exp"}:
        return False
    issued_at, expires_at = claims.get("iat"), claims.get("exp")
    if (claims.get("iss") != API_TOKEN_ISSUER or claims.get("aud") != API_TOKEN_AUDIENCE
        or not isinstance(claims.get("sub"), str) or not _SUBJECT_PATTERN.fullmatch(claims["sub"])
        or isinstance(issued_at, bool) or not isinstance(issued_at, int)
        or isinstance(expires_at, bool) or not isinstance(expires_at, int)
        or expires_at <= issued_at or expires_at - issued_at > API_TOKEN_MAX_LIFETIME_SECONDS):
        return False
    current = time.time() if now is None else now
    return (issued_at <= current + API_TOKEN_CLOCK_SKEW_SECONDS and expires_at > current
            and expires_at - current <= API_TOKEN_MAX_LIFETIME_SECONDS + API_TOKEN_CLOCK_SKEW_SECONDS)


def require_api_access(handler: Any) -> bool:
    if api_token_secret() is None:
        send_json(handler, 503, {"error": {"code": "auth_not_configured", "message": "Secure API access is not configured for this deployment."}})
        return False
    authorization = handler.headers.get("Authorization", "")
    match = re.fullmatch(r"(?i:Bearer) +([A-Za-z0-9._~-]+)", authorization.strip(" \t")) if isinstance(authorization, str) else None
    if not verify_api_access_token(match.group(1) if match else None):
        send_json(handler, 401, {"error": {"code": "authentication_required", "message": "A valid TSRP research request token is required."}}, bearer_challenge=True)
        return False
    return True
