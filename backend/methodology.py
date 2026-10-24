"""Declared research-index policy, not an empirically calibrated return model.

Sector saturation thresholds are retained from v9, not represented as peer
averages. V10 removes repeated factors and uses equal weights within each group.
"""

from copy import deepcopy


def _model(bb, ew, eb, fb):
    return {
        "business_weights": {"growth": 1/3, "operating": 1/3, "roe": 1/3},
        "business_benchmarks": dict(zip(("gross", "operating", "fcf", "roe"), bb)),
        "expectation_weights": dict(zip(("required_growth", "ev_sales", "pe", "ev_ebitda"), ew)),
        "expectation_benchmarks": dict(zip(("ev_sales", "pe", "ev_ebitda"), eb)),
        "financial_weights": {"cash_debt": 1/3, "fcf": 1/3, "leverage": 1/3},
        "financial_benchmarks": dict(zip(("operating", "fcf"), fb)),
    }


SECTOR_MODELS = {
    "Technology": _model((.70,.30,.25,.25),(.50,.25,.10,.15),(16,70,40),(.28,.22)),
    "Communication Services": _model((.60,.25,.20,.22),(.45,.25,.15,.15),(12,55,30),(.24,.18)),
    "Consumer Cyclical": _model((.45,.18,.12,.22),(.40,.20,.20,.20),(6,35,20),(.16,.10)),
    "Consumer Defensive": _model((.40,.16,.12,.22),(.30,.20,.25,.25),(5,32,18),(.15,.11)),
    "Industrials": _model((.40,.18,.12,.20),(.35,.15,.25,.25),(5,35,20),(.16,.10)),
    "Healthcare": _model((.65,.22,.16,.20),(.45,.25,.15,.15),(10,50,28),(.20,.14)),
    "Energy": _model((.35,.20,.15,.18),(.25,.15,.25,.35),(4,25,12),(.18,.13)),
    "Basic Materials": _model((.35,.18,.12,.18),(.25,.15,.25,.35),(4,25,12),(.16,.10)),
    "Utilities": _model((.35,.22,.10,.14),(.20,.15,.30,.35),(5,28,16),(.20,.09)),
    "Real Estate": _model((.55,.35,.18,.14),(.20,.15,.25,.40),(8,35,22),(.28,.15)),
}

DEFAULT_SECTOR_MODEL = _model((.60,.30,.20,.25),(.45,.25,.15,.15),(14,60,35),(.25,.18))


def normalize_sector_label(value):
    """Keep provider sector metadata a bounded display/model key string."""
    if not isinstance(value, str):
        return "Unknown sector"
    normalized = value.strip()
    return normalized[:100] if normalized else "Unknown sector"


def has_sector_calibration(sector):
    """Return whether TSRP has an explicit calibration for this sector."""
    return normalize_sector_label(sector) in SECTOR_MODELS


def get_sector_model(sector):
    """Return an isolated model so callers cannot mutate shared calibrations."""
    key = normalize_sector_label(sector)
    return deepcopy(SECTOR_MODELS.get(key, DEFAULT_SECTOR_MODEL))
