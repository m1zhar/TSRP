"""TSRP sector calibrations reproduced from the prior research baseline.

The score and valuation formulas remain centralized in ``engine.py``. These
calibrations preserve the current sector benchmarks and weighting scheme.
"""

def _model(bw, bb, ew, eb, fw, fb, discount):
    return {
        "discount_rate": discount,
        "business_weights": dict(zip(("growth", "gross", "operating", "fcf", "roe", "balance"), bw)),
        "business_benchmarks": dict(zip(("gross", "operating", "fcf", "roe"), bb)),
        "expectation_weights": dict(zip(("required_growth", "ev_sales", "pe", "ev_ebitda"), ew)),
        "expectation_benchmarks": dict(zip(("ev_sales", "pe", "ev_ebitda"), eb)),
        "financial_weights": dict(zip(("cash_debt", "operating", "fcf", "leverage"), fw)),
        "financial_benchmarks": dict(zip(("operating", "fcf"), fb)),
    }


SECTOR_MODELS = {
    "Technology": _model((.25,.20,.15,.20,.10,.10),(.70,.30,.25,.25),(.50,.25,.10,.15),(16,70,40),(.30,.20,.30,.20),(.28,.22),.10),
    "Communication Services": _model((.20,.15,.20,.20,.10,.15),(.60,.25,.20,.22),(.45,.25,.15,.15),(12,55,30),(.25,.25,.30,.20),(.24,.18),.10),
    "Consumer Cyclical": _model((.20,.10,.20,.20,.15,.15),(.45,.18,.12,.22),(.40,.20,.20,.20),(6,35,20),(.25,.25,.25,.25),(.16,.10),.11),
    "Consumer Defensive": _model((.10,.10,.20,.25,.15,.20),(.40,.16,.12,.22),(.30,.20,.25,.25),(5,32,18),(.20,.25,.30,.25),(.15,.11),.09),
    "Industrials": _model((.15,.10,.20,.20,.15,.20),(.40,.18,.12,.20),(.35,.15,.25,.25),(5,35,20),(.20,.25,.25,.30),(.16,.10),.10),
    "Healthcare": _model((.20,.15,.15,.15,.10,.25),(.65,.22,.16,.20),(.45,.25,.15,.15),(10,50,28),(.35,.20,.20,.25),(.20,.14),.10),
    "Energy": _model((.10,.05,.20,.30,.10,.25),(.35,.20,.15,.18),(.25,.15,.25,.35),(4,25,12),(.20,.20,.30,.30),(.18,.13),.12),
    "Basic Materials": _model((.10,.10,.20,.25,.10,.25),(.35,.18,.12,.18),(.25,.15,.25,.35),(4,25,12),(.20,.20,.25,.35),(.16,.10),.11),
    "Utilities": _model((.05,.05,.20,.20,.15,.35),(.35,.22,.10,.14),(.20,.15,.30,.35),(5,28,16),(.10,.20,.20,.50),(.20,.09),.08),
    "Real Estate": _model((.10,.05,.15,.25,.10,.35),(.55,.35,.18,.14),(.20,.15,.25,.40),(8,35,22),(.10,.15,.25,.50),(.28,.15),.09),
}

DEFAULT_SECTOR_MODEL = _model((.20,.15,.20,.20,.15,.10),(.60,.30,.20,.25),(.45,.25,.15,.15),(14,60,35),(.30,.25,.25,.20),(.25,.18),.10)


def get_sector_model(sector):
    return SECTOR_MODELS.get(sector or "", DEFAULT_SECTOR_MODEL)
