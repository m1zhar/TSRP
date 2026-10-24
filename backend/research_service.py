"""Yahoo/SEC adapters and the single server-side TSRP analysis pipeline."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
from threading import BoundedSemaphore, Lock, local
import time
import unicodedata
from typing import Any
from urllib.parse import unquote

import pandas as pd
from curl_cffi.requests import Session as CurlSession
from curl_cffi.requests.exceptions import (
    HTTPError as CurlHTTPError,
    RequestException as CurlRequestException,
    Timeout as CurlTimeout,
)
import yfinance as yf

from engine import (
    CURRENCY_PROFILES,
    business_quality_detail,
    clamp,
    choose_model_fcf_margin,
    currency_profile_is_supported,
    dcf_enterprise_value,
    financial_strength_detail,
    fixed_quote_currency_conversion_rate,
    history_cagr_periods,
    model_rates,
    quote_currency_major_unit,
    score_label,
    snapshot_diff,
    solve_supported_required_growth,
    supported_score_breakdown,
)
from financial_data import (
    MAX_STATEMENT_AGE_DAYS,
    compute_fcf_detail,
    finite_product,
    finite_quotient,
    latest_period_aligned_fcf_detail,
    period_observations,
    period_observations_for_aliases,
    safe_number,
    statement_period,
    trailing_aligned_fcf_margins,
)
from methodology import get_sector_model, has_sector_calibration, normalize_sector_label
from provider_utils import get_ticker_info, provider_ticker_matches


MAX_TICKER_LENGTH = 15
MAX_SEARCH_LENGTH = 80
MARKET_TICKER_LIMIT = 7
# Keep live quote freshness consistent with the company header's stale-data rule.
MAX_MARKET_QUOTE_AGE_SECONDS = 48 * 60 * 60
MAX_MARKET_TIMESTAMP_FUTURE_SKEW_SECONDS = 5 * 60
# Converted market value may only use FX observed no more than one day before
# the market quote; a separately fresh FX quote can still be the wrong period.
MAX_MARKET_FX_ALIGNMENT_SECONDS = 24 * 60 * 60
# Cross rates are only comparable when their underlying USD legs were observed
# within one day of one another; calendar-date comparison can hide a 48-hour gap.
MAX_FX_CROSS_OBSERVATION_GAP_SECONDS = 24 * 60 * 60
# Bound external financial inputs so later arithmetic cannot approach float overflow.
MAX_SCENARIO_FINANCIAL_AMOUNT = 1e16
# Historical fallback market_at values are session dates, not exact instants;
# the browser compares them by UTC calendar date with the fresh retrieval time.
MAX_TOP_STOCK_FALLBACK_AGE_DAYS = 2
MAX_YAHOO_REQUEST_TIMEOUT_SECONDS = 8.0
MAX_YAHOO_RESPONSE_BYTES = 8 * 1024 * 1024
YAHOO_ACCESS_ENABLED_ENV = "TSRP_YAHOO_FINANCE_ENABLED"
MAX_SEC_TICKER_MAP_BYTES = 2 * 1024 * 1024
MAX_SEC_COMPANY_FACTS_BYTES = 8 * 1024 * 1024
FORECAST_YEARS = 10
METHODOLOGY_VERSION = "score-v9"
ANALYSIS_SCHEMA_VERSION = "analysis-v4"
SEC_TAGS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "assets": ("Assets",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    # Do not treat the combined cash/restricted-cash aggregate as cash
    # available to offset enterprise value.
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "debt_current": ("DebtCurrent", "LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
    "debt_long": ("LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "CashProvidedByUsedInOperatingActivities"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements", "PurchaseOfPropertyPlantAndEquipment"),
}
SEC_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SEC_ENTITY_SUFFIXES = {
    "ag", "co", "company", "corp", "corporation", "inc", "incorporated",
    "limited", "llc", "lp", "ltd", "plc", "sa", "se", "the",
}
TICKER_RE = re.compile(r"^[A-Z0-9]+(?:[.\-][A-Z0-9]+)*$")
EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PERSONAL_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\+?\d[\d\s()./‐‑‒–—―−﹘﹣－-]{7,}\d(?![A-Za-z0-9])")
_PROVIDER_WORKERS = 12
_PROVIDER_TASK_CAPACITY = 24
_PROVIDER_SLOTS = BoundedSemaphore(_PROVIDER_TASK_CAPACITY)
_PROVIDER_EXECUTOR = ThreadPoolExecutor(max_workers=_PROVIDER_WORKERS, thread_name_prefix="tsrp-provider")
_PROVIDER_REQUEST_CONTEXT = local()
_MARKET_PROVIDER_WORKERS = 2
_MARKET_PROVIDER_TASK_CAPACITY = 4
_MARKET_PROVIDER_SLOTS = BoundedSemaphore(_MARKET_PROVIDER_TASK_CAPACITY)
_MARKET_PROVIDER_EXECUTOR = ThreadPoolExecutor(max_workers=_MARKET_PROVIDER_WORKERS, thread_name_prefix="tsrp-market")


def _bounded_yahoo_timeout(timeout: Any) -> float | tuple[float, float]:
    """Cap each request by both the transport ceiling and the provider task deadline."""
    cancel_event = getattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        raise TimeoutError("Provider task cancelled by disconnected client")
    request_limit = MAX_YAHOO_REQUEST_TIMEOUT_SECONDS
    deadline = getattr(_PROVIDER_REQUEST_CONTEXT, "deadline", None)
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Yahoo provider task deadline exceeded")
        request_limit = min(request_limit, remaining)
    if timeout is None:
        return request_limit
    if isinstance(timeout, tuple) and len(timeout) == 2:
        bounded = []
        for value in timeout:
            numeric = finite(value)
            bounded.append(
                request_limit
                if numeric is None or numeric <= 0
                else min(numeric, request_limit)
            )
        return bounded[0], bounded[1]
    numeric = finite(timeout)
    if numeric is None or numeric <= 0:
        return request_limit
    return min(numeric, request_limit)


def _yahoo_access_enabled() -> bool:
    return os.environ.get(YAHOO_ACCESS_ENABLED_ENV, "").strip().lower() in {"1", "true"}


def _bounded_sec_timeout(timeout: Any) -> float:
    """Apply an absolute provider-task budget to a curl-based SEC request."""
    numeric = finite(timeout)
    request_limit = numeric if numeric is not None and numeric > 0 else 15.0
    deadline = getattr(_PROVIDER_REQUEST_CONTEXT, "deadline", None)
    if deadline is None:
        return request_limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Provider task deadline exceeded")
    return min(request_limit, remaining)


def _sec_provider_deadline_expired() -> bool:
    deadline = getattr(_PROVIDER_REQUEST_CONTEXT, "deadline", None)
    return deadline is not None and time.monotonic() >= deadline


def _sec_get(url: str, **kwargs):
    """Pace SEC request dispatches to 8/s per process, below the 10/s ceiling."""
    global _SEC_NEXT_REQUEST_AT
    with _SEC_REQUEST_PACE_LOCK:
        now = time.monotonic()
        scheduled = max(now, _SEC_NEXT_REQUEST_AT)
        deadline = getattr(_PROVIDER_REQUEST_CONTEXT, "deadline", None)
        if deadline is not None and scheduled >= deadline:
            raise TimeoutError("SEC request pacing exceeds provider deadline")
        while now < scheduled:
            if deadline is not None and scheduled >= deadline:
                raise TimeoutError("SEC request pacing exceeds provider deadline")
            time.sleep(scheduled - now)
            now = time.monotonic()
        if deadline is not None and now >= deadline:
            raise TimeoutError("SEC request pacing exceeds provider deadline")
        kwargs["timeout"] = _bounded_sec_timeout(kwargs.get("timeout"))
        _SEC_NEXT_REQUEST_AT = now + _SEC_REQUEST_INTERVAL_SECONDS
        # Keep the lock through response headers so delayed thread wakeups cannot
        # bunch several nominally spaced requests into one burst.
        return _SEC_SESSION.get(url, **kwargs)


class _YahooResponseTooLarge(ValueError):
    """Raised when a Yahoo response exceeds the application body limit."""


class _YahooAccessDisabled(RuntimeError):
    """Raised when Yahoo access has not been explicitly enabled by the operator."""


class _BoundedYahooSession(CurlSession):
    """Keep yfinance's curl transport while bounding time and decoded body bytes."""

    def request(self, method: str, url: str, **kwargs):
        if not _yahoo_access_enabled():
            raise _YahooAccessDisabled("Yahoo Finance access is disabled by configuration")
        kwargs["timeout"] = _bounded_yahoo_timeout(kwargs.get("timeout"))
        if kwargs.get("content_callback") is not None:
            raise ValueError("Custom Yahoo response callbacks are not supported")

        body = bytearray()

        def read_bounded_chunk(chunk):
            if len(body) + len(chunk) > MAX_YAHOO_RESPONSE_BYTES:
                raise _YahooResponseTooLarge("Yahoo response exceeded the configured byte limit")
            body.extend(chunk)
            return len(chunk)

        kwargs["content_callback"] = read_bounded_chunk
        response = super().request(method, url, **kwargs)
        # curl_cffi uses the callback instead of its ordinary body buffer.
        # Restore the requests-compatible response payload for yfinance parsers.
        response.content = bytes(body)
        return response


_YAHOO_SESSION = _BoundedYahooSession(impersonate="chrome")
_SEC_SESSION = CurlSession()
_SEC_REQUEST_INTERVAL_SECONDS = 0.125
_SEC_REQUEST_PACE_LOCK = Lock()
_SEC_NEXT_REQUEST_AT = 0.0
_SEC_TICKER_MAP_TTL_SECONDS = 6 * 60 * 60
_SEC_TICKER_MAP_FAILURE_TTL_SECONDS = 30
_SEC_TICKER_MAP_LOCK = Lock()
_SEC_TICKER_MAP_CACHE: dict[str, tuple[str, str]] | None = None
_SEC_TICKER_MAP_CACHE_EXPIRES_AT = 0.0
_SEC_TICKER_MAP_FAILURE_KIND: str | None = None
_SEC_TICKER_MAP_FAILURE_EXPIRES_AT = 0.0
_SEC_FACTS_LOCK = Lock()
_SEC_FACTS_INFLIGHT: dict[tuple[str, str], Future] = {}


class ResearchError(Exception):
    """Safe, user-facing provider or validation error."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _require_yahoo_access() -> None:
    if not _yahoo_access_enabled():
        raise ResearchError(
            "provider_disabled",
            "Market-data access is not enabled for this deployment.",
            503,
        )


class _SecTickerMapCachedFailure(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class _SecResponseTooLarge(Exception):
    """Raised when an SEC JSON document exceeds its decompressed byte budget."""


def _unique_sec_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate keys instead of silently selecting one SEC value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate SEC JSON object key")
        result[key] = value
    return result


def _reject_sec_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported SEC JSON constant: {value}")


def _finite_sec_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite SEC JSON number")
    return number


def _read_sec_json(response: Any, max_bytes: int) -> Any:
    """Decode a streamed SEC response without buffering an unbounded body."""
    content_length = response.headers.get("Content-Length")
    try:
        declared_size = int(content_length) if content_length is not None else None
    except (TypeError, ValueError):
        declared_size = None
    if declared_size is not None and declared_size > max_bytes:
        raise _SecResponseTooLarge

    body = bytearray()
    for chunk in response.iter_content():
        if _sec_provider_deadline_expired():
            raise TimeoutError("SEC response exceeded the provider deadline")
        if not isinstance(chunk, (bytes, bytearray)):
            raise ValueError("Invalid SEC response body")
        if len(body) + len(chunk) > max_bytes:
            raise _SecResponseTooLarge
        body.extend(chunk)
    if _sec_provider_deadline_expired():
        raise TimeoutError("SEC response exceeded the provider deadline")
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_sec_json_object,
            parse_constant=_reject_sec_json_constant,
            parse_float=_finite_sec_json_float,
        )
        if _sec_provider_deadline_expired():
            raise TimeoutError("SEC response decoding exceeded the provider deadline")
        return payload
    except json.JSONDecodeError as exc:
        if _sec_provider_deadline_expired():
            raise TimeoutError("SEC response ended after the provider deadline") from exc
        raise
    except RecursionError as exc:
        if _sec_provider_deadline_expired():
            raise TimeoutError("SEC response ended after the provider deadline") from exc
        raise ValueError("SEC JSON nesting exceeds the supported depth") from exc


def finite(value: Any) -> float | None:
    return safe_number(value)


def _snapshot_proof_key() -> bytes | None:
    # Derive a separate signing key from the existing server-only API secret.
    from api_auth import api_token_secret

    secret = api_token_secret()
    return hmac.new(secret, b"tsrp-snapshot-proof-v1", hashlib.sha256).digest() if secret else None


def _comparison_source(value: Any) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("source"), str):
        return None
    return {key: value.get(key) for key in ("source", "period", "currency", "kind")}


def _snapshot_proof_claims(value: dict) -> dict:
    return {
        "ticker": value.get("ticker"),
        "methodology_version": value.get("methodology_version"),
        "analysis_schema_version": value.get("analysis_schema_version"),
        "sector": value.get("sector"),
        "reporting_currency": value.get("reporting_currency"),
        "retrieved_at": value.get("retrieved_at"),
        "score": value.get("score"),
        "business_quality": value.get("business_quality"),
        "financial_strength": value.get("financial_strength"),
        "score_trace": value.get("score_trace"),
        "market_expectations": value.get("market_expectations"),
        "historical_growth": value.get("historical_growth"),
        "consensus_growth": value.get("consensus_growth"),
        "analyst_estimate_period": value.get("analyst_estimate_period"),
        "required_growth": value.get("required_growth"),
        "revenue": value.get("revenue"),
        "revenue_period": value.get("revenue_period"),
        "revenue_source": value.get("revenue_source"),
        "free_cash_flow": value.get("free_cash_flow"),
        "fcf_period": value.get("fcf_period"),
        "fcf_source": value.get("fcf_source"),
        "enterprise_value": value.get("enterprise_value"),
        "model_fcf_margin": value.get("model_fcf_margin"),
        "discount_rate": value.get("discount_rate"),
        "terminal_growth": value.get("terminal_growth"),
        "solver_status": value.get("solver_status"),
        "sources": {
            "revenue": _comparison_source(value.get("sources", {}).get("revenue") if isinstance(value.get("sources"), dict) else None),
            "free_cash_flow": _comparison_source(value.get("sources", {}).get("free_cash_flow") if isinstance(value.get("sources"), dict) else None),
        },
    }


def _issue_snapshot_proof(value: dict) -> str | None:
    key = _snapshot_proof_key()
    if key is None:
        return None
    try:
        payload = json.dumps(
            _snapshot_proof_claims(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    encode = lambda part: base64.urlsafe_b64encode(part).decode("ascii").rstrip("=")
    return f"{encode(payload)}.{encode(signature)}"


def _verified_snapshot_claims(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    key = _snapshot_proof_key()
    proof = value.get("snapshot_proof")
    if key is None or not isinstance(proof, str) or len(proof) > 12_000:
        return None
    try:
        payload_part, signature_part = proof.split(".")
        decode = lambda part: base64.b64decode(
            part + "=" * (-len(part) % 4), altchars=b"-_", validate=True
        )
        payload = decode(payload_part)
        signature = decode(signature_part)
        if len(signature) != hashlib.sha256().digest_size:
            return None
        if not hmac.compare_digest(signature, hmac.new(key, payload, hashlib.sha256).digest()):
            return None
        claims = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        return None
    expected_keys = {
        "ticker", "methodology_version", "analysis_schema_version", "sector",
        "reporting_currency", "retrieved_at", "score", "business_quality",
        "financial_strength", "score_trace", "market_expectations", "historical_growth",
        "consensus_growth", "analyst_estimate_period", "required_growth", "revenue",
        "revenue_period", "revenue_source", "free_cash_flow", "fcf_period", "fcf_source",
        "enterprise_value", "model_fcf_margin", "discount_rate", "terminal_growth",
        "solver_status", "sources",
    }
    if not isinstance(claims, dict) or set(claims) != expected_keys:
        return None
    if any(
        value.get(field) != claims.get(field)
        for field in (
            "ticker", "methodology_version", "analysis_schema_version", "sector",
            "reporting_currency", "retrieved_at",
        )
    ):
        return None
    # Use only server-signed research values below; never compare editable
    # browser fields as if they were sourced facts. User assumptions and saved_at
    # intentionally remain outside the signature.
    return {**value, **claims}


def _chronological_history(history: Any) -> pd.DataFrame:
    """Keep valid dated bars in chronological order before selecting a latest quote."""
    if not isinstance(history, pd.DataFrame) or history.empty:
        return pd.DataFrame()
    if not isinstance(history.index, pd.DatetimeIndex) and not all(
        isinstance(value, (str, date, datetime)) for value in history.index
    ):
        return pd.DataFrame(columns=history.columns)
    try:
        parsed = pd.to_datetime(history.index, errors="coerce", utc=True)
        valid = ~parsed.isna()
        if not valid.any():
            return pd.DataFrame(columns=history.columns)
        positions = [int(position) for position in range(len(parsed)) if valid[position]]
        positions.sort(key=lambda position: (parsed[position].value, position))
        # Sort on normalized UTC instants, but retain the exchange-local index:
        # converting midnight bars to UTC can change their calendar trading date.
        ordered = history.iloc[positions].copy()
        ordered.index = history.index.take(positions)
        return ordered
    except (TypeError, ValueError, OverflowError):
        return pd.DataFrame(columns=history.columns)


def _market_history_not_in_future(history: Any, now: datetime | None = None) -> pd.DataFrame:
    """Exclude provider bars whose observation time is ahead of the server clock."""
    ordered = _chronological_history(history)
    if ordered.empty:
        return ordered
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = pd.Timestamp(current.astimezone(timezone.utc)) + pd.Timedelta(
        MAX_MARKET_TIMESTAMP_FUTURE_SKEW_SECONDS, unit="s"
    )
    valid_positions = []
    for position, value in enumerate(ordered.index):
        try:
            stamp = pd.Timestamp(value)
            stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        except (TypeError, ValueError, OverflowError):
            continue
        if stamp <= cutoff:
            valid_positions.append(position)
    return ordered.iloc[valid_positions]


def _market_timestamp_is_not_in_future(value: Any, now: datetime | None = None) -> bool:
    """Reject quote timestamps materially ahead of the server clock."""
    stamp = finite(value)
    if stamp is None or stamp <= 0:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    try:
        observed_at = datetime.fromtimestamp(stamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return False
    return (observed_at - current.astimezone(timezone.utc)).total_seconds() <= MAX_MARKET_TIMESTAMP_FUTURE_SKEW_SECONDS


def _chart_price_history(history: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Return sorted, unique daily chart points; omit ambiguous duplicate sessions."""
    ordered = _market_history_not_in_future(history, now)
    if ordered.empty or "Close" not in ordered.columns:
        return []
    by_date: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for index, row in ordered.tail(1300).iterrows():
        close = finite(row.get("Close"))
        if close is None or close <= 0:
            continue
        try:
            session_date = pd.Timestamp(index).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            continue
        if session_date in by_date:
            duplicates.add(session_date)
            continue
        volume = finite(row.get("Volume"))
        by_date[session_date] = {
            "date": session_date,
            "close": close,
            "volume": volume if volume is None or volume >= 0 else None,
        }
    return [by_date[session_date] for session_date in sorted(by_date) if session_date not in duplicates]


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _contains_personal_search_data(value: str) -> bool:
    decoded = value
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    normalized = unicodedata.normalize("NFKC", decoded)
    normalized = normalized.replace("\u3002", ".").replace("\uff61", ".")
    normalized = "".join(character for character in normalized if unicodedata.category(character) != "Cf")
    return bool(EMAIL_RE.search(normalized)) or any(
        9 <= sum(character.isdigit() for character in match.group()) <= 19
        for match in PERSONAL_NUMBER_RE.finditer(normalized)
    )


def validate_ticker(value: Any) -> str:
    if not isinstance(value, str):
        raise ResearchError("invalid_ticker", "Enter a valid listed-company ticker.")
    symbol = normalize_ticker(value)
    if _contains_personal_search_data(symbol):
        raise ResearchError(
            "personal_data_not_allowed",
            "Email addresses and phone or long-number text cannot be used for company research.",
        )
    if not symbol or len(symbol) > MAX_TICKER_LENGTH or not TICKER_RE.fullmatch(symbol):
        raise ResearchError("invalid_ticker", "Enter a valid listed-company ticker.")
    if symbol.startswith("^") or "=" in symbol or symbol.endswith(("-USD", "-EUR", "-GBP", "-BTC", "-ETH")):
        raise ResearchError("unsupported_instrument", "TSRP analyzes listed company stocks only.")
    return symbol


def validate_search(value: Any) -> str:
    query = " ".join(str(value or "").split())
    if len(query) > MAX_SEARCH_LENGTH:
        raise ResearchError("query_too_long", f"Search is limited to {MAX_SEARCH_LENGTH} characters.")
    if _contains_personal_search_data(query):
        raise ResearchError(
            "personal_data_not_allowed",
            "Email addresses and phone or long-number text cannot be used in company search.",
        )
    return query


def _first_number(source: Any, *keys: str) -> float | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = finite(source.get(key))
        if value is not None:
            return value
    return None


def _row_period(frame: pd.DataFrame | None, names: tuple[str, ...] | list[str]) -> tuple[float | None, str | None]:
    observations = period_observations(frame, list(names))
    if not observations:
        return None, None
    period, value = observations[0]
    return value, str(period)[:10]


def _yahoo_total_debt_detail(frame: pd.DataFrame | None) -> tuple[float | None, str | None, str, list[tuple[str, float]]]:
    """Use a reported total or sum same-period current and long-term debt.

    Long-term debt alone is not a defensible proxy for total debt. If Yahoo
    provides only one side of the balance, leave total debt unavailable.
    """
    candidates: list[tuple[str, int, float, str, list[tuple[str, float]]]] = []
    for period, value in period_observations_for_aliases(frame, ["Total Debt"]):
        if value >= 0:
            candidates.append((str(period)[:10], 1, value, "reported", []))

    current_aliases = ["Current Debt", "Current Debt And Capital Lease Obligation", "Current Portion Of Long Term Debt"]
    long_term_aliases = ["Long Term Debt And Capital Lease Obligation", "Long Term Debt"]
    current_by_period = dict(period_observations_for_aliases(frame, current_aliases))
    long_term_by_period = dict(period_observations_for_aliases(frame, long_term_aliases))

    def matched_label(aliases: list[str], period: str, value: float) -> str:
        for alias in aliases:
            if (period, value) in period_observations(frame, [alias]):
                return alias
        return aliases[0]

    for period in current_by_period.keys() & long_term_by_period.keys():
        current = current_by_period[period]
        long_term = long_term_by_period[period]
        total = finite(current + long_term)
        if current < 0 or long_term < 0 or total is None:
            continue
        components = [
            (matched_label(current_aliases, period, current), current),
            (matched_label(long_term_aliases, period, long_term), long_term),
        ]
        candidates.append((str(period)[:10], 0, total, "calculated: Yahoo Finance current debt + long-term debt", components))

    if not candidates:
        return None, None, "unavailable: Yahoo Finance total debt or aligned components missing", []
    period, _priority, value, kind, components = max(candidates, key=lambda item: (item[0], item[1]))
    return value, period, kind, components


def _fx_rate(source: str, target: str) -> float | None:
    detail = _fx_rate_detail(source, target)
    rate = finite(detail.get("rate")) if isinstance(detail, dict) else None
    return rate if rate is not None and rate > 0 else None


def _rate_to_usd_detail(currency: str, deadline: float | None = None) -> dict | None:
    currency = currency.strip().upper()
    if currency not in CURRENCY_PROFILES:
        return None
    if currency == "USD":
        return {"rate": 1.0, "pair": "USD", "period": None, "direction": "identity"}
    for symbol, direct in ((f"{currency}USD=X", True), (f"USD{currency}=X", False)):
        try:
            timeout = 8
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0.05:
                    break
                timeout = min(timeout, remaining)
            provider_results, provider_unavailable = _bounded_provider_calls(
                {"fx": lambda: yf.Ticker(symbol, session=_YAHOO_SESSION).history(period="5d", timeout=timeout)},
                timeout_seconds=timeout,
            )
            if provider_unavailable:
                continue
            history = _chronological_history(provider_results.get("fx"))
            if history is None or history.empty or "Close" not in history:
                continue
            closes = history["Close"].dropna()
            if closes.empty:
                continue
            stamp = pd.Timestamp(closes.index[-1])
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            age = (datetime.now(timezone.utc) - stamp.to_pydatetime()).total_seconds()
            if age > 72 * 60 * 60 or age < -5 * 60:
                continue
            quote = finite(closes.iloc[-1])
            if quote is None or quote <= 0:
                continue
            rate = quote if direct else finite_quotient(1, quote)
            if rate is None or rate <= 0:
                continue
            return {"rate": rate, "pair": symbol, "period": stamp.isoformat(), "direction": "pair quote" if direct else "inverse pair quote"}
        except Exception:
            continue
    return None


def _fx_rate_detail(
    source: str,
    target: str,
    quote_cache: dict[str, dict | None] | None = None,
    *,
    deadline: float | None = None,
) -> dict | None:
    if source.strip() in {"GBp", "GBX", "ZAc", "ILA"} or target.strip() in {"GBp", "GBX", "ZAc", "ILA"}:
        return None
    source, target = source.strip().upper(), target.strip().upper()
    if source not in CURRENCY_PROFILES or target not in CURRENCY_PROFILES:
        return None
    if source == target:
        return {"rate": 1.0, "pairs": [], "period": "Same-currency identity"}
    cache = quote_cache if quote_cache is not None else {}

    def quote(code):
        if code not in cache:
            cache[code] = _rate_to_usd_detail(code, deadline)
        return cache[code]

    from_usd = quote(source)
    to_usd = quote(target)
    if not from_usd or not to_usd or not from_usd.get("rate") or not to_usd.get("rate"):
        return None
    legs = ((source, from_usd), (target, to_usd))
    dated = []
    for currency, item in legs:
        period = item.get("period")
        if currency == "USD" and not period:
            continue
        if not period:
            return None
        try:
            stamp = pd.Timestamp(period)
            stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        except (TypeError, ValueError, OverflowError):
            return None
        dated.append((item, stamp))
    if len(dated) == 2:
        gap_seconds = abs((dated[0][1] - dated[1][1]).total_seconds())
        if gap_seconds > MAX_FX_CROSS_OBSERVATION_GAP_SECONDS:
            return None
    rate = finite_quotient(from_usd["rate"], to_usd["rate"])
    if rate is None or rate <= 0:
        return None
    latest_stamp = max((stamp for _, stamp in dated), default=None)
    return {
        "rate": rate,
        "pairs": [item["pair"] for item, _ in dated],
        "directions": [item["direction"] for item, _ in dated],
        "leg_periods": [stamp.isoformat() for _, stamp in dated],
        "period": latest_stamp.isoformat() if latest_stamp is not None else "Same-currency identity",
    }


def _fx_detail_aligned_to_market_timestamp(detail: Any, market_at: Any) -> bool:
    """Require every FX leg to be at or just before the market-value timestamp."""
    if not isinstance(detail, dict) or not isinstance(market_at, str):
        return False
    try:
        market_stamp = pd.Timestamp(market_at)
        if pd.isna(market_stamp) or market_stamp.tzinfo is None:
            return False
        market_stamp = market_stamp.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError):
        return False

    raw_periods = detail.get("leg_periods")
    if isinstance(raw_periods, list) and raw_periods:
        periods = raw_periods
    else:
        periods = [detail.get("period")]
    for raw_period in periods:
        if not isinstance(raw_period, str) or raw_period == "Same-currency identity":
            return False
        try:
            fx_stamp = pd.Timestamp(raw_period)
            if pd.isna(fx_stamp):
                return False
            fx_stamp = fx_stamp.tz_localize("UTC") if fx_stamp.tzinfo is None else fx_stamp.tz_convert("UTC")
            age_seconds = (market_stamp - fx_stamp).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return False
        if age_seconds < -5 * 60 or age_seconds > MAX_MARKET_FX_ALIGNMENT_SECONDS:
            return False
    return True


def _convert(value: float | None, source: str, target: str) -> float | None:
    number = finite(value)
    if number is None:
        return None
    rate = _fx_rate(source, target)
    return None if rate is None else finite_product(number, rate)


def _sec_identity() -> str | None:
    value = os.getenv("SEC_USER_AGENT", "").strip()
    if len(value) > 300 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    contacts = list(EMAIL_RE.finditer(value))
    if len(contacts) != 1:
        return None
    contact = contacts[0]
    identity = value[:contact.start()].strip(" <>")
    trailing = value[contact.end():].strip(" >")
    # SEC's published example identifies the requesting organization as well
    # as a contact. Do not treat an email address alone as a declared client.
    return value if identity and any(character.isalpha() for character in identity) and not trailing else None


def _sec_names_match(left: Any, right: Any) -> bool:
    def tokens(value: Any) -> set[str]:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
        return {token for token in normalized.split() if token not in SEC_ENTITY_SUFFIXES}

    left_tokens, right_tokens = tokens(left), tokens(right)
    return bool(left_tokens and right_tokens and left_tokens == right_tokens)


def _sec_ticker_map(user_agent: str) -> dict[str, tuple[str, str]]:
    global _SEC_TICKER_MAP_CACHE, _SEC_TICKER_MAP_CACHE_EXPIRES_AT
    global _SEC_TICKER_MAP_FAILURE_KIND, _SEC_TICKER_MAP_FAILURE_EXPIRES_AT
    now = time.monotonic()
    if _SEC_TICKER_MAP_CACHE is not None and now < _SEC_TICKER_MAP_CACHE_EXPIRES_AT:
        return dict(_SEC_TICKER_MAP_CACHE)
    if _SEC_TICKER_MAP_FAILURE_KIND is not None and now < _SEC_TICKER_MAP_FAILURE_EXPIRES_AT:
        raise _SecTickerMapCachedFailure(_SEC_TICKER_MAP_FAILURE_KIND)

    # One process-local refresh at a time avoids duplicate full-map downloads
    # when multiple company analyses start together on a cold function instance.
    with _SEC_TICKER_MAP_LOCK:
        now = time.monotonic()
        if _SEC_TICKER_MAP_CACHE is not None and now < _SEC_TICKER_MAP_CACHE_EXPIRES_AT:
            return dict(_SEC_TICKER_MAP_CACHE)
        if _SEC_TICKER_MAP_FAILURE_KIND is not None and now < _SEC_TICKER_MAP_FAILURE_EXPIRES_AT:
            raise _SecTickerMapCachedFailure(_SEC_TICKER_MAP_FAILURE_KIND)

        try:
            response = _sec_get(
                "https://www.sec.gov/files/company_tickers.json",
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=10,
                stream=True,
            )
            try:
                response.raise_for_status()
                payload = _read_sec_json(response, MAX_SEC_TICKER_MAP_BYTES)
            finally:
                response.close()
            if not isinstance(payload, dict):
                raise ValueError("Invalid SEC ticker map")
            output = {}
            for row in payload.values():
                if not isinstance(row, dict):
                    raise ValueError("Invalid SEC ticker map row")
                ticker, cik, title = row.get("ticker"), row.get("cik_str"), row.get("title")
                if (
                    not isinstance(ticker, str) or not ticker or ticker != ticker.strip()
                    or type(cik) is not int or cik <= 0
                    or not isinstance(title, str) or not title.strip()
                ):
                    raise ValueError("Invalid SEC ticker map row")
                normalized_ticker = ticker.upper()
                if normalized_ticker in output:
                    raise ValueError("Duplicate SEC ticker map row")
                output[normalized_ticker] = (str(cik).zfill(10), title.strip())
            if not output:
                raise ValueError("Invalid SEC ticker map")
        except Exception as exc:
            if isinstance(exc, (CurlTimeout, TimeoutError)):
                failure_kind = "timeout"
            elif isinstance(exc, json.JSONDecodeError) and _sec_provider_deadline_expired():
                failure_kind = "timeout"
            elif isinstance(exc, _SecResponseTooLarge):
                failure_kind = "response_too_large"
            elif isinstance(exc, CurlHTTPError):
                failure_kind = "rate_limited" if getattr(exc.response, "status_code", None) == 429 else "http_error"
            elif isinstance(exc, (KeyError, TypeError, ValueError)):
                failure_kind = "parse_error"
            elif isinstance(exc, CurlRequestException):
                failure_kind = "network_error"
            else:
                failure_kind = "network_error"
            _SEC_TICKER_MAP_FAILURE_KIND = failure_kind
            _SEC_TICKER_MAP_FAILURE_EXPIRES_AT = time.monotonic() + _SEC_TICKER_MAP_FAILURE_TTL_SECONDS
            if failure_kind == "response_too_large":
                raise _SecTickerMapCachedFailure(failure_kind) from exc
            raise

        _SEC_TICKER_MAP_CACHE = output
        _SEC_TICKER_MAP_CACHE_EXPIRES_AT = time.monotonic() + _SEC_TICKER_MAP_TTL_SECONDS
        _SEC_TICKER_MAP_FAILURE_KIND = None
        _SEC_TICKER_MAP_FAILURE_EXPIRES_AT = 0.0
        return dict(output)


def _fetch_sec_company_facts(cik: str, user_agent: str) -> tuple[dict | None, dict]:
    try:
        response = _sec_get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=15,
            stream=True,
        )
        try:
            response.raise_for_status()
            facts = _read_sec_json(response, MAX_SEC_COMPANY_FACTS_BYTES)
        finally:
            response.close()
        if not isinstance(facts, dict) or not isinstance(facts.get("facts"), dict):
            return None, {"kind": "parse_error", "label": "SEC returned no usable company facts"}
        return facts, {"kind": "available", "label": "Company facts available", "cik": cik}
    except (CurlTimeout, TimeoutError):
        return None, {"kind": "timeout", "label": "SEC did not respond before the request timed out"}
    except _SecResponseTooLarge:
        return None, {"kind": "response_too_large", "label": "SEC response exceeded the safe size limit"}
    except CurlHTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        if code == 429:
            return None, {"kind": "rate_limited", "label": "SEC temporarily limited this request"}
        return None, {"kind": "http_error", "label": "SEC returned an HTTP error"}
    except json.JSONDecodeError:
        if _sec_provider_deadline_expired():
            return None, {"kind": "timeout", "label": "SEC did not respond before the request timed out"}
        return None, {"kind": "parse_error", "label": "SEC data could not be parsed"}
    except (KeyError, TypeError, ValueError):
        return None, {"kind": "parse_error", "label": "SEC data could not be parsed"}
    except CurlRequestException:
        return None, {"kind": "network_error", "label": "SEC could not be reached"}


def _coalesced_sec_company_facts(cik: str, user_agent: str) -> tuple[dict | None, dict]:
    """Share one active SEC fetch per issuer and identity; retain no completed response."""
    key = (cik, user_agent)
    with _SEC_FACTS_LOCK:
        future = _SEC_FACTS_INFLIGHT.get(key)
        is_fetcher = future is None
        if is_fetcher:
            future = Future()
            _SEC_FACTS_INFLIGHT[key] = future

    if is_fetcher:
        try:
            # Company facts are only read by analysis code. Sharing the response among
            # concurrent callers avoids copying a large SEC payload for each request.
            future.set_result(_fetch_sec_company_facts(cik, user_agent))
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            with _SEC_FACTS_LOCK:
                if _SEC_FACTS_INFLIGHT.get(key) is future:
                    del _SEC_FACTS_INFLIGHT[key]
    return future.result()


def _sec_facts(ticker: str, reporting_currency: str, trading_currency: str) -> tuple[dict | None, dict]:
    user_agent = _sec_identity()
    if not user_agent:
        return None, {"kind": "not_configured", "label": "Unavailable: add a monitored SEC contact identity"}
    try:
        mapping = _sec_ticker_map(user_agent)
        match = mapping.get(ticker)
        if not match:
            return None, {"kind": "not_covered", "label": "No SEC ticker mapping for this listing"}
        cik, company = match
        facts, state = _coalesced_sec_company_facts(cik, user_agent)
        if facts is None:
            return None, state
        if not _sec_names_match(company, facts.get("entityName")):
            return None, {"kind": "entity_mismatch", "label": "SEC ticker mapping and company-facts entity did not match"}
        return facts, {**state, "company": company}
    except (CurlTimeout, TimeoutError):
        return None, {"kind": "timeout", "label": "SEC did not respond before the request timed out"}
    except CurlHTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        if code == 429:
            return None, {"kind": "rate_limited", "label": "SEC temporarily limited this request"}
        return None, {"kind": "http_error", "label": "SEC returned an HTTP error"}
    except CurlRequestException:
        return None, {"kind": "network_error", "label": "SEC could not be reached"}
    except _SecTickerMapCachedFailure as exc:
        labels = {
            "timeout": ("timeout", "SEC did not respond before the request timed out"),
            "rate_limited": ("rate_limited", "SEC temporarily limited this request"),
            "http_error": ("http_error", "SEC returned an HTTP error"),
            "network_error": ("network_error", "SEC could not be reached"),
            "parse_error": ("parse_error", "SEC data could not be parsed"),
            "response_too_large": ("response_too_large", "SEC ticker map exceeded the safe size limit"),
        }
        kind, label = labels.get(exc.kind, labels["network_error"])
        return None, {"kind": kind, "label": label}
    except json.JSONDecodeError:
        if _sec_provider_deadline_expired():
            return None, {"kind": "timeout", "label": "SEC did not respond before the request timed out"}
        return None, {"kind": "parse_error", "label": "SEC data could not be parsed"}
    except (KeyError, TypeError, ValueError):
        return None, {"kind": "parse_error", "label": "SEC data could not be parsed"}


def _sec_candidates(
    facts: dict | None,
    tags: tuple[str, ...],
    currencies: list[str],
    *,
    kind: str = "duration",
    allowed_units: set[str] | None = None,
) -> tuple[list[dict], str | None, dict | None]:
    if not facts:
        return [], None, None
    analysis_date = datetime.now(timezone.utc).date()
    roots = facts.get("facts", {})
    taxonomies = []
    for name in ("us-gaap", "ifrs-full"):
        if isinstance(roots.get(name), dict):
            taxonomies.append((name, roots[name]))
    taxonomies.extend((name, block) for name, block in roots.items() if name not in {"us-gaap", "ifrs-full"} and isinstance(block, dict))
    candidates = []
    for taxonomy, block in taxonomies:
        for tag in tags:
            item = block.get(tag)
            if not isinstance(item, dict):
                continue
            units = item.get("units", {})
            unit_order = [
                unit for unit in currencies
                if unit in units and unit != "XXX" and (allowed_units is None or unit in allowed_units)
            ]
            if allowed_units is None:
                unit_order += [
                    unit for unit in units
                    if unit != "XXX" and re.fullmatch(r"[A-Z]{3}", str(unit)) and unit not in unit_order
                ]
            # Keep each unit's history separate; the global recency rank below
            # chooses the latest comparable series, preserving this order on ties.
            for unit in unit_order:
                annual = []
                for row in units.get(unit, []):
                    if not isinstance(row, dict) or row.get("form") not in SEC_FORMS or not row.get("end") or not row.get("filed") or finite(row.get("val")) is None:
                        continue
                    end_period = statement_period(row.get("end"))
                    filed_period = statement_period(row.get("filed"))
                    start_period = statement_period(row.get("start")) if row.get("start") else None
                    if end_period is None or filed_period is None or (row.get("start") and start_period is None):
                        continue
                    end = pd.Timestamp(end_period).date()
                    filed = pd.Timestamp(filed_period).date()
                    start = pd.Timestamp(start_period).date() if start_period else None
                    if filed < end or filed > analysis_date:
                        continue
                    if kind == "duration":
                        if start is None or not 330 <= (end - start).days <= 400:
                            continue
                    elif kind == "instant":
                        if start is not None:
                            continue
                    else:
                        raise ValueError("SEC fact kind must be duration or instant")
                    annual.append(row)
                if not annual:
                    continue
                by_fy = {}
                fiscal_year_groups: dict[str, dict[tuple[str, str, str], list[dict]]] = {}
                for row in annual:
                    year = str(row.get("fy") or str(row.get("end", ""))[:4])
                    filing_rank = (
                        str(row.get("filed", "")),
                        str(row.get("accn") or row.get("accessionNumber") or ""),
                        str(row.get("end", "")),
                    )
                    fiscal_year_groups.setdefault(year, {}).setdefault(filing_rank, []).append(row)
                for year, filings in fiscal_year_groups.items():
                    newest_rank = max(filings)
                    newest_rows = filings[newest_rank]
                    # SEC can repeat one accession/period with conflicting facts.
                    # Do not let response order choose a value or fall back to an
                    # older filing for the same fiscal year when its latest one
                    # is ambiguous.
                    signatures = {
                        (statement_period(row.get("start")), finite(row.get("val")))
                        for row in newest_rows
                    }
                    if len(signatures) != 1:
                        continue
                    by_fy[year] = newest_rows[0]
                series_rows = sorted(by_fy.values(), key=lambda x: str(x.get("end", "")), reverse=True)
                if not series_rows:
                    continue
                latest = max(
                    series_rows,
                    key=lambda x: (
                        str(x.get("end", "")),
                        str(x.get("filed", "")),
                        str(x.get("accn") or x.get("accessionNumber") or ""),
                    ),
                )
                candidates.append((str(latest.get("end", "")), str(latest.get("filed", "")), {
                    "value": finite(latest.get("val")),
                    "unit": unit,
                    "period": latest.get("end"),
                    "start": latest.get("start"),
                    "filed": latest.get("filed"),
                    "form": latest.get("form"),
                    "taxonomy": taxonomy,
                    "tag": tag,
                    "accession": str(latest.get("accn") or latest.get("accessionNumber") or ""),
                    "_series_records": series_rows,
                    "series": [(str(row.get("end", "")), finite(row.get("val"))) for row in series_rows],
                    "series_details": [
                        {"start": row.get("start"), "period": row.get("end"), "value": finite(row.get("val"))}
                        for row in series_rows
                    ],
                }))
    if not candidates:
        return [], None, None

    # Tags in this list are fallbacks for the same metric, not additive
    # observations. If one filing reports conflicting values under multiple
    # fallback concepts for the same period/unit, do not let tuple order select
    # a financial input. A shared accession identifies facts from that exact
    # filing; unrelated filings on the same date do not collide.
    alias_values: dict[tuple[str, str, str, str, str, str], set[float]] = {}
    for _, _, record in candidates:
        for row in record["_series_records"]:
            accession = str(row.get("accn") or row.get("accessionNumber") or "")
            value = finite(row.get("val"))
            if accession and value is not None:
                key = (
                    record["taxonomy"], record["unit"], str(row.get("start") or ""),
                    str(row.get("end", "")), str(row.get("filed", "")), accession,
                )
                alias_values.setdefault(key, set()).add(value)
    conflicting = {key for key, values in alias_values.items() if len(values) > 1}
    if conflicting:
        filtered_candidates = []
        for _, _, record in candidates:
            rows = [
                row for row in record["_series_records"]
                if (
                    record["taxonomy"], record["unit"], str(row.get("start") or ""),
                    str(row.get("end", "")), str(row.get("filed", "")),
                    str(row.get("accn") or row.get("accessionNumber") or ""),
                ) not in conflicting
            ]
            if not rows:
                continue
            latest = max(rows, key=lambda row: (
                str(row.get("end", "")), str(row.get("filed", "")),
                str(row.get("accn") or row.get("accessionNumber") or ""),
            ))
            record.update({
                "value": finite(latest.get("val")),
                "period": latest.get("end"),
                "start": latest.get("start"),
                "filed": latest.get("filed"),
                "form": latest.get("form"),
                "accession": str(latest.get("accn") or latest.get("accessionNumber") or ""),
                "_series_records": rows,
                "series": [(str(row.get("end", "")), finite(row.get("val"))) for row in rows],
                "series_details": [
                    {"start": row.get("start"), "period": row.get("end"), "value": finite(row.get("val"))}
                    for row in rows
                ],
            })
            filtered_candidates.append((str(latest.get("end", "")), str(latest.get("filed", "")), record))
        candidates = filtered_candidates
    if not candidates:
        return [], None, None
    record = max(candidates, key=lambda row: (row[0], row[1]))[2]
    record.pop("_series_records", None)
    return record["series"], record["unit"], record


def _sec_value(facts: dict | None, tags: tuple[str, ...], currencies: list[str], *, kind: str = "duration") -> dict | None:
    _, _, record = _sec_candidates(facts, tags, currencies, kind=kind)
    return record


def _sec_revenue_currency(facts: dict | None) -> str | None:
    """Return a currency only when the newest annual SEC revenue evidence has one unambiguous unit."""
    if not facts:
        return None
    roots = facts.get("facts", {})
    units: set[str] = set()
    for block in roots.values():
        if not isinstance(block, dict):
            continue
        for tag in SEC_TAGS["revenue"]:
            item = block.get(tag)
            if not isinstance(item, dict) or not isinstance(item.get("units"), dict):
                continue
            units.update(
                unit for unit in item["units"]
                if isinstance(unit, str) and re.fullmatch(r"[A-Z]{3}", unit) and unit != "XXX"
            )

    dated_candidates = []
    for unit in units:
        _, _, record = _sec_candidates(
            facts, SEC_TAGS["revenue"], [unit], kind="duration", allowed_units={unit}
        )
        if record:
            period = statement_period(record.get("period"))
            filed = statement_period(record.get("filed"))
            if period and filed:
                dated_candidates.append((period, filed, unit))
    if not dated_candidates:
        return None
    newest = max((period, filed) for period, filed, _ in dated_candidates)
    newest_units = {unit for period, filed, unit in dated_candidates if (period, filed) == newest}
    return next(iter(newest_units)) if len(newest_units) == 1 else None


def _sec_fcf(facts: dict | None, currencies: list[str], revenue_record: dict | None) -> tuple[float | None, str | None]:
    detail = _sec_fcf_detail(facts, currencies, revenue_record)
    return detail["value"], detail["period"]


def _sec_fcf_detail(facts: dict | None, currencies: list[str], revenue_record: dict | None) -> dict:
    ocf = _sec_value(facts, SEC_TAGS["ocf"], currencies)
    capex = _sec_value(facts, SEC_TAGS["capex"], currencies)
    if not ocf or not capex or not revenue_record:
        return {"value": None, "period": None, "operating_cash_flow": ocf, "capital_spending": capex}
    same_period = (ocf.get("period"), ocf.get("start"), ocf.get("unit")) == (
        capex.get("period"), capex.get("start"), capex.get("unit")
    ) == (revenue_record.get("period"), revenue_record.get("start"), revenue_record.get("unit"))
    if not same_period:
        return {"value": None, "period": None, "operating_cash_flow": ocf, "capital_spending": capex}
    cashflow = finite(ocf.get("value"))
    spending = finite(capex.get("value"))
    if cashflow is None or spending is None:
        return {"value": None, "period": None, "operating_cash_flow": ocf, "capital_spending": capex}
    fcf, _ = compute_fcf_detail(cashflow, spending)
    if fcf is None:
        return {"value": None, "period": None, "operating_cash_flow": ocf, "capital_spending": capex}
    return {
        "value": fcf,
        "period": str(ocf.get("period")),
        "operating_cash_flow": ocf,
        "capital_spending": capex,
    }


def _sec_fcf_margin_history(
    facts: dict | None,
    revenue_record: dict | None,
    *,
    years: int = 4,
) -> list[float]:
    """Build annual SEC FCF margins only from exactly aligned same-unit facts."""
    unit = revenue_record.get("unit") if isinstance(revenue_record, dict) else None
    revenue_series = revenue_record.get("series_details") if isinstance(revenue_record, dict) else None
    if not facts or not isinstance(unit, str) or not unit or not isinstance(revenue_series, list) or years < 1:
        return []

    _, _, operating_record = _sec_candidates(
        facts, SEC_TAGS["ocf"], [unit], kind="duration", allowed_units={unit}
    )
    _, _, capex_record = _sec_candidates(
        facts, SEC_TAGS["capex"], [unit], kind="duration", allowed_units={unit}
    )
    if not operating_record or not capex_record:
        return []

    def period_values(record: dict) -> dict[tuple[str, str], float]:
        output: dict[tuple[str, str], float] = {}
        details = record.get("series_details")
        if not isinstance(details, list):
            return output
        for observation in details:
            if not isinstance(observation, dict):
                continue
            start = statement_period(observation.get("start"))
            period = statement_period(observation.get("period"))
            value = finite(observation.get("value"))
            if start and period and value is not None:
                output[(start, period)] = value
        return output

    operating_by_period = period_values(operating_record)
    capex_by_period = period_values(capex_record)
    margins = []
    seen_periods: set[tuple[str, str]] = set()
    for observation in revenue_series:
        if not isinstance(observation, dict):
            continue
        start = statement_period(observation.get("start"))
        period = statement_period(observation.get("period"))
        revenue = finite(observation.get("value"))
        key = (start, period) if start and period else None
        if key is None or key in seen_periods or revenue is None or revenue <= 0:
            continue
        seen_periods.add(key)
        operating = operating_by_period.get(key)
        capex = capex_by_period.get(key)
        if operating is None or capex is None:
            continue
        fcf, _ = compute_fcf_detail(operating, capex)
        margin = finite_quotient(fcf, revenue)
        if margin is not None:
            margins.append(margin)
            if len(margins) >= years:
                break
    return margins


def _run_provider_call(call, deadline: float, cancel_event=None):
    had_previous_deadline = hasattr(_PROVIDER_REQUEST_CONTEXT, "deadline")
    previous_deadline = getattr(_PROVIDER_REQUEST_CONTEXT, "deadline", None)
    had_previous_cancel_event = hasattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event")
    previous_cancel_event = getattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event", None)
    _PROVIDER_REQUEST_CONTEXT.deadline = deadline
    _PROVIDER_REQUEST_CONTEXT.cancel_event = cancel_event
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise TimeoutError("Provider task cancelled by disconnected client")
        return call()
    finally:
        if had_previous_deadline:
            _PROVIDER_REQUEST_CONTEXT.deadline = previous_deadline
        else:
            del _PROVIDER_REQUEST_CONTEXT.deadline
        if had_previous_cancel_event:
            _PROVIDER_REQUEST_CONTEXT.cancel_event = previous_cancel_event
        else:
            del _PROVIDER_REQUEST_CONTEXT.cancel_event
        _PROVIDER_SLOTS.release()


def _submit_provider_call(call, timeout_seconds: float = 20.0) -> Future | None:
    """Submit bounded upstream work; one runtime instance can hold at most 24 tasks."""
    cancel_event = getattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        return None
    if not _PROVIDER_SLOTS.acquire(blocking=False):
        return None
    try:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        return _PROVIDER_EXECUTOR.submit(_run_provider_call, call, deadline, cancel_event)
    except Exception:
        _PROVIDER_SLOTS.release()
        raise


def _run_market_provider_call(call, deadline: float, cancel_event=None):
    had_previous_deadline = hasattr(_PROVIDER_REQUEST_CONTEXT, "deadline")
    previous_deadline = getattr(_PROVIDER_REQUEST_CONTEXT, "deadline", None)
    had_previous_cancel_event = hasattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event")
    previous_cancel_event = getattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event", None)
    _PROVIDER_REQUEST_CONTEXT.deadline = deadline
    _PROVIDER_REQUEST_CONTEXT.cancel_event = cancel_event
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise TimeoutError("Provider task cancelled by disconnected client")
        return call()
    finally:
        if had_previous_deadline:
            _PROVIDER_REQUEST_CONTEXT.deadline = previous_deadline
        else:
            del _PROVIDER_REQUEST_CONTEXT.deadline
        if had_previous_cancel_event:
            _PROVIDER_REQUEST_CONTEXT.cancel_event = previous_cancel_event
        else:
            del _PROVIDER_REQUEST_CONTEXT.cancel_event
        _MARKET_PROVIDER_SLOTS.release()


def _submit_market_provider_call(call, timeout_seconds: float = 12.0) -> Future | None:
    """Keep slow public screeners from occupying company-research workers."""
    cancel_event = getattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        return None
    if not _MARKET_PROVIDER_SLOTS.acquire(blocking=False):
        return None
    try:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        return _MARKET_PROVIDER_EXECUTOR.submit(_run_market_provider_call, call, deadline, cancel_event)
    except Exception:
        _MARKET_PROVIDER_SLOTS.release()
        raise


def _cancel_provider_call(future: Future) -> bool:
    """Release capacity only when a queued task was canceled before its worker began."""
    cancelled = future.cancel()
    if cancelled:
        _PROVIDER_SLOTS.release()
    return cancelled


def _cancel_market_provider_call(future: Future) -> bool:
    """Release market-list capacity only when work never started."""
    cancelled = future.cancel()
    if cancelled:
        _MARKET_PROVIDER_SLOTS.release()
    return cancelled


def _request_cancel_event():
    """Return the current request's disconnect signal, if the ASGI bridge set one."""
    return getattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event", None)


def _wait_for_provider_futures(futures, timeout_seconds: float):
    """Wait within the provider budget, waking promptly when the client disconnects."""
    pending = set(futures)
    completed = set()
    cancel_event = _request_cancel_event()
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while pending:
        if cancel_event is not None and cancel_event.is_set():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        interval = min(remaining, 0.05) if cancel_event is not None else remaining
        finished, pending = wait(pending, timeout=interval)
        completed.update(finished)
        if cancel_event is None:
            break
    return completed, pending


def run_with_provider_cancellation(cancel_event, call, *args):
    """Run one synchronous route with its client-disconnect signal in worker context."""
    if cancel_event.is_set():
        raise InterruptedError("request disconnected before provider work")

    had_previous_cancel_event = hasattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event")
    previous_cancel_event = getattr(_PROVIDER_REQUEST_CONTEXT, "cancel_event", None)
    _PROVIDER_REQUEST_CONTEXT.cancel_event = cancel_event
    try:
        return call(*args)
    finally:
        if had_previous_cancel_event:
            _PROVIDER_REQUEST_CONTEXT.cancel_event = previous_cancel_event
        else:
            del _PROVIDER_REQUEST_CONTEXT.cancel_event


def _bounded_provider_calls(
    calls: dict[str, Any],
    timeout_seconds: float = 20.0,
    *,
    failures: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Run provider reads under a total wait budget and a process-wide task cap."""
    futures: dict[Future, str] = {}
    rejected: list[str] = []
    for name, call in calls.items():
        future = _submit_provider_call(call, timeout_seconds=timeout_seconds)
        if future is None:
            rejected.append(name)
        else:
            futures[future] = name
    completed, pending = _wait_for_provider_futures(tuple(futures), timeout_seconds)
    results: dict[str, Any] = {}
    for future in completed:
        name = futures[future]
        try:
            results[name] = future.result()
        except Exception:
            results[name] = None
            if failures is not None:
                failures.append(name)
    timed_out = rejected + [futures[future] for future in pending]
    for future in pending:
        _cancel_provider_call(future)
    return results, timed_out


def _fetch_info(instrument: yf.Ticker) -> dict | None:
    return get_ticker_info(instrument)


def _as_frame(value: Any) -> pd.DataFrame:
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _estimate_growth(frame: pd.DataFrame) -> tuple[float | None, str | None]:
    if frame is None or frame.empty or "growth" not in frame.columns:
        return None, None
    for period in ("+1y", "0y"):
        if period in frame.index:
            value = finite(frame.loc[period, "growth"])
            if value is not None:
                return value, "Next-year analyst revenue estimate" if period == "+1y" else "Current-year analyst revenue estimate"
    return None, None


def _quote_timestamp(info: dict, fast_info: dict, history: pd.DataFrame, now: datetime | None = None) -> str | None:
    current = now or datetime.now(timezone.utc)
    stamp = _first_number(info, "regularMarketTime")
    if stamp is not None and _market_timestamp_is_not_in_future(stamp, current):
        try:
            observed_at = datetime.fromtimestamp(stamp, tz=timezone.utc)
            return observed_at.isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    history = _market_history_not_in_future(history, current)
    if not history.empty:
        try:
            stamp = pd.Timestamp(history.index[-1])
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            return stamp.isoformat()
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _period_ratio(numerator: float | None, numerator_period: str | None, denominator: float | None, denominator_period: str | None) -> float | None:
    numerator, denominator = finite(numerator), finite(denominator)
    if numerator is None or denominator is None or denominator <= 0 or not numerator_period or numerator_period != denominator_period:
        return None
    result = numerator / denominator
    return result if math.isfinite(result) else None


def _period_age_days(period: str | None, now: datetime | None = None) -> int | None:
    if not period:
        return None
    try:
        observed = pd.Timestamp(period).date()
    except (TypeError, ValueError, OverflowError):
        return None
    current = (now or datetime.now(timezone.utc)).date()
    return (current - observed).days


def _exclude_future_statement_periods(frame: pd.DataFrame, now: datetime) -> tuple[pd.DataFrame, bool]:
    """Remove statement columns dated after the analysis date, retaining unknown labels."""
    if frame is None or frame.empty:
        return frame, False
    kept_positions = []
    found_future = False
    for position, column in enumerate(frame.columns):
        age_days = _period_age_days(str(column), now)
        if age_days is not None and age_days < 0:
            found_future = True
        else:
            kept_positions.append(position)
    return (frame.iloc[:, kept_positions] if found_future else frame), found_future


def _average_equity_roe(net_income: float | None, income_period: str | None, equity_observations: list[tuple[Any, float]]) -> float | None:
    income = finite(net_income)
    if income is None or not income_period:
        return None
    try:
        end = pd.Timestamp(income_period).date()
    except (TypeError, ValueError, OverflowError):
        return None
    dated = []
    for period, value in equity_observations:
        try:
            date = pd.Timestamp(period).date()
        except (TypeError, ValueError, OverflowError):
            continue
        amount = finite(value)
        if amount is not None:
            dated.append((date, amount))
    current = next((value for date, value in dated if date == end), None)
    prior_candidates = [(date, value) for date, value in dated if 330 <= (end - date).days <= 430]
    if current is None or not prior_candidates:
        return None
    prior = min(prior_candidates, key=lambda item: abs((end - item[0]).days - 365))[1]
    average = (current + prior) / 2
    if not math.isfinite(average) or average <= 0:
        return None
    result = income / average
    return result if math.isfinite(result) else None


def _confidence(fields: dict[str, bool], flags: list[dict]) -> str:
    ratio = sum(bool(value) for value in fields.values()) / max(len(fields), 1)
    if any(flag["severity"] == "high" for flag in flags) or ratio < 0.5:
        return "Low"
    if not fields.get("historical_growth") or ratio < 0.85:
        return "Medium"
    return "High"


def _flag(message: str, severity: str = "medium") -> dict:
    return {"message": message, "severity": severity}


def analyze_company(symbol: Any, assumptions: dict | None = None) -> dict:
    ticker_symbol = validate_ticker(symbol)
    _require_yahoo_access()
    instrument = yf.Ticker(ticker_symbol, session=_YAHOO_SESSION)
    provider_failures: list[str] = []
    provider_data, provider_timeouts = _bounded_provider_calls({
        "info": lambda: _fetch_info(instrument),
        "fast_info": lambda: dict(instrument.fast_info),
        "history": lambda: instrument.history(period="5y", auto_adjust=False, timeout=10),
        "financials": lambda: instrument.financials,
        "balance": lambda: instrument.balance_sheet,
        "cashflow": lambda: instrument.cashflow,
        "estimates": lambda: instrument.revenue_estimate,
        "sec": lambda: _sec_facts(ticker_symbol, "N/A", "N/A"),
    }, timeout_seconds=20, failures=provider_failures)
    raw_info = provider_data.get("info")
    if "info" in provider_timeouts or raw_info is None:
        raise ResearchError("provider_unavailable", "Company data is temporarily unavailable. Please retry shortly.", 503)
    info = raw_info
    analysis_now = datetime.now(timezone.utc)
    if not provider_ticker_matches(ticker_symbol, info.get("symbol")):
        raise ResearchError(
            "entity_mismatch",
            "The market-data provider returned a different company for this ticker. Verify the symbol and retry.",
            503,
        )
    quote_type = str(info.get("quoteType") or "").upper()
    if quote_type and quote_type != "EQUITY":
        raise ResearchError("unsupported_instrument", "TSRP analyzes listed company stocks only.")
    if not quote_type:
        raise ResearchError("provider_unavailable", "Yahoo Finance could not verify this company right now. Please retry shortly.", 503)

    fast_info = provider_data.get("fast_info") if isinstance(provider_data.get("fast_info"), dict) else {}
    history = _chronological_history(_as_frame(provider_data.get("history")))
    financials = _as_frame(provider_data.get("financials"))
    balance = _as_frame(provider_data.get("balance"))
    cashflow = _as_frame(provider_data.get("cashflow"))
    financials, future_income_period = _exclude_future_statement_periods(financials, analysis_now)
    balance, future_balance_period = _exclude_future_statement_periods(balance, analysis_now)
    cashflow, future_cashflow_period = _exclude_future_statement_periods(cashflow, analysis_now)
    future_statement_periods = future_income_period or future_balance_period or future_cashflow_period
    estimate_frame = _as_frame(provider_data.get("estimates"))

    raw_reporting_currency = info.get("financialCurrency")
    yahoo_statement_currency_identified = (
        isinstance(raw_reporting_currency, str)
        and bool(raw_reporting_currency.strip())
        and raw_reporting_currency.strip().upper() not in {"N/A", "NA", "NONE"}
    )
    reporting_currency_identified = yahoo_statement_currency_identified
    reporting_currency = raw_reporting_currency.strip() if yahoo_statement_currency_identified else "N/A"
    if reporting_currency not in {"GBp", "GBX", "ZAc", "ILA"}:
        reporting_currency = reporting_currency.upper()
    trading_currency = str(info.get("currency") or fast_info.get("currency") or "N/A").strip()
    sector = normalize_sector_label(info.get("sector"))
    sector_methodology_supported = has_sector_calibration(sector)
    if assumptions is not None and not sector_methodology_supported:
        raise ResearchError("unsupported_sector", "TSRP does not have a validated score and valuation method for this company’s sector.", 422)
    model = get_sector_model(sector)
    rates = model_rates(reporting_currency, sector)
    currency_supported = currency_profile_is_supported(reporting_currency)
    valuation_calibration_supported = sector_methodology_supported and currency_supported
    price = _first_number(fast_info, "lastPrice", "last_price", "regularMarketPrice", "currentPrice")
    if price is None:
        price = _first_number(info, "currentPrice", "regularMarketPrice")
    previous_close = _first_number(fast_info, "previousClose", "previous_close", "regularMarketPreviousClose")
    if previous_close is None:
        previous_close = _first_number(info, "previousClose", "regularMarketPreviousClose")
    if price is not None and price <= 0:
        price = None
    if previous_close is not None and previous_close <= 0:
        previous_close = None
    market_timestamp = _first_number(info, "regularMarketTime")
    market_timestamp_valid = market_timestamp is not None and _market_timestamp_is_not_in_future(market_timestamp, analysis_now)
    if market_timestamp is not None and not market_timestamp_valid:
        # A future quote time invalidates its accompanying quote values. Fall
        # back only to dated history that independently passes time validation.
        price = None
        previous_close = None
    if not market_timestamp_valid and history is not None and not history.empty and "Close" in history:
        valid_history = _market_history_not_in_future(history, analysis_now)
        closes = valid_history["Close"].dropna()
        if len(closes) >= 2:
            price = finite(closes.iloc[-1])
            previous_close = finite(closes.iloc[-2])
            if price is not None and price <= 0:
                price = None
            if previous_close is not None and previous_close <= 0:
                previous_close = None
    market_cap = _first_number(fast_info, "marketCap", "market_cap")
    if market_cap is None:
        market_cap = _first_number(info, "marketCap")
    if market_cap is not None and market_cap <= 0:
        market_cap = None

    yahoo_revenue_observations = period_observations_for_aliases(financials, ["Total Revenue", "Operating Revenue"])
    yahoo_revenue, yahoo_revenue_period = (yahoo_revenue_observations[0][1], str(yahoo_revenue_observations[0][0])[:10]) if yahoo_revenue_observations else (None, None)
    yahoo_gross, yahoo_gross_period = _row_period(financials, ("Gross Profit",))
    yahoo_operating_income, yahoo_operating_period = _row_period(financials, ("Operating Income",))
    yahoo_net_income, yahoo_net_income_period = _row_period(financials, ("Net Income", "Net Income Common Stockholders"))
    yahoo_ebitda, yahoo_ebitda_period = _row_period(financials, ("EBITDA", "Normalized EBITDA"))
    yahoo_fcf, yahoo_fcf_period, yahoo_fcf_method = latest_period_aligned_fcf_detail(cashflow, financials, ["Total Revenue", "Operating Revenue"])
    yahoo_cash, yahoo_cash_period = _row_period(balance, ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    yahoo_debt, yahoo_debt_period, yahoo_debt_kind, yahoo_debt_components = _yahoo_total_debt_detail(balance)
    yahoo_assets, yahoo_assets_period = _row_period(balance, ("Total Assets",))
    yahoo_equity, yahoo_equity_period = _row_period(balance, ("Stockholders Equity", "Total Equity Gross Minority Interest"))
    if not yahoo_statement_currency_identified:
        # Yahoo's statement frames do not retain currency metadata per row.
        # A listing/quote currency is not evidence of statement currency.
        yahoo_revenue_observations = []
        yahoo_revenue = yahoo_gross = yahoo_operating_income = yahoo_net_income = yahoo_ebitda = None
        yahoo_fcf = yahoo_cash = yahoo_debt = yahoo_assets = yahoo_equity = None
        yahoo_debt_kind = "unavailable: Yahoo Finance statement currency is not identified"
        yahoo_debt_components = []
        yahoo_revenue_period = yahoo_gross_period = yahoo_operating_period = None
        yahoo_net_income_period = yahoo_ebitda_period = yahoo_fcf_period = None
        yahoo_cash_period = yahoo_debt_period = yahoo_assets_period = yahoo_equity_period = None
    sec_result = provider_data.get("sec")
    sec_facts, sec_state = sec_result if isinstance(sec_result, tuple) and len(sec_result) == 2 else (None, {"kind": "unavailable", "label": "SEC company facts are unavailable"})
    sec_currencies = list(dict.fromkeys((reporting_currency, trading_currency, "USD")))
    sec_revenue_series, sec_revenue_unit, sec_revenue_record = _sec_candidates(sec_facts, SEC_TAGS["revenue"], sec_currencies, kind="duration")
    if not yahoo_statement_currency_identified:
        sec_currency = _sec_revenue_currency(sec_facts)
        if sec_currency is not None:
            reporting_currency = sec_currency
            reporting_currency_identified = True
            rates = model_rates(reporting_currency, sector)
            currency_supported = currency_profile_is_supported(reporting_currency)
            valuation_calibration_supported = sector_methodology_supported and currency_supported
            sec_currencies = list(dict.fromkeys((reporting_currency, trading_currency, "USD")))
            sec_revenue_series, sec_revenue_unit, sec_revenue_record = _sec_candidates(
                sec_facts, SEC_TAGS["revenue"], sec_currencies, kind="duration"
            )
    if assumptions is not None and not currency_supported:
        raise ResearchError("unsupported_currency", "TSRP does not have a validated valuation calibration for this reporting currency.", 422)
    sec_net_income = _sec_value(sec_facts, SEC_TAGS["net_income"], sec_currencies, kind="duration")
    sec_assets = _sec_value(sec_facts, SEC_TAGS["assets"], sec_currencies, kind="instant")
    sec_equity = _sec_value(sec_facts, SEC_TAGS["equity"], sec_currencies, kind="instant")
    sec_cash = _sec_value(sec_facts, SEC_TAGS["cash"], sec_currencies, kind="instant")
    sec_debt_current = _sec_value(sec_facts, SEC_TAGS["debt_current"], sec_currencies, kind="instant")
    sec_debt_long = _sec_value(sec_facts, SEC_TAGS["debt_long"], sec_currencies, kind="instant")
    sec_debt = None
    sec_debt_record = None
    if sec_debt_current and sec_debt_long:
        same_basis = (sec_debt_current.get("period"), sec_debt_current.get("unit")) == (
            sec_debt_long.get("period"), sec_debt_long.get("unit")
        )
        if same_basis:
            current = finite(sec_debt_current.get("value"))
            long_term = finite(sec_debt_long.get("value"))
            if current is not None and long_term is not None:
                sec_debt = current + long_term
                sec_debt_record = {
                    **sec_debt_long,
                    "value": sec_debt,
                    "tag": "current + noncurrent debt",
                    "components": [sec_debt_current, sec_debt_long],
                }
    sec_revenue = finite(sec_revenue_record.get("value")) if sec_revenue_record else None
    sec_unit = str(sec_revenue_record.get("unit") or sec_revenue_unit) if sec_revenue_record else sec_revenue_unit
    fx_flags = []
    fx_quotes: dict[str, dict | None] = {}
    fx_attempts: dict[tuple[str, str, bool], dict | None] = {}
    fx_used: dict[tuple[str, str], dict] = {}
    fx_deadline = time.monotonic() + 6.0

    def convert_amount(
        value: float | None,
        source: str,
        target: str,
        *,
        sec_fact: bool = False,
        market_at: str | None = None,
    ) -> float | None:
        number = finite(value)
        if number is None:
            return None
        pair = (source.strip(), target.strip())
        cache_key = (*pair, sec_fact)
        if cache_key not in fx_attempts:
            source_code = pair[0] if pair[0] in {"GBp", "GBX", "ZAc", "ILA"} else pair[0].upper()
            target_code = pair[1] if pair[1] in {"GBp", "GBX", "ZAc", "ILA"} else pair[1].upper()
            fixed_unit_rate = fixed_quote_currency_conversion_rate(pair[0], pair[1])
            # Preserve explicitly matching SEC units without requiring a market FX quote;
            # the independent currency-calibration gate still disables unsupported DCF.
            same_sec_unit = (
                sec_fact
                and source_code == target_code
                and (source_code in {"GBp", "GBX", "ZAc", "ILA"} or (
                    re.fullmatch(r"[A-Z]{3}", source_code) and source_code != "XXX"
                ))
            )
            if fixed_unit_rate is not None:
                detail = {
                    "rate": fixed_unit_rate,
                    "pairs": [],
                    "directions": ["exact quote-subunit scale"],
                    "period": "Fixed quote-subunit conversion",
                }
            elif not sec_fact and (source_code in {"GBp", "GBX", "ZAc", "ILA"} or target_code in {"GBp", "GBX", "ZAc", "ILA"}):
                source_major, source_scale = quote_currency_major_unit(source_code)
                target_major, target_scale = quote_currency_major_unit(target_code)
                fx_detail = _fx_rate_detail(source_major, target_major, fx_quotes, deadline=fx_deadline)
                if (
                    fx_detail is not None
                    and _fx_detail_aligned_to_market_timestamp(fx_detail, market_at)
                ):
                    rate = finite_quotient(
                        finite_product(source_scale, fx_detail.get("rate")),
                        target_scale,
                    )
                    if rate is not None:
                        steps = []
                        if source_scale != 1.0:
                            steps.append({
                                "from": source_code, "to": source_major, "rate": source_scale,
                                "pairs": [], "directions": ["exact quote-subunit scale"],
                                "period": "Fixed quote-subunit conversion",
                            })
                        steps.append({
                            "from": source_major, "to": target_major, "rate": fx_detail.get("rate"),
                            "pairs": fx_detail.get("pairs", []),
                            "directions": fx_detail.get("directions", []),
                            "leg_periods": fx_detail.get("leg_periods", []),
                            "period": fx_detail.get("period"),
                        })
                        if target_scale != 1.0:
                            steps.append({
                                "from": target_major, "to": target_code,
                                "rate": finite_quotient(1.0, target_scale),
                                "pairs": [], "directions": ["exact quote-subunit scale"],
                                "period": "Fixed quote-subunit conversion",
                            })
                        detail = {
                            "rate": rate,
                            "pairs": fx_detail.get("pairs", []),
                            "directions": fx_detail.get("directions", []),
                            "leg_periods": fx_detail.get("leg_periods", []),
                            "period": fx_detail.get("period"),
                            "steps": steps,
                        }
                    else:
                        detail = None
                else:
                    detail = None
                    fx_flags.append(
                        f"FX quote is missing or not aligned to the market timestamp for market capitalization from {pair[0]} to {pair[1]}."
                    )
            elif same_sec_unit:
                detail = {"rate": 1.0, "pairs": [], "directions": ["same reported unit"], "period": "Same-currency identity"}
            elif sec_fact:
                # SEC statement facts are historical: flows cover a fiscal year
                # and balance-sheet facts are point-in-time. A current FX quote
                # cannot validly translate either one. Until the provider path
                # supplies a demonstrably period-aligned rate, fail closed and
                # let the caller use a separately sourced comparable value.
                detail = None
                fx_flags.append(
                    f"Period-aligned FX is unavailable for SEC-reported {pair[0]} values to {pair[1]}; current spot rates are not used for historical facts."
                )
            else:
                detail = _fx_rate_detail(pair[0], pair[1], fx_quotes, deadline=fx_deadline)
                if (
                    detail is not None
                    and source_code != target_code
                    and not _fx_detail_aligned_to_market_timestamp(detail, market_at)
                ):
                    detail = None
                    fx_flags.append(
                        f"FX quote is missing or not aligned to the market timestamp for market capitalization from {pair[0]} to {pair[1]}."
                    )
            fx_attempts[cache_key] = detail
        detail = fx_attempts[cache_key]
        if detail is not None:
            fx_used[pair] = detail
        rate = detail.get("rate") if detail is not None else None
        if rate is None:
            return None
        converted = finite_product(number, rate)
        if converted is None:
            fx_flags.append(f"Converted amount from {pair[0]} to {pair[1]} is outside the supported numeric range.")
            return None
        return converted

    def choose_sec_or_yahoo(
        record: dict | None,
        fallback: float | None,
        fallback_period: str | None,
        label: str,
    ) -> tuple[float | None, dict | None]:
        if record is None:
            return fallback, None
        sec_period = statement_period(record.get("period"))
        yahoo_period = statement_period(fallback_period)
        if (
            fallback is not None
            and sec_period is not None
            and yahoo_period is not None
            and sec_period < yahoo_period <= datetime.now(timezone.utc).date().isoformat()
        ):
            return fallback, None
        unit = str(record.get("unit") or reporting_currency)
        raw = finite(record.get("value"))
        converted = convert_amount(raw, unit, reporting_currency, sec_fact=True)
        if converted is None:
            if unit != reporting_currency:
                fx_flags.append(f"FX unavailable for {unit} to {reporting_currency}; {label} falls back only when Yahoo has a value.")
            return fallback, None
        return converted, record

    revenue, revenue_record = choose_sec_or_yahoo(sec_revenue_record, yahoo_revenue, yahoo_revenue_period, "revenue")
    revenue_source = "SEC EDGAR" if revenue_record else ("Yahoo Finance" if yahoo_revenue is not None else None)
    revenue_period = str(revenue_record.get("period")) if revenue_record else yahoo_revenue_period
    net_income, net_income_record = choose_sec_or_yahoo(sec_net_income, yahoo_net_income, yahoo_net_income_period, "net income")
    assets, assets_record = choose_sec_or_yahoo(sec_assets, yahoo_assets, yahoo_assets_period, "assets")
    equity, equity_record = choose_sec_or_yahoo(sec_equity, yahoo_equity, yahoo_equity_period, "equity")
    cash, cash_record = choose_sec_or_yahoo(sec_cash, yahoo_cash, yahoo_cash_period, "cash")
    debt, debt_record = choose_sec_or_yahoo(sec_debt_record, yahoo_debt, yahoo_debt_period, "debt")
    debt = debt if debt is not None else yahoo_debt
    net_income_period = str(net_income_record.get("period")) if net_income_record else yahoo_net_income_period
    assets_period = str(assets_record.get("period")) if assets_record else yahoo_assets_period
    equity_period = str(equity_record.get("period")) if equity_record else yahoo_equity_period
    cash_period = str(cash_record.get("period")) if cash_record else yahoo_cash_period
    debt_period = str(debt_record.get("period")) if debt_record else yahoo_debt_period
    cash_debt_comparable = bool(cash_period and debt_period and cash_period == debt_period)

    sec_fcf_detail = _sec_fcf_detail(sec_facts, sec_currencies, sec_revenue_record)
    sec_cashflow_fcf, sec_fcf_period = sec_fcf_detail["value"], sec_fcf_detail["period"]
    if revenue_source == "SEC EDGAR" and sec_cashflow_fcf is not None:
        free_cash_flow = convert_amount(sec_cashflow_fcf, sec_unit or reporting_currency, reporting_currency, sec_fact=True)
        fcf_method = "calculated: annual SEC operating cash flow less capital spending"
        fcf_source = "SEC EDGAR"
        fcf_period = sec_fcf_period
    elif yahoo_fcf is not None and yahoo_fcf_period == revenue_period:
        free_cash_flow = yahoo_fcf
        fcf_method = yahoo_fcf_method
        fcf_source = "Yahoo Finance"
        fcf_period = yahoo_fcf_period
    else:
        free_cash_flow = None
        fcf_source = None
        fcf_period = None
        fcf_method = None
    if free_cash_flow is None:
        fcf_source = None
        fcf_period = None
        fcf_method = None

    revenue_age_days = _period_age_days(revenue_period, analysis_now)
    fcf_age_days = _period_age_days(fcf_period, analysis_now)
    balance_age_days = _period_age_days(debt_period, analysis_now)
    yahoo_income_age_days = _period_age_days(yahoo_revenue_period, analysis_now)
    net_income_age_days = _period_age_days(net_income_period, analysis_now)
    revenue_current = revenue_age_days is not None and 0 <= revenue_age_days <= MAX_STATEMENT_AGE_DAYS
    fcf_current = fcf_age_days is not None and 0 <= fcf_age_days <= MAX_STATEMENT_AGE_DAYS
    balance_current = cash_debt_comparable and balance_age_days is not None and 0 <= balance_age_days <= MAX_STATEMENT_AGE_DAYS
    yahoo_income_current = yahoo_income_age_days is not None and 0 <= yahoo_income_age_days <= MAX_STATEMENT_AGE_DAYS
    net_income_current = net_income_age_days is not None and 0 <= net_income_age_days <= MAX_STATEMENT_AGE_DAYS

    # Market cap is price × shares, so use the listing/quote currency, not the
    # separate currency in which the issuer reports its financial statements.
    market_cap_currency = trading_currency.strip()
    if market_cap_currency not in {"GBp", "GBX", "ZAc", "ILA"}:
        market_cap_currency = market_cap_currency.upper()
    market_at = _quote_timestamp(info, fast_info, history, analysis_now)
    market_cap_reporting = convert_amount(
        market_cap, market_cap_currency, reporting_currency, market_at=market_at
    )
    if (
        market_cap is not None
        and market_cap_reporting is None
        and market_cap_currency != reporting_currency
        and not any("market capitalization" in flag.lower() for flag in fx_flags)
    ):
        fx_flags.append(f"FX unavailable or invalid for market capitalization from {market_cap_currency} to {reporting_currency}.")
    invalid_provider_market_time = market_timestamp is not None and not market_timestamp_valid
    stale = True
    if market_at:
        try:
            parsed_market_at = datetime.fromisoformat(market_at.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - parsed_market_at.astimezone(timezone.utc)).total_seconds()
            stale = age_seconds > MAX_MARKET_QUOTE_AGE_SECONDS or age_seconds < -5 * 60
        except (TypeError, ValueError, OverflowError):
            stale = True
    if invalid_provider_market_time:
        # A provider quote explicitly marked with an invalid future time cannot
        # be made fresh merely because a daily history bar is dated today.
        stale = True
    enterprise_value = None
    enterprise_value_period = None
    if (
        not stale
        and sector_methodology_supported
        and currency_supported
        and market_cap_reporting is not None
        and balance_current
        and cash is not None and cash >= 0
        and debt is not None and debt >= 0
    ):
        enterprise_value = finite(market_cap_reporting + debt - cash)
        if enterprise_value is not None:
            enterprise_value_period = f"Market data {market_at}; balance sheet {debt_period}"

    def nonfuture_revenue_observations(observations):
        return [
            (period, value)
            for period, value in observations
            if value is not None
            and (age := _period_age_days(str(period), analysis_now)) is not None
            and age >= 0
        ]

    sec_growth_series = nonfuture_revenue_observations(sec_revenue_series)
    yahoo_growth_series = nonfuture_revenue_observations(yahoo_revenue_observations)
    if revenue_source == "SEC EDGAR" and len(sec_growth_series) >= 2:
        history_observations = sec_growth_series
        history_source = "SEC EDGAR"
    elif len(yahoo_growth_series) >= 2:
        history_observations = yahoo_growth_series
        history_source = "Yahoo Finance"
    elif sec_growth_series:
        history_observations = sec_growth_series
        history_source = "SEC EDGAR"
    else:
        history_observations = []
        history_source = None
    historical_growth, historical_years, history_start, history_end = history_cagr_periods(history_observations, max_intervals=10)
    historical_growth_3y = history_cagr_periods(history_observations, max_intervals=3)[0]
    history_period = history_end
    history_age_days = _period_age_days(history_period, analysis_now)
    history_current = history_age_days is not None and 0 <= history_age_days <= MAX_STATEMENT_AGE_DAYS

    gross_margin = _period_ratio(yahoo_gross, yahoo_gross_period, yahoo_revenue, yahoo_revenue_period) if yahoo_income_current else None
    operating_margin = _period_ratio(yahoo_operating_income, yahoo_operating_period, yahoo_revenue, yahoo_revenue_period) if yahoo_income_current else None
    profit_margin = _period_ratio(net_income, net_income_period, revenue, revenue_period) if net_income_current and revenue_current else None
    fcf_margin = _period_ratio(free_cash_flow, fcf_period, revenue, revenue_period) if fcf_current and revenue_current else None
    if revenue_source == "SEC EDGAR":
        trailing_fcf_margins = _sec_fcf_margin_history(
            sec_facts, revenue_record, years=4
        ) if revenue_current and fcf_current else []
    else:
        trailing_fcf_margins = [] if not fcf_current else trailing_aligned_fcf_margins(
            cashflow, financials, ["Total Revenue", "Operating Revenue"], years=4
        )
    model_fcf_margin, model_fcf_refused, model_fcf_note = choose_model_fcf_margin(
        fcf_margin, trailing_fcf_margins, model["financial_benchmarks"]["fcf"]
    )
    if not sector_methodology_supported:
        model_fcf_margin = None
        model_fcf_refused = True
        model_fcf_note = "Unavailable: TSRP does not have a validated sector calibration for this company."
    if net_income_record and equity_record:
        equity_observations = [
            (period, convert_amount(value, str(equity_record.get("unit") or reporting_currency), reporting_currency, sec_fact=True))
            for period, value in equity_record.get("series", [])
        ]
    elif not net_income_record and not equity_record:
        equity_observations = period_observations(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
    else:
        equity_observations = []
    roe = _average_equity_roe(net_income, net_income_period, equity_observations) if net_income_current else None
    debt_to_assets = _period_ratio(debt, debt_period, assets, assets_period) if balance_current and debt is not None and debt >= 0 else None
    ev_sales = _period_ratio(enterprise_value, revenue_period, revenue, revenue_period) if revenue_current and enterprise_value is not None and enterprise_value > 0 else None
    ev_ebitda = None
    if enterprise_value is not None and enterprise_value > 0 and yahoo_income_current and yahoo_ebitda is not None and yahoo_ebitda > 0 and yahoo_ebitda_period == revenue_period:
        ev_ebitda = finite(enterprise_value / yahoo_ebitda)
    pe = finite(market_cap_reporting / net_income) if not stale and market_cap_reporting is not None and market_cap_reporting > 0 and net_income_current and net_income is not None and net_income > 0 else None

    assumptions = validate_assumptions(assumptions) if assumptions is not None else None
    discount_rate = assumptions["discount_rate"] if assumptions else rates["discount_rate"]
    terminal_growth = assumptions["terminal_growth"] if assumptions else rates["terminal_growth"]
    selected_margin = assumptions["fcf_margin"] if assumptions else model_fcf_margin
    start_growth = assumptions["starting_growth"] if assumptions else None

    required_growth = None
    solver_status = "unavailable"
    solver_low = solver_high = None
    growth_clamped = False
    if not stale and revenue_current and fcf_current and balance_current:
        solve = solve_supported_required_growth(
            reporting_currency,
            sector_methodology_supported,
            enterprise_value,
            revenue,
            selected_margin,
            discount_rate,
            terminal_growth,
            start_margin=fcf_margin if fcf_margin is not None and fcf_margin > 0 and assumptions is None else selected_margin,
        )
        required_growth = solve["growth"]
        solver_status = solve["status"]
        solver_low, solver_high = solve["low"], solve["high"]
        growth_clamped = solver_status in {"below_range", "above_range"}

    consensus_growth, consensus_label = _estimate_growth(estimate_frame)
    target_price = _first_number(info, "targetMeanPrice")
    analyst_count = _first_number(info, "numberOfAnalystOpinions")
    flags: list[dict] = []
    provider_labels = {
        "history": "market-price history", "financials": "income statements",
        "balance": "balance sheet", "cashflow": "cash-flow statements",
        "estimates": "analyst estimates", "sec": "SEC company facts", "fast_info": "quote summary",
    }
    for provider in provider_timeouts:
        flags.append(_flag(f"The {provider_labels.get(provider, provider)} request exceeded the available provider time or capacity budget; affected values are unavailable.", "high"))
    for provider in provider_failures:
        flags.append(_flag(f"The {provider_labels.get(provider, provider)} request failed; affected values may be incomplete. Retry later.", "medium"))
    if future_statement_periods:
        flags.append(_flag("Yahoo Finance returned future-dated financial statement periods; those periods were excluded from this analysis.", "medium"))
    if revenue is None:
        flags.append(_flag("Annual revenue is unavailable.", "high"))
    if model_fcf_refused and assumptions is None:
        flags.append(_flag("The latest free-cash-flow margin is non-positive or unavailable; reverse DCF is not solved.", "high"))
    elif required_growth is None:
        flags.append(_flag("The available price, revenue, and cash-flow evidence does not support a growth solve.", "high"))
    if free_cash_flow is None:
        flags.append(_flag("Period-aligned free cash flow is unavailable."))
    elif fcf_margin is not None and fcf_margin <= 0:
        flags.append(_flag("Free cash flow is negative; TSRP does not solve growth required by price from it."))
    if historical_growth is None:
        flags.append(_flag("There is not enough positive annual revenue history for a comparable growth rate."))
    if revenue_age_days is None or revenue_age_days < 0 or revenue_age_days > MAX_STATEMENT_AGE_DAYS:
        flags.append(_flag("The latest annual revenue period is missing, future-dated, or more than 18 months old; it is excluded from current valuation and scoring.", "high"))
    if fcf_age_days is None or fcf_age_days < 0 or fcf_age_days > MAX_STATEMENT_AGE_DAYS:
        flags.append(_flag("The latest free-cash-flow period is missing, future-dated, or more than 18 months old; it is excluded from current valuation and scoring.", "high"))
    if balance_age_days is None or balance_age_days < 0 or balance_age_days > MAX_STATEMENT_AGE_DAYS:
        flags.append(_flag("The latest comparable balance-sheet period is missing, future-dated, or more than 18 months old; cash/debt are excluded from enterprise value and scoring.", "high"))
    if not history_current:
        flags.append(_flag("Historical revenue growth is stale or has no verified period and is excluded from scoring.", "high"))
    if cash is None or debt is None:
        flags.append(_flag("Cash or debt data is incomplete; balance-sheet evidence is weaker."))
    elif not cash_debt_comparable:
        flags.append(_flag("Cash and debt come from different balance-sheet dates; TSRP does not combine them for valuation or scoring."))
    if (cash is not None and cash < 0) or (debt is not None and debt < 0):
        flags.append(_flag("A reported cash or debt value is negative and is excluded from enterprise value and score inputs.", "high"))
    if assets is not None and assets <= 0:
        flags.append(_flag("Reported total assets are zero or negative; debt-to-assets is unavailable.", "high"))
    if growth_clamped:
        flags.append(_flag("The growth requirement is outside the tested range and is shown as a bound."))
    if rates["used_fallback_currency"]:
        flags.append(_flag("The reporting currency has no TSRP calibration. The USD fallback is disclosed, but price-based valuation is disabled.", "high"))
    if not sector_methodology_supported:
        flags.append(_flag("No validated TSRP sector calibration is available. The score, enterprise-value valuation, and scenario model are unavailable; observed company facts remain visible.", "high"))
    if not currency_supported:
        flags.append(_flag("The reporting currency or quote subunit is not supported for the DCF; currency-dependent valuation is unavailable.", "high"))
    if stale:
        flags.append(_flag("The market quote is stale or has no verified timestamp; price-based valuation is unavailable.", "high"))
    if sec_state["kind"] != "available":
        flags.append(_flag("SEC company facts are unavailable. Yahoo Finance data remains separately identified."))
    if not yahoo_statement_currency_identified:
        message = (
            "Yahoo Finance did not identify a financial-statement currency, so Yahoo statement amounts are excluded. "
            + ("The reporting currency is identified from SEC filing units." if reporting_currency_identified else "The trading currency is not used as a substitute, so statement amounts and currency-dependent valuation are unavailable.")
        )
        flags.append(_flag(message, "high"))
    if trading_currency == "N/A":
        flags.append(_flag("The provider did not identify the listing currency; market capitalization and price-based valuation are unavailable.", "high"))
    if fx_flags:
        flags.extend(_flag(message, "high") for message in sorted(set(fx_flags)))
    if sector == "Real Estate":
        flags.append(_flag("Real-estate caveat: this model uses free cash flow and earnings, not property-specific measures."))

    business_detail = business_quality_detail(
        historical_growth if history_current else None, gross_margin, operating_margin,
        fcf_margin, roe, debt_to_assets, model,
    ) if sector_methodology_supported else None
    scoring_cash = cash if balance_current and cash is not None and cash >= 0 else None
    scoring_debt = debt if balance_current and debt is not None and debt >= 0 else None
    financial_detail = financial_strength_detail(
        scoring_cash, scoring_debt, operating_margin, fcf_margin, debt_to_assets, model,
    ) if sector_methodology_supported else None
    business_quality = business_detail["score"] if business_detail else None
    financial_strength = financial_detail["score"] if financial_detail else None
    score_required_growth = required_growth
    score_growth_clamped = growth_clamped or (model_fcf_refused and assumptions is None)
    if assumptions is not None:
        # User scenario inputs may change the displayed valuation sensitivity,
        # but must not flow back into the baseline TSRP Score.
        score_required_growth = None
        score_growth_clamped = False
        if not model_fcf_refused and not stale and revenue_current and fcf_current and balance_current:
            baseline_solve = solve_supported_required_growth(
                reporting_currency,
                sector_methodology_supported,
                enterprise_value,
                revenue,
                model_fcf_margin,
                rates["discount_rate"],
                rates["terminal_growth"],
                start_margin=fcf_margin if fcf_margin is not None and fcf_margin > 0 else model_fcf_margin,
            )
            score_required_growth = baseline_solve["growth"]
            score_growth_clamped = baseline_solve["status"] in {"below_range", "above_range"}
    score_trace = supported_score_breakdown(
        business_detail,
        financial_detail,
        historical_growth if sector_methodology_supported and history_current else None,
        score_required_growth if sector_methodology_supported else None,
        consensus_growth if sector_methodology_supported else None,
        growth_clamped=score_growth_clamped,
    )
    final_score = score_trace["score"]
    score_is_defensible = final_score is not None and sector_methodology_supported

    data_fields = {
        "revenue": revenue is not None and revenue_current,
        "free_cash_flow": free_cash_flow is not None and fcf_current,
        "historical_growth": historical_growth is not None and history_current,
        "cash": cash is not None and balance_current,
        "debt": debt is not None and balance_current,
        "enterprise_value": enterprise_value is not None,
    }
    if sector_methodology_supported and not score_is_defensible:
        supported_component_weight = math.fsum(
            component["weight"]
            for component in score_trace["components"]
            if component["value"] is not None
        )
        flags.append(_flag(
            "TSRP Score is withheld: evidence coverage "
            f"{score_trace['evidence_coverage']:.0%} and supported component weight "
            f"{supported_component_weight:.0%}; each must reach 70%. Missing inputs are excluded, not assigned substitute scores.",
            "high",
        ))
    confidence = _confidence(data_fields, flags)
    if not score_is_defensible:
        confidence = "Low"
    if model_fcf_refused or growth_clamped:
        confidence = "Low"
    elif sec_state["kind"] != "available" and confidence == "High":
        confidence = "Medium"

    def provenance(source: str | None, period: str | None, currency: str, status: str = "reported") -> dict:
        return {"source": source or "Unavailable", "period": period or "N/A", "currency": currency, "kind": status}

    def fact_provenance(record: dict | None, fallback_source: str | None, period: str | None) -> dict:
        source = "SEC EDGAR" if record else fallback_source
        result = provenance(source, period, reporting_currency, "reported" if source else "unavailable")
        if record:
            result.update({
                "filing_date": record.get("filed"),
                "filing_form": record.get("form"),
                "taxonomy": record.get("taxonomy"),
                "tag": record.get("tag"),
                "source_unit": record.get("unit"),
            })
            components = record.get("components")
            if isinstance(components, list):
                result["components"] = [{
                    "value": finite(component.get("value")),
                    "period": component.get("period"),
                    "source_unit": component.get("unit"),
                    "filing_date": component.get("filed"),
                    "filing_form": component.get("form"),
                    "taxonomy": component.get("taxonomy"),
                    "tag": component.get("tag"),
                } for component in components if isinstance(component, dict)]
        return result

    if debt_record:
        debt_provenance = fact_provenance(debt_record, None, debt_period)
    elif debt is not None:
        debt_provenance = provenance("Yahoo Finance", debt_period, reporting_currency, yahoo_debt_kind)
        if yahoo_debt_components:
            debt_provenance["components"] = [{
                "value": value,
                "period": debt_period,
                "source_unit": reporting_currency,
                "filing_date": None,
                "filing_form": None,
                "taxonomy": "Yahoo Finance statement",
                "tag": label,
            } for label, value in yahoo_debt_components]
    else:
        debt_provenance = provenance(None, debt_period, reporting_currency, yahoo_debt_kind)

    def conversion_steps(source: str, target: str, detail: dict) -> list[dict]:
        steps = detail.get("steps")
        if isinstance(steps, list) and steps:
            return steps
        return [{
            "from": source,
            "to": target,
            "rate": detail.get("rate"),
            "pairs": detail.get("pairs", []),
            "directions": detail.get("directions", []),
            "leg_periods": detail.get("leg_periods", []),
            "period": detail.get("period"),
        }]

    fcf_provenance = provenance(fcf_source, fcf_period, reporting_currency, fcf_method or "unavailable")
    if fcf_source == "SEC EDGAR":
        fcf_provenance["filing_facts"] = {
            "operating_cash_flow": fact_provenance(sec_fcf_detail.get("operating_cash_flow"), None, sec_fcf_period),
            "capital_spending": fact_provenance(sec_fcf_detail.get("capital_spending"), None, sec_fcf_period),
        }

    market_cap_conversion = fx_attempts.get((market_cap_currency, reporting_currency, False))
    if market_cap_currency != reporting_currency and market_cap_conversion and market_cap_conversion.get("rate") is not None:
        market_steps = conversion_steps(market_cap_currency, reporting_currency, market_cap_conversion)
        market_uses_fx = any(step.get("pairs") for step in market_steps)
        market_uses_unit_scale = any(step.get("period") == "Fixed quote-subunit conversion" for step in market_steps)
        market_source_parts = ["Yahoo Finance"]
        if market_uses_fx:
            market_source_parts.append("TSRP FX conversion")
        if market_uses_unit_scale:
            market_source_parts.append("exact quote-unit scaling")
        market_cap_reporting_source = provenance(
            " + ".join(market_source_parts), market_at, reporting_currency,
            f"listing-currency market capitalization converted from {market_cap_currency} to {reporting_currency}",
        )
        market_cap_reporting_source["conversions"] = market_steps
    else:
        market_cap_reporting_source = provenance(
            "Yahoo Finance" if market_cap_reporting is not None else None,
            market_at,
            reporting_currency,
            "market capitalization in listing quote currency" if market_cap_reporting is not None else (
                "unavailable: no validated currency coverage for market capitalization"
                if market_cap_currency == reporting_currency and not currency_supported
                else "unavailable: quote currency or a valid FX rate is missing"
            ),
        )

    ev_sales_source = provenance(
        "TSRP calculation" if ev_sales is not None else None,
        f"Market value {market_at}; annual revenue {revenue_period}" if ev_sales is not None else None,
        reporting_currency,
        "enterprise value / annual reported revenue",
    )
    ev_ebitda_source = provenance(
        "TSRP calculation" if ev_ebitda is not None else None,
        f"Market value {market_at}; annual EBITDA {yahoo_ebitda_period}" if ev_ebitda is not None else None,
        reporting_currency,
        "enterprise value / same-period annual EBITDA",
    )
    pe_source = provenance(
        "TSRP calculation" if pe is not None else None,
        f"Market value {market_at}; annual net income {net_income_period}" if pe is not None else None,
        reporting_currency,
        "current market capitalization / annual net income; not a trailing-twelve-month P/E",
    )

    fx_history_used = any(
        any(step.get("pairs") for step in conversion_steps(source, target, detail))
        for (source, target), detail in fx_used.items()
    )
    fx_conversion_unavailable = any(
        detail is None
        and source != target
        and fixed_quote_currency_conversion_rate(source, target) is None
        for (source, target, _sec_fact), detail in fx_attempts.items()
    )
    quote_unit_scale_used = any(
        any(
            step.get("period") == "Fixed quote-subunit conversion"
            for step in conversion_steps(source, target, detail)
        )
        for (source, target), detail in fx_used.items()
    )
    fx_source_parts = []
    if fx_history_used:
        fx_source_parts.append("Yahoo Finance daily FX history")
    if quote_unit_scale_used:
        fx_source_parts.append("TSRP exact quote-unit scaling")
    if fx_conversion_unavailable:
        fx_source_parts.append("some required FX conversions unavailable")
    fx_periods = sorted({
        str(step.get("period"))
        for (source, target), detail in fx_used.items()
        for step in conversion_steps(source, target, detail)
        if step.get("pairs") and step.get("period")
    })
    if quote_unit_scale_used:
        fx_periods.append("Fixed quote-subunit conversion")
    if fx_conversion_unavailable:
        fx_periods.append("Some requested conversions unavailable" if fx_periods else "No valid conversion used")

    if fx_history_used and quote_unit_scale_used:
        fx_kind = "timestamped conversion rates and fixed quote-unit scales"
    elif fx_history_used:
        fx_kind = "timestamped conversion rates"
    elif quote_unit_scale_used:
        fx_kind = "fixed quote-unit scale"
    else:
        fx_kind = "No currency conversion used"
    if fx_conversion_unavailable:
        fx_kind = (
            "Required currency conversion unavailable"
            if fx_kind == "No currency conversion used"
            else f"{fx_kind}; some required conversions unavailable"
        )

    sources = {
        "share_price": provenance("Yahoo Finance", _quote_timestamp(info, fast_info, history, analysis_now), trading_currency, "market data"),
        "market_cap": provenance("Yahoo Finance", market_at, market_cap_currency, "market data; currency follows the listing quote currency"),
        "market_cap_reporting": market_cap_reporting_source,
        "enterprise_value": provenance("TSRP calculation" if enterprise_value is not None else None, enterprise_value_period, reporting_currency, "market capitalization + same-date reported debt − cash; excludes other claims not separately captured" if sector_methodology_supported else "unavailable: no validated TSRP sector valuation method"),
        "ev_sales": ev_sales_source,
        "ev_ebitda": ev_ebitda_source,
        "pe": pe_source,
        "valuation_assumptions": provenance(
            "TSRP static calibration" if valuation_calibration_supported else None,
            "No live market-yield timestamp" if valuation_calibration_supported else None,
            rates["currency"] if valuation_calibration_supported else "N/A",
            rates["calibration_basis"] if valuation_calibration_supported else (
                "unavailable: no validated sector calibration; valuation rate assumptions are withheld"
                if not sector_methodology_supported
                else "unavailable: no validated calibration for the reporting currency; USD rates are not substituted"
            ),
        ),
        "revenue": fact_provenance(revenue_record, revenue_source, revenue_period),
        "net_income": fact_provenance(net_income_record, "Yahoo Finance" if net_income is not None else None, net_income_period),
        "free_cash_flow": fcf_provenance,
        "historical_growth": provenance(history_source, f"{history_start} to {history_end}" if history_start and history_end else None, reporting_currency, "calculated from dated annual reported revenue"),
        "analyst_growth": provenance("Yahoo Finance analyst estimates" if consensus_growth is not None else None, consensus_label, reporting_currency, "consensus estimate"),
        "cash": fact_provenance(cash_record, "Yahoo Finance" if cash is not None else None, cash_period),
        "debt": debt_provenance,
        "equity": fact_provenance(equity_record, "Yahoo Finance" if equity is not None else None, equity_period),
        "gross_margin": provenance("Yahoo Finance annual income statement" if gross_margin is not None else None, yahoo_gross_period, reporting_currency, "calculated from same-period gross profit / revenue"),
        "operating_margin": provenance("Yahoo Finance annual income statement" if operating_margin is not None else None, yahoo_operating_period, reporting_currency, "calculated from same-period operating income / revenue"),
        "profit_margin": provenance("Period-aligned net income / revenue" if profit_margin is not None else None, revenue_period, reporting_currency, "calculated"),
        "free_cash_flow_margin": provenance("Period-aligned free cash flow / revenue" if fcf_margin is not None else None, fcf_period, reporting_currency, "calculated"),
        "return_on_equity": provenance("SEC EDGAR" if net_income_record and equity_record else "Yahoo Finance" if roe is not None else None, net_income_period, reporting_currency, "net income / average beginning and ending equity"),
        "debt_to_assets": provenance("Period-aligned balance-sheet values" if debt_to_assets is not None else None, debt_period, reporting_currency, "calculated"),
        "fx": {
            "source": " + ".join(fx_source_parts) if fx_source_parts else "FX conversion unavailable" if fx_conversion_unavailable else "No conversion required",
            "period": "; ".join(fx_periods) or "No conversion used",
            "currency": reporting_currency,
            "kind": fx_kind,
            "conversions": [step for (source, target), detail in fx_used.items() for step in conversion_steps(source, target, detail)],
        },
        "sec_status": sec_state,
    }
    refreshed = datetime.now(timezone.utc).isoformat()

    chart = _chart_price_history(history, analysis_now)

    display = {
        "ticker": ticker_symbol,
        "company": str(info.get("longName") or info.get("shortName") or sec_state.get("company") or ticker_symbol)[:180],
        "exchange": str(info.get("fullExchangeName") or info.get("exchange") or "Exchange unavailable")[:80],
        "quote_type": str(info.get("quoteType") or "EQUITY")[:30],
        "sector": sector,
        "sector_methodology_supported": sector_methodology_supported,
        "industry": str(info.get("industry") or "Industry unavailable")[:120],
        "reporting_currency": reporting_currency,
        "trading_currency": trading_currency,
        "price": price,
        "previous_close": previous_close,
        "market_cap": market_cap,
        "market_cap_reporting": market_cap_reporting,
        "enterprise_value": enterprise_value,
        "revenue": revenue,
        "revenue_period": revenue_period,
        "revenue_source": revenue_source,
        "free_cash_flow": free_cash_flow,
        "fcf_period": fcf_period,
        "fcf_source": fcf_source,
        "fcf_margin": fcf_margin,
        "model_fcf_margin": selected_margin,
        "model_fcf_note": model_fcf_note,
        "model_fcf_refused": model_fcf_refused and assumptions is None,
        "discount_rate": discount_rate if valuation_calibration_supported else None,
        "terminal_growth": terminal_growth if valuation_calibration_supported else None,
        "risk_free": rates["risk_free"] if valuation_calibration_supported else None,
        "equity_risk_premium": rates["erp"] if valuation_calibration_supported else None,
        "sector_spread": rates["sector_spread"] if valuation_calibration_supported else None,
        "rate_currency": rates["currency"] if valuation_calibration_supported else "N/A",
        "currency_methodology_supported": currency_supported,
        "historical_growth": historical_growth,
        "historical_growth_years": historical_years,
        "historical_growth_3y": historical_growth_3y,
        "consensus_growth": consensus_growth,
        "analyst_estimate_period": consensus_label,
        "target_mean_price": target_price,
        "analyst_count": analyst_count,
        "required_growth": required_growth,
        "solver_status": solver_status,
        "solver_low": solver_low,
        "solver_high": solver_high,
        "growth_clamped": growth_clamped,
        "business_quality": business_quality,
        "financial_strength": financial_strength,
        "market_expectations": None,
        "score": final_score,
        "score_label": score_label(final_score) if score_is_defensible else "Unavailable",
        "score_trace": score_trace if score_is_defensible else None,
        "score_available": score_is_defensible,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "confidence": confidence,
        "revenue_growth_source": history_source,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "profit_margin": profit_margin,
        "return_on_equity": roe,
        "cash": cash,
        "debt": debt,
        "assets": assets,
        "equity": equity,
        "debt_to_assets": debt_to_assets,
        "ev_sales": ev_sales,
        "ev_ebitda": ev_ebitda,
        "pe": pe,
        "sources": sources,
        "sec_status": sec_state,
        "quality_flags": flags,
        "retrieved_at": refreshed,
        "market_at": market_at,
        "is_stale": stale,
        "freshness": "Yahoo Finance may provide delayed market data. TSRP does not configure shared edge caching for research responses; check this analysis’s retrieval timestamp and the 48-hour quote-freshness warning.",
        "methodology_version": METHODOLOGY_VERSION,
        "assumptions": assumptions or {
            "starting_growth": required_growth if solver_status == "exact" else None,
            "fcf_margin": selected_margin,
            "discount_rate": discount_rate if valuation_calibration_supported else None,
            "terminal_growth": terminal_growth if valuation_calibration_supported else None,
        },
        "user_assumptions": assumptions,
        "scenario": None,
    }
    if assumptions:
        scenario_available = (
            sector_methodology_supported
            and
            enterprise_value is not None
            and currency_supported
            and not stale
            and revenue_current
            and balance_current
            and revenue is not None
        )
        scenario_ev = dcf_enterprise_value(
            revenue, assumptions["starting_growth"], assumptions["fcf_margin"],
            assumptions["discount_rate"], assumptions["terminal_growth"],
            years=FORECAST_YEARS,
            start_margin=fcf_margin if fcf_margin is not None and fcf_margin > 0 else assumptions["fcf_margin"],
        ) if scenario_available else None
        display["scenario"] = {
            "enterprise_value": scenario_ev,
            "equity_value": finite(scenario_ev - debt + cash) if scenario_ev is not None and balance_current and cash_debt_comparable and debt is not None and debt >= 0 and cash is not None and cash >= 0 else None,
            "inputs": assumptions,
            "kind": "user scenario, not a forecast or target price",
        }
    display["snapshot_proof"] = _issue_snapshot_proof(display)
    return {"company": display, "price_history": chart}


def validate_assumptions(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ResearchError("invalid_assumptions", "Provide all four valuation assumptions.")
    bounds = {
        "starting_growth": (-0.10, 0.40),
        "fcf_margin": (0.01, 0.50),
        "discount_rate": (0.03, 0.20),
        "terminal_growth": (0.0, 0.06),
    }
    if payload.keys() != bounds.keys():
        raise ResearchError("invalid_assumptions", "Provide exactly the four supported valuation assumptions.")
    values = {}
    for field, (low, high) in bounds.items():
        raw_value = payload[field]
        value = None if isinstance(raw_value, bool) else finite(raw_value)
        if value is None or not low <= value <= high:
            raise ResearchError("invalid_assumptions", "Assumptions are outside their supported ranges.")
        values[field] = value
    if values["discount_rate"] <= values["terminal_growth"]:
        raise ResearchError("invalid_assumptions", "The discount rate must exceed long-run growth.")
    return values


def _validate_scenario_basis(symbol: str, payload: Any) -> dict:
    fields = {
        "ticker", "sector", "reporting_currency", "revenue", "revenue_period",
        "revenue_currency", "market_cap_reporting", "enterprise_value", "market_at",
        "cash", "cash_period", "cash_currency", "debt", "debt_period", "debt_currency",
        "starting_fcf_margin",
        "methodology_version", "analysis_schema_version",
    }
    unavailable = ResearchError(
        "scenario_unavailable",
        "Current, comparable company data is unavailable. Refresh the analysis and try again.",
        422,
    )
    if not isinstance(payload, dict) or payload.keys() != fields:
        raise unavailable
    if validate_ticker(payload.get("ticker")) != symbol:
        raise unavailable
    if payload.get("methodology_version") != METHODOLOGY_VERSION or payload.get("analysis_schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise unavailable

    sector = payload.get("sector")
    currency = payload.get("reporting_currency")
    if not isinstance(sector, str) or not has_sector_calibration(sector):
        raise ResearchError("unsupported_sector", "TSRP has no validated scenario method for this sector.", 422)
    if not isinstance(currency, str) or currency not in CURRENCY_PROFILES:
        raise unavailable

    for field in ("revenue_currency", "cash_currency", "debt_currency"):
        if payload.get(field) != currency:
            raise ResearchError("scenario_currency_mismatch", "Scenario facts must use the company’s reporting currency.", 422)

    numbers = {}
    for field in ("revenue", "market_cap_reporting", "enterprise_value", "cash", "debt"):
        raw = payload.get(field)
        number = None if isinstance(raw, bool) else finite(raw)
        if number is None or abs(number) > MAX_SCENARIO_FINANCIAL_AMOUNT:
            raise unavailable
        numbers[field] = number
    if numbers["revenue"] <= 0 or numbers["market_cap_reporting"] <= 0 or numbers["cash"] < 0 or numbers["debt"] < 0:
        raise unavailable

    starting_fcf_margin = payload.get("starting_fcf_margin")
    if starting_fcf_margin is not None:
        starting_fcf_margin = None if isinstance(starting_fcf_margin, bool) else finite(starting_fcf_margin)
        if starting_fcf_margin is None or starting_fcf_margin <= 0:
            raise unavailable

    periods = {}
    for field in ("revenue_period", "cash_period", "debt_period"):
        raw = payload.get(field)
        if not isinstance(raw, str):
            raise unavailable
        age = _period_age_days(raw)
        if age is None or age < 0 or age > MAX_STATEMENT_AGE_DAYS:
            raise unavailable
        periods[field] = raw
    if periods["cash_period"] != periods["debt_period"]:
        raise ResearchError("scenario_period_mismatch", "Cash and debt must come from the same reporting period.", 422)

    quote_time = payload.get("market_at")
    if not isinstance(quote_time, str):
        raise unavailable
    try:
        quote_stamp = datetime.fromisoformat(quote_time.replace("Z", "+00:00"))
        if quote_stamp.tzinfo is None:
            raise ValueError("Market timestamp must include a timezone.")
        age_seconds = (datetime.now(timezone.utc) - quote_stamp.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        raise unavailable
    if age_seconds > 60 * 60 * 48 or age_seconds < -5 * 60:
        raise ResearchError("scenario_data_stale", "Market data is stale. Refresh the company analysis before calculating a scenario.", 422)

    derived_ev = numbers["market_cap_reporting"] + numbers["debt"] - numbers["cash"]
    if derived_ev <= 0 or abs(numbers["enterprise_value"] - derived_ev) > max(1.0, abs(derived_ev) * 1e-8):
        raise ResearchError("scenario_basis_inconsistent", "Company valuation inputs do not reconcile. Refresh the analysis before continuing.", 422)

    return {
        **numbers,
        **periods,
        "ticker": symbol,
        "sector": sector,
        "currency": currency,
        "starting_fcf_margin": starting_fcf_margin,
    }


def scenario_context_is_eligible(symbol: Any, payload: Any) -> bool:
    """Use the scenario endpoint's validation rules before advertising the editor."""
    try:
        _validate_scenario_basis(validate_ticker(symbol), payload)
    except (ResearchError, TypeError, ValueError, OverflowError):
        return False
    return True


def analyze_scenario(symbol: Any, assumptions: Any, basis: Any) -> dict:
    ticker = validate_ticker(symbol)
    values = validate_assumptions(assumptions)
    current = _validate_scenario_basis(ticker, basis)
    scenario_ev = dcf_enterprise_value(
        current["revenue"], values["starting_growth"], values["fcf_margin"],
        values["discount_rate"], values["terminal_growth"],
        years=FORECAST_YEARS,
        start_margin=current["starting_fcf_margin"] or values["fcf_margin"],
    )
    if scenario_ev is None:
        raise ResearchError("scenario_unavailable", "These assumptions do not produce a supported scenario value.", 422)
    return {
        "enterprise_value": scenario_ev,
        "equity_value": finite(scenario_ev - current["debt"] + current["cash"]),
        "inputs": values,
        "kind": "user scenario using the source facts currently shown; not a forecast or target price",
    }


def search_companies(query: Any) -> list[dict]:
    text = validate_search(query)
    if len(text) < 2:
        return []
    _require_yahoo_access()

    def fetch_quotes():
        search = yf.Search(text, max_results=12, timeout=10, session=_YAHOO_SESSION)
        response = getattr(search, "response", None)
        quotes = response.get("quotes") if isinstance(response, dict) else None
        if (
            not isinstance(quotes, list)
            or any(not isinstance(item, dict) for item in quotes)
            or any(not isinstance(item.get("symbol"), str) or not item["symbol"].strip() for item in quotes)
        ):
            raise ValueError("Yahoo company-search response had an unsupported shape")
        return search.quotes

    try:
        provider_results, timed_out = _bounded_provider_calls(
            {"search": fetch_quotes},
            timeout_seconds=12,
        )
        if timed_out:
            raise ResearchError("search_unavailable", "Company search is temporarily unavailable.", 503)
        result = provider_results.get("search")
        if result is None:
            raise ResearchError("search_unavailable", "Company search is temporarily unavailable.", 503)
    except ResearchError:
        raise
    except Exception:
        raise ResearchError("search_unavailable", "Company search is temporarily unavailable.", 503)
    matches = []
    seen = set()
    for item in result or []:
        if not isinstance(item, dict):
            continue
        symbol = normalize_ticker(item.get("symbol"))
        kind = str(item.get("quoteType") or "").upper()
        if not symbol or symbol in seen or kind != "EQUITY" or not TICKER_RE.fullmatch(symbol):
            continue
        seen.add(symbol)
        matches.append({
            "ticker": symbol,
            "company": str(item.get("longname") or item.get("shortname") or symbol)[:180],
            "exchange": str(item.get("exchDisp") or item.get("exchange") or "")[:80],
            "sector": str(item.get("sectorDisp") or "")[:100],
        })
    return matches[:8]


def _daily_movement_quote_fields(quote: dict) -> tuple[str, str]:
    """Select the current regular or extended-hours quote for a daily move."""
    market_state = str(quote.get("marketState", "")).upper()
    if market_state == "PRE":
        return "preMarketPrice", "preMarketTime"
    if market_state == "POST":
        return "postMarketPrice", "postMarketTime"
    return "regularMarketPrice", "regularMarketTime"


def daily_market_movers() -> dict:
    """Return Yahoo's daily gainers and losers, ranked by absolute movement."""
    _require_yahoo_access()
    fetched_at = datetime.now(timezone.utc)
    screens = ("day_gainers", "day_losers")
    unavailable_screens = set(screens)
    incomplete_screens: set[str] = set()
    try:
        futures = {}
        for name in screens:
            future = _submit_market_provider_call(
                lambda name=name: yf.screen(name, count=25, session=_YAHOO_SESSION),
                timeout_seconds=25,
            )
            if future is not None:
                futures[name] = future
        # Yahoo's screener can be slow; keep enough time for both concurrent
        # lists while bounding the request.
        completed, pending = _wait_for_provider_futures(tuple(futures.values()), 25) if futures else (set(), set())
        if pending:
            for future in pending:
                _cancel_market_provider_call(future)
        responses = {}
        for name, future in futures.items():
            if future not in completed:
                continue
            try:
                response = future.result()
            except Exception:
                continue
            if isinstance(response, dict) and isinstance(response.get("quotes"), list):
                responses[name] = response["quotes"]
                unavailable_screens.discard(name)
    except Exception as exc:
        raise ResearchError("market_movers_unavailable", "Yahoo Finance market movers are temporarily unavailable.", 503) from exc
    if not responses:
        raise ResearchError("market_movers_unavailable", "Yahoo Finance market movers are temporarily unavailable.", 503)

    by_symbol: dict[str, dict] = {}
    for screen in screens:
        for quote in responses.get(screen, []):
            if not isinstance(quote, dict):
                incomplete_screens.add(screen)
                continue
            quote_type = quote.get("quoteType")
            if not isinstance(quote_type, str) or not quote_type.strip():
                incomplete_screens.add(screen)
                continue
            if quote_type.strip().upper() != "EQUITY":
                continue
            raw_currency = quote.get("currency")
            if not isinstance(raw_currency, str) or not raw_currency.strip():
                incomplete_screens.add(screen)
                continue
            currency = raw_currency.strip().upper()
            if currency != "USD":
                continue
            try:
                symbol = validate_ticker(quote.get("symbol"))
            except ResearchError:
                incomplete_screens.add(screen)
                continue
            price_field, timestamp_field = _daily_movement_quote_fields(quote)
            price = finite(quote.get(price_field))
            previous_close = finite(quote.get("regularMarketPreviousClose"))
            raw_timestamp = finite(quote.get(timestamp_field))
            if price is None or price <= 0 or previous_close is None or previous_close <= 0 or raw_timestamp is None:
                incomplete_screens.add(screen)
                continue
            timestamp = raw_timestamp / 1000 if raw_timestamp > 100_000_000_000 else raw_timestamp
            try:
                market_time = datetime.fromtimestamp(timestamp, timezone.utc)
            except (OverflowError, OSError, ValueError):
                incomplete_screens.add(screen)
                continue
            age = (fetched_at - market_time).total_seconds()
            if age > MAX_MARKET_QUOTE_AGE_SECONDS or age < -5 * 60:
                incomplete_screens.add(screen)
                continue
            change = price - previous_close
            percentage = change / abs(previous_close) * 100
            if not math.isfinite(percentage) or abs(percentage) < 0.01:
                if not math.isfinite(percentage):
                    incomplete_screens.add(screen)
                continue
            item = {
                "ticker": symbol,
                "change_percent": percentage,
                "absolute_change": change,
                "currency": currency,
                "market_at": market_time.isoformat(),
            }
            old = by_symbol.get(symbol)
            if old is None or market_time > datetime.fromisoformat(old["market_at"]):
                by_symbol[symbol] = item

    items = sorted(
        by_symbol.values(),
        key=lambda item: (
            -abs(item["change_percent"]),
            -abs(item["absolute_change"]),
            -datetime.fromisoformat(item["market_at"]).timestamp(),
            item["ticker"],
        ),
    )[:MARKET_TICKER_LIMIT]
    result = {
        "source": "Yahoo Finance",
        "period": "Daily market movement",
        "top_stocks_rank": "Most active US equities, ordered by trading volume",
        "top_stocks_label": "Top stocks",
        "retrieved_at": fetched_at.isoformat(),
        "items": items,
        "top_stocks": [],
    }
    coverage_groups = []
    for name in screens:
        label = "daily gainers" if name == "day_gainers" else "daily losers"
        if name in unavailable_screens:
            coverage_groups.append(f"{label} unavailable")
        elif name in incomplete_screens:
            coverage_groups.append(f"{label} contained unusable or stale quotes")
    if coverage_groups:
        result["coverage_note"] = f"Partial market data: {'; '.join(coverage_groups)}; this ranking may be incomplete."
    return result


def _popular_stock_history(fetched_at: datetime) -> dict:
    """Use Yahoo daily bars for a fixed familiar large-cap list, separate from volume ranks."""
    tickers = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
    try:
        provider_results, provider_unavailable = _bounded_provider_calls({
            "history": lambda: yf.download(
                " ".join(tickers), period="5d", auto_adjust=False,
                progress=False, threads=False, timeout=10, session=_YAHOO_SESSION,
            ),
        }, timeout_seconds=12)
        history = None if provider_unavailable else provider_results.get("history")
        closes = history.get("Close") if isinstance(history, pd.DataFrame) else None
        closes = _chronological_history(closes)
    except Exception:
        closes = None

    stocks = []
    if isinstance(closes, pd.DataFrame):
        dated_positions = {}
        for position, index_value in enumerate(closes.index):
            try:
                market_day = pd.Timestamp(index_value).date()
            except (TypeError, ValueError, OverflowError):
                continue
            if market_day <= fetched_at.date():
                dated_positions[market_day] = position
        market_days = sorted(dated_positions)
        for symbol in tickers:
            if symbol not in closes.columns:
                continue
            series = closes[symbol]
            current_day = None
            current = None
            for market_day in reversed(market_days):
                value = finite(series.iloc[dated_positions[market_day]])
                if value is not None and value > 0:
                    current_day, current = market_day, value
                    break
            if current_day is None or current is None:
                continue
            market_day = current_day
            current_position = market_days.index(current_day)
            if current_position == 0:
                continue
            previous_day = market_days[current_position - 1]
            previous = finite(series.iloc[dated_positions[previous_day]])
            if previous is None or previous <= 0:
                continue
            change = current - previous
            percentage = change / abs(previous) * 100
            if not math.isfinite(percentage):
                continue
            age_days = (fetched_at.date() - market_day).days
            if age_days < 0 or age_days > MAX_TOP_STOCK_FALLBACK_AGE_DAYS:
                continue
            market_date = market_day.isoformat()
            stocks.append({
                "ticker": symbol,
                "change_percent": percentage,
                "absolute_change": change,
                "currency": "USD",
                "market_at": market_date,
            })

    if not stocks:
        raise ResearchError("top_stocks_unavailable", "Yahoo Finance top stocks are temporarily unavailable.", 503)
    return {
        "source": "Yahoo Finance",
        "rank": "Popular large-cap stocks; not ranked by daily trading volume",
        "label": "Popular stocks",
        "retrieved_at": fetched_at.isoformat(),
        "items": stocks[:MARKET_TICKER_LIMIT],
    }


def daily_popular_stocks() -> dict:
    """Return comparable daily moves for familiar large-cap companies."""
    _require_yahoo_access()
    return _popular_stock_history(datetime.now(timezone.utc))


def daily_top_stocks() -> dict:
    """Return Yahoo's most-active US equities with fresh, comparable daily moves."""
    _require_yahoo_access()
    fetched_at = datetime.now(timezone.utc)
    future = _submit_market_provider_call(lambda: yf.screen("most_actives", count=25, session=_YAHOO_SESSION))
    try:
        if future is None:
            response = None
        else:
            completed, pending = _wait_for_provider_futures((future,), 12)
            if pending:
                _cancel_market_provider_call(future)
                response = None
            else:
                try:
                    response = future.result()
                except Exception:
                    response = None
    except Exception:
        response = None

    quotes = response.get("quotes") if isinstance(response, dict) else None
    if not isinstance(quotes, list):
        return _popular_stock_history(fetched_at)

    stocks: list[dict] = []
    incomplete_quotes = False
    seen: dict[str, int] = {}
    for quote in quotes:
        if not isinstance(quote, dict):
            incomplete_quotes = True
            continue
        quote_type = quote.get("quoteType")
        if not isinstance(quote_type, str) or not quote_type.strip():
            incomplete_quotes = True
            continue
        if quote_type.strip().upper() != "EQUITY":
            continue
        raw_currency = quote.get("currency")
        if not isinstance(raw_currency, str) or not raw_currency.strip():
            incomplete_quotes = True
            continue
        currency = raw_currency.strip().upper()
        if currency != "USD":
            continue
        try:
            symbol = validate_ticker(quote.get("symbol"))
        except ResearchError:
            incomplete_quotes = True
            continue
        price_field, timestamp_field = _daily_movement_quote_fields(quote)
        price = finite(quote.get(price_field))
        previous_close = finite(quote.get("regularMarketPreviousClose"))
        raw_timestamp = finite(quote.get(timestamp_field))
        if price is None or price <= 0 or previous_close is None or previous_close <= 0 or raw_timestamp is None:
            incomplete_quotes = True
            continue
        timestamp = raw_timestamp / 1000 if raw_timestamp > 100_000_000_000 else raw_timestamp
        try:
            market_time = datetime.fromtimestamp(timestamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            incomplete_quotes = True
            continue
        age = (fetched_at - market_time).total_seconds()
        if age > MAX_MARKET_QUOTE_AGE_SECONDS or age < -5 * 60:
            incomplete_quotes = True
            continue
        change = price - previous_close
        percentage = change / abs(previous_close) * 100
        if not math.isfinite(percentage):
            incomplete_quotes = True
            continue
        item = {
            "ticker": symbol,
            "change_percent": percentage,
            "absolute_change": change,
            "currency": "USD",
            "market_at": market_time.isoformat(),
        }
        prior_position = seen.get(symbol)
        if prior_position is None:
            seen[symbol] = len(stocks)
            stocks.append(item)
        elif market_time > datetime.fromisoformat(stocks[prior_position]["market_at"]):
            stocks[prior_position] = item
    if not stocks:
        result = _popular_stock_history(fetched_at)
    else:
        result = {
            "source": "Yahoo Finance",
            "rank": "Most active US equities, ordered by trading volume",
            "label": "Most active",
            "retrieved_at": fetched_at.isoformat(),
            "items": stocks[:MARKET_TICKER_LIMIT],
        }
    if incomplete_quotes:
        result["coverage_note"] = "Yahoo Finance most-active results contained unusable or stale quotes; this list may be incomplete."
    return result


def compare_snapshots(previous: Any, current: Any) -> dict:
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ResearchError("invalid_snapshots", "The saved analyses could not be read.")
    verified_previous = _verified_snapshot_claims(previous)
    verified_current = _verified_snapshot_claims(current)
    if verified_previous is None or verified_current is None:
        return {"comparable": False, "reason": "snapshot_unverified", "changes": [], "excluded_fields": []}
    previous, current = verified_previous, verified_current
    if isinstance(previous.get("ticker"), str) and isinstance(current.get("ticker"), str) and previous["ticker"].upper() != current["ticker"].upper():
        return {"comparable": False, "reason": "ticker_mismatch", "changes": []}
    if isinstance(previous.get("methodology_version"), str) and isinstance(current.get("methodology_version"), str) and previous["methodology_version"] != current["methodology_version"]:
        return {"comparable": False, "reason": "methodology_mismatch", "changes": [], "excluded_fields": []}
    if isinstance(previous.get("analysis_schema_version"), str) and isinstance(current.get("analysis_schema_version"), str) and previous["analysis_schema_version"] != current["analysis_schema_version"]:
        return {"comparable": False, "reason": "schema_mismatch", "changes": [], "excluded_fields": []}
    if isinstance(previous.get("reporting_currency"), str) and isinstance(current.get("reporting_currency"), str) and previous["reporting_currency"].upper() != current["reporting_currency"].upper():
        return {"comparable": False, "reason": "currency_mismatch", "changes": [], "excluded_fields": []}
    if not all(isinstance(value.get(key), str) and value.get(key).strip() for value in (previous, current) for key in ("ticker", "methodology_version", "analysis_schema_version", "reporting_currency")):
        return {"comparable": False, "reason": "snapshot_metadata_missing", "changes": [], "excluded_fields": []}
    if not _valid_snapshot(previous, require_saved_at=True) or not _valid_snapshot(current, require_saved_at=False):
        return {"comparable": False, "reason": "snapshot_schema_invalid", "changes": [], "excluded_fields": []}
    previous_core = {
        key: previous.get(key)
        for key in (
            "methodology_version", "score", "business_quality", "financial_strength",
            "market_expectations", "historical_growth", "consensus_growth", "required_growth",
            "revenue", "revenue_period", "free_cash_flow", "fcf_period", "enterprise_value",
            "model_fcf_margin", "discount_rate", "terminal_growth", "solver_status",
        )
    }
    current_core = {
        key: current.get(key)
        for key in (
            "methodology_version", "score", "business_quality", "financial_strength",
            "market_expectations", "historical_growth", "consensus_growth", "required_growth",
            "revenue", "revenue_period", "free_cash_flow", "fcf_period", "enterprise_value",
            "model_fcf_margin", "discount_rate", "terminal_growth", "solver_status",
        )
    }
    previous_core["user_assumptions"] = previous.get("user_assumptions")
    current_core["user_assumptions"] = current.get("user_assumptions")
    excluded_fields = []
    for field, period_field, source_signature in (
        ("revenue", "revenue_period", lambda item: item.get("revenue_source")),
        ("free_cash_flow", "fcf_period", _fcf_source_signature),
    ):
        if previous.get(period_field) != current.get(period_field):
            previous_core[field] = current_core[field] = None
            excluded_fields.append({"field": field, "reason": "reporting_period_changed"})
        elif source_signature(previous) != source_signature(current):
            previous_core[field] = current_core[field] = None
            excluded_fields.append({"field": field, "reason": "source_definition_changed"})

    if previous.get("consensus_growth") is not None or current.get("consensus_growth") is not None:
        previous_estimate_period = previous.get("analyst_estimate_period")
        current_estimate_period = current.get("analyst_estimate_period")
        supported_periods = {"Current-year analyst revenue estimate", "Next-year analyst revenue estimate"}
        if previous_estimate_period not in supported_periods or current_estimate_period not in supported_periods:
            previous_core["consensus_growth"] = current_core["consensus_growth"] = None
            excluded_fields.append({"field": "consensus_growth", "reason": "estimate_period_unavailable"})
        elif previous_estimate_period != current_estimate_period:
            previous_core["consensus_growth"] = current_core["consensus_growth"] = None
            excluded_fields.append({"field": "consensus_growth", "reason": "estimate_period_changed"})

    old_score_basis = _score_basis_signature(previous)
    new_score_basis = _score_basis_signature(current)
    if old_score_basis != new_score_basis:
        previous_core["score"] = current_core["score"] = None
        excluded_fields.append({"field": "score", "reason": "score_evidence_changed"})
    for field, subscore in (("business_quality", "business_quality"), ("financial_strength", "financial_strength")):
        if _subscore_basis_signature(previous, subscore) != _subscore_basis_signature(current, subscore):
            previous_core[field] = current_core[field] = None
            excluded_fields.append({"field": field, "reason": "score_evidence_changed"})
    result = snapshot_diff(previous_core, current_core)
    if previous.get("user_assumptions") != current.get("user_assumptions"):
        result["changes"] = [
            change for change in result["changes"]
            if change.get("field") != "user_assumptions"
        ]
        previous_assumptions = previous.get("user_assumptions")
        current_assumptions = current.get("user_assumptions")
        for assumption in ("starting_growth", "fcf_margin", "discount_rate", "terminal_growth"):
            before = previous_assumptions.get(assumption) if isinstance(previous_assumptions, dict) else None
            after = current_assumptions.get(assumption) if isinstance(current_assumptions, dict) else None
            if before == after:
                continue
            before_number = finite(before)
            after_number = finite(after)
            delta = after_number - before_number if before_number is not None and after_number is not None else None
            result["changes"].append({
                "field": f"user_{assumption}",
                "before": before,
                "after": after,
                "delta": delta if delta is None or math.isfinite(delta) else None,
            })
    result["excluded_fields"] = excluded_fields
    if result.get("comparable"):
        result["changes"] = result.get("changes", [])
        # These timestamps come from the server-signed claims, not the editable
        # browser snapshot metadata, so the UI can show when each data set was
        # actually retrieved without trusting client-provided dates.
        result["previous_retrieved_at"] = previous["retrieved_at"]
        result["current_retrieved_at"] = current["retrieved_at"]
    return result


def _timestamp_is_valid(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_snapshot(value: dict, *, require_saved_at: bool) -> bool:
    try:
        validate_ticker(value.get("ticker"))
    except ResearchError:
        return False
    currency = value.get("reporting_currency")
    if not isinstance(currency, str) or currency.upper() not in CURRENCY_PROFILES:
        return False
    sector = value.get("sector")
    if not isinstance(sector, str) or not sector.strip() or len(sector) > 100:
        return False
    estimate_period = value.get("analyst_estimate_period")
    if estimate_period is not None and (
        not isinstance(estimate_period, str) or not estimate_period.strip() or len(estimate_period) > 100
    ):
        return False
    if not _timestamp_is_valid(value.get("retrieved_at")) or (require_saved_at and not _timestamp_is_valid(value.get("saved_at"))):
        return False
    if not isinstance(value.get("sources"), dict):
        return False
    for field in (
        "score", "business_quality", "financial_strength", "market_expectations",
        "historical_growth", "consensus_growth", "required_growth", "revenue",
        "free_cash_flow", "enterprise_value", "model_fcf_margin", "discount_rate",
        "terminal_growth",
    ):
        item = value.get(field)
        if item is not None and finite(item) is None:
            return False
    for field in ("score", "business_quality", "financial_strength", "market_expectations"):
        item = value.get(field)
        if item is not None and not 0 <= finite(item) <= 100:
            return False
    model_fcf_margin = value.get("model_fcf_margin")
    if model_fcf_margin is not None and finite(model_fcf_margin) <= 0:
        return False
    discount_rate = value.get("discount_rate")
    terminal_growth = value.get("terminal_growth")
    if discount_rate is not None and not 0.03 <= finite(discount_rate) <= 0.20:
        return False
    if terminal_growth is not None and not 0 <= finite(terminal_growth) <= 0.06:
        return False
    if discount_rate is not None and terminal_growth is not None and discount_rate <= terminal_growth:
        return False
    for value_field, period_field, source_field in (
        ("revenue", "revenue_period", "revenue"),
        ("free_cash_flow", "fcf_period", "free_cash_flow"),
    ):
        amount = value.get(value_field)
        source = value["sources"].get(source_field)
        if not isinstance(source, dict) or not isinstance(source.get("source"), str) or not isinstance(source.get("kind"), str):
            return False
        if amount is not None:
            if not isinstance(value.get(period_field), str) or not value.get(period_field).strip():
                return False
            if source.get("period") != value.get(period_field) or source.get("currency") != currency:
                return False
            expected_source = value.get("revenue_source" if value_field == "revenue" else "fcf_source")
            if source.get("source") != expected_source:
                return False
    trace = value.get("score_trace")
    score = value.get("score")
    if score is None:
        if trace is not None:
            return False
    else:
        if not isinstance(trace, dict) or finite(trace.get("score")) is None or not math.isclose(finite(trace.get("score")), finite(score), rel_tol=1e-10, abs_tol=1e-10):
            return False
        if not has_sector_calibration(sector):
            return False
        if not 0 <= finite(trace.get("score")) <= 100:
            return False
        aggregate_components = trace.get("components")
        expected_aggregate_keys = {"business_quality", "financial_strength"}
        if (
            not isinstance(aggregate_components, list)
            or len(aggregate_components) != len(expected_aggregate_keys)
            or finite(trace.get("evidence_coverage")) is None
            or not 0 <= finite(trace.get("evidence_coverage")) <= 1
        ):
            return False
        aggregate_keys = [
            item.get("key") for item in aggregate_components if isinstance(item, dict)
        ]
        if (
            len(aggregate_keys) != len(expected_aggregate_keys)
            or not all(isinstance(key, str) for key in aggregate_keys)
            or set(aggregate_keys) != expected_aggregate_keys
        ):
            return False
        if not all(
            isinstance(item, dict)
            and isinstance(item.get("key"), str)
            and (item.get("value") is None or (finite(item.get("value")) is not None and 0 <= finite(item.get("value")) <= 100))
            and finite(item.get("evidence_coverage")) is not None
            and 0 <= finite(item.get("evidence_coverage")) <= 1
            for item in aggregate_components
        ):
            return False
        components_by_key = {item["key"]: item for item in aggregate_components}
        for key, top_level in (
            ("business_quality", value.get("business_quality")),
            ("financial_strength", value.get("financial_strength")),
        ):
            component_value = finite(components_by_key[key].get("value"))
            top_level_value = finite(top_level)
            if (component_value is None) != (top_level_value is None):
                return False
            if component_value is not None and not math.isclose(
                component_value, top_level_value, rel_tol=1e-10, abs_tol=1e-10
            ):
                return False
        subscores = trace.get("subscores")
        if not isinstance(subscores, dict) or set(subscores) != {"business_quality", "financial_strength"}:
            return False
        sector_model = get_sector_model(sector)
        for name, top_level in (
            ("business_quality", value.get("business_quality")),
            ("financial_strength", value.get("financial_strength")),
        ):
            detail = subscores.get(name)
            weights = sector_model["business_weights" if name == "business_quality" else "financial_weights"]
            if (
                not isinstance(detail, dict)
                or finite(detail.get("evidence_coverage")) is None
                or not 0 <= finite(detail.get("evidence_coverage")) <= 1
                or not isinstance(detail.get("components"), list)
                or len(detail["components"]) != len(weights)
            ):
                return False
            factor_keys = [item.get("key") for item in detail["components"] if isinstance(item, dict)]
            if len(factor_keys) != len(weights) or not all(isinstance(key, str) for key in factor_keys) or set(factor_keys) != set(weights):
                return False
            detail_score = detail.get("score")
            if detail_score is not None and (
                finite(detail_score) is None or not 0 <= finite(detail_score) <= 100
            ):
                return False
            top_level_score = finite(top_level)
            if detail_score is not None and (
                top_level_score is None
                or not math.isclose(top_level_score, finite(detail_score), rel_tol=1e-10, abs_tol=1e-10)
            ):
                return False
            if not all(
                isinstance(item, dict)
                and isinstance(item.get("key"), str)
                and (item.get("value") is None or (finite(item.get("value")) is not None and 0 <= finite(item.get("value")) <= 100))
                and finite(item.get("weight")) is not None
                and math.isclose(finite(item.get("weight")), weights[item["key"]], rel_tol=1e-10, abs_tol=1e-10)
                for item in detail["components"]
            ):
                return False
            total_weight = math.fsum(weights.values())
            available_weight = math.fsum(
                weights[item["key"]] for item in detail["components"] if item.get("value") is not None
            )
            component_coverage = available_weight / total_weight if total_weight > 0 else 0.0
            if not math.isclose(
                component_coverage, finite(detail["evidence_coverage"]), rel_tol=1e-10, abs_tol=1e-10
            ):
                return False
            expected_subscore = None
            if component_coverage >= 0.5 and available_weight > 0:
                expected_subscore = clamp(math.fsum(
                    finite(item["value"]) * weights[item["key"]]
                    for item in detail["components"] if item.get("value") is not None
                ) / available_weight)
            top_level_score = finite(top_level)
            if (expected_subscore is None) != (top_level_score is None):
                return False
            if expected_subscore is not None and not math.isclose(
                expected_subscore, top_level_score, rel_tol=1e-10, abs_tol=1e-10
            ):
                return False
        growth_inputs = trace.get("growth_comparison_inputs")
        if (
            not isinstance(growth_inputs, dict)
            or set(growth_inputs) != {"historical_growth", "required_growth", "growth_clamped"}
            or growth_inputs.get("historical_growth") is not None
            or growth_inputs.get("required_growth") is not None
            or growth_inputs.get("growth_clamped") is not False
            or trace.get("growth_comparison_available") is not False
            or not isinstance(growth_inputs.get("growth_clamped"), bool)
        ):
            return False
        historical_growth_input = growth_inputs.get("historical_growth")
        required_growth_input = growth_inputs.get("required_growth")
        if (
            historical_growth_input is not None and finite(historical_growth_input) is None
        ) or (
            required_growth_input is not None and finite(required_growth_input) is None
        ):
            return False
        expected_trace = supported_score_breakdown(
            {
                "score": value.get("business_quality"),
                "evidence_coverage": subscores["business_quality"]["evidence_coverage"],
            },
            {
                "score": value.get("financial_strength"),
                "evidence_coverage": subscores["financial_strength"]["evidence_coverage"],
            },
            historical_growth_input,
            required_growth_input,
            growth_clamped=growth_inputs["growth_clamped"],
        )
        if expected_trace["score"] is None or not math.isclose(
            expected_trace["score"], finite(score), rel_tol=1e-10, abs_tol=1e-10
        ):
            return False
        if not math.isclose(
            expected_trace["evidence_coverage"], finite(trace["evidence_coverage"]),
            rel_tol=1e-10, abs_tol=1e-10,
        ):
            return False
        expected_components = {item["key"]: item for item in expected_trace["components"]}
        for key, expected in expected_components.items():
            actual = components_by_key[key]
            expected_value = finite(expected.get("value"))
            actual_value = finite(actual.get("value"))
            if (expected_value is None) != (actual_value is None):
                return False
            if expected_value is not None and not math.isclose(
                expected_value, actual_value, rel_tol=1e-10, abs_tol=1e-10
            ):
                return False
            if not math.isclose(
                expected["evidence_coverage"], finite(actual["evidence_coverage"]),
                rel_tol=1e-10, abs_tol=1e-10,
            ):
                return False
    assumptions = value.get("user_assumptions")
    if assumptions is not None:
        try:
            validate_assumptions(assumptions)
        except ResearchError:
            return False
    return True


def _fcf_source_signature(value: dict) -> tuple[Any, Any]:
    source = value.get("sources", {}).get("free_cash_flow", {})
    return value.get("fcf_source"), source.get("kind") if isinstance(source, dict) else None


def _score_basis_signature(value: dict) -> tuple | None:
    trace = value.get("score_trace")
    if not isinstance(trace, dict):
        return None
    components = trace.get("components")
    if not isinstance(components, list):
        return None
    return (
        finite(trace.get("evidence_coverage")),
        tuple((item.get("key"), finite(item.get("evidence_coverage")), item.get("value") is not None) for item in components if isinstance(item, dict)),
        _subscore_basis_signature(value, "business_quality"),
        _subscore_basis_signature(value, "financial_strength"),
    )


def _subscore_basis_signature(value: dict, name: str) -> tuple | None:
    trace = value.get("score_trace")
    subscores = trace.get("subscores") if isinstance(trace, dict) else None
    detail = subscores.get(name) if isinstance(subscores, dict) else None
    if not isinstance(detail, dict) or not isinstance(detail.get("components"), list):
        return None
    return (
        finite(detail.get("evidence_coverage")),
        tuple(
            (item.get("key"), finite(item.get("weight")), item.get("value") is not None)
            for item in detail["components"] if isinstance(item, dict)
        ),
    )
