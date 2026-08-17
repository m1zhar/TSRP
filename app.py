from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import base64
import html
import math
import re

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from engine import (
    FORECAST_YEARS,
    business_quality_score,
    choose_model_fcf_margin,
    dcf_enterprise_value,
    expectation_score,
    financial_strength_score,
    history_cagr,
    model_rates,
    probability_score,
    reality_score,
    score_label,
    solve_required_growth,
)


ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "tsrp-logo.png"
APP_NAME = "The Saleh Research Project"
APP_SHORT = "TSRP"
EDUCATIONAL_DISCLAIMER = "For educational purposes only. Not investment advice."

st.set_page_config(
    page_title=f"{APP_SHORT} · {APP_NAME}",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "T",
    layout="wide",
    initial_sidebar_state="collapsed",
)

INK = {
    "bg": "#0b0c0f",
    "bg_elevated": "#181714",
    "card": "#181714",
    "card_hover": "#221f1a",
    "surface": "#181714",
    "surface_2": "#2a261f",
    "text": "#fff4dc",
    "text_secondary": "#d2c09a",
    "text_tertiary": "#a39068",
    "blue": "#ffc107",
    "blue_bright": "#ffd54f",
    "cyan": "#ffd27a",
    "purple": "#e6b422",
    "green": "#2ee56b",
    "orange": "#ff9100",
    "red": "#ff3b3b",
    "border": "#3d3420",
    "border_strong": "#5a4a28",
    "fill": "#221f18",
}


def current_scheme():
    return INK


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


CHART_TIMEFRAMES = ["1M", "3M", "6M", "YTD", "1Y", "5Y"]

UP_COLOR = "#089981"
DOWN_COLOR = "#f23645"

_AXIS_STYLE = dict(
    showgrid=True,
    gridcolor="rgba(255,255,255,0.04)",
    zeroline=False,
    tickfont=dict(color="#6a6d78", size=11),
    linecolor="rgba(255,255,255,0.06)",
)


def slice_timeframe(df, timeframe):
    if df.empty or timeframe == "5Y":
        return df
    end = df.index.max()
    if timeframe == "YTD":
        cutoff = pd.Timestamp(year=end.year, month=1, day=1, tz=getattr(end, "tz", None))
    else:
        months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12}[timeframe]
        cutoff = end - pd.DateOffset(months=months)
    sliced = df[df.index >= cutoff]
    return sliced if not sliced.empty else df


SMA_COLORS = {20: "#f7931a", 50: "#00bcd4", 200: "#7c5cff"}


def render_price_chart(history, display_fx=1.0, kind="Candles", timeframe="1Y", smas=()):
    scheme = current_scheme()
    up_color = scheme["green"]
    down_color = scheme["red"]
    accent = scheme["blue"]
    df = history.copy()
    if "Close" not in df.columns:
        st.info("Price history is missing Close data.")
        return
    if display_fx not in (None, 1.0):
        for col in ("Open", "High", "Low", "Close"):
            if col in df.columns:
                df[col] = df[col] * display_fx
    for window in smas:
        df[f"SMA{window}"] = df["Close"].rolling(window).mean()
    df = slice_timeframe(df, timeframe)

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        st.line_chart(df[["Close"]], height=320)
        return

    has_volume = "Volume" in df.columns and df["Volume"].fillna(0).sum() > 0
    if has_volume:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.04)
    else:
        fig = make_subplots(rows=1, cols=1)

    if kind == "Candles" and {"Open", "High", "Low", "Close"}.issubset(df.columns):
        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                increasing_line_color=up_color,
                increasing_fillcolor=up_color,
                decreasing_line_color=down_color,
                decreasing_fillcolor=down_color,
                line=dict(width=1),
                whiskerwidth=0.6,
                name="",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Close"],
                mode="lines",
                line=dict(color=accent, width=2.2),
                fill="tozeroy",
                fillcolor="rgba(41, 98, 255, 0.14)",
                hovertemplate="%{y:,.2f}<extra></extra>",
                name="",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    for window in smas:
        col = f"SMA{window}"
        if col in df.columns and df[col].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df[col],
                    mode="lines",
                    line=dict(color=SMA_COLORS.get(window, "#9aa0ab"), width=1.4),
                    name=f"SMA {window}",
                    showlegend=True,
                    hovertemplate="%{y:,.2f}<extra>SMA " + str(window) + "</extra>",
                ),
                row=1,
                col=1,
            )

    if has_volume:
        vol_colors = [
            up_color if c >= o else down_color
            for o, c in zip(df["Open"].fillna(0), df["Close"].fillna(0))
        ]
        fig.add_trace(
            go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, opacity=0.4, name="", showlegend=False),
            row=2,
            col=1,
        )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=4, r=4, t=8, b=4),
        height=440 if has_volume else 360,
        showlegend=bool(smas),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
            font=dict(color="#9aa0ab", size=11),
            bgcolor="rgba(0,0,0,0)",
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#12161e", bordercolor="rgba(255,255,255,0.1)", font_color="#e8eaed"),
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(**_AXIS_STYLE)
    fig.update_yaxes(**_AXIS_STYLE)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def render_html(markup):
    st.markdown(markup.strip(), unsafe_allow_html=True)


def esc(value):
    return html.escape("" if value is None else str(value))


def brand_logo_svg(size="sm"):
    cls = "logo-mark logo-lg" if size == "lg" else "logo-mark"
    if LOGO_PATH.exists():
        payload = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
        return (
            f'<div class="{cls}" aria-hidden="true">'
            f'<img src="data:image/png;base64,{payload}" alt="TSRP logo" />'
            f"</div>"
        )
    return (
        f'<div class="{cls}" aria-hidden="true">'
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="4" y="18" width="4" height="8" rx="1.2" fill="white" opacity="0.55"/>'
        '<rect x="11" y="12" width="4" height="14" rx="1.2" fill="white" opacity="0.75"/>'
        '<rect x="18" y="7" width="4" height="19" rx="1.2" fill="white"/>'
        '<path d="M5 15.5 L12 11 L19 13.5 L27 6" stroke="#7CFFB2" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="27" cy="6" r="2.2" fill="#7CFFB2"/>'
        "</svg></div>"
    )


def brand_lockup_html():
    return (
        f'<div class="brand-lockup">'
        f"{brand_logo_svg('sm')}"
        f"<div class='brand-text'>"
        f"<div class='brand-title'>{esc(APP_SHORT)}</div>"
        f"<div class='brand-name'>{esc(APP_NAME)}</div>"
        f"</div></div>"
    )


def render_app_header():
    try:
        left, right = st.columns([1.7, 1.3], vertical_alignment="center")
    except TypeError:
        left, right = st.columns([1.7, 1.3])
    with left:
        try:
            logo_col, text_col = st.columns([0.32, 0.68], vertical_alignment="center")
        except TypeError:
            logo_col, text_col = st.columns([0.32, 0.68])
        with logo_col:
            if LOGO_PATH.exists():
                st.image(str(LOGO_PATH), width=56)
            else:
                render_html(brand_logo_svg("sm"))
        with text_col:
            render_html(
                f"<div class='brand-title'>{esc(APP_SHORT)}</div>"
                f"<div class='brand-name'>{esc(APP_NAME)}</div>"
            )
    with right:
        render_html(
            f'<div class="header-actions">'
            f'<div class="live-pill"><span class="live-dot"></span>Live data</div>'
            f'<div class="live-pill badge-muted">{esc(EDUCATIONAL_DISCLAIMER)}</div>'
            f"</div>"
        )


st.markdown(
    """
    <style>
    @import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@500;600&display=swap");

    :root {
        --bg: #0b0c0f;
        --bg-elevated: #181714;
        --card: #181714;
        --card-hover: #221f1a;
        --surface: #181714;
        --surface-2: #2a261f;
        --text: #fff4dc;
        --text-secondary: #d2c09a;
        --text-tertiary: #a39068;
        --blue: #ffc107;
        --blue-bright: #ffd54f;
        --blue-soft: rgba(255, 193, 7, 0.22);
        --cyan: #ffd27a;
        --cyan-soft: rgba(255, 210, 122, 0.18);
        --purple: #e6b422;
        --green: #2ee56b;
        --green-soft: rgba(46, 229, 107, 0.18);
        --orange: #ff9100;
        --orange-soft: rgba(255, 145, 0, 0.18);
        --red: #ff3b3b;
        --red-soft: rgba(255, 59, 59, 0.18);
        --border: #3d3420;
        --border-strong: #5a4a28;
        --fill: #221f18;
        --grid: rgba(255, 255, 255, 0.04);
        --shadow: none;
        --shadow-soft: none;
        --glow-blue: none;
        --radius-xl: 4px;
        --radius-lg: 4px;
        --radius-md: 4px;
        --radius-sm: 2px;
        --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
        --display: "IBM Plex Sans", "Segoe UI", -apple-system, sans-serif;
        --control-h: 40px;
        --side-w: 120px;
    }

    header[data-testid="stHeader"] {
        background: var(--bg) !important;
    }

    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"],
    #MainMenu,
    footer { display: none !important; }

    html, body, [class*="css"] {
        font-family: var(--display) !important;
        -webkit-font-smoothing: antialiased;
        letter-spacing: 0.01em;
    }

    .stApp {
        background: var(--bg) !important;
        color: var(--text);
    }

    .block-container {
        max-width: 1360px;
        padding-top: 12px;
        padding-bottom: 28px;
        margin-left: auto;
        margin-right: auto;
    }

    section[data-testid="stSidebar"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"] {
        display: none !important;
        width: 0 !important;
        min-width: 0 !important;
        visibility: hidden !important;
    }

    [data-testid="stMarkdownContainer"] {
        overflow: visible !important;
    }

    [data-testid="stMarkdownContainer"] img {
        max-width: none !important;
        max-height: none !important;
    }

    div[data-testid="stImage"] {
        margin-bottom: 0 !important;
        overflow: visible !important;
    }

    [data-testid="stImageContainer"],
    [data-testid="stElementContainer"]:has([data-testid="stImage"]) {
        overflow: visible !important;
        min-height: 56px;
    }

    div[data-testid="stImage"] img {
        width: 56px !important;
        height: 56px !important;
        max-width: 56px !important;
        object-fit: contain !important;
        border-radius: 10px;
        display: block;
    }

    div[data-testid="stImageCaption"] { display: none !important; }

    .terminal-header {
        display: flex;
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
        text-align: left;
        gap: 16px;
        padding: 12px 14px;
        margin-bottom: 12px;
        min-height: 56px;
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 4px;
        box-shadow: none;
        position: relative;
        overflow: visible;
        flex-wrap: wrap;
    }

    .terminal-header::before { display: none; }
    .terminal-header > * { position: relative; z-index: 1; overflow: visible; }

    .brand-lockup {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-shrink: 0;
        min-width: 0;
        overflow: visible;
    }

    .brand-lockup.centered {
        flex-direction: row;
        text-align: left;
        gap: 10px;
    }

    .brand-text {
        display: flex;
        flex-direction: column;
        justify-content: center;
        gap: 2px;
        min-width: 0;
        overflow: visible;
        flex-shrink: 1;
    }

    .brand-mark, .logo-mark {
        width: 56px;
        height: 56px;
        border-radius: 10px;
        display: grid;
        place-items: center;
        background: transparent;
        color: #fff;
        flex-shrink: 0;
        overflow: visible;
        border: none;
        box-shadow: none;
        padding: 0;
    }

    .brand-mark svg, .logo-mark svg,
    .brand-mark img, .logo-mark img {
        width: 56px;
        height: 56px;
        display: block;
        object-fit: contain;
        border-radius: 10px;
    }

    .logo-mark.logo-lg {
        width: 56px;
        height: 56px;
        border-radius: 10px;
        margin-bottom: 0;
        box-shadow: none;
    }

    .logo-mark.logo-lg svg { width: 32px; height: 32px; }

    .brand-title {
        font-size: 17px;
        font-weight: 700;
        color: var(--text);
        letter-spacing: 0.02em;
        line-height: 1.2;
        white-space: nowrap;
    }

    .brand-name {
        color: var(--text-tertiary);
        font-size: 12px;
        font-weight: 500;
        letter-spacing: 0.01em;
        margin-top: 0;
        line-height: 1.3;
        white-space: normal;
    }

    .brand-sub {
        color: var(--text-tertiary);
        font-size: .74rem;
        margin-top: 3px;
        letter-spacing: 0.03em;
    }

    .section-kicker {
        font-size: .68rem;
        font-weight: 700;
        letter-spacing: .1em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        margin: 0 0 8px 2px;
    }

    .edu-banner {
        margin: -8px 0 18px;
        padding: 10px 14px;
        border-radius: var(--radius-md);
        border: 1px solid var(--border);
        background: var(--bg-elevated);
        color: var(--text-secondary);
        font-size: .78rem;
        line-height: 1.45;
        text-align: left;
    }

    .edu-banner b { color: var(--orange); font-weight: 650; }

    .try-section {
        margin: 14px 0 6px;
        padding: 12px 0 4px;
        border: none;
        border-radius: 0;
        background: transparent;
        box-shadow: none;
    }

    .try-label {
        font-size: .7rem;
        font-weight: 700;
        letter-spacing: .08em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        margin-bottom: 4px;
    }

    .try-hint {
        color: var(--text-secondary);
        font-size: .86rem;
        margin-bottom: 0;
    }

    [data-testid="stMain"] [data-testid="stHorizontalBlock"] .stButton button[kind="secondary"] {
        background: var(--bg-elevated) !important;
        border: 1px solid var(--border) !important;
        color: var(--text) !important;
        border-radius: 4px !important;
        font-family: var(--display) !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
        box-shadow: none !important;
        min-height: 40px !important;
        height: auto !important;
        max-height: none !important;
        padding: 8px 12px !important;
        justify-content: flex-start !important;
        text-align: left !important;
        overflow: visible !important;
        white-space: normal !important;
        line-height: 1.3 !important;
    }

    [data-testid="stMain"] [data-testid="stHorizontalBlock"] .stButton button[kind="secondary"]:hover {
        background: var(--card-hover) !important;
        border-color: var(--border-strong) !important;
        color: #fff !important;
    }

    .header-actions {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 10px;
        flex-wrap: wrap;
        flex-shrink: 1;
        min-width: 0;
    }

    .topbar-note {
        color: var(--text-tertiary);
        font-size: .78rem;
        text-align: right;
        max-width: 280px;
        line-height: 1.5;
    }

    .live-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 8px;
        border-radius: 2px;
        background: transparent;
        border: 1px solid var(--border);
        color: var(--text-tertiary);
        font-size: 11px;
        font-weight: 400;
        letter-spacing: 0;
    }

    .live-pill.badge-muted {
        background: transparent;
        border-color: var(--border);
        color: var(--text-tertiary);
        text-transform: none;
        letter-spacing: 0;
        max-width: 420px;
        line-height: 1.3;
        text-align: right;
    }

    .live-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--green);
        box-shadow: none;
        animation: none;
    }

    .card, .hero-card, .panel, .metric-card, .learn-card, .risk-item, .info-strip, .empty-state, .score-panel {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        box-shadow: var(--shadow-soft);
    }

    .hero-card, .panel, .score-panel, .empty-state, .learn-card {
        background: var(--card);
        box-shadow: none;
        backdrop-filter: none;
        border: 1px solid var(--border);
    }

    .empty-state {
        padding: 18px 16px;
        border-color: var(--border);
        position: relative;
        overflow: hidden;
        text-align: left;
        border-radius: 4px;
    }

    .empty-state .hero-copy {
        max-width: 42rem;
    }

    .method-steps {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        margin-top: 16px;
    }

    .method-step {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 14px 14px 12px;
    }

    .method-step .n {
        color: var(--text-tertiary);
        font-size: 11px;
        font-weight: 600;
        margin-bottom: 6px;
    }

    .method-step p {
        color: var(--text-secondary);
        font-size: 13px;
        line-height: 1.45;
        margin: 0;
    }

    .source-line {
        margin-top: 8px;
        color: var(--text-tertiary);
        font-size: 12px;
        line-height: 1.5;
    }

    .home {
        min-height: calc(100vh - 176px);
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        padding: 56px 4px 12px;
    }

    .home-lead {
        max-width: 38rem;
    }

    .home-lead .hero-title {
        font-size: 42px;
        max-width: 16ch;
        margin: 12px 0 16px;
        letter-spacing: -0.04em;
    }

    .home-lead .hero-copy {
        max-width: 34rem;
        font-size: 1.05rem;
        line-height: 1.55;
    }

    .home-steps {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 40px;
        border-top: 1px solid var(--border);
        padding-top: 28px;
        margin-top: 48px;
    }

    .home-steps .n {
        color: var(--text-tertiary);
        font-size: 12px;
        font-weight: 600;
        margin-bottom: 8px;
    }

    .home-steps p {
        color: var(--text-secondary);
        font-size: 14px;
        line-height: 1.5;
        margin: 0;
    }

    .empty-state.error-state {
        border-color: rgba(239, 83, 80, 0.45);
        box-shadow: none;
    }

    .empty-state.error-state .eyebrow { color: var(--red); }

    .empty-state.error-state::before,
    .empty-state::before {
        display: none;
    }

    .hero-card, .panel, .score-panel { padding: 26px 28px; }

    .info-strip {
        padding: 12px 18px;
        margin-bottom: 16px;
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 14px;
        flex-wrap: wrap;
        text-align: left;
        box-shadow: none;
        background: var(--bg-elevated);
        border-color: var(--border);
        border-radius: var(--radius-lg);
        font-family: var(--display);
        font-size: .74rem;
    }

    .rate-chip {
        display: inline-flex;
        gap: 6px;
        align-items: center;
        padding: 2px 8px;
        border-radius: 2px;
        background: var(--fill);
        border: 1px solid var(--border);
        color: var(--text-secondary);
        font-family: var(--display);
        font-size: .68rem;
    }

    .eyebrow {
        color: var(--cyan);
        font-size: .68rem;
        font-weight: 700;
        letter-spacing: .1em;
        text-transform: uppercase;
    }

    .hero-title {
        font-size: 26px;
        line-height: 1.2;
        font-weight: 600;
        letter-spacing: -0.03em;
        margin: 6px 0 8px;
        color: var(--text);
    }

    .hero-copy, .panel p, .learn-card p, .risk-item p {
        color: var(--text-secondary);
        line-height: 1.58;
        font-size: .95rem;
        margin: 0;
    }

    .hero-card .hero-copy {
        margin-top: 12px;
        max-width: 38rem;
    }

    .results-grid, .two-col, .learn-grid, .feature-grid, .metric-grid {
        display: grid;
        gap: 14px;
    }

    .results-grid { grid-template-columns: 1.2fr .8fr; margin-bottom: 18px; gap: 16px; }
    .two-col { grid-template-columns: 1fr 1fr; margin-bottom: 18px; gap: 16px; }
    .learn-grid, .feature-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .metric-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 18px; gap: 14px; }

    .hero-card {
        border-left: 2px solid var(--blue);
    }

    .score-panel {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
        min-height: 300px;
        border: 1px solid var(--border);
        position: relative;
        overflow: hidden;
        border-radius: var(--radius-xl);
    }

    .score-panel::before { display: none; }

    .score-panel.good { border-color: rgba(38, 166, 154, 0.45); box-shadow: none; }
    .score-panel.mid { border-color: rgba(255, 152, 0, 0.45); box-shadow: none; }
    .score-panel.low { border-color: rgba(239, 83, 80, 0.45); box-shadow: none; }

    .score-ring-wrap {
        position: relative;
        width: 148px;
        height: 148px;
        margin: 6px auto 10px;
        z-index: 1;
    }

    .score-ring { width: 100%; height: 100%; filter: none; }
    .score-panel.good .score-ring,
    .score-panel.mid .score-ring,
    .score-panel.low .score-ring { filter: none; }

    .score-ring-inner {
        position: absolute;
        inset: 0;
        display: grid;
        place-items: center;
    }

    .score-ring-inner .score-big {
        font-size: 2.55rem;
        margin: 0;
    }

    .score-kicker {
        font-size: .68rem;
        font-weight: 700;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: .08em;
        position: relative;
    }

    .score-big {
        font-family: var(--mono);
        font-size: clamp(3.25rem, 7vw, 4.5rem);
        line-height: 1;
        font-weight: 700;
        letter-spacing: -0.04em;
        margin: 12px 0 6px;
        color: var(--text);
        position: relative;
    }

    .score-panel.good .score-big { color: var(--green); }
    .score-panel.mid .score-big { color: var(--orange); }
    .score-panel.low .score-big { color: var(--red); }

    .score-status {
        font-size: 1rem;
        font-weight: 650;
        color: var(--text);
        margin-bottom: 8px;
        position: relative;
    }

    .score-caption { color: var(--text-secondary); line-height: 1.5; font-size: .88rem; position: relative; }

    .badge-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 14px; }

    .badge {
        display: inline-flex;
        padding: 5px 11px;
        border-radius: var(--radius-sm);
        background: var(--fill);
        border: 1px solid var(--border);
        color: var(--text-secondary);
        font-size: .74rem;
        font-weight: 600;
        letter-spacing: .02em;
    }

    .badge-ticker {
        background: var(--blue-soft);
        border-color: rgba(41, 98, 255, 0.35);
        color: #7eb0ff;
        font-family: var(--mono);
        font-weight: 700;
    }

    .badge-muted { color: var(--text-tertiary); }

    .metric-card {
        padding: 14px 16px 12px;
        position: relative;
        overflow: hidden;
        transition: none;
        animation: none;
        border-radius: var(--radius-lg);
        background: var(--card);
    }

    .metric-card::before {
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: var(--blue);
        opacity: 1;
    }

    .metric-card:hover {
        transform: none;
        border-color: var(--border-strong);
        box-shadow: none;
    }

    .metric-value {
        font-family: var(--mono);
        color: var(--text);
        font-size: 1.42rem;
        font-weight: 700;
        letter-spacing: -0.03em;
        margin-top: 12px;
        font-variant-numeric: tabular-nums;
    }

    .metric-card.accent-purple::before { background: var(--purple); }
    .metric-card.accent-green::before { background: var(--green); }
    .metric-card.accent-cyan::before { background: var(--cyan); }

    .metric-card:nth-child(1),
    .metric-card:nth-child(2),
    .metric-card:nth-child(3),
    .metric-card:nth-child(4) { animation: none; }

    .metric-label {
        color: var(--text-tertiary);
        font-size: .66rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .07em;
    }

    .info-strip span { color: var(--text-secondary); }
    .info-strip b { color: var(--text); font-weight: 600; }
    .panel-pricing { border-top: 3px solid rgba(79, 124, 255, 0.45); }
    .panel-reality { border-top: 3px solid rgba(45, 212, 191, 0.45); }

    .panel h3::before, .learn-card h4::before {
        content: "";
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--blue);
        margin-right: 10px;
        box-shadow: none;
        vertical-align: middle;
        transform: translateY(-1px);
    }

    .panel-reality h3::before { background: var(--green); box-shadow: none; }
    .panel-pricing h3::before { background: var(--blue-bright); }

    .metric-meta {
        color: var(--text-tertiary);
        font-size: .72rem;
        margin-top: 6px;
        letter-spacing: .01em;
    }

    .panel h3, .learn-card h4 {
        margin: 0 0 14px;
        font-size: .95rem;
        font-weight: 650;
        color: var(--text);
        letter-spacing: -0.01em;
        padding-bottom: 10px;
        border-bottom: 1px solid var(--border);
    }

    .row {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        padding: 10px 0;
        border-top: 1px solid var(--grid);
        color: var(--text-secondary);
        font-size: .88rem;
    }

    .row:first-of-type { border-top: none; padding-top: 0; }
    .row b {
        font-family: var(--mono);
        color: var(--text);
        font-weight: 600;
        text-align: right;
        white-space: nowrap;
        font-variant-numeric: tabular-nums;
    }

    .score-row { margin-top: 16px; }
    .score-row:first-child { margin-top: 0; }

    .score-row-head {
        display: flex;
        justify-content: space-between;
        color: var(--text-secondary);
        font-size: .84rem;
        margin-bottom: 8px;
    }

    .score-row-head strong {
        font-family: var(--mono);
        color: var(--text);
        font-weight: 700;
        font-variant-numeric: tabular-nums;
    }

    .score-track {
        height: 5px;
        background: rgba(255, 255, 255, 0.06);
        border-radius: 2px;
        overflow: hidden;
    }

    .score-fill {
        height: 100%;
        border-radius: 2px;
        background: var(--blue);
        box-shadow: none;
    }

    .score-fill.good { background: var(--green); box-shadow: none; }
    .score-fill.mid { background: var(--orange); box-shadow: none; }
    .score-fill.low { background: var(--red); box-shadow: none; }

    .learn-card, .risk-item { padding: 20px 22px; }
    .risk-list { display: grid; gap: 12px; }
    .risk-item b { color: var(--text); }

    .section-heading {
        font-size: 1rem;
        font-weight: 650;
        color: var(--text);
        margin: 8px 0 12px;
        letter-spacing: -0.01em;
    }

    [data-testid="stForm"] {
        background: var(--bg-elevated);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 8px !important;
        margin-bottom: 12px;
        box-shadow: none;
        overflow: visible;
    }

    [data-testid="stForm"] [data-testid="stWidgetLabel"] {
        display: none !important;
    }

    .stTextInput input, .stSelectbox > div > div {
        background: var(--bg) !important;
        border: 1px solid var(--border) !important;
        border-radius: 4px !important;
        color: var(--text) !important;
        min-height: 40px !important;
        height: 40px !important;
        padding: 0 12px !important;
        font-family: var(--display) !important;
        font-size: 14px !important;
        box-shadow: none !important;
    }

    .stTextInput input::placeholder { color: var(--text-tertiary) !important; }

    .stTextInput input:focus {
        border-color: transparent !important;
        box-shadow: none !important;
        outline: none !important;
    }

    .stSelectbox label, .stTextInput label {
        display: none !important;
    }

    .stSelectbox svg { fill: var(--text-secondary) !important; }

    .stButton button {
        background: var(--bg-elevated) !important;
        color: var(--text) !important;
        border: 1px solid var(--border) !important;
        border-radius: 4px !important;
        min-height: 40px !important;
        height: auto !important;
        max-height: none !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
        box-shadow: none !important;
        transform: none !important;
        justify-content: flex-start !important;
        text-align: left !important;
        white-space: normal !important;
        overflow: visible !important;
        padding: 8px 12px !important;
        line-height: 1.35 !important;
    }

    [data-testid="stForm"] .stButton button {
        background: var(--blue) !important;
        color: #111 !important;
        border: 1px solid var(--blue) !important;
        justify-content: center !important;
        text-align: center !important;
        white-space: nowrap !important;
        height: 40px !important;
    }

    .stButton button:hover {
        background: var(--card-hover) !important;
        box-shadow: none !important;
        transform: none !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        border-bottom: 1px solid var(--border);
        background: transparent;
        padding: 0;
        border-radius: 0;
        border-left: none;
        border-right: none;
        border-top: none;
        margin-bottom: 12px;
        flex-wrap: wrap;
    }

    .stTabs [data-baseweb="tab"] {
        background: transparent;
        border: none;
        border-radius: 0 !important;
        color: var(--text-tertiary);
        padding: 8px 14px;
        font-weight: 500;
        font-size: 13px;
        border-bottom: 2px solid transparent !important;
        margin: 0;
    }

    .stTabs [data-baseweb="tab"]:hover {
        color: var(--text);
        background: transparent;
    }

    .stTabs [aria-selected="true"] {
        color: var(--text) !important;
        background: transparent !important;
        border: none !important;
        border-bottom: 2px solid var(--blue) !important;
        box-shadow: none;
    }

    [data-testid="stSegmentedControl"] {
        background: transparent !important;
        border-bottom: 1px solid var(--border);
        padding-bottom: 0;
        margin: 18px 0 14px;
    }

    [data-testid="stSegmentedControl"] div[role="group"],
    [data-testid="stSegmentedControl"] [data-baseweb="button-group"] {
        background: transparent !important;
        border: none !important;
        gap: 0 !important;
        box-shadow: none !important;
    }

    [data-testid="stSegmentedControl"] button {
        background: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        color: var(--text-tertiary) !important;
        border-bottom: 2px solid transparent !important;
        padding: 8px 16px !important;
        font-weight: 500 !important;
    }

    [data-testid="stSegmentedControl"] button[aria-checked="true"],
    [data-testid="stSegmentedControl"] button[aria-pressed="true"] {
        color: var(--text) !important;
        background: transparent !important;
        border-bottom: 2px solid var(--blue) !important;
    }

    [data-testid="stMain"] .st-key-nav_chart button,
    [data-testid="stMain"] .st-key-nav_whatif button,
    [data-testid="stMain"] .st-key-nav_compare button,
    [data-testid="stMain"] .st-key-nav_data button {
        background: transparent !important;
        border: none !important;
        border-bottom: 2px solid transparent !important;
        border-radius: 0 !important;
        color: var(--text-tertiary) !important;
        justify-content: center !important;
        text-align: center !important;
        height: 40px !important;
        min-height: 40px !important;
        box-shadow: none !important;
        font-weight: 500 !important;
        padding: 8px 12px !important;
    }

    [data-testid="stMain"] .st-key-nav_chart button:hover,
    [data-testid="stMain"] .st-key-nav_whatif button:hover,
    [data-testid="stMain"] .st-key-nav_compare button:hover,
    [data-testid="stMain"] .st-key-nav_data button:hover {
        color: var(--text) !important;
        background: transparent !important;
    }

    [data-testid="stMain"] .st-key-nav_chart button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_whatif button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_compare button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_data button[kind="primary"] {
        color: var(--text) !important;
        background: transparent !important;
        border-bottom: 2px solid var(--blue) !important;
    }

    [data-testid="stRadio"] [data-baseweb="radio"] > div:first-child {
        display: none !important;
    }

    [data-testid="stRadio"] label {
        background: transparent !important;
        border: none !important;
        padding-left: 0 !important;
        margin-right: 18px !important;
    }

    .result-head {
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        gap: 24px;
        margin: 4px 0 20px;
        padding-bottom: 18px;
        border-bottom: 1px solid var(--border);
    }

    .result-head .hero-title { margin: 0 0 6px; font-size: 32px; }

    .result-meta {
        color: var(--text-tertiary);
        font-size: 13px;
    }

    .result-score {
        text-align: right;
        flex-shrink: 0;
    }

    .result-score em {
        display: block;
        font-style: normal;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: .08em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        margin-bottom: 4px;
    }

    .result-score b {
        font-family: var(--mono);
        font-size: 40px;
        font-weight: 700;
        line-height: 1;
        color: var(--text);
    }

    .result-score.good b { color: var(--green); }
    .result-score.mid b { color: var(--orange); }
    .result-score.low b { color: var(--red); }

    .growth-plain { margin: 6px 0 20px; }

    .metric-card::before { display: none; }

    div[data-testid="stDataFrame"] {
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        overflow: hidden;
        background: var(--surface);
    }

    [data-testid="stLineChart"] {
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        overflow: hidden;
        background: var(--bg-elevated);
        padding: 8px;
    }

    div[data-testid="stAlert"] {
        border-radius: var(--radius-md);
        border: 1px solid var(--border);
        background: var(--surface) !important;
    }

    .stSuccess, .stInfo {
        background: var(--surface) !important;
        color: var(--text-secondary) !important;
    }

    .stCaption, [data-testid="stCaptionContainer"] {
        color: var(--text-tertiary) !important;
        text-align: left;
    }

    h3, h4 { color: var(--text) !important; font-weight: 650 !important; }

    [data-testid="stSpinner"] { color: var(--blue) !important; }

    .chart-wrap {
        border: 1px solid var(--border-strong);
        border-radius: var(--radius-lg);
        background: var(--bg-elevated);
        padding: 12px 8px 4px;
        margin-bottom: 8px;
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
    }

    .app-footer {
        margin-top: 32px;
        padding: 16px 20px;
        border-radius: var(--radius-lg);
        border: 1px solid var(--border);
        background: var(--bg-elevated);
        color: var(--text-tertiary);
        font-size: .72rem;
        text-align: left;
        letter-spacing: 0;
        line-height: 1.55;
    }

    .app-footer strong { color: var(--cyan); font-weight: 650; }

    div[data-baseweb="popover"], div[data-baseweb="menu"] {
        background: var(--surface-2) !important;
        border: 1px solid var(--border-strong) !important;
    }

    div[data-baseweb="popover"] li, div[data-baseweb="menu"] li {
        color: var(--text) !important;
        background: transparent !important;
    }

    div[data-baseweb="popover"] li:hover, div[data-baseweb="menu"] li:hover {
        background: var(--fill) !important;
    }

    section[data-testid="stSidebar"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="collapsedControl"] {
        display: none !important;
        width: 0 !important;
        min-width: 0 !important;
        visibility: hidden !important;
    }

    .watch-heading {
        display: flex;
        align-items: center;
        justify-content: flex-start;
        gap: 8px;
        font-size: 12px;
        font-weight: 600;
        color: var(--text-tertiary);
        text-transform: none;
        letter-spacing: 0;
        margin: 8px 0 6px;
    }

    .watch-heading::before,
    .watch-heading::after {
        display: none;
    }

    .search-hit {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        width: 100%;
        padding: 8px 10px;
        border-bottom: 1px solid var(--border);
        color: var(--text);
        font-size: 13px;
    }
    .search-hit b { font-family: var(--display); color: #fff; min-width: 72px; }
    .search-hit span { color: var(--text-secondary); }
    .search-hit em { color: var(--text-tertiary); font-style: normal; font-size: 12px; }

    .search-table-head {
        display: grid;
        grid-template-columns: minmax(0, 2.2fr) 92px 1.1fr 1.1fr;
        gap: 8px;
        padding: 6px 10px;
        color: var(--text-tertiary);
        font-size: 11px;
        border-bottom: 1px solid var(--border);
        background: var(--bg-elevated);
    }

    .search-meta {
        color: var(--text-secondary);
        font-size: 12px;
        line-height: var(--control-h);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        height: var(--control-h);
    }

    .watch-quote {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        justify-content: center;
        min-height: 38px;
        font-family: var(--mono);
        font-variant-numeric: tabular-nums;
        line-height: 1.25;
    }

    .watch-price { color: var(--text); font-size: .8rem; font-weight: 700; }
    .watch-chg { font-size: .7rem; font-weight: 700; }
    .watch-chg.up { color: var(--green); }
    .watch-chg.down { color: var(--red); }
    .watch-chg.flat { color: var(--text-tertiary); }

    .stSegmentedControl [role="radiogroup"], div[data-testid="stSegmentedControl"] {
        background: transparent;
    }

    div[data-testid="stSegmentedControl"] button {
        background: rgba(255, 255, 255, 0.03) !important;
        border-color: var(--border) !important;
        color: var(--text-secondary) !important;
        font-size: .78rem !important;
        font-weight: 600 !important;
    }

    div[data-testid="stSegmentedControl"] button[aria-checked="true"],
    div[data-testid="stSegmentedControl"] button[data-selected="true"] {
        background: var(--blue-soft) !important;
        border-color: rgba(41, 98, 255, 0.45) !important;
        color: var(--text) !important;
    }

    .watch-score {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 38px;
        border-radius: var(--radius-sm);
        font-family: var(--mono);
        font-size: .74rem;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        border: 1px solid var(--border);
        color: var(--text-tertiary);
        background: var(--fill);
    }

    .watch-score.good { color: var(--green); background: var(--green-soft); border-color: rgba(8, 153, 129, 0.35); }
    .watch-score.mid { color: var(--orange); background: var(--orange-soft); border-color: rgba(247, 147, 26, 0.35); }
    .watch-score.low { color: var(--red); background: var(--red-soft); border-color: rgba(242, 54, 69, 0.35); }

    .flag-strip {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: center;
        gap: 8px;
        margin: 0 0 18px;
        padding: 12px 14px;
        border-radius: var(--radius-lg);
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
    }

    .gbar-verdict {
        margin-top: 18px;
        padding: 14px 16px;
        border-radius: var(--radius-md);
        border: 1px solid var(--border);
        background: var(--bg-elevated);
        color: var(--text-secondary);
        font-size: .86rem;
        line-height: 1.55;
    }

    .gbar-verdict b { color: var(--text); }

    .learn-card {
        transition: none;
    }

    .learn-card:hover {
        border-color: var(--border-strong);
        transform: none;
    }

    .flag-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 2px;
        font-size: .7rem;
        font-weight: 600;
        letter-spacing: .02em;
        border: 1px solid var(--border);
        background: var(--fill);
        color: var(--text-tertiary);
    }

    .flag-chip.warn { color: var(--orange); background: var(--orange-soft); border-color: rgba(247, 147, 26, 0.3); }
    .flag-chip.bad { color: var(--red); background: var(--red-soft); border-color: rgba(242, 54, 69, 0.3); }

    .conf-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 11px;
        border-radius: 2px;
        font-size: .7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .05em;
        border: 1px solid var(--border-strong);
        background: var(--surface);
        color: var(--text-secondary);
    }

    .conf-chip.High { color: var(--green); border-color: rgba(8, 153, 129, 0.4); }
    .conf-chip.Medium { color: var(--orange); border-color: rgba(247, 147, 26, 0.4); }
    .conf-chip.Low { color: var(--red); border-color: rgba(242, 54, 69, 0.4); }

    .implied-line {
        background: var(--bg-elevated);
        border: 1px solid var(--border);
        border-left: 3px solid var(--blue);
        padding: 14px 16px;
        margin-bottom: 14px;
        border-radius: 4px;
    }
    .implied-main { font-size: 18px; color: var(--text); line-height: 1.35; }
    .implied-main b { font-size: 22px; font-weight: 700; }
    .implied-sub { color: var(--text-secondary); font-size: 13px; margin-top: 6px; }

    .gbar-row { margin-top: 14px; }
    .gbar-row:first-of-type { margin-top: 0; }

    .gbar-head {
        display: flex;
        justify-content: space-between;
        color: var(--text-secondary);
        font-size: .84rem;
        margin-bottom: 7px;
    }

    .gbar-head strong {
        font-family: var(--mono);
        color: var(--text);
        font-weight: 700;
        font-variant-numeric: tabular-nums;
    }

    .gbar-track {
        height: 8px;
        background: rgba(255, 255, 255, 0.05);
        border-radius: 2px;
        overflow: hidden;
    }

    .gbar-fill { height: 100%; border-radius: 2px; }
    .gbar-fill.req { background: var(--blue); box-shadow: none; }
    .gbar-fill.con { background: var(--purple); box-shadow: none; }
    .gbar-fill.his { background: var(--green); box-shadow: none; }
    .gbar-fill.neg { background: var(--red); box-shadow: none; }

    .cmp-table { width: 100%; border-collapse: collapse; }

    .cmp-table th {
        text-align: right;
        padding: 10px 12px;
        color: var(--text);
        font-size: .82rem;
        font-weight: 700;
        border-bottom: 1px solid var(--border-strong);
        border-top: 3px solid transparent;
        font-family: inherit;
        vertical-align: bottom;
    }

    .cmp-table th:first-child { text-align: left; color: var(--text-tertiary); font-weight: 600; }

    .cmp-table .cmp-name {
        display: block;
        color: var(--text);
        font-weight: 650;
        font-size: .84rem;
        line-height: 1.25;
    }

    .cmp-table .cmp-swatch {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 2px;
        margin-right: 6px;
        vertical-align: middle;
    }

    .cmp-table .cmp-ticker {
        display: block;
        color: var(--text-tertiary);
        font-family: var(--mono);
        font-size: .72rem;
        font-weight: 600;
        letter-spacing: .04em;
        margin-top: 3px;
    }

    .cmp-table td {
        text-align: right;
        padding: 9px 12px;
        color: var(--text);
        font-size: .84rem;
        font-family: var(--mono);
        font-variant-numeric: tabular-nums;
        border-bottom: 1px solid var(--grid);
    }

    .cmp-table td:first-child {
        text-align: left;
        color: var(--text-secondary);
        font-family: inherit;
    }

    .cmp-table tr:hover td { background: var(--fill); }
    .cmp-table .cell-good { color: var(--green); font-weight: 700; }
    .cmp-table .cell-mid { color: var(--orange); font-weight: 700; }
    .cmp-table .cell-low { color: var(--red); font-weight: 700; }

    .whatif-note {
        padding: 12px 14px;
        border-radius: var(--radius-md);
        border: 1px solid var(--border);
        background: var(--fill);
        color: var(--text-secondary);
        font-size: .85rem;
        line-height: 1.55;
        margin-bottom: 14px;
    }

    .whatif-note b { color: var(--text); }

    .delta-chip {
        display: inline-flex;
        padding: 3px 10px;
        border-radius: 2px;
        font-family: var(--mono);
        font-size: .8rem;
        font-weight: 700;
    }

    .delta-chip.up { color: var(--green); background: var(--green-soft); }
    .delta-chip.down { color: var(--red); background: var(--red-soft); }

    .stSlider [data-baseweb="slider"] [role="slider"] {
        background: var(--blue) !important;
        border-color: var(--blue) !important;
        box-shadow: none !important;
    }

    .stSlider label {
        color: var(--text-secondary) !important;
        font-size: .74rem !important;
        font-weight: 700 !important;
        letter-spacing: .04em !important;
        text-transform: uppercase !important;
    }

    .stSlider [data-testid="stTickBarMin"], .stSlider [data-testid="stTickBarMax"],
    .stSlider [data-testid="stThumbValue"] {
        color: var(--text-tertiary) !important;
        font-family: var(--mono) !important;
    }

    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p, .stToggle label p {
        color: var(--text-secondary) !important;
        font-size: .8rem !important;
    }

    @media (max-width: 900px) {
        .block-container {
            padding-top: 0.75rem !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            padding-bottom: 2rem !important;
            max-width: 100% !important;
        }

        .results-grid, .two-col, .learn-grid, .feature-grid, .method-steps,
        .home-steps { grid-template-columns: 1fr; }
        .home-lead .hero-title { font-size: 30px; max-width: none; }
        .home { padding-top: 28px; min-height: 0; }
        .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
        .topbar, .terminal-header { flex-direction: column; align-items: flex-start; text-align: left; }
        .brand-lockup, .brand-text { min-width: 0; max-width: 100%; }
        .brand-name { white-space: normal; }
        .topbar-note { text-align: center; max-width: none; }
        .header-actions { width: 100%; justify-content: center; }

        .hero-card, .panel, .score-panel, .empty-state { padding: 18px 16px; }
        .score-panel { min-height: 220px; }
        .hero-title { font-size: 1.55rem; }
        .cmp-table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }

        [data-testid="stSidebar"] { min-width: 0 !important; }

        .stTabs [data-baseweb="tab-list"] {
            overflow-x: auto;
            flex-wrap: nowrap !important;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }

        .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }

        .stTabs [data-baseweb="tab"] {
            white-space: nowrap;
            padding: 10px 12px;
            font-size: .78rem;
        }
    }

    @media (max-width: 560px) {
        .block-container {
            padding-left: 0.7rem !important;
            padding-right: 0.7rem !important;
        }

        .metric-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
        .metric-card { padding: 12px 12px 10px; }
        .metric-value { font-size: 1.05rem; margin-top: 6px; }
        .metric-label { font-size: .62rem; }
        .metric-meta { font-size: .65rem; line-height: 1.3; }

        .terminal-header {
            padding: 10px 12px;
            border-radius: 4px;
            margin-bottom: 12px;
        }

        .brand-mark, .logo-mark { width: 48px; height: 48px; border-radius: 10px; font-size: .8rem; overflow: visible; }
        .brand-mark svg, .logo-mark svg,
        .brand-mark img, .logo-mark img { width: 48px; height: 48px; object-fit: contain; }
        .logo-mark.logo-lg { width: 48px; height: 48px; border-radius: 10px; margin-bottom: 0; }
        .logo-mark.logo-lg svg { width: 28px; height: 28px; }
        div[data-testid="stImage"] img {
            width: 48px !important;
            height: 48px !important;
            max-width: 48px !important;
        }
        .brand-title { font-size: 1.05rem; }
        .brand-sub { font-size: .72rem; }
        .live-pill { font-size: .64rem; padding: 5px 9px; }

        .hero-title { font-size: 1.35rem; letter-spacing: -0.02em; }
        .hero-copy, .panel p, .learn-card p, .risk-item p { font-size: .88rem; }

        .score-ring-wrap { width: 120px; height: 120px; }
        .score-ring-inner .score-big { font-size: 2.1rem; }
        .score-panel { min-height: 200px; padding: 16px; }
        .score-caption { font-size: .8rem; }

        .info-strip {
            flex-direction: column;
            align-items: flex-start;
            gap: 6px;
            font-size: .68rem;
            padding: 10px 12px;
        }

        .flag-strip { gap: 6px; margin-bottom: 12px; }
        .flag-chip, .conf-chip { font-size: .64rem; padding: 4px 8px; }

        .row {
            flex-direction: column;
            align-items: flex-start;
            gap: 4px;
            padding: 10px 0;
            font-size: .84rem;
        }

        .row b { text-align: left; white-space: normal; font-size: .9rem; }

        .panel h3, .learn-card h4 { font-size: .9rem; margin-bottom: 10px; padding-bottom: 8px; }

        [data-testid="stForm"] {
            padding: 0 !important;
            border-radius: 4px;
            margin-bottom: 12px;
        }

        .stTextInput input, .stSelectbox > div > div {
            min-height: var(--control-h) !important;
            height: var(--control-h) !important;
            font-size: .86rem !important;
        }

        .stButton button {
            min-height: 40px !important;
            height: auto !important;
            max-height: none !important;
            border-radius: 4px !important;
        }

        .chart-wrap { padding: 8px 4px 2px; border-radius: 4px; }
        .app-footer { font-size: .68rem; padding: 12px; line-height: 1.45; }

        .gbar-head { font-size: .78rem; }
        .whatif-note { font-size: .8rem; padding: 10px 12px; }

        div[data-testid="stHorizontalBlock"] {
            gap: 0.4rem !important;
        }
    }

    @media (max-width: 400px) {
        .metric-grid { grid-template-columns: 1fr; }
        .badge { font-size: .68rem; padding: 4px 8px; }
        .hero-title { font-size: 1.22rem; }
    }

    /* Prefer phone portrait: touch-friendly tabs and no hover-dependent UI */
    @media (hover: none) and (pointer: coarse) {
        .metric-card:hover { transform: none; }
        .stTabs [data-baseweb="tab"] { min-height: 40px; }
        .stButton button { min-height: 44px !important; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


SECTOR_MODELS = {
    "Technology": {
        "discount_rate": 0.10,
        "business_weights": {"growth": 0.25, "gross": 0.20, "operating": 0.15, "fcf": 0.20, "roe": 0.10, "balance": 0.10},
        "business_benchmarks": {"gross": 0.70, "operating": 0.30, "fcf": 0.25, "roe": 0.25},
        "expectation_weights": {"required_growth": 0.50, "ev_sales": 0.25, "pe": 0.10, "ev_ebitda": 0.15},
        "expectation_benchmarks": {"ev_sales": 16, "pe": 70, "ev_ebitda": 40},
        "financial_weights": {"cash_debt": 0.30, "operating": 0.20, "fcf": 0.30, "leverage": 0.20},
        "financial_benchmarks": {"operating": 0.28, "fcf": 0.22},
    },
    "Communication Services": {
        "discount_rate": 0.10,
        "business_weights": {"growth": 0.20, "gross": 0.15, "operating": 0.20, "fcf": 0.20, "roe": 0.10, "balance": 0.15},
        "business_benchmarks": {"gross": 0.60, "operating": 0.25, "fcf": 0.20, "roe": 0.22},
        "expectation_weights": {"required_growth": 0.45, "ev_sales": 0.25, "pe": 0.15, "ev_ebitda": 0.15},
        "expectation_benchmarks": {"ev_sales": 12, "pe": 55, "ev_ebitda": 30},
        "financial_weights": {"cash_debt": 0.25, "operating": 0.25, "fcf": 0.30, "leverage": 0.20},
        "financial_benchmarks": {"operating": 0.24, "fcf": 0.18},
    },
    "Consumer Cyclical": {
        "discount_rate": 0.11,
        "business_weights": {"growth": 0.20, "gross": 0.10, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.15},
        "business_benchmarks": {"gross": 0.45, "operating": 0.18, "fcf": 0.12, "roe": 0.22},
        "expectation_weights": {"required_growth": 0.40, "ev_sales": 0.20, "pe": 0.20, "ev_ebitda": 0.20},
        "expectation_benchmarks": {"ev_sales": 6, "pe": 35, "ev_ebitda": 20},
        "financial_weights": {"cash_debt": 0.25, "operating": 0.25, "fcf": 0.25, "leverage": 0.25},
        "financial_benchmarks": {"operating": 0.16, "fcf": 0.10},
    },
    "Consumer Defensive": {
        "discount_rate": 0.09,
        "business_weights": {"growth": 0.10, "gross": 0.10, "operating": 0.20, "fcf": 0.25, "roe": 0.15, "balance": 0.20},
        "business_benchmarks": {"gross": 0.40, "operating": 0.16, "fcf": 0.12, "roe": 0.22},
        "expectation_weights": {"required_growth": 0.30, "ev_sales": 0.20, "pe": 0.25, "ev_ebitda": 0.25},
        "expectation_benchmarks": {"ev_sales": 5, "pe": 32, "ev_ebitda": 18},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.25, "fcf": 0.30, "leverage": 0.25},
        "financial_benchmarks": {"operating": 0.15, "fcf": 0.11},
    },
    "Industrials": {
        "discount_rate": 0.10,
        "business_weights": {"growth": 0.15, "gross": 0.10, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.20},
        "business_benchmarks": {"gross": 0.40, "operating": 0.18, "fcf": 0.12, "roe": 0.20},
        "expectation_weights": {"required_growth": 0.35, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.25},
        "expectation_benchmarks": {"ev_sales": 5, "pe": 35, "ev_ebitda": 20},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.25, "fcf": 0.25, "leverage": 0.30},
        "financial_benchmarks": {"operating": 0.16, "fcf": 0.10},
    },
    "Healthcare": {
        "discount_rate": 0.10,
        "business_weights": {"growth": 0.20, "gross": 0.15, "operating": 0.15, "fcf": 0.15, "roe": 0.10, "balance": 0.25},
        "business_benchmarks": {"gross": 0.65, "operating": 0.22, "fcf": 0.16, "roe": 0.20},
        "expectation_weights": {"required_growth": 0.45, "ev_sales": 0.25, "pe": 0.15, "ev_ebitda": 0.15},
        "expectation_benchmarks": {"ev_sales": 10, "pe": 50, "ev_ebitda": 28},
        "financial_weights": {"cash_debt": 0.35, "operating": 0.20, "fcf": 0.20, "leverage": 0.25},
        "financial_benchmarks": {"operating": 0.20, "fcf": 0.14},
    },
    "Energy": {
        "discount_rate": 0.12,
        "business_weights": {"growth": 0.10, "gross": 0.05, "operating": 0.20, "fcf": 0.30, "roe": 0.10, "balance": 0.25},
        "business_benchmarks": {"gross": 0.35, "operating": 0.20, "fcf": 0.15, "roe": 0.18},
        "expectation_weights": {"required_growth": 0.25, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.35},
        "expectation_benchmarks": {"ev_sales": 4, "pe": 25, "ev_ebitda": 12},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.20, "fcf": 0.30, "leverage": 0.30},
        "financial_benchmarks": {"operating": 0.18, "fcf": 0.13},
    },
    "Basic Materials": {
        "discount_rate": 0.11,
        "business_weights": {"growth": 0.10, "gross": 0.10, "operating": 0.20, "fcf": 0.25, "roe": 0.10, "balance": 0.25},
        "business_benchmarks": {"gross": 0.35, "operating": 0.18, "fcf": 0.12, "roe": 0.18},
        "expectation_weights": {"required_growth": 0.25, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.35},
        "expectation_benchmarks": {"ev_sales": 4, "pe": 25, "ev_ebitda": 12},
        "financial_weights": {"cash_debt": 0.20, "operating": 0.20, "fcf": 0.25, "leverage": 0.35},
        "financial_benchmarks": {"operating": 0.16, "fcf": 0.10},
    },
    "Utilities": {
        "discount_rate": 0.08,
        "business_weights": {"growth": 0.05, "gross": 0.05, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.35},
        "business_benchmarks": {"gross": 0.35, "operating": 0.22, "fcf": 0.10, "roe": 0.14},
        "expectation_weights": {"required_growth": 0.20, "ev_sales": 0.15, "pe": 0.30, "ev_ebitda": 0.35},
        "expectation_benchmarks": {"ev_sales": 5, "pe": 28, "ev_ebitda": 16},
        "financial_weights": {"cash_debt": 0.10, "operating": 0.20, "fcf": 0.20, "leverage": 0.50},
        "financial_benchmarks": {"operating": 0.20, "fcf": 0.09},
    },
    "Real Estate": {
        "discount_rate": 0.09,
        "business_weights": {"growth": 0.10, "gross": 0.05, "operating": 0.15, "fcf": 0.25, "roe": 0.10, "balance": 0.35},
        "business_benchmarks": {"gross": 0.55, "operating": 0.35, "fcf": 0.18, "roe": 0.14},
        "expectation_weights": {"required_growth": 0.20, "ev_sales": 0.15, "pe": 0.25, "ev_ebitda": 0.40},
        "expectation_benchmarks": {"ev_sales": 8, "pe": 35, "ev_ebitda": 22},
        "financial_weights": {"cash_debt": 0.10, "operating": 0.15, "fcf": 0.25, "leverage": 0.50},
        "financial_benchmarks": {"operating": 0.28, "fcf": 0.15},
    },
}

DEFAULT_SECTOR_MODEL = {
    "discount_rate": 0.10,
    "business_weights": {"growth": 0.20, "gross": 0.15, "operating": 0.20, "fcf": 0.20, "roe": 0.15, "balance": 0.10},
    "business_benchmarks": {"gross": 0.60, "operating": 0.30, "fcf": 0.20, "roe": 0.25},
    "expectation_weights": {"required_growth": 0.45, "ev_sales": 0.25, "pe": 0.15, "ev_ebitda": 0.15},
    "expectation_benchmarks": {"ev_sales": 14, "pe": 60, "ev_ebitda": 35},
    "financial_weights": {"cash_debt": 0.30, "operating": 0.25, "fcf": 0.25, "leverage": 0.20},
    "financial_benchmarks": {"operating": 0.25, "fcf": 0.18},
}

SEC_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt": ["DebtCurrent", "LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "CashProvidedByUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
        "PurchaseOfPropertyPlantAndEquipment",
    ],
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


def money(value, currency, display_currency=None, display_fx=1.0):
    value = safe_float(value)
    if value is None:
        return "N/A"

    if display_currency and display_fx not in (None, 1.0):
        value = value * display_fx
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


def required_growth_label(analysis):
    if analysis.get("model_fcf_refused") or analysis.get("required_growth") is None:
        return "N/A"
    if analysis.get("growth_clamped"):
        growth = analysis["required_growth"]
        if growth is not None and growth >= 1.0:
            return ">120%/yr"
        return "<-40%/yr"
    return f"{percent(analysis['required_growth'])} /yr"


def implied_line_html(analysis):
    req = required_growth_label(analysis)
    years = analysis.get("historical_growth_years") or 0
    hist = percent(analysis.get("historical_growth"))
    cons = percent(analysis.get("consensus_growth"))
    hist_txt = f"History {hist}" + (f" ({years}y)" if years else "")
    if analysis.get("model_fcf_refused"):
        main = "Price implied growth is N/A — no positive free cash to reverse-solve."
    elif analysis.get("growth_clamped"):
        main = f"Price implies {esc(req)} sales growth — outside the solver’s precise range."
    else:
        main = f"Price implies <b>{esc(req)}</b> sales growth"
    return (
        f'<div class="implied-line">'
        f'<div class="implied-main">{main}</div>'
        f'<div class="implied-sub">{esc(hist_txt)} · Analysts {esc(cons)}</div>'
        f"</div>"
    )


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
    """Prefer OCF − Capex; fall back to reported FCF. Never invent a number."""
    ocf = safe_float(operating_cash_flow)
    cap = normalize_capex(capex)
    if ocf is not None and cap is not None:
        return ocf + cap
    return safe_float(reported_fcf)


def ttm_sum(df, names, periods=4):
    series = historical_series(df, names)
    if len(series) < periods:
        return None
    return float(series.iloc[:periods].sum())


YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def _yf_part(ticker, kind):
    stock = yf.Ticker(ticker)
    empty = pd.DataFrame()
    try:
        if kind == "financials":
            return stock.financials
        if kind == "balance":
            return stock.balance_sheet
        if kind == "cashflow":
            return stock.cashflow
        if kind == "estimate":
            return stock.revenue_estimate
    except Exception:
        pass
    if kind == "estimate":
        return None
    return empty


def quote_to_info(quote):
    if not quote:
        return {}
    price = first_value(quote, "regularMarketPrice", "currentPrice", "lastPrice", "last_price")
    prev = first_value(quote, "regularMarketPreviousClose", "previousClose", "previous_close")
    mcap = first_value(quote, "marketCap", "market_cap")
    currency = quote.get("currency") or quote.get("financialCurrency")
    return {
        "symbol": quote.get("symbol"),
        "shortName": quote.get("shortName") or quote.get("short_name"),
        "longName": quote.get("longName") or quote.get("displayName") or quote.get("shortName") or quote.get("long_name"),
        "currency": currency,
        "financialCurrency": quote.get("financialCurrency") or currency,
        "currentPrice": price,
        "regularMarketPrice": price,
        "previousClose": prev,
        "marketCap": mcap,
        "trailingPE": first_value(quote, "trailingPE", "trailing_pe"),
        "forwardPE": first_value(quote, "forwardPE"),
        "quoteType": quote.get("quoteType") or quote.get("quote_type"),
        "sector": quote.get("sector"),
        "industry": quote.get("industry"),
        "enterpriseValue": first_value(quote, "enterpriseValue", "enterprise_value"),
        "targetMeanPrice": first_value(quote, "targetMeanPrice", "target_mean_price"),
        "numberOfAnalystOpinions": first_value(quote, "numberOfAnalystOpinions"),
        "grossMargins": first_value(quote, "grossMargins"),
        "operatingMargins": first_value(quote, "operatingMargins"),
        "profitMargins": first_value(quote, "profitMargins"),
        "freeCashflow": first_value(quote, "freeCashflow"),
        "returnOnEquity": first_value(quote, "returnOnEquity"),
        "enterpriseToEbitda": first_value(quote, "enterpriseToEbitda"),
    }


KNOWN_NAMES = {
    "005930.KS": "Samsung Electronics",
    "0700.HK": "Tencent",
    "NESN.SW": "Nestlé",
    "ROG.SW": "Roche",
    "VOW3.DE": "Volkswagen",
    "BMW.DE": "BMW",
    "MC.PA": "LVMH",
}


def _quote_live(ticker):
    ticker = normalize_ticker(ticker)
    try:
        fast = dict(yf.Ticker(ticker).fast_info)
        price = first_value(fast, "lastPrice", "last_price", "regularMarketPrice", "currentPrice")
        prev = first_value(fast, "previousClose", "previous_close", "regularMarketPreviousClose")
        mcap = first_value(fast, "marketCap", "market_cap")
        if price is not None or mcap is not None:
            name = KNOWN_NAMES.get(ticker)
            return {
                "symbol": ticker,
                "currency": fast.get("currency"),
                "financialCurrency": fast.get("currency"),
                "regularMarketPrice": price,
                "currentPrice": price,
                "regularMarketPreviousClose": prev,
                "previousClose": prev,
                "marketCap": mcap,
                "shortName": name or ticker,
                "longName": name,
            }
    except Exception:
        pass
    try:
        history = yf.Ticker(ticker).history(period="5d", auto_adjust=True)
        if history is not None and not history.empty and "Close" in history.columns:
            close = history["Close"].dropna()
            if not close.empty:
                price = float(close.iloc[-1])
                name = KNOWN_NAMES.get(ticker) or ticker
                return {
                    "symbol": ticker,
                    "regularMarketPrice": price,
                    "currentPrice": price,
                    "shortName": name,
                    "longName": name,
                }
    except Exception:
        pass
    return {}


@st.cache_data(ttl=900, show_spinner=False)
def fetch_yahoo_data(ticker):
    ticker = normalize_ticker(ticker)
    parts = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {
            pool.submit(_quote_live, ticker): "quote",
            pool.submit(_yf_part, ticker, "financials"): "financials",
            pool.submit(_yf_part, ticker, "balance"): "balance",
            pool.submit(_yf_part, ticker, "cashflow"): "cashflow",
            pool.submit(_yf_part, ticker, "estimate"): "estimate",
        }
        for fut in as_completed(futs):
            parts[futs[fut]] = fut.result()
    quote = parts.get("quote") or {}
    info = quote_to_info(quote)
    if KNOWN_NAMES.get(ticker):
        info["longName"] = info.get("longName") or KNOWN_NAMES[ticker]
        info["shortName"] = info.get("shortName") or KNOWN_NAMES[ticker]
    return {
        "info": info,
        "fast_info": {
            "currency": quote.get("currency") or info.get("currency"),
            "lastPrice": first_value(quote, "regularMarketPrice", "lastPrice", "last_price", "currentPrice"),
            "marketCap": first_value(quote, "marketCap", "market_cap"),
            "previousClose": first_value(quote, "regularMarketPreviousClose", "previousClose", "previous_close"),
        },
        "financials": parts.get("financials") if parts.get("financials") is not None else pd.DataFrame(),
        "balance": parts.get("balance") if parts.get("balance") is not None else pd.DataFrame(),
        "cashflow": parts.get("cashflow") if parts.get("cashflow") is not None else pd.DataFrame(),
        "quarterly_cashflow": pd.DataFrame(),
        "history": pd.DataFrame(),
        "revenue_estimate": parts.get("estimate"),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_price_history(ticker, period="1y"):
    ticker = normalize_ticker(ticker)
    try:
        history = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        return history if history is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def normalize_ticker(symbol):
    return str(symbol or "").upper().strip()


def ticker_format_ok(symbol):
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9.\-=^]{0,14}", symbol or ""))


def yahoo_data_is_valid(yahoo_data):
    if not yahoo_data:
        return False
    info = yahoo_data.get("info") or {}
    fast_info = yahoo_data.get("fast_info") or {}
    if not info and not fast_info:
        return False
    if info.get("quoteType") == "NONE" or info.get("trailingPegRatio") == "None":
        # yfinance sometimes returns a stub dict for junk symbols
        if not get_quote_price(info, fast_info) and not get_market_cap(info, fast_info):
            return False
    if get_quote_price(info, fast_info) is not None:
        return True
    if get_market_cap(info, fast_info) is not None:
        return True
    financials = yahoo_data.get("financials")
    if financials is not None and not getattr(financials, "empty", True):
        return True
    if info.get("shortName") or info.get("longName") or info.get("symbol"):
        history = yahoo_data.get("history")
        if history is not None and not getattr(history, "empty", True):
            return True
    history = yahoo_data.get("history")
    return history is not None and not getattr(history, "empty", True)


def query_ticker():
    try:
        value = st.query_params.get("ticker", "")
    except Exception:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return normalize_ticker(str(value or ""))


def sync_ticker_query(symbol):
    symbol = normalize_ticker(symbol)
    try:
        current = query_ticker()
        if current == symbol:
            return
        if symbol:
            st.query_params["ticker"] = symbol
        elif "ticker" in st.query_params:
            del st.query_params["ticker"]
    except Exception:
        pass


def _quote_rows_to_hits(rows):
    results = []
    seen = set()
    skip_types = {"OPTION", "CRYPTOCURRENCY", "FUTURE", "CURRENCY", "ECNQUOTE"}
    for row in rows or []:
        symbol = str(row.get("symbol") or "").strip()
        quote_type = str(row.get("quoteType") or "").upper()
        if not symbol or symbol in seen or quote_type in skip_types:
            continue
        seen.add(symbol)
        results.append(
            {
                "symbol": symbol,
                "name": row.get("longname") or row.get("shortname") or row.get("longName") or row.get("shortName") or symbol,
                "type": row.get("typeDisp") or row.get("quoteType") or "",
                "exchange": row.get("exchDisp") or row.get("fullExchangeName") or row.get("exchange") or "",
                "sector": row.get("sector") or row.get("sectorDisp") or "",
                "industry": row.get("industry") or row.get("industryDisp") or "",
                "quote_type": quote_type,
            }
        )
    return results


def _yahoo_search_live(query):
    rows = []
    params = {
        "q": query,
        "quotesCount": 20,
        "newsCount": 0,
        "listsCount": 0,
        "enableFuzzyQuery": "true",
    }
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        try:
            response = requests.get(
                f"https://{host}/v1/finance/search",
                params=params,
                headers=YAHOO_HEADERS,
                timeout=8,
            )
            payload = response.json() if response.ok else {}
            rows = list(payload.get("quotes") or [])
            if rows:
                break
        except Exception:
            continue

    if not rows:
        try:
            search = yf.Search(
                query,
                max_results=20,
                news_count=0,
                lists_count=0,
                enable_fuzzy_query=True,
                raise_errors=False,
            )
            rows = list(search.quotes or [])
        except Exception:
            rows = []
    return _quote_rows_to_hits(rows)


NAME_ALIASES = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "samsung": "005930.KS",
    "samsung electronics": "005930.KS",
    "samsung elec": "005930.KS",
    "toyota": "TM",
    "sony": "SONY",
    "alibaba": "BABA",
    "tencent": "0700.HK",
    "sap": "SAP",
    "asml": "ASML",
    "nestle": "NESN.SW",
    "novartis": "NVS",
    "roche": "ROG.SW",
    "shell": "SHEL",
    "bp": "BP",
    "volkswagen": "VOW3.DE",
    "bmw": "BMW.DE",
    "lvmh": "MC.PA",
    "unilever": "UL",
    "hsbc": "HSBC",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "berkshire": "BRK-B",
    "visa": "V",
    "mastercard": "MA",
    "walmart": "WMT",
    "costco": "COST",
    "coca cola": "KO",
    "coke": "KO",
    "pepsi": "PEP",
    "pepsico": "PEP",
    "disney": "DIS",
    "intel": "INTC",
    "amd": "AMD",
    "broadcom": "AVGO",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "uber": "UBER",
    "airbnb": "ABNB",
    "paypal": "PYPL",
    "shopify": "SHOP",
    "palantir": "PLTR",
    "boeing": "BA",
    "ibm": "IBM",
    "cisco": "CSCO",
    "qualcomm": "QCOM",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "exxon": "XOM",
    "chevron": "CVX",
    "johnson": "JNJ",
    "johnson and johnson": "JNJ",
    "procter": "PG",
    "procter and gamble": "PG",
    "home depot": "HD",
    "mcdonalds": "MCD",
    "nike": "NKE",
    "starbucks": "SBUX",
}


def lookup_alias(query):
    needle = re.sub(r"[^a-z0-9]+", " ", str(query or "").lower())
    needle = " ".join(needle.split())
    if not needle:
        return None
    if needle in NAME_ALIASES:
        return NAME_ALIASES[needle]
    for key, symbol in NAME_ALIASES.items():
        if needle.startswith(key + " "):
            return symbol
    return None


@st.cache_data(ttl=180, show_spinner=False)
def search_companies_v2(query):
    query = str(query or "").strip()
    if not query:
        return []
    results = _yahoo_search_live(query)
    needle = query.lower()
    ticker_needle = query.strip().upper()
    type_rank = {"EQUITY": 0, "ETF": 1, "INDEX": 2, "MUTUALFUND": 3}

    def rank(hit):
        name = str(hit["name"] or "").lower()
        symbol = str(hit["symbol"] or "").upper()
        exact_symbol = 0 if symbol == ticker_needle else 1
        name_prefix = 0 if name.startswith(needle) else 1
        name_hit = 0 if needle in name else 1
        return (
            exact_symbol,
            name_prefix,
            name_hit,
            type_rank.get(hit.get("quote_type"), 9),
        )

    results.sort(key=rank)
    return results[:16]


def search_companies(query):
    results = search_companies_v2(query)
    if results:
        return results
    return _yahoo_search_live(query)


def resolve_company_query(query):
    raw = str(query or "").strip()
    if not raw:
        return None, "Type a company name or ticker.", []

    alias = lookup_alias(raw)
    if alias:
        return alias, None, []

    as_ticker = normalize_ticker(raw)
    hits = search_companies(raw)

    symbol_hits = [hit for hit in hits if hit["symbol"].upper() == as_ticker]
    if symbol_hits:
        return symbol_hits[0]["symbol"], None, []

    if not hits:
        if ticker_format_ok(as_ticker):
            return as_ticker, None, []
        return None, f"No company found for “{raw}”. Try the company name or ticker.", []

    if len(hits) == 1:
        return hits[0]["symbol"], None, []

    return None, None, hits


def render_ticker_error(symbol, reason=None):
    detail = reason or "Yahoo Finance did not return usable company data for that symbol."
    render_html(
        f"""
<div class="empty-state error-state">
  <div class="eyebrow">Not found</div>
  <div class="hero-title">{esc(symbol)}</div>
  <div class="hero-copy">{esc(detail)}</div>
</div>
"""
    )
    recent = [s for s in st.session_state.get("recent", []) if s]
    if recent:
        render_html('<div class="watch-heading">Recent</div>')
        render_watch_row(recent[:5], "err_recent")
    if st.button("Back", key="dismiss_ticker_error"):
        st.session_state.ticker_error = None
        st.session_state.invalid_ticker = ""
        st.rerun()


def remember_ticker(symbol):
    symbol = normalize_ticker(symbol)
    if not symbol:
        return
    recent = [s for s in st.session_state.get("recent", []) if s != symbol]
    st.session_state.recent = [symbol] + recent[:7]


def evidence_dataframe(analysis, company_name, ticker, sector, industry, reporting_currency, trading_currency, display_currency, display_fx_reporting, display_fx_trading=1.0):
    return pd.DataFrame(
        [
            ["Company", company_name],
            ["Ticker", ticker],
            ["Sector", sector],
            ["Industry", industry],
            ["Reporting Currency", reporting_currency],
            ["Trading Currency", trading_currency],
            ["Display Currency", display_currency],
            ["Expectation Reality Score", score(analysis["reality_score"])],
            ["Business Quality", score(analysis["business_quality"])],
            ["Market Expectations", score(analysis["market_expectations"])],
            ["Financial Strength", score(analysis["financial_strength"])],
            ["Enterprise Value", money(analysis["enterprise_value"], reporting_currency, display_currency, display_fx_reporting)],
            ["Revenue", money(analysis["revenue"], reporting_currency, display_currency, display_fx_reporting)],
            ["Free Cash Flow", money(analysis["free_cash_flow"], reporting_currency, display_currency, display_fx_reporting)],
            ["Cash", money(analysis["cash"], reporting_currency, display_currency, display_fx_reporting)],
            ["Debt", money(analysis["debt"], reporting_currency, display_currency, display_fx_reporting)],
            ["Gross Margin", percent(analysis["gross_margin"])],
            ["Operating Margin", percent(analysis["operating_margin"])],
            ["Profit Margin", percent(analysis["profit_margin"])],
            ["FCF Margin", percent(analysis["fcf_margin"])],
            ["FCF margin used in reverse DCF", percent(analysis.get("model_fcf_margin"))],
            ["Discount rate", percent(analysis.get("discount_rate"))],
            ["Terminal growth", percent(analysis.get("terminal_growth"))],
            ["Historical Revenue Growth", percent(analysis["historical_growth"])],
            ["Historical window (years)", str(analysis.get("historical_growth_years") or "—")],
            ["Required Revenue Growth", required_growth_label(analysis)],
            ["Analyst Consensus Growth", percent(analysis.get("consensus_growth"))],
            ["Analyst Target Price", money(analysis.get("target_mean_price"), trading_currency, display_currency, display_fx_trading)],
            ["EV/Sales", multiple(analysis["ev_sales"])],
            ["EV/EBITDA", multiple(analysis["ev_ebitda"])],
            ["P/E", multiple(analysis["pe"])],
            ["Data Confidence", analysis.get("confidence", "—")],
        ],
        columns=["Metric", "Value"],
    )


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_compare_analysis(symbol):
    try:
        data = fetch_yahoo_data(symbol)
        if not yahoo_data_is_valid(data):
            return None
        result = analyze_company(data, None)
        info = data.get("info") or {}
        result["name"] = info.get("longName") or info.get("shortName") or symbol
        return result
    except Exception:
        return None


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
    """Pick the best annual series across tags + taxonomies (us-gaap, ifrs-full)."""
    if not companyfacts:
        return pd.Series(dtype=float), None

    fact_roots = companyfacts.get("facts", {}) or {}
    taxonomies = []
    for key in ("us-gaap", "ifrs-full"):
        if key in fact_roots:
            taxonomies.append(fact_roots[key])
    for key, block in fact_roots.items():
        if key not in ("us-gaap", "ifrs-full") and isinstance(block, dict):
            taxonomies.append(block)

    candidates = []
    for facts in taxonomies:
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
                ]
                if not annual:
                    continue

                annual = sorted(annual, key=lambda x: (x.get("fy", 0), x.get("end", "")), reverse=True)
                yearly = {}
                latest_end = ""
                for row in annual:
                    fy = row.get("fy")
                    if fy not in yearly:
                        yearly[fy] = safe_float(row.get("val"))
                        latest_end = max(latest_end, row.get("end") or "")
                ordered = pd.Series([yearly[fy] for fy in sorted(yearly.keys(), reverse=True)])
                candidates.append((latest_end, len(ordered), ordered, currency))
                break  # best currency for this tag in this taxonomy

    if not candidates:
        return pd.Series(dtype=float), None

    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, ordered, currency = candidates[0]
    return ordered, currency


def pick_fcf_from_sources(sec_fcf, yahoo_annual_fcf, yahoo_ttm_fcf, yahoo_info_fcf, revenue_from_sec):
    """
    Prefer period-matched FCF: SEC with SEC revenue, Yahoo annual with Yahoo revenue,
    then TTM / reported as last resorts. Never invent.
    """
    if revenue_from_sec:
        order = [
            (sec_fcf, "SEC EDGAR (OCF − Capex)"),
            (yahoo_annual_fcf, "Yahoo Finance annual (OCF − Capex)"),
            (yahoo_ttm_fcf, "Yahoo Finance TTM (OCF − Capex)"),
            (yahoo_info_fcf, "Yahoo Finance reported FCF"),
        ]
    else:
        order = [
            (yahoo_annual_fcf, "Yahoo Finance annual (OCF − Capex)"),
            (sec_fcf, "SEC EDGAR (OCF − Capex)"),
            (yahoo_ttm_fcf, "Yahoo Finance TTM (OCF − Capex)"),
            (yahoo_info_fcf, "Yahoo Finance reported FCF"),
        ]
    for value, source in order:
        if value is not None:
            return value, source
    return None, None


def choose_revenue_history(sec_history, yahoo_history):
    """Prefer the series with usable multi-year coverage; break ties on length."""
    sec_ok = sec_history is not None and len(sec_history) >= 2
    yahoo_ok = yahoo_history is not None and len(yahoo_history) >= 2
    if sec_ok and yahoo_ok:
        return sec_history if len(sec_history) >= len(yahoo_history) else yahoo_history
    if sec_ok:
        return sec_history
    if yahoo_ok:
        return yahoo_history
    if sec_history is not None and not sec_history.empty:
        return sec_history
    return yahoo_history if yahoo_history is not None else pd.Series(dtype=float)


def data_coverage_confidence(fields, quality_flags):
    """Confidence tracks field coverage, not how good the fundamentals look."""
    present = sum(1 for v in fields.values() if v)
    total = len(fields) or 1
    ratio = present / total
    bad_count = sum(1 for _, level in quality_flags if level == "bad")
    missing_growth = not fields.get("historical_growth", False)

    if bad_count or ratio < 0.5:
        return "Low", ratio
    if missing_growth or ratio < 0.85:
        return "Medium", ratio
    return "High", ratio


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


def trailing_fcf_margins(cashflow, financials, years=4):
    """Newest-first FCF / revenue for up to `years` annual periods."""
    ocf = historical_series(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = historical_series(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    reported = historical_series(cashflow, ["Free Cash Flow"])
    rev = historical_series(financials, ["Total Revenue", "Operating Revenue"])
    length = min(len(rev), years)
    margins = []
    for i in range(length):
        cap = capex.iloc[i] if i < len(capex) else None
        ocf_i = ocf.iloc[i] if i < len(ocf) else None
        reported_i = reported.iloc[i] if i < len(reported) else None
        fcf = compute_fcf(ocf_i, cap, reported_i)
        revenue_i = safe_float(rev.iloc[i])
        if fcf is not None and revenue_i and revenue_i > 0:
            margins.append(fcf / revenue_i)
    return margins


def analyze_company(yahoo_data, sec_facts):
    info = yahoo_data["info"]
    fast_info = yahoo_data["fast_info"]
    financials = yahoo_data["financials"]
    balance = yahoo_data["balance"]
    cashflow = yahoo_data["cashflow"]
    quarterly_cashflow = yahoo_data.get("quarterly_cashflow")
    if quarterly_cashflow is None:
        quarterly_cashflow = pd.DataFrame()

    reporting_currency, trading_currency = detect_currencies(info, fast_info)
    sec_currencies = sec_currency_candidates(reporting_currency, trading_currency)
    sector = info.get("sector") or "Unknown sector"
    sector_model = get_sector_model(sector)
    rates = model_rates(reporting_currency, sector)
    discount_rate = rates["discount_rate"]
    terminal_growth = rates["terminal_growth"]

    price = get_quote_price(info, fast_info)
    market_cap = get_market_cap(info, fast_info)

    yahoo_revenue = latest_value(financials, ["Total Revenue", "Operating Revenue"])
    yahoo_gross_profit = latest_value(financials, ["Gross Profit"])
    yahoo_operating_income = latest_value(financials, ["Operating Income"])
    yahoo_net_income = latest_value(financials, ["Net Income", "Net Income Common Stockholders"])
    yahoo_ebitda = latest_value(financials, ["EBITDA", "Normalized EBITDA"])

    yahoo_operating_cash_flow = latest_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    yahoo_capex = latest_value(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    yahoo_reported_fcf = latest_value(cashflow, ["Free Cash Flow"])
    yahoo_annual_fcf = compute_fcf(yahoo_operating_cash_flow, yahoo_capex, yahoo_reported_fcf)
    yahoo_info_fcf = safe_float(info.get("freeCashflow"))
    yahoo_ttm_fcf = compute_fcf(
        ttm_sum(quarterly_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]),
        ttm_sum(quarterly_cashflow, ["Capital Expenditure", "Capital Expenditures"]),
        ttm_sum(quarterly_cashflow, ["Free Cash Flow"]),
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
    sec_fcf_raw = compute_fcf(sec_ocf, sec_capex_value)
    if sec_fcf_raw is not None and sec_revenue_currency and reporting_currency and sec_revenue_currency != reporting_currency:
        sec_fcf = convert_amount(sec_fcf_raw, sec_revenue_currency, reporting_currency)
    else:
        sec_fcf = sec_fcf_raw

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

    revenue_from_sec = bool(revenue_source and str(revenue_source).startswith("SEC"))
    free_cash_flow, fcf_source = pick_fcf_from_sources(
        sec_fcf, yahoo_annual_fcf, yahoo_ttm_fcf, yahoo_info_fcf, revenue_from_sec
    )

    market_cap_reporting = convert_amount(market_cap, trading_currency, reporting_currency)
    enterprise_value_trading = safe_float(info.get("enterpriseValue"))
    if enterprise_value_trading is None and market_cap is not None:
        debt_reporting = debt or 0
        cash_reporting = cash or 0
        enterprise_value_trading = market_cap + convert_amount(debt_reporting, reporting_currency, trading_currency) - convert_amount(cash_reporting, reporting_currency, trading_currency)

    enterprise_value = convert_amount(enterprise_value_trading, trading_currency, reporting_currency)
    if enterprise_value is None and market_cap_reporting is not None:
        enterprise_value = market_cap_reporting + (debt or 0) - (cash or 0)

    revenue_history_sec, _ = sec_fact_values(sec_facts, SEC_TAGS["revenue"], sec_currencies)
    revenue_history_yahoo = historical_series(financials, ["Total Revenue", "Operating Revenue"])
    revenue_history = choose_revenue_history(revenue_history_sec, revenue_history_yahoo)

    historical_growth = None
    historical_growth_years = 0
    history_values = revenue_history.tolist() if hasattr(revenue_history, "tolist") else list(revenue_history)
    if len(history_values) >= 2:
        historical_growth, historical_growth_years = history_cagr(history_values, max_years=10)
    historical_growth_3y = None
    if len(history_values) >= 2:
        historical_growth_3y, _ = history_cagr(history_values, max_years=3)

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

    trailing_margins = trailing_fcf_margins(cashflow, financials, years=4)
    sector_mature_fcf = safe_float(sector_model.get("financial_benchmarks", {}).get("fcf"), DEFAULT_FCF_MARGIN)
    model_fcf_margin, model_fcf_refused, model_fcf_note = choose_model_fcf_margin(
        fcf_margin, trailing_margins, sector_mature_fcf
    )
    model_fcf_assumed = False

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
    if pe is None and market_cap_reporting and net_income and net_income > 0:
        pe = market_cap_reporting / net_income

    required_growth, growth_clamped = (None, False)
    if model_fcf_margin is not None:
        required_growth, growth_clamped = solve_required_growth(
            enterprise_value,
            revenue,
            model_fcf_margin,
            discount_rate,
            terminal_growth,
            start_margin=fcf_margin if fcf_margin is not None and fcf_margin > 0 else model_fcf_margin,
        )

    consensus_growth = None
    revenue_estimate = yahoo_data.get("revenue_estimate")
    if revenue_estimate is not None and hasattr(revenue_estimate, "index") and "growth" in getattr(revenue_estimate, "columns", []):
        for period in ("+1y", "0y"):
            if period in revenue_estimate.index:
                consensus_growth = safe_float(revenue_estimate.loc[period, "growth"])
                if consensus_growth is not None:
                    break

    target_mean_price = safe_float(info.get("targetMeanPrice"))
    analyst_count = safe_float(info.get("numberOfAnalystOpinions"))

    quality_flags = []
    if revenue is None:
        quality_flags.append(("Revenue unavailable", "bad"))
    if model_fcf_refused:
        quality_flags.append(("Required growth N/A — no positive free-cash margin to reverse-solve", "bad"))
    elif required_growth is None:
        quality_flags.append(("Required growth not solvable", "bad"))
    if free_cash_flow is None:
        quality_flags.append(("Free cash flow unavailable", "warn"))
    elif fcf_margin is not None and fcf_margin <= 0:
        quality_flags.append((f"Actual FCF margin {fcf_margin:.0%} — reverse DCF not run on negative cash", "warn"))
    if historical_growth is None:
        quality_flags.append(("No revenue growth history — growth scored as incomplete (not assumed 5%)", "warn"))
    if cash is None or debt is None:
        quality_flags.append(("Balance sheet incomplete — leverage scored as missing evidence", "warn"))
    if growth_clamped:
        quality_flags.append(("Required growth hit solver bound — shown as a range, not an exact rate", "warn"))
    if sector == "Real Estate":
        quality_flags.append(("REIT caveat: model uses FCF/P-E, not FFO/AFFO — scores are approximate", "warn"))
    if rates["used_fallback_currency"]:
        quality_flags.append(("Unknown reporting currency — used USD rate world as fallback", "warn"))

    coverage_fields = {
        "revenue": revenue is not None,
        "free_cash_flow": free_cash_flow is not None,
        "historical_growth": historical_growth is not None,
        "cash": cash is not None,
        "debt": debt is not None,
        "enterprise_value": enterprise_value is not None,
    }
    confidence, _ = data_coverage_confidence(coverage_fields, quality_flags)
    if model_fcf_refused or growth_clamped:
        confidence = "Low"
    elif confidence == "High" and historical_growth is None:
        confidence = "Medium"

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
        None if growth_clamped else required_growth,
        historical_growth,
        business_quality,
        consensus_growth,
    )

    final_score = reality_score(
        business_quality,
        financial_strength,
        historical_growth,
        required_growth,
        consensus_growth,
        growth_clamped=growth_clamped or model_fcf_refused,
    )

    sources = {
        "Revenue": revenue_source,
        "Net Income": net_income_source,
        "Assets": assets_source,
        "Equity": equity_source,
        "Cash": cash_source,
        "Debt": debt_source,
        "Free Cash Flow": fcf_source or "Unavailable",
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
        "model_fcf_assumed": model_fcf_assumed,
        "model_fcf_refused": model_fcf_refused,
        "model_fcf_note": model_fcf_note,
        "discount_rate": discount_rate,
        "terminal_growth": terminal_growth,
        "risk_free": rates["risk_free"],
        "erp": rates["erp"],
        "sector_spread": rates["sector_spread"],
        "rate_currency": rates["currency"],
        "historical_growth": historical_growth,
        "historical_growth_years": historical_growth_years,
        "historical_growth_3y": historical_growth_3y,
        "required_growth": required_growth,
        "growth_clamped": growth_clamped,
        "business_quality": business_quality,
        "market_expectations": market_expectations,
        "financial_strength": financial_strength,
        "gap": gap,
        "probability": probability,
        "reality_score": final_score,
        "ev_sales": ev_sales,
        "ev_ebitda": ev_ebitda,
        "pe": pe,
        "consensus_growth": consensus_growth,
        "target_mean_price": target_mean_price,
        "analyst_count": analyst_count,
        "quality_flags": quality_flags,
        "confidence": confidence,
        "sources": sources,
        "has_sec": sec_facts is not None,
    }


def pricing_points(analysis, display_currency=None, display_fx=1.0):
    points = [
        ("Required revenue growth", required_growth_label(analysis)),
        ("FCF margin used in model", percent(analysis["model_fcf_margin"])),
        ("Discount rate", percent(analysis.get("discount_rate"))),
        ("Terminal growth", percent(analysis.get("terminal_growth"))),
        ("EV/Sales pressure", multiple(analysis["ev_sales"])),
    ]
    if analysis.get("target_mean_price") is not None:
        points.append(
            (
                "Analyst mean target",
                money(
                    analysis["target_mean_price"],
                    analysis["trading_currency"],
                    display_currency or analysis["trading_currency"],
                    display_fx,
                ),
            )
        )
    if analysis.get("consensus_growth") is not None:
        points.append(("Analyst consensus growth", percent(analysis["consensus_growth"])))
    return points


def reality_points(analysis, display_currency, display_fx):
    years = analysis.get("historical_growth_years") or 0
    hist_label = f"Historical revenue growth ({years}y)" if years else "Historical revenue growth"
    return [
        (hist_label, percent(analysis["historical_growth"])),
        ("Analyst consensus growth", percent(analysis.get("consensus_growth"))),
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


def score_tone(value):
    value = safe_float(value, 0)
    if value >= 70:
        return "good"
    if value >= 50:
        return "mid"
    return "low"


def score_ring_svg(value, tone):
    scheme = current_scheme()
    pct = clamp(value)
    radius = 54
    circumference = 2 * math.pi * radius
    offset = circumference * (1 - pct / 100)
    colors = {"good": scheme["green"], "mid": scheme["orange"], "low": scheme["red"]}
    color = colors.get(tone, scheme["blue"])
    return (
        f'<div class="score-ring-wrap">'
        f'<svg class="score-ring" viewBox="0 0 128 128" aria-hidden="true">'
        f'<circle cx="64" cy="64" r="{radius}" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="9"/>'
        f'<circle cx="64" cy="64" r="{radius}" fill="none" stroke="{color}" stroke-width="9" '
        f'stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{offset:.2f}" '
        f'stroke-linecap="round" transform="rotate(-90 64 64)"/>'
        f"</svg>"
        f'<div class="score-ring-inner"><div class="score-big">{score(value)}</div></div>'
        f"</div>"
    )


def score_panel_html(analysis, tone):
    return (
        f'<div class="score-panel {tone}">'
        f'<div class="score-kicker">Expectation Reality Score</div>'
        f'{score_ring_svg(analysis["reality_score"], tone)}'
        f'<div class="score-status">{score_label(analysis["reality_score"])}</div>'
        f'<div class="score-caption">Heuristic dashboard — not a buy/sell call. Main tell is required growth vs history and consensus.</div>'
        f"</div>"
    )


def badges_html(sector, industry, ticker):
    badges = [f'<span class="badge badge-ticker">{esc(ticker)}</span>']
    if sector and sector != "Unknown sector":
        badges.append(f'<span class="badge badge-muted">{esc(sector)}</span>')
    if industry and industry not in ("Unknown industry", "Unknown"):
        badges.append(f'<span class="badge badge-muted">{esc(industry)}</span>')
    return f'<div class="badge-row">{"".join(badges)}</div>'


def metric_card(title, value, meta, accent="blue"):
    accent_class = "" if accent == "blue" else f" accent-{accent}"
    return (
        f'<div class="metric-card{accent_class}">'
        f'<div class="metric-label">{esc(title)}</div>'
        f'<div class="metric-value">{esc(value)}</div>'
        f'<div class="metric-meta">{esc(meta)}</div>'
        f"</div>"
    )


def score_bars_html(analysis):
    items = [
        ("Expectation Reality Score", analysis["reality_score"]),
        ("Business Quality", analysis["business_quality"]),
        ("Market Expectations", analysis["market_expectations"]),
        ("Financial Strength", analysis["financial_strength"]),
    ]
    parts = []
    for label, value in items:
        pct = round(clamp(value), 1)
        tone = score_tone(value)
        parts.append(
            f'<div class="score-row">'
            f'<div class="score-row-head"><span>{label}</span><strong>{score(value)}</strong></div>'
            f'<div class="score-track"><div class="score-fill {tone}" style="width:{pct}%"></div></div>'
            f"</div>"
        )
    return "".join(parts)


def make_rows(items):
    html_rows = ""
    for label, value in items:
        html_rows += f"<div class='row'><span>{esc(label)}</span><b>{esc(value)}</b></div>"
    return html_rows


def conclusion_text(analysis):
    if analysis.get("model_fcf_refused"):
        return "Required growth is not computed here. Free cash is missing or negative, so inventing a healthy cash margin would fake the answer."
    if analysis.get("growth_clamped"):
        return "The price sits outside the model's growth search range. Treat the score as a warning label, not a precise implied growth rate."
    if analysis["reality_score"] >= 80:
        return "The company evidence appears to strongly support the expectations embedded in the price."
    if analysis["reality_score"] >= 65:
        return "The company evidence appears to reasonably support expectations, though execution still matters."
    if analysis["reality_score"] >= 50:
        return "The setup is mixed. The market appears to require real future success, but the evidence is not empty."
    if analysis["reality_score"] >= 40:
        return "Expectations look demanding. The company needs stronger execution to support what the market appears to price in."
    return "Expectations look very demanding compared with the company evidence currently available."


def compare_name(symbol, analysis):
    name = str((analysis or {}).get("name") or "").strip()
    known = KNOWN_NAMES.get(str(symbol).upper()) or KNOWN_NAMES.get(symbol)
    if known and (not name or name.upper() == str(symbol).upper()):
        return known
    if not name or name.upper() == str(symbol).upper():
        return symbol
    return name


def compare_company_color(index):
    scheme = current_scheme()
    palette = [scheme["blue"], scheme["green"], scheme["orange"], scheme["red"]]
    return palette[index % len(palette)]


def resolve_compare_peer(token):
    token = str(token or "").strip()
    if not token:
        return None, "Empty name"
    symbol, error, hits = resolve_company_query(token)
    if symbol:
        return symbol, None
    if hits:
        return hits[0]["symbol"], None
    return None, error or f"No company found for “{token}”."


def render_compare_chart(results):
    try:
        import plotly.graph_objects as go
    except ImportError:
        return

    symbols = list(results)
    categories = [
        ("Reality", "reality_score"),
        ("Quality", "business_quality"),
        ("Expectations", "market_expectations"),
        ("Strength", "financial_strength"),
    ]
    x_labels = [label for label, _ in categories]
    fig = go.Figure()
    for index, symbol in enumerate(symbols):
        color = compare_company_color(index)
        name = compare_name(symbol, results[symbol])
        fig.add_trace(
            go.Bar(
                name=name,
                x=x_labels,
                y=[results[symbol][key] for _, key in categories],
                marker_color=color,
                marker_line_width=0,
                hovertemplate="%{y:.0f}<extra>" + name + "</extra>",
            )
        )
    fig.update_layout(
        barmode="group",
        bargap=0.28,
        bargroupgap=0.08,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=4, r=4, t=8, b=8),
        height=320,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(color="#e8eaed", size=12),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(tickfont=dict(color="#e8eaed", size=12), linecolor="rgba(255,255,255,0.06)"),
        yaxis=dict(
            range=[0, 100],
            showgrid=True,
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
            tickfont=dict(color="#6a6d78", size=11),
        ),
        hoverlabel=dict(bgcolor="#12161e", bordercolor="rgba(255,255,255,0.1)", font_color="#e8eaed"),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def compare_table_html(results):
    symbols = list(results)

    def score_cell(value):
        return f'<td class="cell-{score_tone(value)}">{esc(score(value))}</td>'

    def plain_cell(text):
        return f"<td>{esc(text)}</td>"

    rows = [
        ("Reality Score", lambda a: score_cell(a["reality_score"])),
        ("Business Quality", lambda a: score_cell(a["business_quality"])),
        ("Financial Strength", lambda a: score_cell(a["financial_strength"])),
        ("Market Expectations", lambda a: plain_cell(score(a["market_expectations"]))),
        ("Required growth /yr", lambda a: plain_cell(required_growth_label(a))),
        ("Analyst consensus (next FY)", lambda a: plain_cell(percent(a.get("consensus_growth")))),
        ("Historical growth", lambda a: plain_cell(percent(a["historical_growth"]))),
        ("Discount rate", lambda a: plain_cell(percent(a.get("discount_rate")))),
        ("Terminal growth", lambda a: plain_cell(percent(a.get("terminal_growth")))),
        ("Reporting currency", lambda a: plain_cell(a.get("reporting_currency"))),
        ("Operating margin", lambda a: plain_cell(percent(a["operating_margin"]))),
        ("FCF margin", lambda a: plain_cell(percent(a["fcf_margin"]))),
        ("EV/Sales", lambda a: plain_cell(multiple(a["ev_sales"]))),
        ("EV/EBITDA", lambda a: plain_cell(multiple(a["ev_ebitda"]))),
        ("P/E", lambda a: plain_cell(multiple(a["pe"]))),
        ("Sector", lambda a: plain_cell(a["sector"])),
        ("Data confidence", lambda a: plain_cell(a.get("confidence", "—"))),
    ]

    header = (
        "<tr><th>Metric</th>"
        + "".join(
            f'<th style="border-top-color:{esc(compare_company_color(i))}">'
            f'<span class="cmp-name"><span class="cmp-swatch" style="background:{esc(compare_company_color(i))}"></span>{esc(compare_name(s, results[s]))}</span>'
            f'<span class="cmp-ticker">{esc(s)}</span></th>'
            for i, s in enumerate(symbols)
        )
        + "</tr>"
    )
    body = ""
    for label, cell_fn in rows:
        body += f"<tr><td>{esc(label)}</td>" + "".join(cell_fn(results[s]) for s in symbols) + "</tr>"
    return f'<table class="cmp-table">{header}{body}</table>'


if "ticker" not in st.session_state:
    st.session_state.ticker = ""

if "display_currency" not in st.session_state:
    st.session_state.display_currency = "USD"

if "ticker_error" not in st.session_state:
    st.session_state.ticker_error = None

if "invalid_ticker" not in st.session_state:
    st.session_state.invalid_ticker = ""

if "recent" not in st.session_state:
    st.session_state.recent = []

if "search_hits" not in st.session_state:
    st.session_state.search_hits = []

if "company_search" not in st.session_state:
    st.session_state.company_search = ""

if st.session_state.get("pending_search") is not None:
    st.session_state.company_search = st.session_state.pending_search
    del st.session_state.pending_search

if st.session_state.display_currency not in DISPLAY_CURRENCIES:
    st.session_state.display_currency = "USD"

if (
    not st.session_state.ticker
    and not st.session_state.search_hits
    and not st.session_state.ticker_error
):
    incoming = query_ticker()
    if incoming and ticker_format_ok(incoming):
        st.session_state.ticker = incoming
        if not st.session_state.company_search:
            st.session_state.company_search = incoming


def render_watch_row(items, key_prefix):
    items = [item for item in items if item]
    if not items:
        return
    cols = st.columns(len(items), gap="small")
    for col, item in zip(cols, items):
        if isinstance(item, (tuple, list)):
            label, symbol = item[0], item[1]
        else:
            label, symbol = item, item
        with col:
            if st.button(label, key=f"{key_prefix}_{symbol}", use_container_width=True, type="secondary"):
                st.session_state.ticker = symbol
                st.session_state.pending_search = label
                st.session_state.search_hits = []
                st.session_state.ticker_error = None
                st.session_state.invalid_ticker = ""
                st.rerun()


st.markdown('<div class="app-wrap">', unsafe_allow_html=True)

render_app_header()

try:
    search_form = st.form("search_form", border=False)
except TypeError:
    search_form = st.form("search_form")

with search_form:
    try:
        col_a, col_c = st.columns([5.2, 1], gap="small", vertical_alignment="center")
    except TypeError:
        col_a, col_c = st.columns([5.2, 1])
    with col_a:
        typed = st.text_input(
            "Search",
            placeholder="Company name or ticker — Apple, Microsoft, NVIDIA",
            label_visibility="collapsed",
            key="company_search",
        )
    with col_c:
        submitted = st.form_submit_button("Analyze", use_container_width=True)

if submitted:
    typed = str(typed or "").strip()
    if not typed:
        st.session_state.ticker = ""
        st.session_state.search_hits = []
        st.session_state.ticker_error = None
        st.session_state.invalid_ticker = ""
        sync_ticker_query("")
    else:
        symbol, error, hits = resolve_company_query(typed)
        st.session_state.search_hits = hits
        if symbol:
            st.session_state.ticker = symbol
            st.session_state.invalid_ticker = ""
            st.session_state.ticker_error = None
            st.session_state.search_hits = []
        else:
            st.session_state.ticker = ""
            st.session_state.invalid_ticker = typed
            st.session_state.ticker_error = error

if st.session_state.search_hits:
    render_html('<div class="watch-heading">Pick a listing</div>')
    for hit in st.session_state.search_hits:
        bits = [hit["name"], hit["symbol"]]
        if hit.get("exchange"):
            bits.append(hit["exchange"])
        if hit.get("sector"):
            bits.append(hit["sector"])
        elif hit.get("type"):
            bits.append(hit["type"])
        label = "  ·  ".join(bits)
        if st.button(label, key=f"pick_{hit['symbol']}", use_container_width=True, type="secondary"):
            st.session_state.ticker = hit["symbol"]
            st.session_state.pending_search = hit["name"]
            st.session_state.search_hits = []
            st.session_state.ticker_error = None
            st.session_state.invalid_ticker = ""
            st.rerun()
    st.caption("Tap a row. Company name or ticker both work.")
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

if st.session_state.ticker_error:
    render_ticker_error(st.session_state.invalid_ticker or "input", st.session_state.ticker_error)
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

if not st.session_state.ticker:
    render_html(
        f"""
<div class="home">
  <div>
    <div class="home-lead">
      <div class="eyebrow">{esc(APP_NAME)}</div>
      <div class="hero-title">What growth is the price asking for?</div>
      <div class="hero-copy">Search a name or ticker. The model reverse-solves the sales growth today’s price needs, then sets it next to history and consensus. Missing cash stays N/A.</div>
    </div>
    <div class="home-steps">
      <div><div class="n">1 · Price</div><p>Start from the live quote. No fair-value guess first.</p></div>
      <div><div class="n">2 · Required growth</div><p>Solve for the sales path that justifies that price in the reporting currency.</p></div>
      <div><div class="n">3 · Check</div><p>Compare with the company’s history and analyst consensus.</p></div>
    </div>
  </div>
  <div class="source-line">Yahoo Finance · {esc(EDUCATIONAL_DISCLAIMER)}</div>
</div>
"""
    )
    recent = [s for s in st.session_state.get("recent", []) if s]
    if recent:
        render_html('<div class="watch-heading">Recent</div>')
        render_watch_row(recent[:5], "empty_recent")

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


ticker = st.session_state.ticker

with st.spinner(f"Loading {ticker}..."):
    yahoo_data = fetch_yahoo_data(ticker)
    info = yahoo_data["info"]

if not yahoo_data_is_valid(yahoo_data):
    st.session_state.ticker = ""
    st.session_state.invalid_ticker = ticker
    st.session_state.ticker_error = f"“{ticker}” was not found on Yahoo Finance."
    render_ticker_error(ticker, st.session_state.ticker_error)
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

remember_ticker(ticker)
sync_ticker_query(ticker)

analysis = analyze_company(yahoo_data, None)
reporting_currency = analysis["reporting_currency"]
trading_currency = analysis["trading_currency"]

cur_col, _ = st.columns([1.4, 3.6])
with cur_col:
    display_currency = st.selectbox(
        "Show amounts in",
        DISPLAY_CURRENCIES,
        key="display_currency",
        help="Display only. The reverse DCF still uses the company’s reporting currency.",
    )

raw_fx_reporting = fx_rate(reporting_currency, display_currency)
raw_fx_trading = fx_rate(trading_currency, display_currency)
display_fx_reporting = raw_fx_reporting or 1.0
display_fx_trading = raw_fx_trading or 1.0

company_name = info.get("longName") or info.get("shortName") or KNOWN_NAMES.get(ticker) or ticker
if str(company_name).upper() == ticker and KNOWN_NAMES.get(ticker):
    company_name = KNOWN_NAMES[ticker]
sector = analysis["sector"]
industry = info.get("industry") or "Unknown industry"

currency_note = reporting_currency
if reporting_currency != display_currency:
    if raw_fx_reporting is None:
        currency_note = f"{reporting_currency} → {display_currency} (FX unavailable)"
        st.warning(
            f"Could not fetch FX rate for {reporting_currency}/{display_currency}. "
            "Amounts are shown without conversion."
        )
    else:
        currency_note = f"{reporting_currency} → {display_currency} @ {display_fx_reporting:.4f}"

if trading_currency != display_currency and raw_fx_trading is None:
    st.warning(
        f"Could not fetch FX rate for {trading_currency}/{display_currency}. "
        "        Quote prices may be unconverted."
    )

tone = score_tone(analysis["reality_score"])

render_html(
    f'<div class="result-head">'
    f'<div><div class="hero-title">{esc(company_name)}</div>'
    f'<div class="result-meta">{esc(ticker)}'
    f'{f" · {esc(sector)}" if sector and sector != "Unknown sector" else ""}'
    f' · {esc(reporting_currency)}'
    f' · {esc(percent(analysis.get("discount_rate")))} discount</div>'
    f'<div class="hero-copy" style="margin-top:10px">{esc(conclusion_text(analysis))}</div></div>'
    f'<div class="result-score {esc(tone)}"><em>Score</em><b>{esc(score(analysis["reality_score"]))}</b></div>'
    f"</div>"
)

bad_flags = [label for label, level in analysis["quality_flags"] if level in ("warn", "bad")]
if bad_flags:
    render_html(f'<div class="source-line">{esc(" · ".join(bad_flags[:3]))}</div>')

render_html(implied_line_html(analysis))

render_html(
    f'<div class="metric-grid">'
    f'{metric_card("Price", money(analysis["price"], trading_currency, display_currency, display_fx_trading), f"{trading_currency} quote", "blue")}'
    f'{metric_card("Market cap", money(analysis["market_cap"], trading_currency, display_currency, display_fx_trading), trading_currency, "purple")}'
    f'{metric_card("Revenue", money(analysis["revenue"], reporting_currency, display_currency, display_fx_reporting), analysis["sources"]["Revenue"], "green")}'
    f'{metric_card("Free cash flow", money(analysis["free_cash_flow"], reporting_currency, display_currency, display_fx_reporting), analysis["sources"]["Free Cash Flow"], "cyan")}'
    f"</div>"
)


def growth_bar(label, value, css_class):
    if value is None:
        return (
            f'<div class="gbar-row"><div class="gbar-head"><span>{label}</span><strong>n/a</strong></div>'
            f'<div class="gbar-track"></div></div>'
        )
    width = round(min(max(abs(value) * 300, 2), 100), 1)
    fill_class = "neg" if value < 0 else css_class
    return (
        f'<div class="gbar-row"><div class="gbar-head"><span>{label}</span><strong>{percent(value)}</strong></div>'
        f'<div class="gbar-track"><div class="gbar-fill {fill_class}" style="width:{width}%"></div></div></div>'
    )


def growth_verdict(analysis):
    if analysis.get("model_fcf_refused"):
        return '<div class="gbar-verdict">No required growth until free cash is positive. Showing history and consensus only.</div>'
    if analysis.get("growth_clamped"):
        return '<div class="gbar-verdict">Implied growth sits outside the solver range, so the bars skip a fake-precise required rate.</div>'
    required = analysis["required_growth"]
    consensus = analysis["consensus_growth"]
    if required is None or consensus is None:
        return ""
    gap_pp = (required - consensus) * 100
    if gap_pp > 3:
        text = (
            f"The market appears to require about <b>{gap_pp:.1f} points more annual revenue growth</b> "
            f"than analysts currently forecast for next year. The price assumes the company beats consensus, sustained for years."
        )
    elif gap_pp < -3:
        text = (
            f"The market appears to require about <b>{abs(gap_pp):.1f} points less annual growth</b> "
            f"than analysts forecast for next year. Expectations look conservative relative to consensus."
        )
    else:
        text = "The growth the market requires is <b>roughly in line</b> with analyst consensus for next year."
    count = analysis["analyst_count"]
    if count:
        text += f" Based on {int(count)} analyst estimates."
    return f'<div class="gbar-verdict">{text}</div>'


if any(analysis[k] is not None for k in ("required_growth", "consensus_growth", "historical_growth")):
    years = analysis.get("historical_growth_years") or 0
    hist_caption = f"Historical ({years}y CAGR)" if years else "Historical CAGR"
    render_html(
        f'<div class="growth-plain">'
        f'{growth_bar("Required", analysis["required_growth"] if not analysis.get("growth_clamped") else None, "req")}'
        f'{growth_bar("Analysts", analysis["consensus_growth"], "con")}'
        f'{growth_bar(hist_caption, analysis["historical_growth"], "his")}'
        f"{growth_verdict(analysis)}"
        f"</div>"
    )

def pick_detail_section():
    options = [("Chart", "nav_chart"), ("What-If", "nav_whatif"), ("Compare", "nav_compare"), ("Data", "nav_data")]
    current = st.session_state.get("detail_section")
    cols = st.columns(len(options), gap="small")
    for col, (name, key) in zip(cols, options):
        with col:
            if st.button(name, key=key, use_container_width=True, type="primary" if current == name else "secondary"):
                st.session_state.detail_section = None if current == name else name
                st.rerun()
    return st.session_state.get("detail_section")


def chart_control(label, options, default, key):
    if hasattr(st, "segmented_control"):
        selected = st.segmented_control(label, options, default=default, key=key, label_visibility="collapsed")
        return selected or default
    return st.radio(label, options, index=options.index(default), horizontal=True, key=key, label_visibility="collapsed")


def chart_control_multi(label, options, default, key):
    if hasattr(st, "pills"):
        return st.pills(label, options, selection_mode="multi", default=default, key=key, label_visibility="collapsed") or []
    return st.multiselect(label, options, default=default, key=key, label_visibility="collapsed")


detail = pick_detail_section()

if detail == "Chart":
    ctrl_kind, ctrl_tf, ctrl_ma = st.columns([1, 1.7, 1.5])
    with ctrl_kind:
        chart_kind = chart_control("Chart type", ["Candles", "Line"], "Candles", "chart_kind")
    with ctrl_tf:
        chart_tf = chart_control("Timeframe", CHART_TIMEFRAMES, "1Y", "chart_tf")
    with ctrl_ma:
        ma_selected = chart_control_multi("Moving averages", ["SMA 20", "SMA 50", "SMA 200"], ["SMA 50"], "chart_ma")
    smas = tuple(int(label.split()[1]) for label in ma_selected if str(label).startswith("SMA "))

    refresh_col, _ = st.columns([1, 5])
    with refresh_col:
            if st.button("↻ Refresh chart data", key="refresh_chart"):
                fetch_yahoo_data.clear()
                fetch_price_history.clear()
                st.rerun()

    period = "5y" if chart_tf == "5Y" else "1y"
    history = fetch_price_history(ticker, period)
    if history is None or history.empty:
        st.info("No price history available for this ticker.")
    else:
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        fx = display_fx_trading if trading_currency != display_currency else 1.0
        render_price_chart(history, fx, chart_kind, chart_tf, smas)
        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(f"{ticker} · {chart_tf} · prices in {display_currency} · Yahoo Finance")

elif detail == "What-If":
    market_cap_reporting = safe_float(analysis["market_cap_reporting"])
    if not analysis["revenue"] or not market_cap_reporting or market_cap_reporting <= 0:
        st.info("Revenue or market cap is unavailable, so the what-if model cannot run for this ticker.")
    else:
        base_growth = analysis["required_growth"]
        if base_growth is None:
            base_growth = analysis["historical_growth"] if analysis["historical_growth"] is not None else 0.08
        base_growth_pct = float(min(max(base_growth * 100, -10.0), 40.0))
        # What-If may start from DEFAULT only as an explicit slider seed — never used in the scored model
        seed_margin = analysis["model_fcf_margin"] if analysis["model_fcf_margin"] is not None else DEFAULT_FCF_MARGIN
        base_margin_pct = float(min(max(seed_margin * 100, 1.0), 50.0))
        margin_note = (
            " Reverse DCF was not solved on the main page because free cash is not positive. These sliders are a hypothetical."
            if analysis.get("model_fcf_refused")
            else f" Model cash margin: {analysis.get('model_fcf_note', '')}."
        )
        base_discount = safe_float(analysis.get("discount_rate"), DISCOUNT_RATE)
        base_terminal = safe_float(analysis.get("terminal_growth"), TERMINAL_GROWTH)

        render_html(
            '<div class="whatif-note">Set your own assumptions and see the price they justify. '
            "The sliders start at this company’s currency-aware rates and the growth the main model implied, "
            "so the first result should sit near today’s price. Change growth or margin to what <b>you</b> believe."
            f"{margin_note}</div>"
        )

        sl_left, sl_right = st.columns(2)
        with sl_left:
            wi_growth = st.slider("Starting revenue growth (fades to terminal)", -10.0, 40.0, round(base_growth_pct, 1), 0.5, format="%.1f%%") / 100
            wi_margin = st.slider("FCF margin at maturity", 1.0, 50.0, round(base_margin_pct, 1), 0.5, format="%.1f%%") / 100
        with sl_right:
            wi_discount = st.slider("Discount rate", 3.0, 20.0, round(base_discount * 100, 2), 0.25, format="%.2f%%") / 100
            wi_terminal = st.slider("Terminal growth", 0.0, 6.0, round(min(max(base_terminal * 100, 0.0), 6.0), 2), 0.25, format="%.2f%%") / 100

        if wi_discount <= wi_terminal:
            st.warning("Discount rate must be above terminal growth for the model to converge.")
        else:
            implied_ev = dcf_enterprise_value(
                analysis["revenue"],
                wi_growth,
                wi_margin,
                wi_discount,
                wi_terminal,
                start_margin=analysis["fcf_margin"] if analysis.get("fcf_margin") and analysis["fcf_margin"] > 0 else wi_margin,
            )
            if implied_ev is None:
                st.info("These inputs do not produce a valid valuation.")
            else:
                implied_equity = implied_ev - (analysis["debt"] or 0) + (analysis["cash"] or 0)
                ratio = implied_equity / market_cap_reporting
                upside = ratio - 1
                price = safe_float(analysis["price"])
                implied_price = price * ratio if price is not None else None
                delta_cls = "up" if upside >= 0 else "down"
                delta_txt = f"{upside:+.1%}"
                price_txt = money(implied_price, trading_currency, display_currency, display_fx_trading) if implied_price is not None else "N/A"
                current_txt = money(price, trading_currency, display_currency, display_fx_trading)

                render_html(
                    f'<div class="metric-grid" style="margin-top:14px">'
                    f'{metric_card("Implied fair price", price_txt, "At your assumptions", "blue")}'
                    f'{metric_card("Current price", current_txt, f"{trading_currency} quote", "purple")}'
                    f'{metric_card("Implied enterprise value", money(implied_ev, reporting_currency, display_currency, display_fx_reporting), "DCF at your inputs", "green")}'
                    f'<div class="metric-card accent-cyan"><div class="metric-label">Upside / downside</div>'
                    f'<div class="metric-value"><span class="delta-chip {delta_cls}">{delta_txt}</span></div>'
                    f'<div class="metric-meta">vs current market cap</div></div>'
                    f"</div>"
                )

                req = analysis["required_growth"]
                if req is not None:
                    render_html(
                        f'<div class="whatif-note">For reference, the current price implies about '
                        f"<b>{required_growth_label(analysis)}</b> starting growth at a {percent(analysis['model_fcf_margin'])} FCF margin, "
                        f"{percent(analysis.get('discount_rate'))} discount rate, and {percent(analysis.get('terminal_growth'))} terminal growth "
                        f"(faded path, {analysis.get('rate_currency', reporting_currency)} money world).</div>"
                    )

elif detail == "Compare":
    with st.form("compare_form"):
        cmp_col, btn_col = st.columns([3, 1])
        with cmp_col:
            cmp_input = st.text_input(
                "Peers to compare",
                placeholder="Microsoft, Google, Samsung",
                help="Up to 3 companies, comma separated. Names or tickers both work. The current company is always included.",
            )
        with btn_col:
            st.write("")
            cmp_submit = st.form_submit_button("Compare", use_container_width=True)

    if cmp_submit:
        tokens = [part.strip() for part in cmp_input.replace(";", ",").split(",") if part.strip()]
        valid_peers = []
        seen = {ticker}
        for token in tokens[:6]:
            symbol, error = resolve_compare_peer(token)
            if not symbol:
                st.error(error or f"Could not resolve “{token}”")
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            valid_peers.append(symbol)
            if len(valid_peers) == 3:
                break
        st.session_state.compare_symbols = valid_peers

    peer_symbols = [s for s in st.session_state.get("compare_symbols", []) if s != ticker]

    if not peer_symbols:
        st.info("Enter one to three company names or tickers above — Microsoft, Google, Samsung all work.")
    else:
        analysis["name"] = company_name
        compare_results = {ticker: analysis}
        with st.spinner("Analyzing peers..."):
            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = {pool.submit(fetch_compare_analysis, symbol): symbol for symbol in peer_symbols}
                for fut in as_completed(futs):
                    symbol = futs[fut]
                    peer = fut.result()
                    if peer is None:
                        st.error(f"Invalid ticker: {symbol}")
                    else:
                        compare_results[symbol] = peer

        if len(compare_results) > 1:
            render_compare_chart(compare_results)
            render_html(f'<div class="panel">{compare_table_html(compare_results)}</div>')
            st.caption(
                "Each ticker uses its own reporting-currency discount and terminal rates. "
                "Scores are heuristics — compare required growth vs consensus inside the same money world, not as a global ranking."
            )

elif detail == "Data":
    st.caption("Numbers behind the reverse DCF, plus which source was used.")
    table = evidence_dataframe(
        analysis,
        company_name,
        ticker,
        sector,
        industry,
        reporting_currency,
        trading_currency,
        display_currency,
        display_fx_reporting,
        display_fx_trading,
    )
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.download_button(
        "Download CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=f"tsrp_{ticker}_{display_currency}.csv",
        mime="text/csv",
        use_container_width=False,
    )
    source_table = pd.DataFrame(
        [[k, v] for k, v in analysis["sources"].items()],
        columns=["Data item", "Source"],
    )
    st.dataframe(source_table, use_container_width=True, hide_index=True)
    st.caption("Yahoo Finance was the source for this load.")
    if reporting_currency != trading_currency:
        st.caption(
            f"Statements are in {reporting_currency}. Price and market cap are in {trading_currency}."
        )

render_html(
    f'<div class="app-footer">{esc(APP_SHORT)} · Yahoo Finance · {esc(EDUCATIONAL_DISCLAIMER)}</div>'
)

st.markdown("</div>", unsafe_allow_html=True)
