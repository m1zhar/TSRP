"""Pure market-cap unit resolution; do not confuse price subunits with totals."""

import math

from engine import normalize_market_currency, quote_currency_major_unit
from financial_data import finite_product, safe_number


# A magnitude cross-check, not a claim the provider share count is up to date.
# Allows modest asynchronous quote/share revisions, never a 100x unit mistake.
MAX_MARKET_CAP_SCALE_DIFFERENCE = .05


def _positive(source, *keys):
    for key in keys:
        value = safe_number(source.get(key))
        if value is not None and value > 0:
            return value
    return None


def resolve_market_cap(profile, fast, trading_currency):
    """Return market cap in the declared quote unit, with normalization evidence.

    yfinance FastInfo normally multiplies shares by the quote, so GBp values
    are pence. Its fallback and profile marketCap can already be pounds.
    Subunit totals therefore require a price × shares magnitude cross-check;
    the field name or listing currency alone does not establish their scale.
    No ADR ratio, share-class aggregation or missing share count is invented.
    """
    profile = profile if isinstance(profile, dict) else {}
    fast = fast if isinstance(fast, dict) else {}
    currency = normalize_market_currency(trading_currency)
    major, scale = quote_currency_major_unit(currency)
    failure = {"value": None, "raw_value": None, "raw_currency": None,
               "currency": currency, "kind": "Market capitalization is unavailable.", "unit_warning": False}
    for source, label in ((fast, "fast quote"), (profile, "company profile")):
        value = _positive(source, "marketCap", "market_cap")
        if value is None:
            continue
        if not currency or currency == "N/A":
            return {**failure, "kind": "Market capitalization has no verified listing currency."}
        if scale == 1:
            if normalize_market_currency(source.get("currency") or currency) != currency:
                continue
            return {**failure, "value": value, "raw_value": value, "raw_currency": currency,
                    "kind": f"Yahoo Finance {label} market capitalization in listing quote currency"}

        references = [source] if source is profile else [source, profile]
        candidates = []
        for reference in references:
            reference_currency = normalize_market_currency(reference.get("currency"))
            to_quote = 1.0 if reference_currency == currency else None
            if reference_currency == major:
                to_quote = 1 / scale
            price = _positive(reference, "lastPrice", "last_price", "currentPrice", "regularMarketPrice")
            shares = _positive(reference, "shares", "sharesOutstanding")
            quoted_total = finite_product(finite_product(price, shares), to_quote)
            major_total = finite_product(quoted_total, scale)
            if quoted_total is None or major_total is None or quoted_total <= 0 or major_total <= 0:
                continue
            for raw_currency, reference_total, multiplier in ((currency, quoted_total, 1.0), (major, major_total, 1 / scale)):
                if math.isclose(value, reference_total, rel_tol=MAX_MARKET_CAP_SCALE_DIFFERENCE, abs_tol=0):
                    normalized = finite_product(value, multiplier)
                    if normalized is not None:
                        candidates.append((normalized, raw_currency, multiplier))
            # Prefer the selected source's own price/share observations. Do not
            # let a conflicting profile reference choose a different scale.
            if candidates:
                break
        if len(candidates) == 1:
            normalized, raw_currency, multiplier = candidates[0]
            return {**failure, "value": normalized, "raw_value": value, "raw_currency": raw_currency,
                    "kind": f"Yahoo Finance {label} market capitalization; original unit {raw_currency}, normalized to {currency} by ×{multiplier:g}; unit scale cross-checked against quote × shares within 5%, not independent share-count validation"}
    return {**failure, "unit_warning": scale != 1,
            "kind": "Market capitalization unit scale could not be verified against comparable price and shares; dependent valuation is unavailable." if scale != 1 else failure["kind"]}
