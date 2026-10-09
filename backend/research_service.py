"""Yahoo/SEC adapters and the single server-side TSRP analysis pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
import math
import os
import re
import time
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from engine import (
    CURRENCY_PROFILES,
    business_quality_detail,
    choose_model_fcf_margin,
    dcf_enterprise_value,
    financial_strength_detail,
    history_cagr_periods,
    model_rates,
    score_label,
    snapshot_diff,
    solve_required_growth_detail,
    supported_score_breakdown,
)
from financial_data import latest_period_aligned_fcf_detail, period_observations, trailing_aligned_fcf_margins
from methodology import get_sector_model


MAX_TICKER_LENGTH = 15
MAX_SEARCH_LENGTH = 80
MARKET_TICKER_LIMIT = 7
FORECAST_YEARS = 10
METHODOLOGY_VERSION = "score-v3"
ANALYSIS_SCHEMA_VERSION = "analysis-v2"
MAX_STATEMENT_AGE_DAYS = 550
SEC_TAGS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "assets": ("Assets",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    "debt_current": ("DebtCurrent", "LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
    "debt_long": ("LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
    "debt_total": ("LongTermDebtAndFinanceLeaseObligations", "LongTermDebt"),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", "CashProvidedByUsedInOperatingActivities"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements", "PurchaseOfPropertyPlantAndEquipment"),
}
SEC_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SEC_ENTITY_SUFFIXES = {
    "ag", "co", "company", "corp", "corporation", "inc", "incorporated",
    "limited", "llc", "lp", "ltd", "plc", "sa", "se", "the",
}
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


class ResearchError(Exception):
    """Safe, user-facing provider or validation error."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def finite(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def validate_ticker(value: Any) -> str:
    symbol = normalize_ticker(value)
    if not symbol or len(symbol) > MAX_TICKER_LENGTH or not TICKER_RE.fullmatch(symbol):
        raise ResearchError("invalid_ticker", "Enter a valid listed-company ticker.")
    if symbol.startswith("^") or "=" in symbol or symbol.endswith(("-USD", "-EUR", "-GBP", "-BTC", "-ETH")):
        raise ResearchError("unsupported_instrument", "TSRP analyzes listed company stocks only.")
    return symbol


def validate_search(value: Any) -> str:
    query = " ".join(str(value or "").split())
    if len(query) > MAX_SEARCH_LENGTH:
        raise ResearchError("query_too_long", f"Search is limited to {MAX_SEARCH_LENGTH} characters.")
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


def _fx_rate(source: str, target: str) -> float | None:
    if source.strip() in {"GBp", "GBX", "ZAc", "ILA"} or target.strip() in {"GBp", "GBX", "ZAc", "ILA"}:
        return None
    source, target = source.strip().upper(), target.strip().upper()
    if source not in CURRENCY_PROFILES or target not in CURRENCY_PROFILES:
        return None
    if source == target:
        return 1.0
    source_usd = _rate_to_usd(source)
    target_usd = _rate_to_usd(target)
    if source_usd is None or target_usd in (None, 0):
        return None
    return source_usd / target_usd


def _rate_to_usd(currency: str, inverse: bool = False) -> float | None:
    currency = currency.strip().upper()
    if currency not in CURRENCY_PROFILES:
        return None
    if currency == "USD":
        return 1.0
    direct = f"{currency}USD=X"
    reverse = f"USD{currency}=X"
    order = (reverse, direct) if inverse else (direct, reverse)
    for symbol in order:
        try:
            instrument = yf.Ticker(symbol)
            history = instrument.history(period="5d", timeout=8)
            if history is None or history.empty or "Close" not in history:
                continue
            latest = history["Close"].dropna()
            if latest.empty:
                continue
            stamp = pd.Timestamp(latest.index[-1])
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            age = (datetime.now(timezone.utc) - stamp.to_pydatetime()).total_seconds()
            if age > 72 * 60 * 60 or age < -5 * 60:
                continue
            price = finite(latest.iloc[-1])
            if price and price > 0:
                direct_pair = symbol == direct
                if inverse:
                    return 1 / price if direct_pair else price
                return price if direct_pair else 1 / price
        except Exception:
            continue
    return None


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
            history = yf.Ticker(symbol).history(period="5d", timeout=timeout)
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
            rate = quote if direct else 1 / quote
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
    dated = [item for item in (from_usd, to_usd) if item.get("period")]
    if len(dated) == 2:
        dates = [pd.Timestamp(item["period"]).date() for item in dated]
        if abs((dates[0] - dates[1]).days) > 1:
            return None
    rate = from_usd["rate"] / to_usd["rate"]
    if not math.isfinite(rate) or rate <= 0:
        return None
    return {
        "rate": rate,
        "pairs": [item["pair"] for item in dated],
        "directions": [item["direction"] for item in dated],
        "period": max((item["period"] for item in dated), default="Same-currency identity"),
    }


def _convert(value: float | None, source: str, target: str) -> float | None:
    number = finite(value)
    if number is None:
        return None
    rate = _fx_rate(source, target)
    return None if rate is None else number * rate


def _sec_identity() -> str | None:
    value = os.getenv("SEC_USER_AGENT", "").strip()
    return value if EMAIL_RE.search(value) else None


def _sec_names_match(left: Any, right: Any) -> bool:
    def tokens(value: Any) -> set[str]:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
        return {token for token in normalized.split() if token not in SEC_ENTITY_SUFFIXES}

    left_tokens, right_tokens = tokens(left), tokens(right)
    return bool(left_tokens and right_tokens and left_tokens == right_tokens)


def _sec_ticker_map(user_agent: str) -> dict[str, tuple[str, str]]:
    response = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=(4, 10),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Invalid SEC ticker map")
    output = {}
    for row in payload.values():
        if isinstance(row, dict) and row.get("ticker") and row.get("cik_str") is not None and row.get("title"):
            output[str(row["ticker"]).upper()] = (str(row["cik_str"]).zfill(10), str(row["title"]))
    return output


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
        response = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=(4, 15),
        )
        response.raise_for_status()
        facts = response.json()
        if not isinstance(facts, dict) or not isinstance(facts.get("facts"), dict):
            return None, {"kind": "parse_error", "label": "SEC returned no usable company facts"}
        if not _sec_names_match(company, facts.get("entityName")):
            return None, {"kind": "entity_mismatch", "label": "SEC ticker mapping and company-facts entity did not match"}
        return facts, {"kind": "available", "label": "Company facts available", "company": company, "cik": cik}
    except requests.Timeout:
        return None, {"kind": "timeout", "label": "SEC did not respond before the request timed out"}
    except requests.HTTPError as exc:
        code = getattr(exc.response, "status_code", None)
        if code == 429:
            return None, {"kind": "rate_limited", "label": "SEC temporarily limited this request"}
        return None, {"kind": "http_error", "label": "SEC returned an HTTP error"}
    except requests.RequestException:
        return None, {"kind": "network_error", "label": "SEC could not be reached"}
    except (KeyError, TypeError, ValueError):
        return None, {"kind": "parse_error", "label": "SEC data could not be parsed"}


def _sec_candidates(
    facts: dict | None,
    tags: tuple[str, ...],
    currencies: list[str],
    *,
    kind: str = "duration",
) -> tuple[list[dict], str | None, dict | None]:
    if not facts:
        return [], None, None
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
            unit_order = [unit for unit in currencies if unit in units]
            unit_order += [unit for unit in units if re.fullmatch(r"[A-Z]{3}", str(unit)) and unit not in unit_order]
            for unit in unit_order:
                annual = []
                for row in units.get(unit, []):
                    if not isinstance(row, dict) or row.get("form") not in SEC_FORMS or not row.get("end") or not row.get("filed") or finite(row.get("val")) is None:
                        continue
                    try:
                        end = pd.Timestamp(row["end"]).date()
                        filed = pd.Timestamp(row["filed"]).date()
                        start = pd.Timestamp(row["start"]).date() if row.get("start") else None
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if filed < end or filed > datetime.now(timezone.utc).date() + timedelta(days=1):
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
                for row in sorted(annual, key=lambda x: (str(x.get("filed", "")), str(x.get("accn", "")), str(x.get("end", ""))), reverse=True):
                    year = row.get("fy") or str(row.get("end", ""))[:4]
                    by_fy.setdefault(year, row)
                series_rows = sorted(by_fy.values(), key=lambda x: str(x.get("end", "")), reverse=True)
                latest = max(annual, key=lambda x: (str(x.get("end", "")), str(x.get("filed", ""))))
                candidates.append((str(latest.get("end", "")), str(latest.get("filed", "")), {
                    "value": finite(latest.get("val")),
                    "unit": unit,
                    "period": latest.get("end"),
                    "start": latest.get("start"),
                    "filed": latest.get("filed"),
                    "form": latest.get("form"),
                    "taxonomy": taxonomy,
                    "tag": tag,
                    "series": [(str(row.get("end", "")), finite(row.get("val"))) for row in series_rows],
                }))
                break
    if not candidates:
        return [], None, None
    record = max(candidates, key=lambda row: (row[0], row[1]))[2]
    return record["series"], record["unit"], record


def _sec_value(facts: dict | None, tags: tuple[str, ...], currencies: list[str], *, kind: str = "duration") -> dict | None:
    _, _, record = _sec_candidates(facts, tags, currencies, kind=kind)
    return record


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
    return {
        "value": cashflow - abs(spending),
        "period": str(ocf.get("period")),
        "operating_cash_flow": ocf,
        "capital_spending": capex,
    }


def _bounded_provider_calls(calls: dict[str, Any], timeout_seconds: float = 20.0) -> tuple[dict[str, Any], list[str]]:
    """Run provider reads concurrently under one total wait budget, without context-manager joins."""
    executor = ThreadPoolExecutor(max_workers=max(1, len(calls)))
    futures = {executor.submit(call): name for name, call in calls.items()}
    completed, pending = wait(tuple(futures), timeout=timeout_seconds)
    results: dict[str, Any] = {}
    for future in completed:
        name = futures[future]
        try:
            results[name] = future.result()
        except Exception:
            results[name] = None
    timed_out = [futures[future] for future in pending]
    for future in pending:
        future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)
    return results, timed_out


def _fetch_info(instrument: yf.Ticker) -> dict:
    try:
        return instrument.get_info() or {}
    except Exception:
        try:
            return instrument.info or {}
        except Exception:
            return {}


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


def _quote_timestamp(info: dict, fast_info: dict, history: pd.DataFrame) -> str | None:
    stamp = _first_number(info, "regularMarketTime")
    if stamp:
        try:
            return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    if history is not None and not history.empty:
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


def _clean_sector(value: Any) -> str:
    return str(value or "Unknown sector")[:100]


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
    instrument = yf.Ticker(ticker_symbol)
    provider_data, provider_timeouts = _bounded_provider_calls({
        "info": lambda: _fetch_info(instrument),
        "fast_info": lambda: dict(instrument.fast_info),
        "history": lambda: instrument.history(period="5y", auto_adjust=False, timeout=10),
        "financials": lambda: instrument.financials,
        "balance": lambda: instrument.balance_sheet,
        "cashflow": lambda: instrument.cashflow,
        "estimates": lambda: instrument.revenue_estimate,
        "sec": lambda: _sec_facts(ticker_symbol, "N/A", "N/A"),
    }, timeout_seconds=20)
    info = provider_data.get("info") if isinstance(provider_data.get("info"), dict) else {}
    quote_type = str(info.get("quoteType") or "").upper()
    if quote_type and quote_type != "EQUITY":
        raise ResearchError("unsupported_instrument", "TSRP analyzes listed company stocks only.")
    if not quote_type:
        raise ResearchError("company_unavailable", "Yahoo Finance could not confirm this as a listed company stock.", 404)

    fast_info = provider_data.get("fast_info") if isinstance(provider_data.get("fast_info"), dict) else {}
    history = _as_frame(provider_data.get("history"))
    financials = _as_frame(provider_data.get("financials"))
    balance = _as_frame(provider_data.get("balance"))
    cashflow = _as_frame(provider_data.get("cashflow"))
    estimate_frame = _as_frame(provider_data.get("estimates"))

    reporting_currency = str(info.get("financialCurrency") or info.get("currency") or fast_info.get("currency") or "N/A").strip()
    if reporting_currency not in {"GBp", "GBX", "ZAc", "ILA"}:
        reporting_currency = reporting_currency.upper()
    trading_currency = str(info.get("currency") or fast_info.get("currency") or "N/A").strip()
    sector = _clean_sector(info.get("sector"))
    model = get_sector_model(sector)
    rates = model_rates(reporting_currency, sector)
    currency_supported = reporting_currency in CURRENCY_PROFILES
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
    if not _first_number(info, "regularMarketTime") and history is not None and not history.empty and "Close" in history:
        closes = history["Close"].dropna()
        if len(closes) >= 2:
            price = finite(closes.iloc[-1])
            previous_close = finite(closes.iloc[-2])
    market_cap = _first_number(fast_info, "marketCap", "market_cap")
    if market_cap is None:
        market_cap = _first_number(info, "marketCap")
    if market_cap is not None and market_cap <= 0:
        market_cap = None

    yahoo_revenue_observations = period_observations(financials, ["Total Revenue", "Operating Revenue"])
    yahoo_revenue, yahoo_revenue_period = (yahoo_revenue_observations[0][1], str(yahoo_revenue_observations[0][0])[:10]) if yahoo_revenue_observations else (None, None)
    yahoo_gross, yahoo_gross_period = _row_period(financials, ("Gross Profit",))
    yahoo_operating_income, yahoo_operating_period = _row_period(financials, ("Operating Income",))
    yahoo_net_income, yahoo_net_income_period = _row_period(financials, ("Net Income", "Net Income Common Stockholders"))
    yahoo_ebitda, yahoo_ebitda_period = _row_period(financials, ("EBITDA", "Normalized EBITDA"))
    yahoo_fcf, yahoo_fcf_period, yahoo_fcf_method = latest_period_aligned_fcf_detail(cashflow, financials, ["Total Revenue", "Operating Revenue"])
    yahoo_cash, yahoo_cash_period = _row_period(balance, ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
    yahoo_debt, yahoo_debt_period = _row_period(balance, ("Total Debt", "Long Term Debt And Capital Lease Obligation"))
    yahoo_assets, yahoo_assets_period = _row_period(balance, ("Total Assets",))
    yahoo_equity, yahoo_equity_period = _row_period(balance, ("Stockholders Equity", "Total Equity Gross Minority Interest"))
    sec_result = provider_data.get("sec")
    sec_facts, sec_state = sec_result if isinstance(sec_result, tuple) and len(sec_result) == 2 else (None, {"kind": "unavailable", "label": "SEC company facts are unavailable"})
    sec_currencies = list(dict.fromkeys((reporting_currency, trading_currency, "USD")))
    sec_revenue_series, sec_revenue_unit, sec_revenue_record = _sec_candidates(sec_facts, SEC_TAGS["revenue"], sec_currencies, kind="duration")
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
                sec_debt_record = {**sec_debt_long, "tag": "current + noncurrent debt"}
    if sec_debt is None:
        sec_debt_record = _sec_value(sec_facts, SEC_TAGS["debt_total"], sec_currencies, kind="instant")
        sec_debt = finite(sec_debt_record.get("value")) if sec_debt_record else None

    sec_revenue = finite(sec_revenue_record.get("value")) if sec_revenue_record else None
    sec_unit = str(sec_revenue_record.get("unit") or sec_revenue_unit) if sec_revenue_record else sec_revenue_unit
    fx_flags = []
    fx_quotes: dict[str, dict | None] = {}
    fx_used: dict[tuple[str, str], dict] = {}
    fx_deadline = time.monotonic() + 6.0

    def convert_amount(value: float | None, source: str, target: str) -> float | None:
        number = finite(value)
        if number is None:
            return None
        key = (source.strip(), target.strip())
        if key not in fx_used:
            detail = _fx_rate_detail(key[0], key[1], fx_quotes, deadline=fx_deadline)
            if detail is not None:
                fx_used[key] = detail
            else:
                fx_used[key] = {"rate": None}
        rate = fx_used[key].get("rate")
        if rate is None:
            return None
        converted = number * rate
        if not math.isfinite(converted):
            fx_flags.append(f"Converted amount from {key[0]} to {key[1]} is outside the supported numeric range.")
            return None
        return converted

    def choose_sec_or_yahoo(record: dict | None, fallback: float | None, label: str) -> tuple[float | None, dict | None]:
        if record is None:
            return fallback, None
        unit = str(record.get("unit") or reporting_currency)
        raw = finite(record.get("value"))
        converted = convert_amount(raw, unit, reporting_currency)
        if converted is None and unit != reporting_currency:
            fx_flags.append(f"FX unavailable for {unit} to {reporting_currency}; {label} falls back only when Yahoo has a value.")
            return fallback, None
        return converted, record

    revenue, revenue_record = choose_sec_or_yahoo(sec_revenue_record, yahoo_revenue, "revenue")
    revenue_source = "SEC EDGAR" if revenue_record else ("Yahoo Finance" if yahoo_revenue is not None else None)
    revenue_period = str(revenue_record.get("period")) if revenue_record else yahoo_revenue_period
    net_income, net_income_record = choose_sec_or_yahoo(sec_net_income, yahoo_net_income, "net income")
    assets, assets_record = choose_sec_or_yahoo(sec_assets, yahoo_assets, "assets")
    equity, equity_record = choose_sec_or_yahoo(sec_equity, yahoo_equity, "equity")
    cash, cash_record = choose_sec_or_yahoo(sec_cash, yahoo_cash, "cash")
    debt, debt_record = choose_sec_or_yahoo(sec_debt_record, yahoo_debt, "debt")
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
        free_cash_flow = convert_amount(sec_cashflow_fcf, sec_unit or reporting_currency, reporting_currency)
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

    analysis_now = datetime.now(timezone.utc)
    revenue_age_days = _period_age_days(revenue_period, analysis_now)
    history_period = str(sec_revenue_record.get("period")) if revenue_source == "SEC EDGAR" and sec_revenue_record else yahoo_revenue_period
    history_age_days = _period_age_days(history_period, analysis_now)
    fcf_age_days = _period_age_days(fcf_period, analysis_now)
    balance_age_days = _period_age_days(debt_period, analysis_now)
    yahoo_income_age_days = _period_age_days(yahoo_revenue_period, analysis_now)
    net_income_age_days = _period_age_days(net_income_period, analysis_now)
    revenue_current = revenue_age_days is not None and 0 <= revenue_age_days <= MAX_STATEMENT_AGE_DAYS
    history_current = history_age_days is not None and 0 <= history_age_days <= MAX_STATEMENT_AGE_DAYS
    fcf_current = fcf_age_days is not None and 0 <= fcf_age_days <= MAX_STATEMENT_AGE_DAYS
    balance_current = cash_debt_comparable and balance_age_days is not None and 0 <= balance_age_days <= MAX_STATEMENT_AGE_DAYS
    yahoo_income_current = yahoo_income_age_days is not None and 0 <= yahoo_income_age_days <= MAX_STATEMENT_AGE_DAYS
    net_income_current = net_income_age_days is not None and 0 <= net_income_age_days <= MAX_STATEMENT_AGE_DAYS

    # Market cap is price × shares, so use the listing/quote currency, not the
    # separate currency in which the issuer reports its financial statements.
    market_cap_currency = trading_currency.strip()
    if market_cap_currency not in {"GBp", "GBX", "ZAc", "ILA"}:
        market_cap_currency = market_cap_currency.upper()
    market_cap_reporting = convert_amount(market_cap, market_cap_currency, reporting_currency)
    if market_cap is not None and market_cap_reporting is None and market_cap_currency != reporting_currency:
        fx_flags.append(f"FX unavailable or invalid for market capitalization from {market_cap_currency} to {reporting_currency}.")
    market_at = _quote_timestamp(info, fast_info, history)
    stale = True
    if market_at:
        try:
            parsed_market_at = datetime.fromisoformat(market_at.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - parsed_market_at.astimezone(timezone.utc)).total_seconds()
            stale = age_seconds > 60 * 60 * 48 or age_seconds < -5 * 60
        except (TypeError, ValueError, OverflowError):
            stale = True
    enterprise_value = None
    enterprise_value_period = None
    if (
        not stale
        and currency_supported
        and market_cap_reporting is not None
        and balance_current
        and cash is not None and cash >= 0
        and debt is not None and debt >= 0
    ):
        enterprise_value = finite(market_cap_reporting + debt - cash)
        if enterprise_value is not None:
            enterprise_value_period = f"Market data {market_at}; balance sheet {debt_period}"

    sec_growth_series = [(period, value) for period, value in sec_revenue_series if value is not None]
    yahoo_growth_series = yahoo_revenue_observations
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

    gross_margin = _period_ratio(yahoo_gross, yahoo_gross_period, yahoo_revenue, yahoo_revenue_period) if yahoo_income_current else None
    operating_margin = _period_ratio(yahoo_operating_income, yahoo_operating_period, yahoo_revenue, yahoo_revenue_period) if yahoo_income_current else None
    profit_margin = _period_ratio(net_income, net_income_period, revenue, revenue_period) if net_income_current and revenue_current else None
    fcf_margin = _period_ratio(free_cash_flow, fcf_period, revenue, revenue_period) if fcf_current and revenue_current else None
    yahoo_trailing_margins = [] if revenue_source == "SEC EDGAR" or not fcf_current else trailing_aligned_fcf_margins(
        cashflow, financials, ["Total Revenue", "Operating Revenue"], years=4
    )
    model_fcf_margin, model_fcf_refused, model_fcf_note = choose_model_fcf_margin(
        fcf_margin, yahoo_trailing_margins, model["financial_benchmarks"]["fcf"]
    )
    if net_income_record and equity_record:
        equity_observations = [
            (period, convert_amount(value, str(equity_record.get("unit") or reporting_currency), reporting_currency))
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
    if selected_margin is not None and selected_margin > 0 and currency_supported and not stale and revenue_current and fcf_current and balance_current:
        solve = solve_required_growth_detail(
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
        flags.append(_flag(f"The {provider_labels.get(provider, provider)} request timed out; affected values are unavailable.", "high"))
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
    if not currency_supported:
        flags.append(_flag("The reporting currency or quote subunit is not supported for the DCF; currency-dependent valuation is unavailable.", "high"))
    if stale:
        flags.append(_flag("The market quote is stale or has no verified timestamp; price-based valuation is unavailable.", "high"))
    if sec_state["kind"] != "available":
        flags.append(_flag("SEC company facts are unavailable. Yahoo Finance data remains separately identified."))
    if reporting_currency == "N/A" or trading_currency == "N/A":
        flags.append(_flag("The provider did not identify a reporting or trading currency; currency-dependent valuation is unavailable.", "high"))
    if fx_flags:
        flags.extend(_flag(message, "high") for message in sorted(set(fx_flags)))
    if sector == "Real Estate":
        flags.append(_flag("Real-estate caveat: this model uses free cash flow and earnings, not property-specific measures."))

    business_detail = business_quality_detail(historical_growth if history_current else None, gross_margin, operating_margin, fcf_margin, roe, debt_to_assets, model)
    scoring_cash = cash if balance_current and cash is not None and cash >= 0 else None
    scoring_debt = debt if balance_current and debt is not None and debt >= 0 else None
    financial_detail = financial_strength_detail(scoring_cash, scoring_debt, operating_margin, fcf_margin, debt_to_assets, model)
    business_quality = business_detail["score"]
    financial_strength = financial_detail["score"]
    score_trace = supported_score_breakdown(
        business_detail,
        financial_detail,
        historical_growth if history_current else None,
        required_growth,
        consensus_growth,
        growth_clamped=growth_clamped or (model_fcf_refused and assumptions is None),
    )
    final_score = score_trace["score"]
    score_is_defensible = final_score is not None

    data_fields = {
        "revenue": revenue is not None and revenue_current,
        "free_cash_flow": free_cash_flow is not None and fcf_current,
        "historical_growth": historical_growth is not None and history_current,
        "cash": cash is not None and balance_current,
        "debt": debt is not None and balance_current,
        "enterprise_value": enterprise_value is not None,
    }
    if not score_is_defensible:
        flags.append(_flag("Weighted evidence coverage is below the score threshold; missing inputs are not assigned substitute scores.", "high"))
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
        result = provenance("SEC EDGAR" if record else fallback_source, period, reporting_currency)
        if record:
            result.update({
                "filing_date": record.get("filed"),
                "filing_form": record.get("form"),
                "taxonomy": record.get("taxonomy"),
                "tag": record.get("tag"),
                "source_unit": record.get("unit"),
            })
        return result

    fcf_provenance = provenance(fcf_source, fcf_period, reporting_currency, fcf_method or "unavailable")
    if fcf_source == "SEC EDGAR":
        fcf_provenance["filing_facts"] = {
            "operating_cash_flow": fact_provenance(sec_fcf_detail.get("operating_cash_flow"), None, sec_fcf_period),
            "capital_spending": fact_provenance(sec_fcf_detail.get("capital_spending"), None, sec_fcf_period),
        }

    market_cap_conversion = fx_used.get((market_cap_currency, reporting_currency))
    if market_cap_currency != reporting_currency and market_cap_conversion and market_cap_conversion.get("rate") is not None:
        market_cap_reporting_source = provenance(
            "Yahoo Finance + TSRP FX conversion", market_at, reporting_currency,
            f"listing-currency market capitalization converted from {market_cap_currency} to {reporting_currency}",
        )
        market_cap_reporting_source["conversions"] = [{
            "from": market_cap_currency,
            "to": reporting_currency,
            "rate": market_cap_conversion.get("rate"),
            "pairs": market_cap_conversion.get("pairs", []),
            "directions": market_cap_conversion.get("directions", []),
            "period": market_cap_conversion.get("period"),
        }]
    else:
        market_cap_reporting_source = provenance(
            "Yahoo Finance" if market_cap_reporting is not None else None,
            market_at,
            reporting_currency,
            "market capitalization in listing quote currency" if market_cap_reporting is not None else "unavailable: quote currency or a valid FX rate is missing",
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

    sources = {
        "share_price": provenance("Yahoo Finance", _quote_timestamp(info, fast_info, history), trading_currency, "market data"),
        "market_cap": provenance("Yahoo Finance", market_at, market_cap_currency, "market data; currency follows the listing quote currency"),
        "market_cap_reporting": market_cap_reporting_source,
        "enterprise_value": provenance("TSRP calculation" if enterprise_value is not None else None, enterprise_value_period, reporting_currency, "market capitalization + same-date reported debt − cash; excludes other claims not separately captured"),
        "ev_sales": ev_sales_source,
        "ev_ebitda": ev_ebitda_source,
        "pe": pe_source,
        "valuation_assumptions": provenance("TSRP static calibration" if currency_supported else None, "No live market-yield timestamp", rates["currency"], rates["calibration_basis"]),
        "revenue": fact_provenance(revenue_record, revenue_source, revenue_period),
        "net_income": fact_provenance(net_income_record, "Yahoo Finance" if net_income is not None else None, net_income_period),
        "free_cash_flow": fcf_provenance,
        "historical_growth": provenance(history_source, f"{history_start} to {history_end}" if history_start and history_end else None, reporting_currency, "calculated from dated annual reported revenue"),
        "analyst_growth": provenance("Yahoo Finance analyst estimates" if consensus_growth is not None else None, consensus_label, reporting_currency, "consensus estimate"),
        "cash": fact_provenance(cash_record, "Yahoo Finance" if cash is not None else None, cash_period),
        "debt": fact_provenance(debt_record, "Yahoo Finance" if debt is not None else None, debt_period),
        "equity": fact_provenance(equity_record, "Yahoo Finance" if equity is not None else None, equity_period),
        "gross_margin": provenance("Yahoo Finance annual income statement" if gross_margin is not None else None, yahoo_gross_period, reporting_currency, "calculated from same-period gross profit / revenue"),
        "operating_margin": provenance("Yahoo Finance annual income statement" if operating_margin is not None else None, yahoo_operating_period, reporting_currency, "calculated from same-period operating income / revenue"),
        "profit_margin": provenance("Period-aligned net income / revenue" if profit_margin is not None else None, revenue_period, reporting_currency, "calculated"),
        "free_cash_flow_margin": provenance("Period-aligned free cash flow / revenue" if fcf_margin is not None else None, fcf_period, reporting_currency, "calculated"),
        "return_on_equity": provenance("SEC EDGAR" if net_income_record and equity_record else "Yahoo Finance" if roe is not None else None, net_income_period, reporting_currency, "net income / average beginning and ending equity"),
        "debt_to_assets": provenance("Period-aligned balance-sheet values" if debt_to_assets is not None else None, debt_period, reporting_currency, "calculated"),
        "fx": {"source": "Yahoo Finance daily FX history", "period": "; ".join(sorted({str(detail.get("period")) for detail in fx_used.values() if detail.get("period") and detail.get("period") != "Same-currency identity"})) or "No conversion used", "currency": reporting_currency, "kind": "timestamped conversion rates", "conversions": [{"from": key[0], "to": key[1], "rate": detail.get("rate"), "pairs": detail.get("pairs", []), "directions": detail.get("directions", []), "period": detail.get("period")} for key, detail in fx_used.items()]},
        "sec_status": sec_state,
    }
    refreshed = datetime.now(timezone.utc).isoformat()

    chart = []
    if history is not None and not history.empty and "Close" in history.columns:
        for index, row in history.tail(1300).iterrows():
            close = finite(row.get("Close"))
            if close is None:
                continue
            chart.append({
                "date": index.isoformat(),
                "close": close,
                "volume": finite(row.get("Volume")),
            })

    display = {
        "ticker": ticker_symbol,
        "company": str(info.get("longName") or info.get("shortName") or sec_state.get("company") or ticker_symbol)[:180],
        "exchange": str(info.get("fullExchangeName") or info.get("exchange") or "Exchange unavailable")[:80],
        "quote_type": str(info.get("quoteType") or "EQUITY")[:30],
        "sector": sector,
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
        "discount_rate": discount_rate,
        "terminal_growth": terminal_growth,
        "risk_free": rates["risk_free"],
        "equity_risk_premium": rates["erp"],
        "sector_spread": rates["sector_spread"],
        "rate_currency": rates["currency"],
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
        "score_label": score_label(final_score) if final_score is not None else "Unavailable",
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
        "freshness": "Yahoo Finance may provide delayed market data. Successful ticker analyses may be cached at the edge for 5 minutes and served stale for up to 15 minutes during refresh; availability depends on the deployment cache.",
        "methodology_version": METHODOLOGY_VERSION,
        "assumptions": assumptions or {
            "starting_growth": required_growth if solver_status == "exact" else None,
            "fcf_margin": selected_margin,
            "discount_rate": discount_rate,
            "terminal_growth": terminal_growth,
        },
        "user_assumptions": assumptions,
        "scenario": None,
    }
    if assumptions:
        scenario_available = (
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
            years=FORECAST_YEARS, start_margin=assumptions["fcf_margin"],
        ) if scenario_available else None
        display["scenario"] = {
            "enterprise_value": scenario_ev,
            "equity_value": finite(scenario_ev - debt + cash) if scenario_ev is not None and balance_current and cash_debt_comparable and debt is not None and debt >= 0 and cash is not None and cash >= 0 else None,
            "inputs": assumptions,
            "kind": "user scenario, not a forecast or target price",
        }
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
    values = {}
    for field, (low, high) in bounds.items():
        value = finite(payload.get(field))
        if value is None or not low <= value <= high:
            raise ResearchError("invalid_assumptions", "Assumptions are outside their supported ranges.")
        values[field] = value
    if values["discount_rate"] <= values["terminal_growth"]:
        raise ResearchError("invalid_assumptions", "The discount rate must exceed long-run growth.")
    return values


def analyze_scenario(symbol: Any, assumptions: Any) -> dict:
    values = validate_assumptions(assumptions)
    result = analyze_company(symbol, values)
    return result


def search_companies(query: Any) -> list[dict]:
    text = validate_search(query)
    if len(text) < 2:
        return []
    try:
        result = yf.Search(text, max_results=12, timeout=10).quotes
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


def daily_market_movers() -> dict:
    """Return Yahoo's daily gainers and losers, ranked by absolute movement."""
    fetched_at = datetime.now(timezone.utc)
    screens = ("day_gainers", "day_losers")
    executor = ThreadPoolExecutor(max_workers=len(screens))
    try:
        futures = {name: executor.submit(yf.screen, name, count=25) for name in screens}
        # Yahoo's screener can be slow; keep enough time for both concurrent
        # lists while bounding the request.
        completed, pending = wait(tuple(futures.values()), timeout=25)
        if pending:
            for future in pending:
                future.cancel()
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
    except Exception as exc:
        raise ResearchError("market_movers_unavailable", "Yahoo Finance market movers are temporarily unavailable.", 503) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if not responses:
        raise ResearchError("market_movers_unavailable", "Yahoo Finance market movers are temporarily unavailable.", 503)

    by_symbol: dict[str, dict] = {}
    for screen in screens:
        for quote in responses.get(screen, []):
            if not isinstance(quote, dict) or str(quote.get("quoteType", "")).upper() != "EQUITY":
                continue
            try:
                symbol = validate_ticker(quote.get("symbol"))
            except ResearchError:
                continue
            currency = str(quote.get("currency", "")).upper()
            if currency != "USD":
                continue
            price = finite(quote.get("regularMarketPrice"))
            previous_close = finite(quote.get("regularMarketPreviousClose"))
            raw_timestamp = finite(quote.get("regularMarketTime"))
            if price is None or price <= 0 or previous_close is None or previous_close <= 0 or raw_timestamp is None:
                continue
            timestamp = raw_timestamp / 1000 if raw_timestamp > 100_000_000_000 else raw_timestamp
            try:
                market_time = datetime.fromtimestamp(timestamp, timezone.utc)
            except (OverflowError, OSError, ValueError):
                continue
            age = (fetched_at - market_time).total_seconds()
            if age > 72 * 60 * 60 or age < -5 * 60:
                continue
            change = price - previous_close
            percentage = change / abs(previous_close) * 100
            if not math.isfinite(percentage) or abs(percentage) < 0.01:
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
    return {
        "source": "Yahoo Finance",
        "period": "Daily market movement",
        "top_stocks_rank": "Most active US equities, ordered by trading volume",
        "top_stocks_label": "Top stocks",
        "retrieved_at": fetched_at.isoformat(),
        "items": items,
        "top_stocks": [],
    }


def _popular_stock_history(fetched_at: datetime) -> dict:
    """Use real Yahoo daily bars for familiar large caps if its screener fails."""
    tickers = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
    try:
        history = yf.download(
            " ".join(tickers), period="5d", auto_adjust=False,
            progress=False, threads=False, timeout=10,
        )
        closes = history.get("Close") if isinstance(history, pd.DataFrame) else None
    except Exception:
        closes = None

    stocks = []
    if isinstance(closes, pd.DataFrame):
        for symbol in tickers:
            if symbol not in closes.columns:
                continue
            values = closes[symbol].dropna()
            if len(values) < 2:
                continue
            current = finite(values.iloc[-1])
            previous = finite(values.iloc[-2])
            if current is None or current <= 0 or previous is None or previous <= 0:
                continue
            change = current - previous
            percentage = change / abs(previous) * 100
            if not math.isfinite(percentage):
                continue
            try:
                market_date = values.index[-1].date().isoformat()
            except (AttributeError, TypeError, ValueError):
                continue
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


def daily_top_stocks() -> dict:
    """Return Yahoo's most-active US equities with fresh, comparable daily moves."""
    fetched_at = datetime.now(timezone.utc)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(yf.screen, "most_actives", count=25)
        completed, pending = wait((future,), timeout=12)
        if pending:
            future.cancel()
            response = None
        else:
            try:
                response = future.result()
            except Exception:
                response = None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    quotes = response.get("quotes") if isinstance(response, dict) else None
    if not isinstance(quotes, list):
        return _popular_stock_history(fetched_at)

    stocks: list[dict] = []
    seen = set()
    for quote in quotes:
        if not isinstance(quote, dict) or str(quote.get("quoteType", "")).upper() != "EQUITY":
            continue
        try:
            symbol = validate_ticker(quote.get("symbol"))
        except ResearchError:
            continue
        if symbol in seen or str(quote.get("currency", "")).upper() != "USD":
            continue
        seen.add(symbol)
        market_state = str(quote.get("marketState", "")).upper()
        price_field, timestamp_field = "regularMarketPrice", "regularMarketTime"
        if market_state == "PRE":
            price_field, timestamp_field = "preMarketPrice", "preMarketTime"
        elif market_state == "POST":
            price_field, timestamp_field = "postMarketPrice", "postMarketTime"
        price = finite(quote.get(price_field))
        previous_close = finite(quote.get("regularMarketPreviousClose"))
        raw_timestamp = finite(quote.get(timestamp_field))
        if price is None or price <= 0 or previous_close is None or previous_close <= 0 or raw_timestamp is None:
            continue
        timestamp = raw_timestamp / 1000 if raw_timestamp > 100_000_000_000 else raw_timestamp
        try:
            market_time = datetime.fromtimestamp(timestamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
        age = (fetched_at - market_time).total_seconds()
        if age > 72 * 60 * 60 or age < -5 * 60:
            continue
        change = price - previous_close
        percentage = change / abs(previous_close) * 100
        if not math.isfinite(percentage):
            continue
        stocks.append({
            "ticker": symbol,
            "change_percent": percentage,
            "absolute_change": change,
            "currency": "USD",
            "market_at": market_time.isoformat(),
        })
        if len(stocks) == MARKET_TICKER_LIMIT:
            break
    if not stocks:
        return _popular_stock_history(fetched_at)
    return {
        "source": "Yahoo Finance",
        "rank": "Most active US equities, ordered by trading volume",
        "label": "Top stocks",
        "retrieved_at": fetched_at.isoformat(),
        "items": stocks,
    }


def compare_snapshots(previous: Any, current: Any) -> dict:
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise ResearchError("invalid_snapshots", "The saved analyses could not be read.")
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
        for key in ("methodology_version", "score", "business_quality", "financial_strength", "historical_growth", "consensus_growth", "required_growth", "revenue", "revenue_period", "free_cash_flow", "fcf_period", "enterprise_value", "solver_status")
    }
    current_core = {
        key: current.get(key)
        for key in ("methodology_version", "score", "business_quality", "financial_strength", "historical_growth", "consensus_growth", "required_growth", "revenue", "revenue_period", "free_cash_flow", "fcf_period", "enterprise_value", "solver_status")
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
    result["excluded_fields"] = excluded_fields
    if result.get("comparable"):
        result["changes"] = result.get("changes", [])
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
    if not _timestamp_is_valid(value.get("retrieved_at")) or (require_saved_at and not _timestamp_is_valid(value.get("saved_at"))):
        return False
    if not isinstance(value.get("sources"), dict):
        return False
    for field in ("score", "business_quality", "financial_strength", "historical_growth", "consensus_growth", "required_growth", "revenue", "free_cash_flow", "enterprise_value"):
        item = value.get(field)
        if item is not None and finite(item) is None:
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
        if not isinstance(trace.get("components"), list) or finite(trace.get("evidence_coverage")) is None or not 0 <= finite(trace.get("evidence_coverage")) <= 1:
            return False
        if not all(isinstance(item, dict) and isinstance(item.get("key"), str) and (item.get("value") is None or finite(item.get("value")) is not None) for item in trace["components"]):
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
        tuple((item.get("key"), item.get("value") is not None) for item in detail["components"] if isinstance(item, dict)),
    )
