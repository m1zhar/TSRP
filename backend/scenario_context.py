"""Short signed contexts prevent browser edits from changing scenario base facts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
from typing import Any


_VERSION = "v3"
_MAX_TOKEN_LENGTH = 4096
_FIELDS = frozenset({
    "ticker", "sector", "reporting_currency", "revenue", "revenue_period",
    "revenue_currency", "market_cap_reporting", "enterprise_value", "market_at",
    "cash", "cash_period", "cash_currency", "debt", "debt_period", "debt_currency",
    "starting_fcf_margin",
    "other_claims",
    "methodology_version", "analysis_schema_version",
})


def _secret() -> bytes | None:
    raw = os.environ.get("TSRP_SCENARIO_SIGNING_SECRET", "")
    if raw != raw.strip():
        return None
    value = raw.encode("utf-8")
    if len(value) < 32 or len(set(value)) < 12:
        return None
    for block_size in range(1, 17):
        if len(value) % block_size == 0 and value == value[:block_size] * (len(value) // block_size):
            return None
    return value


def scenario_context_key_available() -> bool:
    return _secret() is not None


def _source_meta(company: dict[str, Any], key: str) -> dict[str, Any]:
    sources = company.get("sources")
    value = sources.get(key) if isinstance(sources, dict) else None
    return value if isinstance(value, dict) else {}


def _finite_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and abs(number) <= 10 else None


def _basis_from_company(company: Any) -> dict[str, Any] | None:
    if not isinstance(company, dict):
        return None
    revenue_source = _source_meta(company, "revenue")
    cash_source = _source_meta(company, "cash")
    debt_source = _source_meta(company, "debt")
    return {
        "ticker": company.get("ticker"),
        "sector": company.get("sector"),
        "reporting_currency": company.get("reporting_currency"),
        "revenue": company.get("revenue"),
        "revenue_period": company.get("revenue_period") or revenue_source.get("period"),
        "revenue_currency": revenue_source.get("currency"),
        "market_cap_reporting": company.get("market_cap_reporting"),
        "enterprise_value": company.get("enterprise_value"),
        "other_claims": company.get("other_claims"),
        "market_at": company.get("market_at"),
        "cash": company.get("cash"),
        "cash_period": cash_source.get("period"),
        "cash_currency": cash_source.get("currency"),
        "debt": company.get("debt"),
        "debt_period": debt_source.get("period"),
        "debt_currency": debt_source.get("currency"),
        # Reverse DCF fades from the latest positive reported margin to the
        # modeled margin. Sign that starting input so scenario defaults match.
        "starting_fcf_margin": _finite_or_none(company.get("unlevered_fcf_margin")),
        "methodology_version": company.get("methodology_version"),
        "analysis_schema_version": company.get("analysis_schema_version"),
    }


def issue_scenario_context(company: Any) -> str | None:
    """Sign analysis facts without exposing the signing key to the browser."""
    secret = _secret()
    basis = _basis_from_company(company)
    if secret is None or basis is None:
        return None
    try:
        payload = json.dumps(basis, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signed = f"{_VERSION}.{encoded}"
    signature = hmac.new(secret, signed.encode("ascii"), hashlib.sha256).digest()
    signature_text = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{signed}.{signature_text}"


def verify_scenario_context(token: Any, ticker: Any) -> dict[str, Any] | None:
    """Return signed base facts only when the token is intact and ticker-bound."""
    secret = _secret()
    if secret is None or not isinstance(token, str) or len(token) > _MAX_TOKEN_LENGTH:
        return None
    try:
        version, encoded, supplied_text = token.split(".")
        if version != _VERSION or not encoded or not supplied_text:
            return None
        signed = f"{version}.{encoded}".encode("ascii")
        supplied_signature = base64.b64decode(
            supplied_text + "=" * (-len(supplied_text) % 4), altchars=b"-_", validate=True
        )
        expected_signature = hmac.new(secret, signed, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_signature, supplied_signature):
            return None
        raw_payload = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        basis = json.loads(raw_payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return None
    if not isinstance(basis, dict) or basis.keys() != _FIELDS or basis.get("ticker") != ticker:
        return None
    return basis
