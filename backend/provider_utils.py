"""Small shared helpers for reading upstream market-provider objects."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from financial_data import safe_number


# Provider diagnostics can contain raw tickers or company-search text. Suppress
# yfinance's logger before calls; TSRP logs only safe request outcomes itself.
_YFINANCE_LOGGER = logging.getLogger("yfinance")
_YFINANCE_LOGGER.setLevel(logging.CRITICAL + 1)
_YFINANCE_LOGGER.propagate = False


def quote_from_parts(parts: Any) -> dict:
    """Read a quote result safely, including the legacy error fallback shape."""
    if not isinstance(parts, dict):
        return {}
    quote = parts.get("quote")
    return quote if isinstance(quote, dict) else {}


def resolve_quote_currencies(profile: Any, fast_info: Any) -> tuple[Any, Any]:
    """Return only explicitly sourced currencies; never infer reporting from the quote."""
    profile = profile if isinstance(profile, dict) else {}
    fast_info = fast_info if isinstance(fast_info, dict) else {}
    trading = fast_info.get("currency") or profile.get("currency")
    reporting = profile.get("financialCurrency")
    return reporting or None, trading or None


def get_ticker_info(instrument: Any) -> dict | None:
    """Read company metadata once; use the legacy property only if needed."""
    getter = getattr(instrument, "get_info", None)
    response = getter() if callable(getter) else getattr(instrument, "info", None)
    return response if isinstance(response, dict) and response else None


def get_ticker_fast_info(instrument: Any) -> dict:
    """Read only used quote fields, isolating optional upstream field failures.

    Iterating FastInfo eagerly evaluates unrelated statistics and can discard
    a usable quote when a single unused property fails.
    """
    fast = instrument.fast_info
    result = {}
    for key, aliases in {
        "currency": ("currency",),
        "lastPrice": ("lastPrice", "last_price"),
        "previousClose": ("previousClose", "previous_close"),
        "marketCap": ("marketCap", "market_cap"),
        "shares": ("shares",),
    }.items():
        for alias in aliases:
            try:
                value = fast.get(alias)
            except Exception:
                # Do not log upstream exception text or discard other fields.
                continue
            if key == "currency":
                usable = value.strip() if isinstance(value, str) else None
            else:
                usable = safe_number(value)
            if usable is not None and usable != "":
                result[key] = usable
                break
    return result


def get_revenue_estimates(instrument: Any):
    """Preserve fiscal dates omitted by yfinance's public estimate dataframe.

    Read only the already-fetched pinned adapter cache. No private network call,
    extra request, inferred year or dependency on the cache being present.
    """
    frame = instrument.revenue_estimate
    if not isinstance(frame, pd.DataFrame):
        return frame
    result = frame.copy()
    try:
        trend = getattr(getattr(instrument, "_analysis", None), "_earnings_trend", None)
    except Exception:
        trend = None
    if isinstance(trend, list):
        metadata = {}
        for item in trend:
            if not isinstance(item, dict) or item.get("period") not in {"0y", "+1y"}:
                continue
            period = item["period"]
            row = {"end_date": item.get("endDate")}
            if period in metadata and metadata[period] != row:
                metadata[period] = {"end_date": None}
            else:
                metadata[period] = row
        result.attrs["fiscal_periods"] = metadata
    return result


def provider_ticker_matches(requested: Any, returned: Any) -> bool:
    """Check provider identity, allowing Yahoo's dot/hyphen class-share aliases."""
    if not isinstance(requested, str) or not requested.strip():
        return False
    if returned is None:
        return True  # Some provider-compatible profiles omit the symbol field.
    if not isinstance(returned, str):
        return False
    if not returned:
        return True
    if not returned.strip():
        return False
    requested_symbol = requested.strip().upper()
    returned_symbol = returned.strip().upper()
    return requested_symbol == returned_symbol or requested_symbol.replace(".", "-") == returned_symbol.replace(".", "-")
