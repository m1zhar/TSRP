"""Small, deterministic helpers for period-safe financial statement data.

This module has no provider or network dependencies. The application passes
provider tables in and receives only period-aligned values back.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

import pandas as pd

MAX_STATEMENT_AGE_DAYS = 550


def safe_number(value, default=None):
    try:
        if value is None or pd.api.types.is_bool(value) or pd.isna(value):
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def finite_product(left, right):
    """Multiply finite numeric inputs without allowing overflow to escape."""
    left_number = safe_number(left)
    right_number = safe_number(right)
    if left_number is None or right_number is None:
        return None
    return safe_number(left_number * right_number)


def finite_quotient(numerator, denominator):
    """Divide finite numeric inputs only when the result is finite."""
    numerator_value = safe_number(numerator)
    denominator_value = safe_number(denominator)
    if numerator_value is None or denominator_value in (None, 0):
        return None
    return safe_number(numerator_value / denominator_value)


def perioded_financial_value(value, period, *, as_of=None, max_age_days=MAX_STATEMENT_AGE_DAYS):
    """Return a finite value only for a verifiable, current reporting period."""
    number = safe_number(value)
    if number is None or as_of is None:
        return None, None
    if isinstance(period, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period.strip()):
        return None, None
    try:
        parsed = pd.Timestamp(period)
        if pd.isna(parsed):
            return None, None
    except (TypeError, ValueError, OverflowError):
        return None, None
    if as_of is not None:
        try:
            observed_at = pd.Timestamp(as_of)
            if pd.isna(observed_at):
                return None, None
            age_days = (observed_at.date() - parsed.date()).days
            if age_days < 0 or age_days > max_age_days:
                return None, None
        except (TypeError, ValueError, OverflowError):
            return None, None
    return number, parsed.date().isoformat()


def statement_period(value):
    """Normalize only complete, parseable statement dates to ISO calendar dates."""
    if isinstance(value, str) and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?",
        value.strip(),
    ):
        return None
    if not isinstance(value, (str, date, datetime)):
        return None
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def annual_filing_records(
    rows,
    allowed_forms=("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"),
    *,
    kind="any",
):
    """Return latest annual filing rows, optionally separating flows and instants."""
    selected = {}
    forms = {str(form).strip().upper() for form in allowed_forms}
    if kind not in {"any", "duration", "instant"}:
        raise ValueError("SEC fact kind must be any, duration, or instant")
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("form") or "").strip().upper() not in forms:
            continue
        fiscal_year_value = safe_number(row.get("fy"))
        value = safe_number(row.get("val"))
        if fiscal_year_value is None or not fiscal_year_value.is_integer() or value is None:
            continue
        period = statement_period(row.get("end"))
        filed = statement_period(row.get("filed"))
        if period is None or filed is None:
            continue
        start = statement_period(row.get("start")) if row.get("start") else None
        if row.get("start") and start is None:
            continue
        if kind == "duration":
            if start is None or not 330 <= (date.fromisoformat(period) - date.fromisoformat(start)).days <= 400:
                continue
        elif kind == "instant" and start is not None:
            continue
        fiscal_year = int(fiscal_year_value)
        accession = str(row.get("accn") or row.get("accessionNumber") or "").strip()
        rank = (filed, period, accession)
        previous = selected.get(fiscal_year)
        if previous is None or rank > previous[0]:
            selected[fiscal_year] = (rank, row)

    records = [row for _, row in selected.values()]
    records.sort(key=lambda row: str(row.get("end") or ""), reverse=True)
    return records


def annual_filing_observations(
    rows,
    allowed_forms=("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"),
    *,
    kind="duration",
):
    """Return newest-first, latest-filed annual observations keyed by period end.

    Keep the dated value series derived from the same selected rows used by
    current-value and provenance selectors, including amended filings.
    """
    observations = []
    for row in annual_filing_records(rows, allowed_forms, kind=kind):
        period = pd.Timestamp(row["end"]).date().isoformat()
        value = safe_number(row.get("val"))
        observations.append((period, value))
    return observations


def perioded_record(record, *, as_of, max_age_days=MAX_STATEMENT_AGE_DAYS):
    """Return a dated record only when its value and reporting period are current."""
    if not isinstance(record, dict):
        return None
    value, period = perioded_financial_value(
        record.get("value"), record.get("period"), as_of=as_of, max_age_days=max_age_days
    )
    if value is None or period is None:
        return None
    return {**record, "value": value, "period": period}


def records_share_period(*records):
    """Whether every supplied dated record refers to the same complete period."""
    if not records or any(not isinstance(record, dict) for record in records):
        return False
    periods = [statement_period(record.get("period")) for record in records]
    return all(periods) and len(set(periods)) == 1


def valid_nonnegative_amounts(*values):
    """Require finite, nonnegative amounts before balance-sheet arithmetic."""
    if not values:
        return False
    numbers = [safe_number(value) for value in values]
    return all(number is not None and number >= 0 for number in numbers)


def enterprise_value_from_components(market_cap, debt, cash):
    """Calculate EV from comparable-currency market cap, debt, and cash inputs."""
    if not valid_nonnegative_amounts(market_cap, debt, cash):
        return None
    try:
        value = math.fsum((float(market_cap), float(debt), -float(cash)))
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def period_aligned_ratio(numerator, numerator_period, denominator, denominator_period):
    """Compute a ratio only from finite values sharing one complete statement date."""
    numerator_value = safe_number(numerator)
    denominator_value = safe_number(denominator)
    left_period = statement_period(numerator_period)
    right_period = statement_period(denominator_period)
    if (
        numerator_value is None
        or denominator_value is None
        or denominator_value <= 0
        or left_period is None
        or left_period != right_period
    ):
        return None
    ratio = numerator_value / denominator_value
    return ratio if math.isfinite(ratio) else None


def sum_perioded_records(*records, as_of):
    """Sum dated statement components only when period, unit, and freshness agree."""
    current = [perioded_record(record, as_of=as_of) for record in records]
    if len(current) < 2 or any(record is None for record in current):
        return None
    if not records_share_period(*current):
        return None
    units = [record.get("unit") for record in current]
    if any(not unit for unit in units) or len(set(units)) != 1:
        return None
    values = [safe_number(record.get("value")) for record in current]
    if any(value is None for value in values):
        return None
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(total):
        return None
    return {**current[-1], "value": total, "components": current}


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
        calculated = operating + spending
        if math.isfinite(calculated):
            return calculated, "calculated: operating cash flow less capital spending"
        return None, None
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


def period_observations(frame, names):
    """Return newest-first ``(period, value)`` observations for one statement row."""
    if frame is None or getattr(frame, "empty", True):
        return []
    for name in names:
        if name not in frame.index:
            continue
        observations = period_observations_for_aliases(frame, [name])
        if observations:
            return observations
    return []


def period_observations_for_aliases(frame, names):
    """Merge observations from explicitly equivalent provider row labels.

    Prefer coverage across synonymous labels, but exclude a period if aliases
    disagree instead of selecting whichever row happened to appear first.
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    values_by_period = {}
    ambiguous_periods = set()
    for name in names:
        if name not in frame.index:
            continue
        rows = frame.loc[[name]]
        for _, row in rows.iterrows():
            for period, value in row.items():
                normalized_period = statement_period(period)
                number = safe_number(value)
                if normalized_period is None or number is None or normalized_period in ambiguous_periods:
                    continue
                if normalized_period in values_by_period:
                    if values_by_period[normalized_period] != number:
                        values_by_period.pop(normalized_period)
                        ambiguous_periods.add(normalized_period)
                    continue
                values_by_period[normalized_period] = number
    return sorted(values_by_period.items(), key=lambda observation: observation[0], reverse=True)


def _value_by_period(observations):
    return {_period_key(period): value for period, value in observations if value is not None}


def period_aligned_fcf_details(cashflow, revenue, revenue_names, *, years=None):
    """Find FCF only where its source period matches a revenue period.

    Returning the matched period lets the caller disclose the date and prevents a
    TTM or stale annual cash-flow value from being divided by unrelated revenue.
    """
    revenue_observations = period_observations_for_aliases(revenue, revenue_names)
    operating = _value_by_period(period_observations_for_aliases(cashflow, [
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
    ]))
    capex = _value_by_period(period_observations_for_aliases(cashflow, [
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
        margin = period_aligned_ratio(fcf, period, revenue_value, period)
        if margin is not None:
            margins.append(margin)
    return margins


def period_aligned_ttm_fcf(cashflow, periods=4, *, period_starts=None):
    """Return TTM FCF only when discrete quarter durations are verifiable.

    Period-end dates alone cannot distinguish discrete quarterly flows from
    year-to-date flows. Those observations are unavailable unless callers also
    provide each quarter's verified start date. The four quarter durations must
    also be contiguous; valid-looking but overlapping intervals would double-count.
    """
    if periods != 4 or not isinstance(period_starts, dict):
        return None, None
    operating_observations = period_observations_for_aliases(cashflow, [
        "Operating Cash Flow",
        "Total Cash From Operating Activities",
    ])
    operating = _value_by_period(operating_observations)
    capex_observations = period_observations_for_aliases(cashflow, [
        "Capital Expenditure",
        "Capital Expenditures",
    ])
    capex = _value_by_period(capex_observations)
    reported = _value_by_period(period_observations(cashflow, ["Free Cash Flow"]))

    ordered_periods = sorted(
        set(operating) | set(capex) | set(reported), reverse=True
    )
    parsed_periods = []
    for period in ordered_periods:
        try:
            date = pd.Timestamp(period).date()
            start = pd.Timestamp(period_starts.get(_period_key(period))).date()
        except (TypeError, ValueError, OverflowError):
            return None, None
        if not 60 <= (date - start).days <= 120:
            return None, None
        if parsed_periods and parsed_periods[-1][0] == date:
            continue
        if parsed_periods:
            newer_start = parsed_periods[-1][3]
            if (
                not 60 <= (parsed_periods[-1][0] - date).days <= 120
                or (newer_start - date).days != 1
            ):
                return None, None
        key = _period_key(period)
        fcf = compute_fcf(operating.get(key), capex.get(key), reported.get(key))
        if fcf is None:
            return None, None
        parsed_periods.append((date, period, fcf, start))
        if len(parsed_periods) >= periods:
            break
    if len(parsed_periods) < periods:
        return None, None
    elapsed = (parsed_periods[0][0] - parsed_periods[periods - 1][0]).days
    if not 250 <= elapsed <= 400:
        return None, None
    try:
        total_fcf = math.fsum(value for _, _, value, _ in parsed_periods[:periods])
    except (OverflowError, ValueError):
        return None, None
    if not math.isfinite(total_fcf):
        return None, None
    return total_fcf, parsed_periods[0][1]
