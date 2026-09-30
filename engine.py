"""Currency-aware reverse DCF core. No Streamlit, no I/O."""

from __future__ import annotations

import math

FORECAST_YEARS = 10
FADE_START_YEAR = 5
SOLVER_LOW = -0.40
SOLVER_HIGH = 1.20
MISSING_EVIDENCE_SCORE = 40

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
        if value is None:
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def clamp(value, low=0.0, high=100.0):
    value = _f(value, 0.0)
    return max(low, min(high, value))


def currency_profile(code):
    key = str(code or "USD").upper()
    if key in CURRENCY_PROFILES:
        return CURRENCY_PROFILES[key], key, False
    return CURRENCY_PROFILES["USD"], "USD", True


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
    }


def year_growth(initial_growth, terminal_growth, year, years=FORECAST_YEARS, fade_start=FADE_START_YEAR):
    """High growth through fade_start, then linear fade to terminal by year `years`."""
    if year <= fade_start:
        return initial_growth
    span = max(years - fade_start, 1)
    t = (year - fade_start) / span
    return initial_growth + (terminal_growth - initial_growth) * t


def year_margin(start_margin, mature_margin, year, years=FORECAST_YEARS):
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
    growth = _f(growth, 0.0)
    fcf_margin = _f(fcf_margin)
    discount_rate = _f(discount_rate)
    terminal_growth = _f(terminal_growth)
    start_margin = _f(start_margin, fcf_margin)

    years = int(_f(years, 0) or 0)
    fade_start = int(_f(fade_start, 0) or 0)
    if revenue is None or fcf_margin is None or discount_rate is None or terminal_growth is None:
        return None
    if years <= 0 or fade_start < 0 or fade_start > years:
        return None
    if revenue <= 0 or fcf_margin <= 0 or growth <= -1 or discount_rate <= terminal_growth:
        return None
    if terminal_growth < -1 or discount_rate <= -1:
        return None

    present_value = 0.0
    projected_revenue = revenue
    final_fcf = 0.0
    for year in range(1, years + 1):
        projected_revenue *= 1 + year_growth(growth, terminal_growth, year, years, fade_start)
        margin = year_margin(start_margin, fcf_margin, year, years)
        if margin is None or margin <= 0:
            return None
        final_fcf = projected_revenue * margin
        present_value += final_fcf / ((1 + discount_rate) ** year)

    terminal_value = final_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    present_value += terminal_value / ((1 + discount_rate) ** years)
    return present_value


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
    if target < low_value:
        return {"growth": low, "status": "below_range", "low": low, "high": high}
    if target > high_value:
        return {"growth": high, "status": "above_range", "low": low, "high": high}

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


def cagr(start, end, years):
    start = _f(start)
    end = _f(end)
    years = _f(years)
    if start is None or end is None or years is None:
        return None
    if start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def history_cagr(values_newest_first, max_years=10):
    """values_newest_first: sequence of revenue, index 0 = latest year."""
    series = [_f(v) for v in list(values_newest_first or [])]
    series = [v for v in series if v is not None]
    if len(series) < 2:
        return None, 0
    years = min(len(series) - 1, max_years)
    newest = series[0]
    oldest = series[years]
    return cagr(oldest, newest, years), years


def average_positive_margins(margins):
    """Mean of positive cash margins; None if none are usable."""
    usable = [_f(m) for m in list(margins or [])]
    usable = [m for m in usable if m is not None and m > 0]
    if not usable:
        return None
    return sum(usable) / len(usable)


def choose_model_fcf_margin(latest_margin, trailing_margins, mature_margin=None):
    """
    Reverse DCF only runs on real positive cash conversion.
    Trailing average is preferred when it exists; latest year is blended in.
    Never invent a healthy margin for a cash-burning company.
    """
    latest = _f(latest_margin)
    avg = average_positive_margins(trailing_margins)
    _ = mature_margin  # kept for callers that still pass sector mature as a label only

    if latest is not None and latest <= 0:
        return None, True, "latest free-cash-flow margin is non-positive — reverse DCF not solved"
    if latest is not None and latest > 0 and avg is not None:
        blended = 0.5 * latest + 0.5 * avg
        return blended, False, "blend of latest and multi-year average"
    if latest is not None and latest > 0:
        return latest, False, "latest year"
    if avg is not None:
        return avg, False, "multi-year average (latest year not usable)"
    return None, True, "no positive free-cash margin — reverse DCF not solved"


def growth_reality_score(benchmark_growth, required_growth):
    benchmark_growth = _f(benchmark_growth)
    required_growth = _f(required_growth)
    if required_growth is None or benchmark_growth is None:
        return MISSING_EVIDENCE_SCORE
    gap = required_growth - benchmark_growth
    return clamp(75 - gap * 160)


def business_quality_score(revenue_growth, gross_margin, operating_margin, fcf_margin, roe, debt_to_assets, sector_model):
    benchmarks = sector_model["business_benchmarks"]
    weights = sector_model["business_weights"]

    growth_score = MISSING_EVIDENCE_SCORE if revenue_growth is None else clamp(50 + revenue_growth * 250)
    gross_score = MISSING_EVIDENCE_SCORE if gross_margin is None else clamp((gross_margin / benchmarks["gross"]) * 100)
    operating_score = MISSING_EVIDENCE_SCORE if operating_margin is None else clamp((operating_margin / benchmarks["operating"]) * 100)
    fcf_score = MISSING_EVIDENCE_SCORE if fcf_margin is None else clamp((fcf_margin / benchmarks["fcf"]) * 100)
    roe_score = MISSING_EVIDENCE_SCORE if roe is None else clamp((roe / benchmarks["roe"]) * 100)
    balance_score = MISSING_EVIDENCE_SCORE if debt_to_assets is None else clamp(100 - (debt_to_assets / 0.80 * 100))

    return clamp(
        growth_score * weights["growth"]
        + gross_score * weights["gross"]
        + operating_score * weights["operating"]
        + fcf_score * weights["fcf"]
        + roe_score * weights["roe"]
        + balance_score * weights["balance"]
    )


def expectation_score(required_growth, ev_sales, pe, ev_ebitda, sector_model):
    benchmarks = sector_model["expectation_benchmarks"]
    weights = sector_model["expectation_weights"]

    growth_pressure = MISSING_EVIDENCE_SCORE if required_growth is None else clamp(50 + required_growth * 180)
    sales_pressure = clamp((ev_sales / benchmarks["ev_sales"]) * 100) if ev_sales else MISSING_EVIDENCE_SCORE
    pe_pressure = clamp((pe / benchmarks["pe"]) * 100) if pe and pe > 0 else MISSING_EVIDENCE_SCORE
    ebitda_pressure = clamp((ev_ebitda / benchmarks["ev_ebitda"]) * 100) if ev_ebitda and ev_ebitda > 0 else MISSING_EVIDENCE_SCORE

    return clamp(
        growth_pressure * weights["required_growth"]
        + sales_pressure * weights["ev_sales"]
        + pe_pressure * weights["pe"]
        + ebitda_pressure * weights["ev_ebitda"]
    )


def financial_strength_score(cash, debt, operating_margin, fcf_margin, debt_to_assets, sector_model):
    benchmarks = sector_model["financial_benchmarks"]
    weights = sector_model["financial_weights"]

    if cash is not None and debt is not None:
        cash_debt_score = 100 if debt <= 0 else clamp(50 + (cash / max(debt, 1)) * 35)
    else:
        cash_debt_score = MISSING_EVIDENCE_SCORE

    operating_score = MISSING_EVIDENCE_SCORE if operating_margin is None else clamp((operating_margin / benchmarks["operating"]) * 100)
    fcf_score = MISSING_EVIDENCE_SCORE if fcf_margin is None else clamp((fcf_margin / benchmarks["fcf"]) * 100)
    leverage_score = MISSING_EVIDENCE_SCORE if debt_to_assets is None else clamp(100 - (debt_to_assets / 0.80 * 100))

    return clamp(
        cash_debt_score * weights["cash_debt"]
        + operating_score * weights["operating"]
        + fcf_score * weights["fcf"]
        + leverage_score * weights["leverage"]
    )


def reality_score(
    business_quality,
    financial_strength,
    historical_growth,
    required_growth,
    consensus_growth=None,
    growth_clamped=False,
):
    """
    Composite is a heuristic dashboard, not a calibrated probability.
    Market expectations enter once, via required vs history / consensus.
    """
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
    """Return the deterministic score components used by :func:`reality_score`.

    The UI uses this trace to explain the score; keeping it here prevents a
    second presentation-layer formula from drifting away from the engine.
    """
    required_growth = _f(required_growth)
    historical_growth = _f(historical_growth)
    consensus_growth = _f(consensus_growth)
    if growth_clamped or required_growth is None:
        history_leg = MISSING_EVIDENCE_SCORE
        street_leg = MISSING_EVIDENCE_SCORE
    else:
        history_leg = growth_reality_score(historical_growth, required_growth)
        street_leg = growth_reality_score(consensus_growth, required_growth)

    components = [
        {"key": "business_quality", "label": "Business quality", "value": clamp(business_quality), "weight": 0.35},
        {"key": "financial_strength", "label": "Financial strength", "value": clamp(financial_strength), "weight": 0.30},
        {"key": "historical_fit", "label": "Historical growth fit", "value": clamp(history_leg), "weight": 0.20},
        {"key": "consensus_fit", "label": "Consensus growth fit", "value": clamp(street_leg), "weight": 0.15},
    ]
    for component in components:
        component["contribution"] = component["value"] * component["weight"]

    return {
        "score": clamp(sum(component["contribution"] for component in components)),
        "components": components,
        "missing_evidence_score": MISSING_EVIDENCE_SCORE,
        "growth_comparison_available": not growth_clamped and required_growth is not None,
    }


def score_label(value):
    value = _f(value, 0)
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
        "enterprise_value",
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
            if not math.isclose(delta, 0.0, abs_tol=1e-12):
                changes.append({"field": field, "before": before_number, "after": after_number, "delta": delta})
        elif before != after:
            changes.append({"field": field, "before": before, "after": after, "delta": None})
    return {"comparable": True, "reason": "compatible", "changes": changes}
