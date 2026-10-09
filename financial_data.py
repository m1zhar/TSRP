"""Small, deterministic helpers for period-safe financial statement data.

This module has no provider or network dependencies. The application passes
provider tables in and receives only period-aligned values back.
"""

from __future__ import annotations

import math

import pandas as pd


def safe_number(value):
    try:
        if value is None or pd.isna(value):
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize_capex(capex):
    """Return capex as a cash outflow, regardless of provider sign convention."""
    value = safe_number(capex)
    return None if value is None else -abs(value)


def compute_fcf(operating_cash_flow, capex, reported_fcf=None):
    """Prefer OCF less capex; use a provider-reported value only as fallback."""
    return compute_fcf_detail(operating_cash_flow, capex, reported_fcf)[0]


def compute_fcf_detail(operating_cash_flow, capex, reported_fcf=None):
    """Return FCF and its actual method so provenance cannot overstate inputs."""
    operating = safe_number(operating_cash_flow)
    spending = normalize_capex(capex)
    if operating is not None and spending is not None:
        return operating + spending, "calculated: operating cash flow less capital spending"
    reported = safe_number(reported_fcf)
    if reported is not None:
        return reported, "provider-reported free cash flow; OCF or capex unavailable"
    return None, None


def _period_key(value):
    """Normalize provider date labels without changing the original display value."""
    try:
        parsed = pd.Timestamp(value)
        if not pd.isna(parsed):
            return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    return str(value)


def _row(frame, names):
    if frame is None or getattr(frame, "empty", True):
        return None
    for name in names:
        if name in frame.index:
            row = frame.loc[name].dropna()
            if not row.empty:
                return row
    return None


def period_observations(frame, names):
    """Return newest-first ``(period, value)`` observations for one statement row."""
    row = _row(frame, names)
    if row is None:
        return []
    try:
        parsed = pd.to_datetime(row.index, errors="coerce")
        if parsed.notna().all():
            row = row.iloc[parsed.argsort()[::-1]]
    except (TypeError, ValueError, IndexError):
        pass
    return [(period, safe_number(value)) for period, value in row.items() if safe_number(value) is not None]


def _value_by_period(observations):
    return {_period_key(period): value for period, value in observations if value is not None}


def period_aligned_fcf_details(cashflow, revenue, revenue_names, *, years=None):
    """Find FCF only where its source period matches a revenue period.

    Returning the matched period lets the caller disclose the date and prevents a
    TTM or stale annual cash-flow value from being divided by unrelated revenue.
    """
    revenue_observations = period_observations(revenue, revenue_names)
    operating = _value_by_period(period_observations(cashflow, [
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
    ]))
    capex = _value_by_period(period_observations(cashflow, [
        "Capital Expenditure",
        "Capital Expenditures",
    ]))
    reported = _value_by_period(period_observations(cashflow, ["Free Cash Flow"]))

    matches = []
    for period, revenue_value in revenue_observations:
        key = _period_key(period)
        fcf, method = compute_fcf_detail(operating.get(key), capex.get(key), reported.get(key))
        if fcf is not None:
            matches.append((period, revenue_value, fcf, method))
        if years and len(matches) >= years:
            break
    return matches


def period_aligned_fcf(cashflow, revenue, revenue_names, *, years=None):
    """Compatibility view of period-aligned FCF without the method field."""
    return [match[:3] for match in period_aligned_fcf_details(cashflow, revenue, revenue_names, years=years)]


def latest_period_aligned_fcf(cashflow, revenue, revenue_names):
    matches = period_aligned_fcf(cashflow, revenue, revenue_names, years=1)
    return (matches[0][2], matches[0][0]) if matches else (None, None)


def latest_period_aligned_fcf_detail(cashflow, revenue, revenue_names):
    matches = period_aligned_fcf_details(cashflow, revenue, revenue_names, years=1)
    return (matches[0][2], matches[0][0], matches[0][3]) if matches else (None, None, None)


def trailing_aligned_fcf_margins(cashflow, revenue, revenue_names, years=4):
    margins = []
    for period, revenue_value, fcf in period_aligned_fcf(cashflow, revenue, revenue_names, years=years):
        if revenue_value is not None and revenue_value > 0:
            margins.append(fcf / revenue_value)
    return margins


def period_aligned_ttm_fcf(cashflow, periods=4, *, period_starts=None):
    """Return TTM FCF only when discrete quarter durations are verifiable.

    Period-end dates alone cannot distinguish discrete quarterly flows from
    year-to-date flows. Those observations are unavailable unless callers also
    provide each quarter's verified start date.
    """
    if periods != 4 or not isinstance(period_starts, dict):
        return None, None
    operating = _value_by_period(period_observations(cashflow, [
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
    ]))
    capex = _value_by_period(period_observations(cashflow, [
        "Capital Expenditure",
        "Capital Expenditures",
    ]))
    reported = _value_by_period(period_observations(cashflow, ["Free Cash Flow"]))

    ordered_periods = [period for period, _ in period_observations(cashflow, [
        "Operating Cash Flow",
        "Capital Expenditure",
        "Free Cash Flow",
    ])]
    common = []
    parsed_periods = []
    for period in ordered_periods:
        try:
            date = pd.Timestamp(period).date()
            start = pd.Timestamp(period_starts.get(_period_key(period))).date()
        except (TypeError, ValueError, OverflowError):
            return None, None
        if not 60 <= (date - start).days <= 120:
            return None, None
        if parsed_periods and not 60 <= (parsed_periods[-1][0] - date).days <= 120:
            return None, None
        if parsed_periods and parsed_periods[-1][0] == date:
            continue
        key = _period_key(period)
        fcf = compute_fcf(operating.get(key), capex.get(key), reported.get(key))
        if fcf is None:
            return None, None
        parsed_periods.append((date, period, fcf))
        if len(parsed_periods) >= periods:
            break
    if len(parsed_periods) < periods:
        return None, None
    elapsed = (parsed_periods[0][0] - parsed_periods[periods - 1][0]).days
    if not 250 <= elapsed <= 400:
        return None, None
    return float(sum(value for _, _, value in parsed_periods[:periods])), parsed_periods[0][1]
