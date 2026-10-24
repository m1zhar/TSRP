"""Validate analyst revenue growth without inventing forecast dates or currency."""

from datetime import date
import math
import re

import pandas as pd

from financial_data import safe_number, statement_period
from engine import normalize_market_currency


def revenue_estimate(frame, *, as_of=None):
    unavailable = {"growth": None, "label": None, "end_date": None, "currency": None}
    if not isinstance(frame, pd.DataFrame) or frame.empty or not frame.columns.is_unique or "growth" not in frame.columns:
        return unavailable
    today = as_of or date.today()
    metadata = frame.attrs.get("fiscal_periods", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    for period, label in (("+1y", "Next-year analyst revenue estimate"), ("0y", "Current-year analyst revenue estimate")):
        if period not in frame.index or list(frame.index).count(period) != 1:
            continue
        row = frame.loc[period]
        growth_raw = row.get("growth")
        if not pd.api.types.is_scalar(growth_raw):
            continue
        growth = safe_number(growth_raw)
        if growth is None or growth < -1 or not math.isfinite(growth * 100):
            continue
        # A stated zero/negative contributor count is not consensus. Missing
        # count remains unknown rather than being replaced by a made-up count.
        count_raw = row.get("numberOfAnalysts")
        count_missing = count_raw is None or (pd.api.types.is_scalar(count_raw) and bool(pd.isna(count_raw)))
        if not count_missing:
            if not pd.api.types.is_scalar(count_raw):
                continue
            count = safe_number(count_raw)
            if count is None or count <= 0 or not count.is_integer():
                continue
        raw_end = metadata.get(period, {})
        raw_end = raw_end.get("end_date") if isinstance(raw_end, dict) else None
        if "endDate" in row:
            raw_end = row["endDate"]
        if pd.api.types.is_scalar(raw_end) and bool(pd.isna(raw_end)):
            raw_end = None
        end = statement_period(raw_end) if raw_end is not None else None
        if raw_end is not None and end is None:
            continue
        if end is not None:
            days_ahead = (date.fromisoformat(end) - today).days
            # Current-year forecasts may remain estimates until results are
            # published after year end. These bounds are freshness policy,
            # not a claim that the estimate was recently revised.
            if not ((0 <= days_ahead <= 800) if period == "+1y" else (-120 <= days_ahead <= 400)):
                continue
        raw_currency = row.get("currency")
        currency = normalize_market_currency(raw_currency) if isinstance(raw_currency, str) else None
        if currency is None or not re.fullmatch(r"[A-Z]{3}", currency) or currency == "XXX":
            currency = None
        return {"growth": growth, "label": label, "end_date": end, "currency": currency}
    return unavailable
