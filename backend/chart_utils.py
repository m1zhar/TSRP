"""Pure presentation helpers for provider-supplied chart data."""

from __future__ import annotations

import math

from financial_data import finite_product, safe_number


def scale_price_values(values, fx_rate):
    """Scale chart prices only with a finite, positive FX rate and finite outputs."""
    rate = safe_number(fx_rate)
    if rate is None or rate <= 0:
        return None
    try:
        source_values = list(values)
    except (TypeError, ValueError):
        return []
    return [finite_product(value, rate) for value in source_values]


def volume_bar_colors(open_values, close_values, *, up_color, down_color, neutral_color):
    """Color volume bars only when both comparable open and close values exist."""
    try:
        closes = list(close_values)
    except (TypeError, ValueError):
        return []
    try:
        opens = list(open_values) if open_values is not None else []
    except (TypeError, ValueError):
        opens = []
    if len(opens) != len(closes):
        return [neutral_color] * len(closes)

    colors = []
    for opening, closing in zip(opens, closes):
        try:
            opening = float(opening)
            closing = float(closing)
        except (TypeError, ValueError, OverflowError):
            colors.append(neutral_color)
            continue
        if not math.isfinite(opening) or not math.isfinite(closing) or opening == closing:
            colors.append(neutral_color)
        else:
            colors.append(up_color if closing > opening else down_color)
    return colors
