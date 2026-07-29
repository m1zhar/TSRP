from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import yfinance as yf


st.set_page_config(
    page_title="TSRP",
    page_icon="T",
    layout="wide",
    initial_sidebar_state="collapsed",
)


SEC_USER_AGENT = "TSRP Intelligence app contact@example.com"

DISCOUNT_RATE = 0.10
TERMINAL_GROWTH = 0.03
FORECAST_YEARS = 10
DEFAULT_FCF_MARGIN = 0.12

DISPLAY_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CNY", "KRW", "HKD", "CAD", "AUD", "CHF", "INR", "SAR", "AED", "TWD", "DKK", "SEK", "NOK", "SGD", "BRL", "MXN"]

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "KRW": "₩",
    "HKD": "HK$",
    "CAD": "C$",
    "AUD": "A$",
    "CHF": "CHF ",
    "INR": "₹",
    "SAR": "﷼",
    "AED": "AED ",
    "TWD": "NT$",
    "DKK": "kr ",
    "SEK": "kr ",
    "NOK": "kr ",
    "SGD": "S$",
    "BRL": "R$",
    "MXN": "MX$",
}


st.markdown(
    """
    <style>
    :root {
        --bg: #050505;
        --paper: #f5f0e8;
        --accent: #75e0c5;
        --accent-dim: rgba(117,224,197,.18);
        --border: rgba(245,240,232,.14);
        --muted: rgba(245,240,232,.62);
    }

    header[data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"],
    #MainMenu,
    footer {
        display: none !important;
        visibility: hidden !important;
    }

    * { letter-spacing: 0 !important; }

    .stApp {
        background: var(--bg);
        color: var(--paper);
        font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .block-container {
        max-width: 1260px;
        padding-top: 0;
        padding-bottom: 36px;
    }

    .app-wrap {
        padding-top: 24px;
        animation: fadeUp .45s ease both;
    }

    .nav {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 18px;
        padding-bottom: 18px;
        border-bottom: 1px solid var(--border);
        margin-bottom: 18px;
    }

    .brand {
        font-size: 1.05rem;
        font-weight: 950;
        color: var(--paper);
    }

    .mission {
        color: var(--muted);
        max-width: 760px;
        line-height: 1.5;
        margin-top: 4px;
        font-size: .95rem;
    }

    .search-panel,
    .landing-card,
    .panel,
    .mini-card,
    .learn-card,
    .risk-item,
    .currency-banner {
        background: rgba(245,240,232,.035);
        border: 1px solid var(--border);
        border-radius: 24px;
        transition: transform .18s ease, border-color .18s ease, background .18s ease;
    }

    .search-panel { padding: 28px; margin-bottom: 18px; }

    .currency-banner {
        padding: 14px 20px;
        margin-bottom: 16px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
    }

    .currency-banner span { color: var(--muted); font-size: .92rem; }
    .currency-banner b { color: var(--paper); }

    .landing-card:hover,
    .panel:hover,
    .mini-card:hover,
    .learn-card:hover,
    .risk-item:hover {
        transform: translateY(-2px);
        border-color: rgba(117,224,197,.65);
        background: rgba(245,240,232,.055);
    }

    .landing-grid {
        display: grid;
        grid-template-columns: 1.25fr .75fr;
        gap: 16px;
        margin-bottom: 16px;
    }

    .landing-card { padding: 34px; min-height: 330px; }

    .score-card {
        background: var(--accent);
        color: var(--bg);
        border: 1px solid var(--accent);
        border-radius: 24px;
        padding: 32px;
        min-height: 330px;
    }

    .eyebrow {
        color: rgba(245,240,232,.66);
        font-size: .76rem;
        font-weight: 850;
        text-transform: uppercase;
    }

    .eyebrow-dark {
        color: rgba(5,5,5,.70);
        font-size: .76rem;
        font-weight: 850;
        text-transform: uppercase;
    }

    .headline {
        color: var(--paper);
        font-size: clamp(2.8rem, 6.5vw, 6rem);
        line-height: .9;
        font-weight: 950;
        margin-top: 14px;
        margin-bottom: 18px;
    }

    .headline-small {
        color: var(--paper);
        font-size: clamp(2.2rem, 5vw, 4.6rem);
        line-height: .92;
        font-weight: 950;
        margin-top: 12px;
        margin-bottom: 16px;
    }

    .muted { color: rgba(245,240,232,.50); }

    .copy {
        color: rgba(245,240,232,.78);
        line-height: 1.7;
        font-size: 1.02rem;
        max-width: 880px;
    }

    .score-number {
        font-size: clamp(5rem, 11vw, 8.8rem);
        line-height: .82;
        font-weight: 950;
        margin-top: 20px;
        color: var(--bg);
    }

    .score-label {
        margin-top: 22px;
        font-weight: 950;
        color: var(--bg);
        font-size: 1.12rem;
    }

    .score-explain {
        color: rgba(5,5,5,.76);
        line-height: 1.55;
        margin-top: 12px;
    }

    .metric-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 16px;
    }

    .mini-card { padding: 18px; min-height: 112px; }

    .mini-value {
        color: var(--paper);
        font-size: 1.55rem;
        font-weight: 920;
        margin-top: 10px;
    }

    .mini-sub {
        color: rgba(245,240,232,.55);
        font-size: .84rem;
        margin-top: 6px;
    }

    .two-col {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
        margin-bottom: 16px;
    }

    .panel { padding: 24px; }

    .panel h3, .learn-card h4 {
        margin: 0 0 14px 0;
        color: var(--paper);
    }

    .row {
        display: flex;
        justify-content: space-between;
        gap: 14px;
        border-top: 1px solid rgba(245,240,232,.12);
        padding: 13px 0;
        color: rgba(245,240,232,.75);
    }

    .row:first-of-type { border-top: none; }
    .row b { color: var(--paper); white-space: nowrap; text-align: right; }

    .bar-wrap { margin-top: 16px; }

    .bar-label {
        display: flex;
        justify-content: space-between;
        color: rgba(245,240,232,.78);
        font-size: .94rem;
        margin-bottom: 8px;
    }

    .bar {
        height: 10px;
        background: rgba(245,240,232,.10);
        border-radius: 999px;
        overflow: hidden;
    }

    .bar-fill {
        height: 100%;
        background: var(--accent);
        border-radius: 999px;
    }

    .learn-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
    }

    .learn-card, .risk-item { padding: 20px; }

    .learn-card p, .risk-item p {
        color: rgba(245,240,232,.72);
        line-height: 1.6;
        margin: 0;
    }

    .risk-list { display: grid; gap: 10px; }
    .risk-item b { color: var(--paper); }
    .risk-item p { margin-top: 7px; }

    .stTextInput input {
        background: rgba(245,240,232,.035) !important;
        border: 1px solid rgba(245,240,232,.20) !important;
        color: var(--paper) !important;
        border-radius: 16px !important;
        height: 48px !important;
    }

    .stTextInput input::placeholder { color: rgba(245,240,232,.35) !important; }

    .stButton button {
        background: var(--accent) !important;
        color: var(--bg) !important;
        border: 1px solid var(--accent) !important;
        border-radius: 16px !important;
        height: 48px !important;
        font-weight: 950 !important;
    }

    .stButton button:hover {
        background: var(--paper) !important;
        color: var(--bg) !important;
        border: 1px solid var(--paper) !important;
    }

    .stSelectbox > div > div {
        background: rgba(245,240,232,.035) !important;
        border: 1px solid rgba(245,240,232,.20) !important;
        border-radius: 16px !important;
        color: var(--paper) !important;
        min-height: 48px !important;
    }

    .stSelectbox label, .stTextInput label {
        color: rgba(245,240,232,.72) !important;
        font-weight: 700 !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 1px solid var(--border);
        background: transparent;
    }

    .stTabs [data-baseweb="tab"] {
        background: rgba(245,240,232,.035);
        border: 1px solid var(--border);
        border-radius: 999px;
        color: rgba(245,240,232,.72);
        padding: 8px 14px;
    }

    .stTabs [aria-selected="true"] {
        background: var(--paper) !important;
        color: var(--bg) !important;
    }

    div[data-testid="stDataFrame"] {
        border: 1px solid var(--border);
        border-radius: 18px;
        overflow: hidden;
    }

    div[data-testid="stAlert"] {
        border-radius: 16px;
        border: 1px solid var(--border);
    }

    .stCaption, [data-testid="stCaptionContainer"] {
        color: rgba(245,240,232,.45) !important;
    }

    [data-testid="stForm"] {
        border: 1px solid var(--border);
        border-radius: 24px;
        padding: 20px 24px 8px;
        background: rgba(245,240,232,.02);
        margin-bottom: 18px;
    }

    @keyframes fadeUp {
        from { opacity: 0; transform: translateY(12px); }
        to { opacity: 1; transform: translateY(0); }
    }

    @media (max-width: 900px) {
        .nav, .landing-grid, .two-col, .learn-grid { grid-template-columns: 1fr; display: grid; }
        .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @media (max-width: 560px) {
        .metric-grid { grid-template-columns: 1fr; }
        .landing-card, .score-card, .search-panel { padding: 22px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


SECTOR_MODELS = {
    "Technology": {
        "business_weights": {"growth": 0.25, "gross": 0.20, "operating": 0.15, "fcf": 0.20, "roe": 0.10, "balance": 0.10},
        "business_benchmarks": {"gross": 0.70, "operating": 0.30, "fcf": 0.25, "roe": 0.25},        "expectation_weights": {"required_growth": 0.50, "ev_sales": 0.25, "pe": 0.10, "ev_ebitda": 0.15},
        "expectation_benchmarks": {"ev_sales": 16, "pe": 70, "ev_ebitda": 40},
        "financial_weights": {"cash_debt": 0.30, "operating": 0.20, "fcf": 0.30, "leverage": 0.20},
        "financial_benchmarks": {"operating": 0.28, "fcf": 0.22},
    },
    "Communication Services": {
        "business_weights": {"growth": 0.20, "gross": 0.15, "operating": 0.20, "fcf": 0.20, "roe": 0.10, "balance": 0.15},
        "business_benchmarks": {"gross": 0.60, "operating": 0.25, "fcf": 0.20, "roe": 0.22},
        "expectation_weights": {"required_growth": 0.45, "ev_sales": 0.25, "pe": 0.15, "ev_ebitda": 0.15},
        "expectation_benchmarks": {"ev_sales": 12, "pe": 55, "ev_ebitda": 30},
        "financial_weights": {"cash_debt": 0.25, "operating": 0.25, "fcf": 0.30, "leverage": 0.20},
        "financial_benchmarks": {"operating": 0.24, "fcf": 0.18},
    },
    "Consumer Cyclical": {
        "business_weights": {"growth": 0.20, "gross": 0.10, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.15},
        "business_benchmarks": {"gross": 0.45, "operating": 0.18, "fcf": 0.12, "roe": 0.22},
        "expectation_weights": {"required_growth": 0.40, "ev_sales": 0.20, "pe": 0.20, "ev_ebitda": 0.20},
        "expectation_benchmarks": {"ev_sales": 6, "pe": 35, "ev_ebitda": 20},
        "financial_weights": {"cash_debt": 0.25, "operating": 0.25, "fcf": 0.25, "leverage": 0.25},
        "financial_benchmarks": {"operating": 0.16, "fcf": 0.10},
    },
    "Consumer Defensive": {
        "business_weights": {"growth": 0.10, "gross": 0.10, "operating": 0.20, "fcf": 0.25, "roe": 0.15, "balance": 0.20},
        "business_benchmarks": {"gross": 0.40, "operating": 0.16, "fcf": 0.12, "roe": 0.22},
        "expectation_weights": {"required_growth": 0.30, "ev_sales": 0.20, "pe": 0.25, "ev_ebitda": 0.25},
        "expectation_benchmarks": {"ev_sales": 5, "pe": 32, "ev_ebitda": 18},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.25, "fcf": 0.30, "leverage": 0.25},
        "financial_benchmarks": {"operating": 0.15, "fcf": 0.11},
    },
    "Industrials": {
        "business_weights": {"growth": 0.15, "gross": 0.10, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.20},
        "business_benchmarks": {"gross": 0.40, "operating": 0.18, "fcf": 0.12, "roe": 0.20},
        "expectation_weights": {"required_growth": 0.35, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.25},
        "expectation_benchmarks": {"ev_sales": 5, "pe": 35, "ev_ebitda": 20},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.25, "fcf": 0.25, "leverage": 0.30},
        "financial_benchmarks": {"operating": 0.16, "fcf": 0.10},
    },
    "Healthcare": {
        "business_weights": {"growth": 0.20, "gross": 0.15, "operating": 0.15, "fcf": 0.15, "roe": 0.10, "balance": 0.25},
        "business_benchmarks": {"gross": 0.65, "operating": 0.22, "fcf": 0.16, "roe": 0.20},
        "expectation_weights": {"required_growth": 0.45, "ev_sales": 0.25, "pe": 0.15, "ev_ebitda": 0.15},
        "expectation_benchmarks": {"ev_sales": 10, "pe": 50, "ev_ebitda": 28},
        "financial_weights": {"cash_debt": 0.35, "operating": 0.20, "fcf": 0.20, "leverage": 0.25},
        "financial_benchmarks": {"operating": 0.20, "fcf": 0.14},
    },
    "Energy": {
        "business_weights": {"growth": 0.10, "gross": 0.05, "operating": 0.20, "fcf": 0.30, "roe": 0.10, "balance": 0.25},
        "business_benchmarks": {"gross": 0.35, "operating": 0.20, "fcf": 0.15, "roe": 0.18},
        "expectation_weights": {"required_growth": 0.25, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.35},
        "expectation_benchmarks": {"ev_sales": 4, "pe": 25, "ev_ebitda": 12},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.20, "fcf": 0.30, "leverage": 0.30},
        "financial_benchmarks": {"operating": 0.18, "fcf": 0.13},
    },
    "Basic Materials": {
        "business_weights": {"growth": 0.10, "gross": 0.10, "operating": 0.20, "fcf": 0.25, "roe": 0.10, "balance": 0.25},
        "business_benchmarks": {"gross": 0.35, "operating": 0.18, "fcf": 0.12, "roe": 0.18},
        "expectation_weights": {"required_growth": 0.25, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.35},
        "expectation_benchmarks": {"ev_sales": 4, "pe": 25, "ev_ebitda": 12},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.20, "fcf": 0.25, "leverage": 0.35},
        "financial_benchmarks": {"operating": 0.16, "fcf": 0.10},
    },
    "Utilities": {
        "business_weights": {"growth": 0.05, "gross": 0.05, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.35},
        "business_benchmarks": {"gross": 0.35, "operating": 0.22, "fcf": 0.10, "roe": 0.14},
        "expectation_weights": {"required_growth": 0.20, "ev_sales": 0.15, "pe": 0.30, "ev_ebitda": 0.35},
        "expectation_benchmarks": {"ev_sales": 5, "pe": 28, "ev_ebitda": 16},
        "financial_weights": {"cash_debt": 0.10, "operating": 0.20, "fcf": 0.20, "leverage": 0.50},
        "financial_benchmarks": {"operating": 0.20, "fcf": 0.09},
    },
    "Real Estate": {
        "business_weights": {"growth": 0.10, "gross": 0.05, "operating": 0.15, "fcf": 0.25, "roe": 0.10, "balance": 0.35},
        "business_benchmarks": {"gross": 0.55, "operating": 0.35, "fcf": 0.18, "roe": 0.14},
        "expectation_weights": {"required_growth": 0.20, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.40},
        "expectation_benchmarks": {"ev_sales": 8, "pe": 35, "ev_ebitda": 22},
        "financial_weights": {"cash_debt": 0.10, "operating": 0.15, "fcf": 0.25, "leverage": 0.50},
        "financial_benchmarks": {"operating": 0.28, "fcf": 0.15},
    },
}

DEFAULT_SECTOR_MODEL = {
    "business_weights": {"growth": 0.20, "gross": 0.15, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.10},
    "business_benchmarks": {"gross": 0.60, "operating": 0.30, "fcf": 0.20, "roe": 0.25},
    "expectation_weights": {"required_growth": 0.45, "ev_sales": 0.25, "pe": 0.15, "ev_ebitda": 0.15},
    "expectation_benchmarks": {"ev_sales": 14, "pe": 60, "ev_ebitda": 35},
    "financial_weights": {"cash_debt": 0.30, "operating": 0.25, "fcf": 0.25, "leverage": 0.20},
    "financial_benchmarks": {"operating": 0.25, "fcf": 0.18},
}

SEC_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt": ["DebtCurrent", "LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}


def get_sector_model(sector):
    return SECTOR_MODELS.get(sector, DEFAULT_SECTOR_MODEL)


def safe_float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def clamp(value, low=0, high=100):
    value = safe_float(value, 0)
    return max(low, min(high, value))


def money(value, currency, display_currency=None, fx_rate=1.0):
    value = safe_float(value)
    if value is None:
        return "N/A"

    if display_currency and fx_rate not in (None, 1.0):
        value = value * fx_rate
        currency = display_currency

    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    sign = "-" if value < 0 else ""
    value = abs(value)

    if currency == "JPY" and value >= 1_000_000_000:
        return f"{sign}{symbol}{value / 1_000_000_000:.2f}B"
    if currency == "JPY" and value >= 1_000_000:
        return f"{sign}{symbol}{value / 1_000_000:.0f}M"

    if value >= 1_000_000_000_000:
        return f"{sign}{symbol}{value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"{sign}{symbol}{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}{symbol}{value / 1_000_000:.2f}M"
    return f"{sign}{symbol}{value:,.2f}"


def percent(value):
    value = safe_float(value)
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def multiple(value):
    value = safe_float(value)
    if value is None or value <= 0:
        return "N/A"
    return f"{value:.1f}x"


def score(value):
    value = safe_float(value)
    if value is None:
        return "N/A"
    return str(round(clamp(value)))


def first_value(source, *keys):
    for key in keys:
        value = safe_float(source.get(key)) if isinstance(source, dict) else None
        if value is not None:
            return value
    return None


def detect_currencies(info, fast_info):
    reporting = info.get("financialCurrency") or info.get("currency") or fast_info.get("currency") or "USD"
    trading = info.get("currency") or fast_info.get("currency") or reporting
    return str(reporting).upper(), str(trading).upper()


def get_quote_price(info, fast_info):
    return first_value(
        fast_info,
        "lastPrice",
        "last_price",
        "regularMarketPrice",
        "currentPrice",
    ) or first_value(info, "currentPrice", "regularMarketPrice", "previousClose")


def get_market_cap(info, fast_info):
    return first_value(fast_info, "marketCap", "market_cap") or first_value(info, "marketCap")


@st.cache_data(ttl=3600, show_spinner=False)
def rate_to_usd(currency):
    currency = str(currency).upper()
    if currency == "USD":
        return 1.0

    direct = f"{currency}USD=X"
    inverse = f"USD{currency}=X"

    for symbol in (direct, inverse):
        try:
            ticker = yf.Ticker(symbol)
            try:
                price = first_value(dict(ticker.fast_info), "lastPrice", "last_price", "regularMarketPrice", "currentPrice")
            except Exception:
                price = None
            if price is None:
                hist = ticker.history(period="5d")
                if not hist.empty:
                    price = safe_float(hist["Close"].iloc[-1])
            if price and price > 0:
                if symbol == direct:
                    return price
                return 1.0 / price
        except Exception:
            continue

    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fx_rate(from_currency, to_currency):
    from_currency = str(from_currency).upper()
    to_currency = str(to_currency).upper()
    if from_currency == to_currency:
        return 1.0

    from_usd = rate_to_usd(from_currency)
    to_usd = rate_to_usd(to_currency)
    if from_usd is None or to_usd is None or to_usd == 0:
        return None
    return from_usd / to_usd


def convert_amount(amount, from_currency, to_currency):
    amount = safe_float(amount)
    if amount is None:
        return None
    rate = fx_rate(from_currency, to_currency)
    if rate is None:
        return amount
    return amount * rate


def get_row(df, names):
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            row = df.loc[name].dropna()
            if not row.empty:
                return row
    return None


def latest_value(df, names):
    row = get_row(df, names)
    if row is None or row.empty:
        return None
    sorted_row = sort_financial_row(row)
    return safe_float(sorted_row.iloc[0])


def sort_financial_row(row):
    try:
        dates = pd.to_datetime(row.index, errors="coerce")
        if dates.notna().all():
            return row.iloc[dates.argsort()[::-1]]
    except Exception:
        pass
    return row


def historical_series(df, names):
    row = get_row(df, names)
    if row is None:
        return pd.Series(dtype=float)
    sorted_row = sort_financial_row(row)
    return sorted_row.apply(safe_float).dropna()


def cagr(start, end, years):
    start = safe_float(start)
    end = safe_float(end)
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def normalize_capex(capex):
    capex = safe_float(capex)
    if capex is None:
        return None
    return -abs(capex)


def compute_fcf(operating_cash_flow, capex, reported_fcf=None):
    if reported_fcf is not None:
        return safe_float(reported_fcf)
    ocf = safe_float(operating_cash_flow)
    cap = normalize_capex(capex)
    if ocf is None or cap is None:
        return None
    return ocf + cap


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yahoo_data(ticker):
    stock = yf.Ticker(ticker)

    try:
        info = stock.get_info()
    except Exception:
        info = {}

    try:
        fast_info = dict(stock.fast_info)
    except Exception:
        fast_info = {}

    try:
        financials = stock.financials
    except Exception:
        financials = pd.DataFrame()

    try:
        balance = stock.balance_sheet
    except Exception:
        balance = pd.DataFrame()

    try:
        cashflow = stock.cashflow
    except Exception:
        cashflow = pd.DataFrame()

    try:
        history = stock.history(period="5y", auto_adjust=True)
    except Exception:
        history = pd.DataFrame()

    return {
        "info": info or {},
        "fast_info": fast_info or {},
        "financials": financials,
        "balance": balance,
        "cashflow": cashflow,
        "history": history,
    }


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_sec_ticker_map():
    url = "https://www.sec.gov/files/company_tickers.json"
    response = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=15)
    response.raise_for_status()

    rows = []
    for item in response.json().values():
        rows.append(
            {
                "ticker": item["ticker"].upper(),
                "cik": str(item["cik_str"]).zfill(10),
                "company": item["title"],
            }
        )

    return pd.DataFrame(rows)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_sec_companyfacts(ticker):
    try:
        ticker_map = fetch_sec_ticker_map()
        match = ticker_map[ticker_map["ticker"] == ticker.upper()]

        if match.empty:
            return None

        cik = match.iloc[0]["cik"]
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

        response = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=20)
        response.raise_for_status()

        return response.json()
    except Exception:
        return None


def sec_currency_candidates(reporting_currency, trading_currency):
    seen = []
    for currency in (reporting_currency, trading_currency, "USD"):
        if currency and currency not in seen:
            seen.append(currency)
    return seen


def sec_fact_values(companyfacts, tags, preferred_currencies):
    if not companyfacts:
        return pd.Series(dtype=float), None

    facts = companyfacts.get("facts", {}).get("us-gaap", {})

    for tag in tags:
        item = facts.get(tag)
        if not item:
            continue

        units = item.get("units", {})
        currency_order = preferred_currencies + [c for c in units if c not in preferred_currencies]

        for currency in currency_order:
            values = units.get(currency, [])
            annual = [
                x
                for x in values
                if x.get("form") in ["10-K", "20-F", "40-F"]
                and x.get("val") is not None
                and x.get("fy") is not None
            ]            if annual:
                annual = sorted(annual, key=lambda x: (x.get("fy", 0), x.get("end", "")), reverse=True)
                yearly = {}
                for row in annual:
                    fy = row.get("fy")
                    if fy not in yearly:
                        yearly[fy] = safe_float(row.get("val"))
                ordered = pd.Series([yearly[fy] for fy in sorted(yearly.keys(), reverse=True)])
                return ordered, currency

    return pd.Series(dtype=float), None


def sec_latest(companyfacts, tags, preferred_currencies):
    values, _ = sec_fact_values(companyfacts, tags, preferred_currencies)
    if values.empty:
        return None
    return safe_float(values.iloc[0])


def sec_debt(companyfacts, preferred_currencies):
    current_debt = sec_latest(
        companyfacts,
        ["DebtCurrent", "LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent"],
        preferred_currencies,
    )
    long_debt = sec_latest(
        companyfacts,
        ["LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"],
        preferred_currencies,
    )
    total = (current_debt or 0) + (long_debt or 0)

    if total > 0:
        return total

    return sec_latest(companyfacts, ["LongTermDebtAndFinanceLeaseObligations", "LongTermDebt"], preferred_currencies)


def pick_value(primary, fallback, primary_name, fallback_name, primary_currency=None, reporting_currency=None):
    if primary is not None:
        if primary_currency and reporting_currency and primary_currency != reporting_currency:
            converted = convert_amount(primary, primary_currency, reporting_currency)
            if converted is not None:
                return converted, f"{primary_name} ({primary_currency}→{reporting_currency})"
        return primary, primary_name
    return fallback, fallback_name


def solve_required_growth(enterprise_value, revenue, fcf_margin):
    enterprise_value = safe_float(enterprise_value)
    revenue = safe_float(revenue)
    fcf_margin = safe_float(fcf_margin)

    if enterprise_value is None or revenue is None or fcf_margin is None:
        return None
    if enterprise_value <= 0 or revenue <= 0 or fcf_margin <= 0:
        return None
    if DISCOUNT_RATE <= TERMINAL_GROWTH:
        return None

    def value_company(growth):
        present_value = 0
        projected_revenue = revenue
        final_fcf = 0

        for year in range(1, FORECAST_YEARS + 1):
            projected_revenue *= 1 + growth
            final_fcf = projected_revenue * fcf_margin
            present_value += final_fcf / ((1 + DISCOUNT_RATE) ** year)

        terminal_value = final_fcf * (1 + TERMINAL_GROWTH) / (DISCOUNT_RATE - TERMINAL_GROWTH)
        present_value += terminal_value / ((1 + DISCOUNT_RATE) ** FORECAST_YEARS)
        return present_value

    low = -0.20
    high = 0.80

    for _ in range(80):
        mid = (low + high) / 2
        if value_company(mid) < enterprise_value:
            low = mid
        else:
            high = mid

    return (low + high) / 2


def business_quality_score(revenue_growth, gross_margin, operating_margin, fcf_margin, roe, debt_to_assets, sector_model):
    benchmarks = sector_model["business_benchmarks"]
    weights = sector_model["business_weights"]

    growth_score = clamp(50 + (revenue_growth or 0) * 250)
    gross_score = clamp(((gross_margin or 0) / benchmarks["gross"]) * 100)
    operating_score = clamp(((operating_margin or 0) / benchmarks["operating"]) * 100)
    fcf_score = clamp(((fcf_margin or 0) / benchmarks["fcf"]) * 100)
    roe_score = clamp(((roe or 0) / benchmarks["roe"]) * 100)
    balance_score = clamp(100 - ((debt_to_assets or 0.25) / 0.80 * 100))

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

    growth_pressure = clamp(50 + (required_growth or 0) * 180)
    sales_pressure = clamp(((ev_sales or 3) / benchmarks["ev_sales"]) * 100) if ev_sales else 50
    pe_pressure = clamp(((pe or 25) / benchmarks["pe"]) * 100) if pe and pe > 0 else 50
    ebitda_pressure = clamp(((ev_ebitda or 14) / benchmarks["ev_ebitda"]) * 100) if ev_ebitda and ev_ebitda > 0 else 50

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
        cash_debt_score = 60

    operating_score = clamp(((operating_margin or 0) / benchmarks["operating"]) * 100)
    fcf_score = clamp(((fcf_margin or 0) / benchmarks["fcf"]) * 100)
    leverage_score = clamp(100 - ((debt_to_assets or 0.25) / 0.80 * 100))

    return clamp(
        cash_debt_score * weights["cash_debt"]
        + operating_score * weights["operating"]
        + fcf_score * weights["fcf"]
        + leverage_score * weights["leverage"]
    )


def growth_reality_score(historical_growth, required_growth):
    if required_growth is None:
        return 50
    if historical_growth is None:
        historical_growth = 0.05

    growth_gap = required_growth - historical_growth
    return clamp(75 - growth_gap * 160)


def probability_score(required_growth, historical_growth, business_quality, market_expectations):
    if required_growth is None:
        return 50
    if historical_growth is None:
        historical_growth = 0.05

    growth_gap = required_growth - historical_growth
    quality_support = (business_quality - 50) * 0.35
    expectation_penalty = max(0, market_expectations - business_quality) * 0.35
    growth_penalty = max(0, growth_gap) * 120

    return clamp(55 + quality_support - expectation_penalty - growth_penalty)


def reality_score(business_quality, financial_strength, market_expectations, historical_growth, required_growth):
    growth_reality = growth_reality_score(historical_growth, required_growth)
    expectation_reasonableness = clamp(100 - market_expectations * 0.55)
    reality_gap_score = clamp(50 + (business_quality - market_expectations) * 0.45)

    return clamp(
        business_quality * 0.30
        + financial_strength * 0.25
        + growth_reality * 0.20
        + expectation_reasonableness * 0.15
        + reality_gap_score * 0.10
    )


def score_label(value):
    value = safe_float(value, 0)
    if value >= 85:
        return "Strong support"
    if value >= 70:
        return "Reasonable support"
    if value >= 55:
        return "Mixed but explainable"
    if value >= 40:
        return "Demanding expectations"
    return "Very demanding expectations"


def analyze_company(yahoo_data, sec_facts):
    info = yahoo_data["info"]
    fast_info = yahoo_data["fast_info"]
    financials = yahoo_data["financials"]
    balance = yahoo_data["balance"]
    cashflow = yahoo_data["cashflow"]

    reporting_currency, trading_currency = detect_currencies(info, fast_info)
    sec_currencies = sec_currency_candidates(reporting_currency, trading_currency)
    sector = info.get("sector") or "Unknown sector"
    sector_model = get_sector_model(sector)

    price = get_quote_price(info, fast_info)
    market_cap = get_market_cap(info, fast_info)

    yahoo_revenue = latest_value(financials, ["Total Revenue", "Operating Revenue"])
    yahoo_gross_profit = latest_value(financials, ["Gross Profit"])
    yahoo_operating_income = latest_value(financials, ["Operating Income"])
    yahoo_net_income = latest_value(financials, ["Net Income", "Net Income Common Stockholders"])
    yahoo_ebitda = latest_value(financials, ["EBITDA", "Normalized EBITDA"])

    yahoo_operating_cash_flow = latest_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    yahoo_capex = latest_value(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    yahoo_free_cash_flow = compute_fcf(
        yahoo_operating_cash_flow,
        yahoo_capex,
        latest_value(cashflow, ["Free Cash Flow"]),
    )

    yahoo_cash = latest_value(balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])
    yahoo_debt = latest_value(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
    yahoo_assets = latest_value(balance, ["Total Assets"])
    yahoo_equity = latest_value(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])

    sec_revenue, sec_revenue_currency = sec_fact_values(sec_facts, SEC_TAGS["revenue"], sec_currencies)
    sec_revenue_latest = safe_float(sec_revenue.iloc[0]) if not sec_revenue.empty else None

    sec_net_income = sec_latest(sec_facts, SEC_TAGS["net_income"], sec_currencies)
    sec_assets = sec_latest(sec_facts, SEC_TAGS["assets"], sec_currencies)
    sec_equity = sec_latest(sec_facts, SEC_TAGS["equity"], sec_currencies)
    sec_cash = sec_latest(sec_facts, SEC_TAGS["cash"], sec_currencies)
    sec_debt_value = sec_debt(sec_facts, sec_currencies)

    sec_ocf = sec_latest(sec_facts, SEC_TAGS["operating_cash_flow"], sec_currencies)
    sec_capex_value = sec_latest(sec_facts, SEC_TAGS["capex"], sec_currencies)
    sec_fcf = compute_fcf(sec_ocf, sec_capex_value)

    revenue, revenue_source = pick_value(
        sec_revenue_latest,
        yahoo_revenue,
        "SEC EDGAR",
        "Yahoo Finance",
        sec_revenue_currency,
        reporting_currency,
    )
    net_income, net_income_source = pick_value(sec_net_income, yahoo_net_income, "SEC EDGAR", "Yahoo Finance", sec_revenue_currency, reporting_currency)
    total_assets, assets_source = pick_value(sec_assets, yahoo_assets, "SEC EDGAR", "Yahoo Finance", sec_revenue_currency, reporting_currency)
    equity, equity_source = pick_value(sec_equity, yahoo_equity, "SEC EDGAR", "Yahoo Finance", sec_revenue_currency, reporting_currency)
    cash, cash_source = pick_value(sec_cash, yahoo_cash, "SEC EDGAR", "Yahoo Finance", sec_revenue_currency, reporting_currency)
    debt, debt_source = pick_value(sec_debt_value, yahoo_debt, "SEC EDGAR", "Yahoo Finance", sec_revenue_currency, reporting_currency)
    free_cash_flow, fcf_source = pick_value(sec_fcf, yahoo_free_cash_flow, "SEC EDGAR", "Yahoo Finance", sec_revenue_currency, reporting_currency)

    market_cap_reporting = convert_amount(market_cap, trading_currency, reporting_currency)
    enterprise_value_trading = safe_float(info.get("enterpriseValue"))
    if enterprise_value_trading is None and market_cap is not None:
        debt_reporting = debt or 0
        cash_reporting = cash or 0
        enterprise_value_trading = market_cap + convert_amount(debt_reporting, reporting_currency, trading_currency) - convert_amount(cash_reporting, reporting_currency, trading_currency)

    enterprise_value = convert_amount(enterprise_value_trading, trading_currency, reporting_currency)
    if enterprise_value is None and market_cap_reporting is not None:
        enterprise_value = market_cap_reporting + (debt or 0) - (cash or 0)

    revenue_history, _ = sec_fact_values(sec_facts, SEC_TAGS["revenue"], sec_currencies)
    if revenue_history.empty:
        revenue_history = historical_series(financials, ["Total Revenue", "Operating Revenue"])

    historical_growth = None
    if len(revenue_history) >= 2:
        years = min(len(revenue_history) - 1, 3)
        newest = revenue_history.iloc[0]
        oldest = revenue_history.iloc[years]
        historical_growth = cagr(oldest, newest, years)

    gross_margin = safe_float(info.get("grossMargins"))
    if gross_margin is None and yahoo_gross_profit is not None and revenue:
        gross_margin = yahoo_gross_profit / revenue

    operating_margin = safe_float(info.get("operatingMargins"))
    if operating_margin is None and yahoo_operating_income is not None and revenue:
        operating_margin = yahoo_operating_income / revenue

    profit_margin = safe_float(info.get("profitMargins"))
    if profit_margin is None and net_income is not None and revenue:
        profit_margin = net_income / revenue

    fcf_margin = None
    if free_cash_flow is not None and revenue:
        fcf_margin = free_cash_flow / revenue

    model_fcf_margin = fcf_margin if fcf_margin is not None and fcf_margin > 0 else DEFAULT_FCF_MARGIN

    roe = safe_float(info.get("returnOnEquity"))
    if roe is None and net_income is not None and equity:
        roe = net_income / equity

    debt_to_assets = None
    if debt is not None and total_assets:
        debt_to_assets = debt / total_assets

    ev_sales = enterprise_value / revenue if enterprise_value and revenue else None

    ev_ebitda = safe_float(info.get("enterpriseToEbitda"))
    if (ev_ebitda is None or ev_ebitda <= 0) and enterprise_value is not None and yahoo_ebitda:
        ev_ebitda = enterprise_value / yahoo_ebitda

    pe = safe_float(info.get("trailingPE"))
    if pe is not None and pe <= 0:
        pe = None

    required_growth = solve_required_growth(enterprise_value, revenue, model_fcf_margin)

    business_quality = business_quality_score(
        historical_growth,
        gross_margin,
        operating_margin,
        fcf_margin,
        roe,
        debt_to_assets,
        sector_model,
    )

    market_expectations = expectation_score(required_growth, ev_sales, pe, ev_ebitda, sector_model)

    financial_strength = financial_strength_score(
        cash,
        debt,
        operating_margin,
        fcf_margin,
        debt_to_assets,
        sector_model,
    )

    gap = business_quality - market_expectations

    probability = probability_score(
        required_growth,
        historical_growth,
        business_quality,
        market_expectations,
    )

    final_score = reality_score(
        business_quality,
        financial_strength,
        market_expectations,
        historical_growth,
        required_growth,
    )

    sources = {
        "Revenue": revenue_source,
        "Net Income": net_income_source,
        "Assets": assets_source,
        "Equity": equity_source,
        "Cash": cash_source,
        "Debt": debt_source,
        "Free Cash Flow": fcf_source,
        "Price / Market Data": "Yahoo Finance",
        "Reporting Currency": f"Yahoo Finance ({reporting_currency})",
        "Trading Currency": f"Yahoo Finance ({trading_currency})",
    }

    return {
        "reporting_currency": reporting_currency,
        "trading_currency": trading_currency,
        "sector": sector,
        "sector_model": sector_model,
        "price": price,
        "market_cap": market_cap,
        "market_cap_reporting": market_cap_reporting,
        "enterprise_value": enterprise_value,
        "enterprise_value_trading": enterprise_value_trading,
        "revenue": revenue,
        "free_cash_flow": free_cash_flow,
        "cash": cash,
        "debt": debt,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "profit_margin": profit_margin,
        "fcf_margin": fcf_margin,
        "model_fcf_margin": model_fcf_margin,
        "historical_growth": historical_growth,
        "required_growth": required_growth,
        "business_quality": business_quality,
        "market_expectations": market_expectations,
        "financial_strength": financial_strength,
        "gap": gap,
        "probability": probability,
        "reality_score": final_score,
        "ev_sales": ev_sales,
        "ev_ebitda": ev_ebitda,
        "pe": pe,
        "sources": sources,
        "has_sec": sec_facts is not None,
    }


def conclusion_text(analysis):
    if analysis["reality_score"] >= 80:
        return "The company evidence appears to strongly support the expectations embedded in the price."
    if analysis["reality_score"] >= 65:
        return "The company evidence appears to reasonably support expectations, though execution still matters."
    if analysis["reality_score"] >= 50:
        return "The setup is mixed. The market appears to require real future success, but the evidence is not empty."
    if analysis["reality_score"] >= 40:
        return "Expectations look demanding. The company needs stronger execution to support what the market appears to price in."
    return "Expectations look very demanding compared with the company evidence currently available."


def pricing_points(analysis):
    return [
        ("Required revenue growth", f"{percent(analysis['required_growth'])} per year"),
        ("FCF margin used in model", percent(analysis["model_fcf_margin"])),
        ("EV/Sales pressure", multiple(analysis["ev_sales"])),
        ("Probability estimate", f"{score(analysis['probability'])}/100"),
    ]
def reality_points(analysis, display_currency, display_fx):
    return [
        ("Historical revenue growth", percent(analysis["historical_growth"])),
        ("Operating margin", percent(analysis["operating_margin"])),
        ("Free cash flow margin", percent(analysis["fcf_margin"])),
        (
            "Cash vs debt",
            f"{money(analysis['cash'], analysis['reporting_currency'], display_currency, display_fx)} / {money(analysis['debt'], analysis['reporting_currency'], display_currency, display_fx)}",
        ),
    ]


def risk_rows(analysis):
    rows = []

    if analysis["required_growth"] is not None and analysis["required_growth"] > 0.18:
        rows.append(("Growth burden", "Required growth is high", "The company needs strong execution for years."))

    if analysis["market_expectations"] > 70:
        rows.append(("Expectation burden", "Expectations are high", "Good results may still not satisfy the market."))

    if analysis["fcf_margin"] is not None and analysis["fcf_margin"] < 0.05:
        rows.append(("Cash conversion", "Low FCF margin", "Revenue may not convert into enough free cash flow."))

    if analysis["debt"] is not None and analysis["cash"] is not None and analysis["debt"] > analysis["cash"] * 2:
        rows.append(("Balance sheet", "Debt is much larger than cash", "Less flexibility if business conditions weaken."))

    if analysis["reporting_currency"] != analysis["trading_currency"]:
        rows.append(
            (
                "Currency mix",
                f"Statements in {analysis['reporting_currency']}, quote in {analysis['trading_currency']}",
                "Valuation ratios are normalized to reporting currency before scoring.",
            )
        )

    if not rows:
        rows.append(("Expectation reset", "Main risk is expectation pressure", "The story can weaken if expectations rise too far."))

    return rows


def make_rows(items):
    html = ""
    for label, value in items:
        html += f"<div class='row'><span>{label}</span><b>{value}</b></div>"
    return html


if "ticker" not in st.session_state:
    st.session_state.ticker = ""

if "display_currency" not in st.session_state:
    st.session_state.display_currency = "USD"


st.markdown('<div class="app-wrap">', unsafe_allow_html=True)

st.markdown(
    """
    <div class="nav">
        <div>
            <div class="brand">TSRP</div>
            <div class="mission">
                Expectation Reality Check. Understand what a stock price appears to expect from a company.
                No buy, sell, or hold recommendations.
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.form("search_form"):
    col_a, col_b, col_c = st.columns([3, 1.2, 1])
    with col_a:
        typed = st.text_input("Company ticker", value=st.session_state.ticker, placeholder="AAPL, SAP.DE, 7203.T").upper().strip()
    with col_b:
        display_currency = st.selectbox("Display currency", DISPLAY_CURRENCIES, index=DISPLAY_CURRENCIES.index(st.session_state.display_currency))
    with col_c:
        st.write("")
        submitted = st.form_submit_button("Analyze", use_container_width=True)

if submitted:
    st.session_state.ticker = typed
    st.session_state.display_currency = display_currency
else:
    display_currency = st.session_state.display_currency

if not st.session_state.ticker:
    st.markdown(
        """
        <div class="search-panel">
            <div class="eyebrow">Start here</div>
            <div class="headline">Enter a ticker</div>
            <div class="copy">
                TSRP uses Yahoo Finance for market data and SEC EDGAR for official U.S. filing data when available.
                Financial statements stay in the company's reporting currency; you can view amounts in any display currency.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns(2)
    with left:
        st.markdown(
            """
            <div class="panel">
                <h3>One job</h3>
                <p>Translate market price into expectations, then compare those expectations against business reality.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            """
            <div class="panel">
                <h3>Multiple sources</h3>
                <p>Yahoo Finance supplies market data. SEC EDGAR supplies official U.S. filing facts when available.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


ticker = st.session_state.ticker

with st.spinner(f"Loading {ticker}..."):
    yahoo_data = fetch_yahoo_data(ticker)
    info = yahoo_data["info"]
    sec_facts = fetch_sec_companyfacts(ticker)

if not info:
    st.markdown(
        """
        <div class="search-panel">
            <div class="eyebrow">No data returned</div>
            <div class="headline">Check ticker</div>
            <div class="copy">Yahoo Finance did not return company data for that ticker.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

analysis = analyze_company(yahoo_data, sec_facts)
reporting_currency = analysis["reporting_currency"]
trading_currency = analysis["trading_currency"]
display_fx_reporting = fx_rate(reporting_currency, display_currency) or 1.0
display_fx_trading = fx_rate(trading_currency, display_currency) or 1.0

company_name = info.get("longName") or info.get("shortName") or ticker
sector = analysis["sector"]
industry = info.get("industry") or "Unknown industry"

currency_note = reporting_currency
if reporting_currency != display_currency:
    currency_note = f"{reporting_currency} → {display_currency} @ {display_fx_reporting:.4f}"

st.markdown(
    f"""
    <div class="currency-banner">
        <span>Reporting currency: <b>{reporting_currency}</b> · Trading currency: <b>{trading_currency}</b></span>
        <span>Display: <b>{display_currency}</b>{f" · FX {currency_note}" if reporting_currency != display_currency else ""}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="landing-grid">
        <div class="landing-card">
            <div class="eyebrow">Expectation Reality Check</div>
            <div class="headline-small">{company_name} <span class="muted">{ticker}</span></div>
            <div class="copy">{conclusion_text(analysis)}</div>
        </div>
        <div class="score-card">
            <div class="eyebrow-dark">Expectation Reality Score</div>
            <div class="score-number">{score(analysis["reality_score"])}</div>
            <div class="score-label">{score_label(analysis["reality_score"])}</div>
            <div class="score-explain">How well the company evidence supports the expectations implied by the current price.</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="metric-grid">
        <div class="mini-card">
            <div class="eyebrow">Price</div>
            <div class="mini-value">{money(analysis["price"], trading_currency, display_currency, display_fx_trading)}</div>
            <div class="mini-sub">{trading_currency} quote · Yahoo Finance</div>
        </div>
        <div class="mini-card">
            <div class="eyebrow">Market cap</div>
            <div class="mini-value">{money(analysis["market_cap"], trading_currency, display_currency, display_fx_trading)}</div>
            <div class="mini-sub">{trading_currency} · Yahoo Finance</div>
        </div>
        <div class="mini-card">
            <div class="eyebrow">Revenue</div>
            <div class="mini-value">{money(analysis["revenue"], reporting_currency, display_currency, display_fx_reporting)}</div>
            <div class="mini-sub">{analysis["sources"]["Revenue"]}</div>
        </div>
        <div class="mini-card">
            <div class="eyebrow">Free cash flow</div>
            <div class="mini-value">{money(analysis["free_cash_flow"], reporting_currency, display_currency, display_fx_reporting)}</div>
            <div class="mini-sub">{analysis["sources"]["Free Cash Flow"]}</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="two-col">
        <div class="panel">
            <h3>What the market is pricing</h3>
            {make_rows(pricing_points(analysis))}
        </div>
        <div class="panel">
            <h3>What the business shows</h3>
            {make_rows(reality_points(analysis, display_currency, display_fx_reporting))}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="panel">
        <h3>Score breakdown</h3>
        <div class="bar-wrap">
            <div class="bar-label"><span>Expectation Reality Score</span><span>{score(analysis["reality_score"])}</span></div>
            <div class="bar"><div class="bar-fill" style="width:{clamp(analysis["reality_score"])}%"></div></div>
        </div>
        <div class="bar-wrap">
            <div class="bar-label"><span>Business Quality</span><span>{score(analysis["business_quality"])}</span></div>
            <div class="bar"><div class="bar-fill" style="width:{clamp(analysis["business_quality"])}%"></div></div>
        </div>
        <div class="bar-wrap">
            <div class="bar-label"><span>Market Expectations</span><span>{score(analysis["market_expectations"])}</span></div>
            <div class="bar"><div class="bar-fill" style="width:{clamp(analysis["market_expectations"])}%"></div></div>
        </div>
        <div class="bar-wrap">
            <div class="bar-label"><span>Financial Strength</span><span>{score(analysis["financial_strength"])}</span></div>
            <div class="bar"><div class="bar-fill" style="width:{clamp(analysis["financial_strength"])}%"></div></div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_learn, tab_evidence, tab_sources, tab_model, tab_risk = st.tabs(
    ["Learn", "Evidence", "Data Sources", "Sector Model", "Expectation Breakers"]
)

with tab_learn:
    st.markdown(
        """
        <div class="learn-grid">
            <div class="learn-card">
                <h4>Market expectations</h4>
                <p>The future performance that appears necessary to support the current valuation.</p>
            </div>
            <div class="learn-card">
                <h4>Business reality</h4>
                <p>The company evidence today: growth, margins, cash flow, cash, debt, and profitability.</p>
            </div>
            <div class="learn-card">
                <h4>Reality gap</h4>
                <p>Business Quality minus Market Expectations. It shows whether evidence is ahead of or behind the expectation burden.</p>
            </div>
            <div class="learn-card">
                <h4>Expectation Reality Score</h4>
                <p>The final score summarizing whether company evidence supports what the market appears to expect.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with tab_evidence:
    table = pd.DataFrame(
        [
            ["Company", company_name],
            ["Ticker", ticker],
            ["Sector", sector],
            ["Industry", industry],
            ["Reporting Currency", reporting_currency],
            ["Trading Currency", trading_currency],
            ["Display Currency", display_currency],
            ["Enterprise Value", money(analysis["enterprise_value"], reporting_currency, display_currency, display_fx_reporting)],
            ["Revenue", money(analysis["revenue"], reporting_currency, display_currency, display_fx_reporting)],
            ["Free Cash Flow", money(analysis["free_cash_flow"], reporting_currency, display_currency, display_fx_reporting)],
            ["Cash", money(analysis["cash"], reporting_currency, display_currency, display_fx_reporting)],
            ["Debt", money(analysis["debt"], reporting_currency, display_currency, display_fx_reporting)],
            ["Gross Margin", percent(analysis["gross_margin"])],
            ["Operating Margin", percent(analysis["operating_margin"])],
            ["Profit Margin", percent(analysis["profit_margin"])],
            ["FCF Margin", percent(analysis["fcf_margin"])],
            ["Historical Revenue Growth", percent(analysis["historical_growth"])],
            ["Required Revenue Growth", percent(analysis["required_growth"])],
            ["EV/Sales", multiple(analysis["ev_sales"])],
            ["EV/EBITDA", multiple(analysis["ev_ebitda"])],
            ["P/E", multiple(analysis["pe"])],
        ],
        columns=["Metric", "Value"],
    )
    st.dataframe(table, use_container_width=True, hide_index=True)

    history = yahoo_data["history"]
    if history is not None and not history.empty:
        chart_data = history[["Close"]].copy()
        if trading_currency != display_currency and display_fx_trading not in (None, 1.0):
            chart_data["Close"] = chart_data["Close"] * display_fx_trading
        st.line_chart(chart_data, height=300)

with tab_sources:
    source_table = pd.DataFrame(
        [[k, v] for k, v in analysis["sources"].items()],
        columns=["Data Item", "Source Used"],
    )
    st.dataframe(source_table, use_container_width=True, hide_index=True)

    if analysis["has_sec"]:
        st.success("SEC EDGAR filing facts were found and used where available.")
    else:
        st.info("SEC EDGAR facts were not available for this ticker. Yahoo Finance was used as fallback.")

    if reporting_currency != trading_currency:
        st.info(
            f"Financial statements are reported in {reporting_currency}. "
            f"Market price and market cap are quoted in {trading_currency}. "
            f"Valuation ratios normalize enterprise value into {reporting_currency} before calculation."
        )

with tab_model:
    model = analysis["sector_model"]
    st.markdown(f"### Sector framework: {sector}")
    st.caption("Weights and benchmarks are selected automatically from Yahoo Finance sector classification.")

    business_rows = [
        ["Revenue growth", model["business_weights"]["growth"]],
        ["Gross margin", model["business_weights"]["gross"]],
        ["Operating margin", model["business_weights"]["operating"]],
        ["FCF margin", model["business_weights"]["fcf"]],
        ["ROE", model["business_weights"]["roe"]],
        ["Balance sheet", model["business_weights"]["balance"]],
    ]
    expectation_rows = [
        ["Required growth", model["expectation_weights"]["required_growth"]],
        ["EV/Sales", model["expectation_weights"]["ev_sales"]],
        ["P/E", model["expectation_weights"]["pe"]],
        ["EV/EBITDA", model["expectation_weights"]["ev_ebitda"]],
    ]
    financial_rows = [
        ["Cash relative to debt", model["financial_weights"]["cash_debt"]],
        ["Operating margin", model["financial_weights"]["operating"]],
        ["FCF margin", model["financial_weights"]["fcf"]],
        ["Leverage", model["financial_weights"]["leverage"]],
    ]

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### Business Quality weights")
        st.dataframe(
            pd.DataFrame(business_rows, columns=["Metric", "Weight"]).assign(
                Weight=lambda x: x["Weight"].map(lambda v: f"{v:.0%}")
            ),
            hide_index=True,
            use_container_width=True,
        )
    with col2:
        st.markdown("#### Market Expectations weights")
        st.dataframe(
            pd.DataFrame(expectation_rows, columns=["Metric", "Weight"]).assign(
                Weight=lambda x: x["Weight"].map(lambda v: f"{v:.0%}")
            ),
            hide_index=True,
            use_container_width=True,
        )
    with col3:
        st.markdown("#### Financial Strength weights")
        st.dataframe(
            pd.DataFrame(financial_rows, columns=["Metric", "Weight"]).assign(
                Weight=lambda x: x["Weight"].map(lambda v: f"{v:.0%}")
            ),
            hide_index=True,
            use_container_width=True,
        )


with tab_risk:
    risks = risk_rows(analysis)
    risk_html = ""
    for title, signal, meaning in risks:
        risk_html += f"""
        <div class="risk-item">
            <b>{title}</b>
            <p>{signal}. {meaning}</p>
        </div>
        """
    st.markdown(f"<div class='risk-list'>{risk_html}</div>", unsafe_allow_html=True)

st.markdown("</div>", unsafe_allow_html=True)

st.caption(
    f"Data sources: Yahoo Finance via yfinance, SEC EDGAR companyfacts where available. "
    f"Reporting currency: {reporting_currency}. Display currency: {display_currency}. "
    f"No buy, sell, or hold recommendations. Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}."
)
