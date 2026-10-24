"""Pure, currency-aware reverse-DCF calculations."""

from __future__ import annotations

import math
import re

FORECAST_YEARS = 10
FADE_START_YEAR = 5
SOLVER_LOW = -0.40
SOLVER_HIGH = 1.20
SOLVER_MIN_RELATIVE_VALUE_SPREAD = 1e-12
QUOTE_CURRENCY_SUBUNITS_TO_MAJOR = {
    "GBp": ("GBP", 0.01),
    "GBX": ("GBP", 0.01),
    "ZAc": ("ZAR", 0.01),
    "ILA": ("ILS", 0.01),
}
QUOTE_CURRENCY_SUBUNITS = frozenset(QUOTE_CURRENCY_SUBUNITS_TO_MAJOR)

# Long-run nominal bases by reporting currency. These are calibrated levels,
# not live yields — the important property is internal consistency:
# cash, discount rate, and terminal growth share the same money world.
CURRENCY_PROFILES = {
    "USD": {"risk_free": 0.043, "terminal_growth": 0.024, "erp": 0.045, "label": "US dollar"},
    "EUR": {"risk_free": 0.026, "terminal_growth": 0.018, "erp": 0.048, "label": "Euro"},
    "GBP": {"risk_free": 0.040, "terminal_growth": 0.021, "erp": 0.046, "label": "Sterling"},
    "JPY": {"risk_free": 0.010, "terminal_growth": 0.007, "erp": 0.052, "label": "Yen"},
    "CNY": {"risk_free": 0.022, "terminal_growth": 0.028, "erp": 0.055, "label": "Yuan"},
    "KRW": {"risk_free": 0.030, "terminal_growth": 0.022, "erp": 0.055, "label": "Won"},
    "HKD": {"risk_free": 0.035, "terminal_growth": 0.022, "erp": 0.050, "label": "Hong Kong dollar"},
    "CAD": {"risk_free": 0.033, "terminal_growth": 0.021, "erp": 0.047, "label": "Canadian dollar"},
    "AUD": {"risk_free": 0.040, "terminal_growth": 0.024, "erp": 0.048, "label": "Australian dollar"},
    "CHF": {"risk_free": 0.008, "terminal_growth": 0.010, "erp": 0.048, "label": "Swiss franc"},
    "INR": {"risk_free": 0.070, "terminal_growth": 0.050, "erp": 0.055, "label": "Rupee"},
    "SAR": {"risk_free": 0.045, "terminal_growth": 0.025, "erp": 0.050, "label": "Riyal"},
    "AED": {"risk_free": 0.045, "terminal_growth": 0.024, "erp": 0.050, "label": "Dirham"},
    "TWD": {"risk_free": 0.015, "terminal_growth": 0.018, "erp": 0.050, "label": "Taiwan dollar"},
    "DKK": {"risk_free": 0.025, "terminal_growth": 0.018, "erp": 0.048, "label": "Krone"},
    "SEK": {"risk_free": 0.022, "terminal_growth": 0.018, "erp": 0.048, "label": "Krona"},
    "NOK": {"risk_free": 0.035, "terminal_growth": 0.020, "erp": 0.048, "label": "Krone"},
    "SGD": {"risk_free": 0.028, "terminal_growth": 0.020, "erp": 0.047, "label": "Singapore dollar"},
    "BRL": {"risk_free": 0.110, "terminal_growth": 0.040, "erp": 0.060, "label": "Real"},
    "MXN": {"risk_free": 0.090, "terminal_growth": 0.035, "erp": 0.055, "label": "Peso"},
}

SECTOR_SPREAD = {
    "Utilities": -0.018,
    "Consumer Defensive": -0.012,
    "Real Estate": -0.008,
    "Industrials": -0.002,
    "Communication Services": 0.000,
    "Technology": 0.006,
    "Healthcare": 0.006,
    "Consumer Cyclical": 0.010,
    "Basic Materials": 0.012,
    "Energy": 0.016,
}


def _f(value, default=None):
    try:
        if value is None or isinstance(value, bool):
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except (TypeError, ValueError, OverflowError):
        return default


def clamp(value, low=0.0, high=100.0):
    value = _f(value, 0.0)
    return max(low, min(high, value))


def normalize_market_currency(code, default=""):
    raw = str(code or default or "").strip()
    if raw.upper() in {"N/A", "NA", "UNKNOWN", "NONE", "NULL"}:
        return ""
    return raw if raw in QUOTE_CURRENCY_SUBUNITS else raw.upper()


def fixed_quote_currency_conversion_rate(source, target):
    """Return an exact quote-unit scale only when both codes share a major currency."""
    source_code = normalize_market_currency(source)
    target_code = normalize_market_currency(target)
    if source_code not in QUOTE_CURRENCY_SUBUNITS and target_code not in QUOTE_CURRENCY_SUBUNITS:
        return None
    source_base, source_scale = QUOTE_CURRENCY_SUBUNITS_TO_MAJOR.get(source_code, (source_code, 1.0))
    target_base, target_scale = QUOTE_CURRENCY_SUBUNITS_TO_MAJOR.get(target_code, (target_code, 1.0))
    if not source_base or source_base != target_base:
        return None
    return source_scale / target_scale


def quote_currency_major_unit(code):
    """Return the major ISO currency and quote-unit scale for a market currency."""
    normalized = normalize_market_currency(code)
    return QUOTE_CURRENCY_SUBUNITS_TO_MAJOR.get(normalized, (normalized, 1.0))


def normalize_company_currencies(reporting_currency, trading_currency):
    reporting = normalize_market_currency(reporting_currency, "")
    trading = normalize_market_currency(trading_currency, "")
    return reporting or "N/A", trading or "N/A"


def currency_pair_uses_compatible_units(source_currency, target_currency):
    source = normalize_market_currency(source_currency, "")
    target = normalize_market_currency(target_currency, "")
    if not source or not target:
        return False
    if source == target:
        return True
    return source not in QUOTE_CURRENCY_SUBUNITS and target not in QUOTE_CURRENCY_SUBUNITS


def currency_profile(code):
    raw = normalize_market_currency(code, "")
    if not raw or raw in QUOTE_CURRENCY_SUBUNITS:
        return CURRENCY_PROFILES["USD"], "USD", True
    key = raw.upper()
    if key in CURRENCY_PROFILES:
        return CURRENCY_PROFILES[key], key, False
    return CURRENCY_PROFILES["USD"], "USD", True


def currency_profile_is_supported(reporting_currency):
    """Whether the requested reporting currency has an explicit model profile."""
    return not currency_profile(reporting_currency)[2]


def can_solve_reverse_dcf(reporting_currency, sector_methodology_supported, fcf_margin):
    """Require supported currency/sector evidence and a positive cash-flow margin."""
    margin = _f(fcf_margin)
    return bool(sector_methodology_supported) and currency_profile_is_supported(reporting_currency) and margin is not None and margin > 0


def model_rates(reporting_currency, sector=None):
    """Discount rate and terminal growth for this cash-flow currency + sector."""
    profile, code, used_fallback = currency_profile(reporting_currency)
    spread = SECTOR_SPREAD.get(sector or "", 0.0)
    terminal = profile["terminal_growth"]
    discount = profile["risk_free"] + profile["erp"] + spread
    floor = terminal + 0.015
    discount = min(max(discount, floor), 0.22)
    return {
        "discount_rate": round(discount, 4),
        "terminal_growth": round(terminal, 4),
        "risk_free": profile["risk_free"],
        "erp": profile["erp"],
        "sector_spread": spread,
        "currency": code,
        "currency_label": profile["label"],
        "used_fallback_currency": used_fallback,
        "calibration_basis": "Illustrative sensitivity only: static currency/sector inputs, not a live yield or issuer-specific WACC; cash-flow/discount-rate matching is not independently validated",
    }


def year_growth(initial_growth, terminal_growth, year, years=FORECAST_YEARS, fade_start=FADE_START_YEAR):
    """High growth through fade_start, then linear fade to terminal by year `years`."""
    initial_growth = _f(initial_growth)
    terminal_growth = _f(terminal_growth)
    year = _f(year)
    years = _f(years)
    fade_start = _f(fade_start)
    if None in (initial_growth, terminal_growth, year, years, fade_start) or years <= 0 or year <= 0:
        return None
    if year <= fade_start:
        return initial_growth
    span = max(years - fade_start, 1)
    t = (year - fade_start) / span
    return initial_growth + (terminal_growth - initial_growth) * t


def year_margin(start_margin, mature_margin, year, years=FORECAST_YEARS):
    start_margin = _f(start_margin)
    mature_margin = _f(mature_margin)
    year = _f(year)
    years = _f(years)
    if year is None or years is None or years <= 0 or year <= 0:
        return None
    if start_margin is None or mature_margin is None or start_margin == mature_margin:
        return mature_margin if mature_margin is not None else start_margin
    t = year / years
    return start_margin + (mature_margin - start_margin) * t


def dcf_enterprise_value(
    revenue,
    growth,
    fcf_margin,
    discount_rate,
    terminal_growth,
    years=FORECAST_YEARS,
    fade_start=FADE_START_YEAR,
    start_margin=None,
):
    revenue = _f(revenue)
    growth = _f(growth)
    fcf_margin = _f(fcf_margin)
    discount_rate = _f(discount_rate)
    terminal_growth = _f(terminal_growth)
    start_margin = fcf_margin if start_margin is None else _f(start_margin)

    years_value = _f(years)
    fade_start_value = _f(fade_start)
    if (
        years_value is None
        or fade_start_value is None
        or not years_value.is_integer()
        or not fade_start_value.is_integer()
    ):
        return None
    years = int(years_value)
    fade_start = int(fade_start_value)
    if revenue is None or growth is None or fcf_margin is None or discount_rate is None or terminal_growth is None or start_margin is None:
        return None
    if years <= 0 or years > 50 or fade_start < 0 or fade_start > years:
        return None
    if revenue <= 0 or fcf_margin <= 0 or start_margin <= 0 or growth <= -1 or discount_rate <= terminal_growth:
        return None
    if terminal_growth <= -1 or discount_rate <= -1:
        return None

    try:
        present_value = 0.0
        projected_revenue = revenue
        final_fcf = 0.0
        for year in range(1, years + 1):
            annual_growth = year_growth(growth, terminal_growth, year, years, fade_start)
            if annual_growth is None or annual_growth <= -1:
                return None
            projected_revenue *= 1 + annual_growth
            margin = year_margin(start_margin, fcf_margin, year, years)
            if not math.isfinite(projected_revenue) or margin is None or margin <= 0:
                return None
            final_fcf = projected_revenue * margin
            discount_factor = (1 + discount_rate) ** year
            if not math.isfinite(final_fcf) or not math.isfinite(discount_factor) or discount_factor <= 0:
                return None
            present_value += final_fcf / discount_factor

        terminal_value = final_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
        present_value += terminal_value / ((1 + discount_rate) ** years)
        return present_value if math.isfinite(present_value) and present_value > 0 else None
    except (OverflowError, ZeroDivisionError):
        return None


def solve_required_growth(
    enterprise_value,
    revenue,
    fcf_margin,
    discount_rate,
    terminal_growth,
    years=FORECAST_YEARS,
    fade_start=FADE_START_YEAR,
    start_margin=None,
    low=SOLVER_LOW,
    high=SOLVER_HIGH,
):
    """Return (growth, hit_bound). hit_bound True if result sits on the search wall."""
    detail = solve_required_growth_detail(
        enterprise_value,
        revenue,
        fcf_margin,
        discount_rate,
        terminal_growth,
        years,
        fade_start,
        start_margin,
        low,
        high,
    )
    return detail["growth"], detail["status"] in {"below_range", "above_range"}


def solve_required_growth_detail(
    enterprise_value,
    revenue,
    fcf_margin,
    discount_rate,
    terminal_growth,
    years=FORECAST_YEARS,
    fade_start=FADE_START_YEAR,
    start_margin=None,
    low=SOLVER_LOW,
    high=SOLVER_HIGH,
):
    """Solve reverse DCF growth and explicitly classify bracket outcomes."""
    target = _f(enterprise_value)
    low = _f(low)
    high = _f(high)
    if target is None or target <= 0 or low is None or high is None or low >= high:
        return {"growth": None, "status": "unavailable", "low": low, "high": high}

    low_value = dcf_enterprise_value(
        revenue, low, fcf_margin, discount_rate, terminal_growth, years, fade_start, start_margin
    )
    high_value = dcf_enterprise_value(
        revenue, high, fcf_margin, discount_rate, terminal_growth, years, fade_start, start_margin
    )
    if low_value is None or high_value is None:
        return {"growth": None, "status": "unavailable", "low": low, "high": high}
    if high_value < low_value:
        return {"growth": None, "status": "unavailable", "low": low, "high": high}
    if target < low_value:
        return {"growth": low, "status": "below_range", "low": low, "high": high}
    if target > high_value:
        return {"growth": high, "status": "above_range", "low": low, "high": high}
    # If the supported growth interval changes modeled value by less than
    # floating-point resolution, reverse DCF cannot identify a meaningful rate.
    if math.isclose(low_value, high_value, rel_tol=SOLVER_MIN_RELATIVE_VALUE_SPREAD, abs_tol=0.0):
        return {"growth": None, "status": "unavailable", "low": low, "high": high}
    if target == low_value:
        return {"growth": low, "status": "exact", "low": low, "high": high}
    if target == high_value:
        return {"growth": high, "status": "exact", "low": low, "high": high}

    lo, hi = low, high
    for _ in range(90):
        mid = (lo + hi) / 2
        value = dcf_enterprise_value(
            revenue, mid, fcf_margin, discount_rate, terminal_growth, years, fade_start, start_margin
        )
        if value is None:
            return {"growth": None, "status": "unavailable", "low": low, "high": high}
        if value < target:
            lo = mid
        else:
            hi = mid

    return {"growth": (lo + hi) / 2, "status": "exact", "low": low, "high": high}


def solve_supported_required_growth(
    reporting_currency,
    sector_methodology_supported,
    enterprise_value,
    revenue,
    fcf_margin,
    discount_rate,
    terminal_growth,
    years=FORECAST_YEARS,
    fade_start=FADE_START_YEAR,
    start_margin=None,
):
    """Solve only when the model has a supported currency, sector, and FCF basis."""
    if not can_solve_reverse_dcf(reporting_currency, sector_methodology_supported, fcf_margin):
        return {"growth": None, "status": "unavailable", "low": None, "high": None}
    return solve_required_growth_detail(
        enterprise_value,
        revenue,
        fcf_margin,
        discount_rate,
        terminal_growth,
        years,
        fade_start,
        start_margin,
    )


def cagr(start, end, years):
    start = _f(start)
    end = _f(end)
    years = _f(years)
    if start is None or end is None or years is None:
        return None
    if start <= 0 or end <= 0 or years <= 0:
        return None
    try:
        result = math.exp((math.log(end) - math.log(start)) / years) - 1
    except (OverflowError, ZeroDivisionError, ValueError):
        return None
    return result if math.isfinite(result) else None


def history_cagr(values_newest_first, max_years=10):
    """CAGR for contiguous annual values, newest first; use dated rows when available."""
    if isinstance(max_years, bool) or not isinstance(max_years, int) or max_years < 1:
        return None, 0
    if values_newest_first is None:
        return None, 0
    try:
        raw = list(values_newest_first)
    except TypeError:
        return None, 0
    if len(raw) < 2:
        return None, 0
    series = [_f(value) for value in raw[:max_years + 1]]
    if len(series) < 2 or any(value is None or value <= 0 for value in series):
        return None, 0
    years = len(series) - 1
    newest = series[0]
    oldest = series[-1]
    return cagr(oldest, newest, years), years


def history_cagr_periods(observations_newest_first, max_intervals=10):
    """CAGR from dated annual observations; reject gaps and conflicting duplicate dates."""
    from datetime import date, datetime

    unavailable = (None, 0.0, None, None)
    if isinstance(max_intervals, bool) or not isinstance(max_intervals, int) or max_intervals < 1:
        return unavailable
    if observations_newest_first is None:
        return unavailable
    try:
        raw_observations = list(observations_newest_first)
    except TypeError:
        return unavailable

    def as_date(value):
        try:
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            text = str(value).strip()
            pattern = (
                r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}"
                r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
            )
            if not re.fullmatch(pattern, text):
                return None
            if len(text) == 10:
                return date.fromisoformat(text)
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            return None

    observations_by_date = {}
    for item in raw_observations:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        period, value = item
        parsed = as_date(period)
        amount = _f(value)
        if parsed is not None and amount is not None:
            previous = observations_by_date.get(parsed)
            if previous is not None and previous != amount:
                # A duplicate period with conflicting facts is ambiguous. Do
                # not let provider row order select the historical growth input.
                return unavailable
            observations_by_date[parsed] = amount
    observations = sorted(observations_by_date.items(), key=lambda item: item[0], reverse=True)
    if len(observations) < 2:
        return unavailable

    annual = [observations[0]]
    for observation in observations[1:]:
        gap_days = (annual[-1][0] - observation[0]).days
        if gap_days < 330 or gap_days > 430:
            break
        annual.append(observation)
        if len(annual) - 1 >= max_intervals:
            break
    if len(annual) < 2:
        return None, 0.0, None, None
    newest_date, newest_value = annual[0]
    oldest_date, oldest_value = annual[-1]
    if any(value <= 0 for _, value in annual):
        return None, 0.0, oldest_date.isoformat(), newest_date.isoformat()
    elapsed_years = (newest_date - oldest_date).days / 365.2425
    return cagr(oldest_value, newest_value, elapsed_years), elapsed_years, oldest_date.isoformat(), newest_date.isoformat()


def average_finite_margins(margins):
    """Mean of all finite observed margins, including zero and negative years."""
    usable = [_f(m) for m in list(margins or [])]
    usable = [m for m in usable if m is not None]
    if not usable:
        return None
    # Scale before summing so individually finite observations cannot overflow
    # merely because their unscaled total is larger than the mean.
    try:
        average = math.fsum(value / len(usable) for value in usable)
    except (OverflowError, ValueError):
        return None
    return average if math.isfinite(average) else None


def choose_model_fcf_margin(latest_margin, trailing_margins, mature_margin=None):
    """
    Reverse DCF only runs on real positive cash conversion.
    Trailing average is preferred when it exists; latest year is blended in.
    Never invent a healthy margin for a cash-burning company.
    """
    latest = _f(latest_margin)
    avg = average_finite_margins(trailing_margins)
    _ = mature_margin  # kept for callers that still pass sector mature as a label only

    if latest is not None and latest <= 0:
        return None, True, "latest free-cash-flow margin is non-positive — reverse DCF not solved"
    if latest is not None and latest > 0 and avg is not None:
        if avg > 0:
            blended = 0.5 * latest + 0.5 * avg
            return blended, False, "blend of latest and multi-year average"
        return latest, False, "latest year; multi-year average is non-positive"
    if latest is not None and latest > 0:
        return latest, False, "latest year"
    if avg is not None and avg > 0:
        return avg, False, "multi-year average (latest year not usable)"
    return None, True, "no positive usable free-cash margin — reverse DCF not solved"


def growth_reality_score(benchmark_growth, required_growth):
    benchmark_growth = _f(benchmark_growth)
    required_growth = _f(required_growth)
    if required_growth is None or benchmark_growth is None:
        return None
    gap = required_growth - benchmark_growth
    return clamp(75 - gap * 160)


def business_quality_score(revenue_growth, gross_margin, operating_margin, fcf_margin, roe, debt_to_assets, sector_model):
    return business_quality_detail(
        revenue_growth, gross_margin, operating_margin, fcf_margin, roe, debt_to_assets, sector_model
    )["score"]


def business_quality_detail(revenue_growth, gross_margin, operating_margin, fcf_margin, roe, debt_to_assets, sector_model):
    """Score only observed inputs and disclose the share of factor weight observed."""
    benchmarks = sector_model["business_benchmarks"]
    weights = sector_model["business_weights"]
    raw = {
        "growth": (revenue_growth, lambda value: 50 + value * 250, "Revenue growth"),
        "gross": (gross_margin, lambda value: value / benchmarks["gross"] * 100, "Gross margin"),
        "operating": (operating_margin, lambda value: value / benchmarks["operating"] * 100, "Operating margin"),
        "fcf": (fcf_margin, lambda value: value / benchmarks["fcf"] * 100, "Free-cash-flow margin"),
        "roe": (roe, lambda value: value / benchmarks["roe"] * 100, "Return on average equity"),
        "balance": (debt_to_assets, lambda value: 100 - value / 0.80 * 100, "Debt to assets"),
    }
    return _observed_weighted_score(raw, weights, minimum_coverage=0.5)


def expectation_score(required_growth, ev_sales, pe, ev_ebitda, sector_model):
    return expectation_score_detail(required_growth, ev_sales, pe, ev_ebitda, sector_model)["score"]


def expectation_score_detail(required_growth, ev_sales, pe, ev_ebitda, sector_model):
    """Score only observed, positive valuation evidence and disclose coverage."""
    benchmarks = sector_model["expectation_benchmarks"]
    weights = sector_model["expectation_weights"]
    growth = _f(required_growth)
    sales = _f(ev_sales)
    earnings = _f(pe)
    ebitda = _f(ev_ebitda)
    raw = {
        "required_growth": (None if growth is None else 50 + growth * 180, lambda value: value, "Growth required by price"),
        "ev_sales": (sales if sales is not None and sales > 0 else None, lambda value: value / benchmarks["ev_sales"] * 100, "Enterprise value / sales"),
        "pe": (earnings if earnings is not None and earnings > 0 else None, lambda value: value / benchmarks["pe"] * 100, "Price / earnings"),
        "ev_ebitda": (ebitda if ebitda is not None and ebitda > 0 else None, lambda value: value / benchmarks["ev_ebitda"] * 100, "Enterprise value / EBITDA"),
    }
    return _observed_weighted_score(raw, weights, minimum_coverage=0.5)


def financial_strength_score(cash, debt, operating_margin, fcf_margin, debt_to_assets, sector_model):
    return financial_strength_detail(
        cash, debt, operating_margin, fcf_margin, debt_to_assets, sector_model
    )["score"]


def financial_strength_detail(cash, debt, operating_margin, fcf_margin, debt_to_assets, sector_model):
    benchmarks = sector_model["financial_benchmarks"]
    weights = sector_model["financial_weights"]
    cash_value, debt_value = _f(cash), _f(debt)
    cash_debt_score = None
    if (
        cash_value is not None and debt_value is not None
        and cash_value >= 0 and debt_value >= 0
        and (cash_value > 0 or debt_value > 0)
    ):
        raw_cash_debt_score = 100 if debt_value == 0 else 50 + (cash_value / debt_value) * 35
        cash_debt_score = clamp(raw_cash_debt_score) if math.isfinite(raw_cash_debt_score) else None
    raw = {
        "cash_debt": (cash_debt_score, lambda value: value, "Cash relative to debt"),
        "operating": (operating_margin, lambda value: value / benchmarks["operating"] * 100, "Operating margin"),
        "fcf": (fcf_margin, lambda value: value / benchmarks["fcf"] * 100, "Free-cash-flow margin"),
        "leverage": (debt_to_assets, lambda value: 100 - value / 0.80 * 100, "Debt to assets"),
    }
    return _observed_weighted_score(raw, weights, minimum_coverage=0.5)


def _observed_weighted_score(raw_components, weights, minimum_coverage):
    total_weight = sum(_f(weight, 0) for weight in weights.values())
    observed = []
    for key, (value, transform, label) in raw_components.items():
        number = _f(value)
        try:
            transformed = transform(number) if number is not None else None
        except (OverflowError, ZeroDivisionError, ValueError):
            transformed = None
        score = _f(transformed)
        if score is not None:
            score = clamp(score)
        weight = _f(weights.get(key), 0) or 0
        observed.append({"key": key, "label": label, "value": score, "weight": weight})
    available_weight = sum(item["weight"] for item in observed if item["value"] is not None)
    coverage = available_weight / total_weight if total_weight > 0 else 0.0
    score = None
    if coverage >= minimum_coverage and available_weight > 0:
        score = clamp(sum(item["value"] * item["weight"] for item in observed if item["value"] is not None) / available_weight)
    return {"score": score, "evidence_coverage": coverage, "components": observed}


def supported_score_breakdown(business, financial, historical_growth, required_growth, consensus_growth=None, growth_clamped=False):
    """Build score-v8 from business quality and financial strength only.

    The valuation sensitivity remains available in research, but is not scored:
    its cash-flow proxy and static discount assumptions are not a validated
    FCFF/FCFE basis. Legacy arguments remain accepted for caller compatibility.
    """
    _ = historical_growth, required_growth, consensus_growth, growth_clamped
    base_weights = (0.35, 0.30)
    comparable_weight_total = sum(base_weights)
    specs = [
        ("business_quality", "Business quality", business, base_weights[0] / comparable_weight_total),
        ("financial_strength", "Financial strength", financial, base_weights[1] / comparable_weight_total),
    ]
    components = []
    available_weight = 0.0
    effective_coverage = 0.0
    for key, label, detail, weight in specs:
        detail = detail if isinstance(detail, dict) else {}
        value = _f(detail.get("score"))
        if value is not None:
            value = clamp(value)
        coverage = clamp(detail.get("evidence_coverage"), 0.0, 1.0)
        effective_coverage += weight * coverage
        if value is not None:
            available_weight += weight
        components.append({"key": key, "label": label, "value": value, "weight": weight, "evidence_coverage": coverage})
    evidence_coverage = effective_coverage / sum(weight for _, _, _, weight in specs)
    score = None
    if evidence_coverage >= 0.70 and available_weight >= 0.70:
        for component in components:
            component["applied_weight"] = component["weight"] / available_weight if component["value"] is not None else 0.0
            component["contribution"] = component["value"] * component["applied_weight"] if component["value"] is not None else None
        score = clamp(sum(component["contribution"] or 0 for component in components))
    else:
        for component in components:
            component["applied_weight"] = 0.0
            component["contribution"] = None
    return {
        "score": score,
        "components": components,
        "growth_comparison_inputs": {
            "historical_growth": None,
            "required_growth": None,
            "growth_clamped": False,
        },
        "subscores": {
            "business_quality": business,
            "financial_strength": financial,
        },
        "evidence_coverage": evidence_coverage,
        "minimum_evidence_coverage": 0.70,
        "growth_comparison_available": False,
    }


def reality_score(
    business_quality,
    financial_strength,
    historical_growth,
    required_growth,
    consensus_growth=None,
    growth_clamped=False,
):
    """Return the heuristic score; analyst consensus is retained only for compatibility."""
    return score_breakdown(
        business_quality,
        financial_strength,
        historical_growth,
        required_growth,
        consensus_growth,
        growth_clamped,
    )["score"]


def score_breakdown(
    business_quality,
    financial_strength,
    historical_growth,
    required_growth,
    consensus_growth=None,
    growth_clamped=False,
):
    """Compatibility wrapper over the evidence-aware score-v8 calculation.

    Numeric subscores are treated as fully covered. Pass detail dictionaries
    when the originating subscore has partial evidence coverage.
    """

    def as_detail(value):
        if isinstance(value, dict):
            return value
        score = _f(value)
        return {"score": score, "evidence_coverage": 1.0 if score is not None else 0.0}

    return supported_score_breakdown(
        as_detail(business_quality),
        as_detail(financial_strength),
        historical_growth,
        required_growth,
        consensus_growth,
        growth_clamped,
    )


def score_label(value):
    value = _f(value)
    if value is None:
        return "Unavailable"
    if value >= 85:
        return "Strong support"
    if value >= 70:
        return "Reasonable support"
    if value >= 55:
        return "Mixed but explainable"
    if value >= 40:
        return "Demanding expectations"
    return "Very demanding expectations"


def snapshot_diff(previous, current):
    """Compare two saved analysis payloads without inventing causal explanations."""
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return {"comparable": False, "reason": "snapshot_missing", "changes": []}
    if previous.get("methodology_version") != current.get("methodology_version"):
        return {"comparable": False, "reason": "methodology_mismatch", "changes": []}

    fields = (
        "score",
        "business_quality",
        "financial_strength",
        "market_expectations",
        "historical_growth",
        "consensus_growth",
        "required_growth",
        "revenue",
        "free_cash_flow",
        "fcf_period",
        "enterprise_value",
        "model_fcf_margin",
        "discount_rate",
        "terminal_growth",
        "solver_status",
        "user_assumptions",
    )
    changes = []
    for field in fields:
        before = previous.get(field)
        after = current.get(field)
        before_number = _f(before)
        after_number = _f(after)
        if before_number is not None and after_number is not None:
            delta = after_number - before_number
            if not math.isclose(before_number, after_number, rel_tol=1e-12):
                # Individually finite snapshot values can still have an
                # unrepresentable difference (for example, opposite-signed
                # values near the float limit). Keep the change visible, but
                # never return Infinity to the strict JSON API serializer.
                changes.append({
                    "field": field,
                    "before": before_number,
                    "after": after_number,
                    "delta": delta if math.isfinite(delta) else None,
                })
        elif before != after:
            changes.append({"field": field, "before": before, "after": after, "delta": None})
    return {"comparable": True, "reason": "compatible", "changes": changes}
