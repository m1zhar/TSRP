from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import base64
import html
import json
import math
import os
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
    reality_score,
    score_breakdown,
    score_label,
    snapshot_diff,
    solve_required_growth_detail,
)


ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "tsrp-logo.png"
APP_NAME = "The Saleh Research Project"
APP_SHORT = "TSRP"
EDUCATIONAL_DISCLAIMER = (
    "Educational research only. Not financial, investment, tax, accounting, or legal advice. "
    "TSRP does not make recommendations, predict returns, or guarantee data accuracy. "
    "Market data may be delayed, incomplete, or incorrect. Do your own research and consult a qualified professional before making financial decisions."
)

st.set_page_config(
    page_title=f"{APP_SHORT} · {APP_NAME}",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "T",
    layout="wide",
    initial_sidebar_state="collapsed",
)

INK = {
    "bg": "#f4f6f8",
    "bg_elevated": "#ffffff",
    "card": "#ffffff",
    "card_hover": "#f8fafc",
    "surface": "#ffffff",
    "surface_2": "#edf1f5",
    "text": "#14202b",
    "text_secondary": "#5f6d7a",
    "text_tertiary": "#8a97a5",
    "blue": "#2359c7",
    "blue_bright": "#1746a2",
    "cyan": "#187f78",
    "purple": "#6b55b5",
    "green": "#18794e",
    "orange": "#9b6500",
    "red": "#b53b49",
    "border": "#d9e0e7",
    "border_strong": "#bbc6d1",
    "fill": "#eef2f6",
}


def current_scheme():
    return INK


SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()
SEC_STATE = {"kind": "not_configured", "detail": "Live SEC EDGAR facts require a monitored contact identity"}

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


CHART_TIMEFRAMES = ["1M", "3M", "6M", "1Y", "3Y", "5Y", "Max"]

UP_COLOR = "#089981"
DOWN_COLOR = "#f23645"

_AXIS_STYLE = dict(
    showgrid=True,
    gridcolor="#e7ebf0",
    zeroline=False,
    tickfont=dict(color="#7a8793", size=11),
    linecolor="#d9e0e7",
)


def slice_timeframe(df, timeframe):
    if df.empty or timeframe == "Max":
        return df
    end = df.index.max()
    if timeframe == "YTD":
        cutoff = pd.Timestamp(year=end.year, month=1, day=1, tz=getattr(end, "tz", None))
    else:
        months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60}[timeframe]
        cutoff = end - pd.DateOffset(months=months)
    sliced = df[df.index >= cutoff]
    return sliced if not sliced.empty else df


SMA_COLORS = {20: "#a96a00", 50: "#187f78", 200: "#6b55b5"}


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
                fillcolor="rgba(35, 89, 199, 0.10)",
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
            font=dict(color="#5f6d7a", size=11),
            bgcolor="rgba(255,255,255,0)",
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#ffffff", bordercolor="#d9e0e7", font_color="#14202b"),
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
                f"<div class='brand-name'>{esc(APP_NAME)} · Evidence before opinion</div>"
            )
    with right:
        action_col, status_col = st.columns([1, 1.5], gap="small")
        with action_col:
            if st.session_state.get("ticker") and st.button("↻", key="header_refresh", help="Refresh all market data"):
                fetch_yahoo_data.clear()
                fetch_price_history.clear()
                fetch_sec_companyfacts.clear()
                fetch_sec_ticker_map.clear()
                st.rerun()
        with status_col:
            current_ticker = st.session_state.get("ticker")
            context = f"Analyzing {current_ticker}" if current_ticker else "Ready for a company search"
            render_html(f'<div class="header-context">{esc(context)}<br>Market data is refreshed on demand and may be delayed.</div>')
        render_html(
            f'<div class="header-actions">'
            f'<div class="live-pill"><span class="status-dot"></span>Yahoo Finance · source disclosed</div>'
            f'<div class="live-pill badge-muted">Educational research only</div>'
            f"</div>"
        )


st.markdown(
    """
    <style>
    :root {
        --bg: #F3EBDD;
        --bg-elevated: #FBF7EF;
        --card: #FBF7EF;
        --card-hover: #EAE0D1;
        --surface: #FBF7EF;
        --surface-2: #EAE0D1;
        --text: #24211D;
        --text-secondary: #6F675E;
        --text-tertiary: #8A8177;
        --blue: #70583E;
        --blue-bright: #5D4934;
        --blue-soft: rgba(112, 88, 62, 0.12);
        --cyan: #70583E;
        --cyan-soft: rgba(112, 88, 62, 0.10);
        --purple: #70583E;
        --green: #416C55;
        --green-soft: rgba(65, 108, 85, 0.12);
        --orange: #95692F;
        --orange-soft: rgba(149, 105, 47, 0.12);
        --red: #A34D48;
        --red-soft: rgba(163, 77, 72, 0.12);
        --border: #D7CAB8;
        --border-strong: #C5B59F;
        --fill: #EAE0D1;
        --grid: #E5D9C9;
        --shadow: 0 1px 3px rgba(78, 61, 44, .08);
        --shadow-soft: 0 1px 2px rgba(78, 61, 44, .06);
        --glow-blue: none;
        --radius-xl: 14px;
        --radius-lg: 12px;
        --radius-md: 10px;
        --radius-sm: 8px;
        --mono: "SFMono-Regular", "Roboto Mono", Consolas, monospace;
        --display: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        --control-h: 44px;
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

    button:focus-visible, input:focus-visible, [role="button"]:focus-visible,
    [data-baseweb="select"]:focus-within, [data-baseweb="slider"]:focus-within {
        outline: 2px solid var(--blue) !important;
        outline-offset: 2px !important;
    }

    .section-label {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
        margin: 22px 0 10px;
        color: var(--text);
        font-size: 15px;
        font-weight: 650;
        letter-spacing: -0.01em;
    }

    .section-label small {
        color: var(--text-tertiary);
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0;
    }

    .panel-kicker {
        color: var(--text-tertiary);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: .08em;
        text-transform: uppercase;
        margin-bottom: 7px;
    }

    .header-context {
        color: var(--text-tertiary);
        font-size: 11px;
        line-height: 1.35;
        margin-top: 2px;
    }

    .source-chip {
        display: inline-flex;
        align-items: center;
        padding: 4px 8px;
        border: 1px solid var(--border);
        border-radius: 999px;
        color: var(--text-secondary);
        background: var(--surface);
        font-size: 11px;
        white-space: nowrap;
    }

    .status-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        display: inline-block;
        margin-right: 6px;
        background: var(--green);
    }

    .status-dot.warn { background: var(--orange); }
    .status-dot.bad { background: var(--red); }

    .overview-grid {
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(280px, .65fr);
        gap: 14px;
        margin-top: 14px;
    }

    .overview-stack { display: grid; gap: 14px; }

    .signal-copy {
        color: var(--text);
        font-size: 18px;
        font-weight: 600;
        line-height: 1.35;
        letter-spacing: -0.02em;
        margin-bottom: 10px;
    }

    .signal-limitations {
        color: var(--text-tertiary);
        font-size: 12px;
        line-height: 1.5;
        margin-top: 10px;
    }

    .risk-row {
        display: grid;
        grid-template-columns: minmax(100px, .7fr) minmax(0, 1.4fr) auto;
        gap: 12px;
        align-items: start;
        padding: 11px 0;
        border-top: 1px solid var(--grid);
    }

    .risk-row:first-child { border-top: 0; padding-top: 0; }
    .risk-row strong { color: var(--text); font-size: 13px; }
    .risk-row span { color: var(--text-secondary); font-size: 12px; line-height: 1.45; }
    .risk-severity { color: var(--orange) !important; font-size: 10px !important; font-weight: 700; text-transform: uppercase; }

    .risk-cell {
        min-height: 46px;
        padding: 11px 0;
        border-top: 1px solid var(--grid);
    }

    .risk-cell.risk-first { border-top: 0; padding-top: 0; }
    .risk-cell strong { color: var(--text); font-size: 13px; }
    .risk-cell span { color: var(--text-secondary); font-size: 12px; line-height: 1.45; }

    .note-box {
        padding: 11px 13px;
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        color: var(--text-secondary);
        background: var(--surface);
        font-size: 12px;
        line-height: 1.5;
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

    @media (max-width: 900px) {
        [data-testid="stSegmentedControl"] {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
        }

        [data-testid="stSegmentedControl"]::-webkit-scrollbar { display: none; }
        [data-testid="stSegmentedControl"] [data-baseweb="button-group"] { width: max-content; min-width: 100%; }
        [data-testid="stSegmentedControl"] button { flex: 0 0 auto; padding-left: 11px !important; padding-right: 11px !important; white-space: nowrap; }
    }

    [data-testid="stMain"] .st-key-nav_overview button,
    [data-testid="stMain"] .st-key-nav_price button,
    [data-testid="stMain"] .st-key-nav_expectations button,
    [data-testid="stMain"] .st-key-nav_scenarios button,
    [data-testid="stMain"] .st-key-nav_compare button,
    [data-testid="stMain"] .st-key-nav_data button,
    [data-testid="stMain"] .st-key-nav_methodology button {
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

    [data-testid="stMain"] .st-key-nav_overview button:hover,
    [data-testid="stMain"] .st-key-nav_price button:hover,
    [data-testid="stMain"] .st-key-nav_expectations button:hover,
    [data-testid="stMain"] .st-key-nav_scenarios button:hover,
    [data-testid="stMain"] .st-key-nav_compare button:hover,
    [data-testid="stMain"] .st-key-nav_data button:hover,
    [data-testid="stMain"] .st-key-nav_methodology button:hover {
        color: var(--text) !important;
        background: transparent !important;
    }

    [data-testid="stMain"] .st-key-nav_overview button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_price button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_expectations button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_scenarios button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_compare button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_data button[kind="primary"],
    [data-testid="stMain"] .st-key-nav_methodology button[kind="primary"] {
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
        max-width: 980px;
        margin-left: auto;
        margin-right: auto;
    }

    .watch-heading small {
        color: var(--text-tertiary);
        font-size: 11px;
        font-weight: 500;
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
    /* Editorial research workspace reset: quiet hierarchy, useful density, no decorative chrome. */
    :root {
        --bg: #F3EBDD;
        --bg-elevated: #FBF7EF;
        --card: #FBF7EF;
        --card-hover: #EAE0D1;
        --surface: #FBF7EF;
        --surface-2: #EAE0D1;
        --text: #24211D;
        --text-secondary: #6F675E;
        --text-tertiary: #8A8177;
        --blue: #70583E;
        --blue-bright: #5D4934;
        --blue-soft: #EFE5D7;
        --cyan: #70583E;
        --cyan-soft: #F0E7DA;
        --purple: #70583E;
        --green: #416C55;
        --green-soft: #E7EFE9;
        --orange: #95692F;
        --orange-soft: #F5EBDD;
        --red: #A34D48;
        --red-soft: #F6E7E5;
        --border: #D7CAB8;
        --border-strong: #C5B59F;
        --fill: #EAE0D1;
        --grid: #E5D9C9;
        --shadow: 0 1px 3px rgba(78, 61, 44, .08);
        --shadow-soft: 0 1px 2px rgba(78, 61, 44, .06);
        --glow-blue: none;
        --radius-xl: 10px;
        --radius-lg: 8px;
        --radius-md: 7px;
        --radius-sm: 5px;
        --mono: "SFMono-Regular", "Roboto Mono", Consolas, monospace;
        --display: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    html, body, [class*="css"] {
        font-family: var(--display) !important;
        letter-spacing: 0 !important;
    }

    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"] {
        background: var(--bg) !important;
        color: var(--text) !important;
    }

    header[data-testid="stHeader"] { background: var(--bg) !important; }

    .block-container {
        max-width: 1240px !important;
        padding: 28px 38px 48px !important;
    }

    .terminal-header {
        background: transparent !important;
        border: 0 !important;
        border-bottom: 1px solid var(--border) !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        padding: 0 0 18px !important;
        margin-bottom: 25px !important;
        min-height: 64px;
    }

    .brand-title {
        color: var(--text) !important;
        font-size: 18px !important;
        font-weight: 700 !important;
        letter-spacing: -.02em !important;
    }

    .brand-name { color: var(--text-secondary) !important; font-size: 12px !important; }
    .header-context { color: var(--text-tertiary) !important; font-size: 11px !important; }
    .header-actions { gap: 14px !important; }

    .live-pill,
    .live-pill.badge-muted {
        border: 0 !important;
        border-radius: 0 !important;
        background: transparent !important;
        color: var(--text-tertiary) !important;
        padding: 0 !important;
        font-size: 11px !important;
    }

    .live-pill.badge-muted {
        border-left: 1px solid var(--border) !important;
        padding-left: 14px !important;
    }

    .status-dot, .live-dot { width: 6px !important; height: 6px !important; }

    [data-testid="stForm"] {
        background: transparent !important;
        border: 0 !important;
        padding: 0 !important;
        margin-bottom: 28px !important;
        max-width: 980px !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }

    .stTextInput input,
    .stSelectbox > div > div {
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 7px !important;
        color: var(--text) !important;
        min-height: 46px !important;
        height: 46px !important;
        font-size: 14px !important;
        box-shadow: var(--shadow) !important;
    }

    .stTextInput input:focus,
    .stTextInput input:focus-within {
        border-color: var(--blue) !important;
        box-shadow: 0 0 0 3px rgba(35, 89, 199, .12) !important;
    }

    .stTextInput input::placeholder { color: var(--text-tertiary) !important; }

    .stButton button {
        background: var(--card) !important;
        color: var(--text) !important;
        border: 1px solid var(--border) !important;
        border-radius: 7px !important;
        box-shadow: var(--shadow) !important;
        font-weight: 600 !important;
        min-height: 40px !important;
    }

    [data-testid="stMain"] [data-testid="stForm"] .stButton button,
    [data-testid="stMain"] [data-testid="stForm"] .stButton button[kind="secondary"] {
        background: var(--blue) !important;
        border-color: var(--blue) !important;
        color: #ffffff !important;
        border-radius: 7px !important;
    }

    .stButton button:hover {
        background: var(--card-hover) !important;
        border-color: var(--border-strong) !important;
        box-shadow: var(--shadow) !important;
    }

    [data-testid="stMain"] [data-testid="stForm"] .stButton button:hover,
    [data-testid="stMain"] [data-testid="stForm"] .stButton button[kind="secondary"]:hover {
        background: var(--blue-bright) !important;
        border-color: var(--blue-bright) !important;
    }

    [data-testid="stFormSubmitButton"] button,
    [data-testid="stFormSubmitButton"] button[kind="primary"],
    [data-testid="stFormSubmitButton"] button[kind="secondary"] {
        background: var(--blue) !important;
        border: 1px solid var(--blue) !important;
        color: #ffffff !important;
        border-radius: 7px !important;
        font-weight: 700 !important;
    }

    [data-testid="stFormSubmitButton"] button:hover {
        background: var(--blue-bright) !important;
        border-color: var(--blue-bright) !important;
    }

    /* One control language everywhere: neutral options, blue selected state. */
    div[data-testid="stSegmentedControl"] [role="radiogroup"],
    div[data-testid="stSegmentedControl"] [data-baseweb="button-group"],
    .stSegmentedControl [role="radiogroup"] {
        background: var(--surface-2) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        gap: 3px !important;
        padding: 3px !important;
    }

    div[data-testid="stSegmentedControl"] button,
    div[data-testid="stSegmentedControl"] [role="radio"],
    .stSegmentedControl button,
    .stSegmentedControl [role="radio"] {
        background: transparent !important;
        border: 0 !important;
        border-radius: 5px !important;
        color: var(--text-secondary) !important;
        font-size: 12px !important;
        font-weight: 650 !important;
        min-height: 32px !important;
        padding: 7px 11px !important;
        box-shadow: none !important;
        white-space: nowrap !important;
    }

    div[data-testid="stSegmentedControl"] button[aria-checked="true"],
    div[data-testid="stSegmentedControl"] button[aria-pressed="true"],
    div[data-testid="stSegmentedControl"] [role="radio"][aria-checked="true"],
    .stSegmentedControl button[aria-checked="true"],
    .stSegmentedControl [role="radio"][aria-checked="true"] {
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        color: var(--blue) !important;
        box-shadow: 0 1px 2px rgba(20, 32, 43, .08) !important;
    }

    div[data-testid="stSegmentedControl"] button:hover,
    .stSegmentedControl button:hover { color: var(--text) !important; background: #f8fafc !important; }

    /* Streamlit 1.50 renders some segmented controls as a bare BaseWeb group. */
    div[data-baseweb="button-group"],
    div[data-testid*="SegmentedControl"] [role="group"],
    div[data-testid*="Pills"] [role="group"] {
        background: var(--surface-2) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        padding: 3px !important;
    }

    div[data-baseweb="button-group"] button,
    div[data-testid*="SegmentedControl"] [role="group"] button,
    div[data-testid*="Pills"] [role="group"] button {
        background: var(--surface-2) !important;
        border: 1px solid transparent !important;
        border-radius: 5px !important;
        color: var(--text-secondary) !important;
        min-height: 32px !important;
        padding: 7px 11px !important;
    }

    div[data-baseweb="button-group"] button[aria-checked="true"],
    div[data-baseweb="button-group"] button[aria-pressed="true"],
    div[data-baseweb="button-group"] button[aria-selected="true"],
    div[data-baseweb="button-group"] button[data-selected="true"] {
        background: var(--card) !important;
        border-color: var(--border) !important;
        color: var(--blue) !important;
        box-shadow: 0 1px 2px rgba(20, 32, 43, .08) !important;
    }

    [data-testid="stSegmentedControl"],
    div[data-baseweb="button-group"] {
        width: max-content !important;
        max-width: 100% !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }

    .overview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }

    button[title="Refresh all market data"],
    button[title="Refresh price history"] {
        width: 40px !important;
        min-width: 40px !important;
        padding-left: 0 !important;
        padding-right: 0 !important;
        font-size: 19px !important;
        line-height: 1 !important;
    }

    .home {
        min-height: auto !important;
        padding: 56px 0 0 !important;
    }

    .home-lead { max-width: 690px !important; }

    .home-lead .hero-title {
        color: var(--text) !important;
        font-size: clamp(40px, 5vw, 58px) !important;
        line-height: 1.03 !important;
        letter-spacing: -.065em !important;
        max-width: 13ch !important;
        margin: 14px 0 19px !important;
    }

    .home-lead .hero-copy {
        color: var(--text-secondary) !important;
        font-size: 17px !important;
        line-height: 1.55 !important;
        max-width: 610px !important;
    }

    .eyebrow { color: var(--blue) !important; letter-spacing: .08em !important; }

    .home-steps {
        border-top: 1px solid var(--border) !important;
        gap: 0 !important;
        margin-top: 68px !important;
        padding-top: 22px !important;
    }

    .home-steps > div {
        min-height: 76px;
        padding: 0 28px 0 0;
        margin-right: 28px;
        border-right: 1px solid var(--border);
    }

    .home-steps > div:last-child { border-right: 0; margin-right: 0; }
    .home-steps .n { color: var(--text) !important; font-size: 13px !important; font-weight: 700 !important; }
    .home-steps p { color: var(--text-secondary) !important; font-size: 13px !important; }

    .source-line {
        max-width: 850px;
        border-top: 1px solid var(--border);
        padding-top: 13px;
        margin-top: 74px !important;
        color: var(--text-tertiary) !important;
        font-size: 11px !important;
    }

    .result-head {
        padding-bottom: 23px !important;
        margin-bottom: 22px !important;
        border-bottom: 1px solid var(--border) !important;
    }

    .result-head .hero-title { font-size: 34px !important; letter-spacing: -.045em !important; }
    .result-meta { color: var(--text-tertiary) !important; font-size: 12px !important; }
    .badge-row { gap: 0 !important; margin-top: 12px !important; }

    .source-chip {
        border: 0 !important;
        border-radius: 0 !important;
        background: transparent !important;
        color: var(--text-secondary) !important;
        padding: 0 !important;
        font-size: 11px !important;
    }

    .source-chip + .source-chip {
        border-left: 1px solid var(--border) !important;
        margin-left: 13px;
        padding-left: 13px !important;
    }

    .result-score b { color: var(--blue) !important; font-size: 38px !important; }
    .result-score em { color: var(--text-secondary) !important; letter-spacing: .03em !important; }

    .section-label {
        color: var(--text) !important;
        font-size: 16px !important;
        font-weight: 700 !important;
        margin: 30px 0 12px !important;
    }

    .section-label small { color: var(--text-tertiary) !important; font-size: 11px !important; }

    .metric-grid { gap: 12px !important; }

    .metric-card,
    .panel,
    .hero-card,
    .score-panel,
    .learn-card,
    .empty-state {
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        box-shadow: var(--shadow) !important;
    }

    .metric-card { padding: 17px 18px 15px !important; }
    .metric-card::before { display: none !important; }
    .metric-label { color: var(--text-tertiary) !important; font-size: 11px !important; letter-spacing: .035em !important; }
    .metric-value { color: var(--text) !important; font-family: var(--display) !important; font-size: 24px !important; letter-spacing: -.035em !important; }
    .metric-meta { color: var(--text-tertiary) !important; font-size: 11px !important; }

    .panel, .hero-card, .score-panel, .learn-card { padding: 21px 23px !important; }
    .panel-kicker { color: var(--text-secondary) !important; text-transform: none !important; letter-spacing: .015em !important; font-size: 12px !important; }
    .signal-copy { color: var(--text) !important; font-size: 20px !important; letter-spacing: -.025em !important; }
    .signal-limitations { color: var(--text-tertiary) !important; font-size: 11px !important; }

    .implied-line {
        background: var(--blue-soft) !important;
        border: 1px solid #d6e2fb !important;
        border-left: 3px solid var(--blue) !important;
        border-radius: 7px !important;
    }

    .implied-main { color: var(--text) !important; }
    .implied-sub { color: var(--text-secondary) !important; }
    .gbar-track { background: #e2e7ed !important; }
    .gbar-verdict, .note-box { background: #f7f9fb !important; border-color: var(--border) !important; color: var(--text-secondary) !important; }

    .risk-row { border-top-color: var(--grid) !important; }
    .risk-row strong { color: var(--text) !important; }
    .risk-row span { color: var(--text-secondary) !important; }

    [data-testid="stSegmentedControl"] {
        border-bottom: 1px solid var(--border) !important;
        margin: 24px 0 20px !important;
    }

    [data-testid="stSegmentedControl"] button {
        background: transparent !important;
        border: 0 !important;
        border-bottom: 2px solid transparent !important;
        border-radius: 0 !important;
        color: var(--text-tertiary) !important;
        font-size: 13px !important;
        font-weight: 600 !important;
        padding: 10px 13px !important;
    }

    [data-testid="stSegmentedControl"] button[aria-checked="true"],
    [data-testid="stSegmentedControl"] button[aria-pressed="true"] {
        background: transparent !important;
        border-bottom-color: var(--blue) !important;
        color: var(--text) !important;
    }

    div[data-testid="stDataFrame"],
    [data-testid="stLineChart"],
    .chart-wrap {
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        box-shadow: var(--shadow) !important;
    }

    .chart-wrap { padding: 10px 6px 3px !important; }
    .app-footer {
        background: transparent !important;
        border: 0 !important;
        border-top: 1px solid var(--border) !important;
        border-radius: 0 !important;
        color: var(--text-tertiary) !important;
        padding: 14px 0 !important;
        font-size: 11px !important;
    }

    div[data-baseweb="popover"], div[data-baseweb="menu"] {
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
    }

    div[data-baseweb="popover"] li, div[data-baseweb="menu"] li { color: var(--text) !important; }
    div[data-baseweb="popover"] li:hover, div[data-baseweb="menu"] li:hover { background: var(--fill) !important; }

    @media (max-width: 900px) {
        .block-container { padding: 20px 18px 36px !important; }
        .terminal-header { margin-bottom: 20px !important; }
        .home { padding-top: 38px !important; }
        .home-lead .hero-title { font-size: 42px !important; }
        .home-steps { margin-top: 48px !important; }
        .home-steps > div { padding-right: 0; margin: 0 0 18px; border-right: 0; border-bottom: 1px solid var(--border); padding-bottom: 17px; }
        .home-steps > div:last-child { border-bottom: 0; padding-bottom: 0; }
        .header-actions { justify-content: flex-start !important; }
        .result-head { align-items: flex-start !important; flex-direction: column !important; }
        .result-score { text-align: left !important; }
    }

    @media (max-width: 560px) {
        .block-container { padding-left: 14px !important; padding-right: 14px !important; }
        .home-lead .hero-title { font-size: 37px !important; }
        .home-lead .hero-copy { font-size: 15px !important; }
        .home-steps { grid-template-columns: 1fr !important; }
        .result-head .hero-title { font-size: 29px !important; }
        .metric-grid { grid-template-columns: 1fr 1fr !important; }
        .metric-card { padding: 14px !important; }
        .metric-value { font-size: 20px !important; }
    }

    .sec-status-strip {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 7px 10px;
        margin: -7px 0 22px;
        padding: 10px 13px;
        background: #f8fafc;
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text-secondary);
        font-size: 12px;
        line-height: 1.35;
    }

    .sec-status-strip .status-dot { margin-right: 1px; }
    .sec-status-title { color: var(--text); font-weight: 750; }
    .sec-status-copy { color: var(--text-secondary); }
    .sec-status-detail {
        margin-left: auto;
        color: var(--text-tertiary);
        font-size: 11px;
    }

    .quality-flags {
        margin: 10px 0 0 !important;
        color: var(--text-tertiary) !important;
        font-size: 11px !important;
        line-height: 1.4;
    }

    .methodology-panel,
    .data-quality-panel { background: var(--card) !important; }
    .methodology-panel p { max-width: 900px; }
    .methodology-panel code {
        color: var(--blue) !important;
        background: var(--blue-soft) !important;
        border-radius: 4px;
        padding: 2px 4px;
    }

    .whatif-note {
        background: #f8fafc !important;
        border: 1px solid var(--border) !important;
        border-left: 3px solid var(--blue) !important;
        border-radius: 8px !important;
        color: var(--text-secondary) !important;
        line-height: 1.5 !important;
    }

    .whatif-note b { color: var(--text) !important; }

    [data-testid="stCaptionContainer"] {
        color: var(--text-tertiary) !important;
        font-size: 11px !important;
    }

    [data-testid="stAlert"] {
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        background: #fffdf7 !important;
        color: var(--text-secondary) !important;
        box-shadow: none !important;
    }

    .stDownloadButton button {
        background: var(--card) !important;
        border: 1px solid var(--border) !important;
        color: var(--text) !important;
        border-radius: 7px !important;
    }

    .stDownloadButton button:hover {
        background: var(--card-hover) !important;
        border-color: var(--border-strong) !important;
    }

    [data-testid="stDataFrame"] { overflow: hidden !important; }

    .cmp-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        overflow: hidden;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: var(--card);
        color: var(--text);
        font-size: 12px;
    }

    .cmp-table th,
    .cmp-table td {
        padding: 11px 13px;
        border-bottom: 1px solid var(--border);
        text-align: left;
        vertical-align: middle;
    }

    .cmp-table th {
        color: var(--text-secondary);
        background: #f8fafc;
        font-size: 11px;
        font-weight: 700;
    }

    .cmp-table th:not(:first-child) { border-top: 3px solid var(--blue); }
    .cmp-table td:first-child { color: var(--text-secondary); font-weight: 650; }
    .cmp-table tr:last-child td { border-bottom: 0; }
    .cmp-table tr:hover td { background: #fbfcfd; }
    .cmp-name { display: block; color: var(--text); font-weight: 700; }
    .cmp-ticker { display: block; margin-top: 3px; color: var(--text-tertiary); font-size: 10px; font-weight: 500; }
    .cmp-swatch { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }

    @media (max-width: 700px) {
        .sec-status-strip { align-items: flex-start; }
        .sec-status-detail { width: 100%; margin-left: 17px; }
        .cmp-table { display: block; overflow-x: auto; white-space: nowrap; }
        .overview-grid { grid-template-columns: 1fr !important; }
        div[data-baseweb="button-group"] {
            display: flex !important;
            flex-wrap: wrap !important;
            justify-content: center !important;
        }
        div[data-baseweb="button-group"] button {
            font-size: 11px !important;
            padding-left: 8px !important;
            padding-right: 8px !important;
        }
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

    if display_currency and display_currency != currency and display_fx is None:
        return "N/A · FX unavailable"
    if display_currency and display_currency != currency:
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
    if analysis.get("solver_status") == "above_range":
        return ">120%/yr"
    if analysis.get("solver_status") == "below_range":
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
        main = f"Price implies {esc(req)} sales growth — outside the model range."
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
        return None
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
    empty = pd.DataFrame()
    try:
        stock = yf.Ticker(ticker)
        if kind == "financials":
            frame = stock.financials
            if frame is not None and not frame.empty:
                return frame
            return stock.get_income_stmt(freq="yearly")
        if kind == "balance":
            frame = stock.balance_sheet
            if frame is not None and not frame.empty:
                return frame
            return stock.get_balance_sheet(freq="yearly")
        if kind == "cashflow":
            frame = stock.cashflow
            if frame is not None and not frame.empty:
                return frame
            return stock.get_cash_flow(freq="yearly")
        if kind == "quarterly_cashflow":
            frame = stock.quarterly_cashflow
            if frame is not None and not frame.empty:
                return frame
            return stock.get_cash_flow(freq="quarterly")
        if kind == "estimate":
            return stock.revenue_estimate
    except Exception:
        return None if kind == "estimate" else empty
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
        "symbol": quote.get("symbol") or quote.get("ticker"),
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
        "exchange": quote.get("exchange") or quote.get("exchangeName") or quote.get("fullExchangeName"),
        "exchangeTimezoneName": quote.get("exchangeTimezoneName"),
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
        "totalRevenue": first_value(quote, "totalRevenue", "totalRevenueTTM"),
        "grossProfit": first_value(quote, "grossProfits", "grossProfit"),
        "operatingIncome": first_value(quote, "operatingIncome"),
        "netIncomeToCommon": first_value(quote, "netIncomeToCommon", "netIncome"),
        "ebitda": first_value(quote, "ebitda", "normalized EBITDA"),
        "operatingCashflow": first_value(quote, "operatingCashflow", "operatingCashFlow"),
        "totalCash": first_value(quote, "totalCash"),
        "totalDebt": first_value(quote, "totalDebt"),
        "totalAssets": first_value(quote, "totalAssets"),
        "totalStockholderEquity": first_value(quote, "totalStockholderEquity"),
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
    stock = yf.Ticker(ticker)
    profile = {}
    try:
        profile = stock.get_info() or {}
    except Exception:
        try:
            profile = stock.info or {}
        except Exception:
            profile = {}
    try:
        fast = dict(stock.fast_info)
        price = first_value(fast, "lastPrice", "last_price", "regularMarketPrice", "currentPrice")
        prev = first_value(fast, "previousClose", "previous_close", "regularMarketPreviousClose")
        mcap = first_value(fast, "marketCap", "market_cap")
    except Exception:
        fast = {}
        price = None
        prev = None
        mcap = None

    quote = dict(profile)
    quote.update(
        {
            "symbol": quote.get("symbol") or ticker,
            "currency": fast.get("currency") or quote.get("currency"),
            "financialCurrency": fast.get("currency") or quote.get("financialCurrency") or quote.get("currency"),
            "regularMarketPrice": price if price is not None else quote.get("regularMarketPrice"),
            "currentPrice": price if price is not None else quote.get("currentPrice"),
            "regularMarketPreviousClose": prev if prev is not None else quote.get("regularMarketPreviousClose"),
            "previousClose": prev if prev is not None else quote.get("previousClose"),
            "marketCap": mcap if mcap is not None else quote.get("marketCap"),
            "exchange": fast.get("exchange") or fast.get("fullExchangeName") or quote.get("exchange"),
        }
    )
    name = KNOWN_NAMES.get(ticker)
    if name:
        quote["shortName"] = quote.get("shortName") or name
        quote["longName"] = quote.get("longName") or name
    if profile or price is not None or mcap is not None:
        return quote

    try:
        history = stock.history(period="5d", auto_adjust=True, timeout=10)
        if history is not None and not history.empty and "Close" in history.columns:
            close = history["Close"].dropna()
            if not close.empty:
                price = float(close.iloc[-1])
                name = KNOWN_NAMES.get(ticker) or ticker
                return {
                    "symbol": ticker,
                    "regularMarketPrice": price,
                    "currentPrice": price,
                    "exchange": None,
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
    errors = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {
            pool.submit(_quote_live, ticker): "quote",
            pool.submit(_yf_part, ticker, "financials"): "financials",
            pool.submit(_yf_part, ticker, "balance"): "balance",
            pool.submit(_yf_part, ticker, "cashflow"): "cashflow",
            pool.submit(_yf_part, ticker, "quarterly_cashflow"): "quarterly_cashflow",
            pool.submit(_yf_part, ticker, "estimate"): "estimate",
        }
        for fut in as_completed(futs):
            kind = futs[fut]
            try:
                parts[kind] = fut.result()
            except Exception as exc:
                parts[kind] = None if kind == "estimate" else pd.DataFrame()
                errors.append(f"Yahoo Finance {kind} unavailable ({type(exc).__name__})")
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
        "quarterly_cashflow": parts.get("quarterly_cashflow") if parts.get("quarterly_cashflow") is not None else pd.DataFrame(),
        "history": pd.DataFrame(),
        "revenue_estimate": parts.get("estimate"),
        "errors": errors,
        "last_refreshed": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_price_history(ticker, period="1y"):
    ticker = normalize_ticker(ticker)
    try:
        history = yf.Ticker(ticker).history(period=period, auto_adjust=True, timeout=10)
        return history if history is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def normalize_ticker(symbol):
    return str(symbol or "").upper().strip()


def ticker_format_ok(symbol):
    # Company tickers can contain dots or hyphens; index/futures syntax is not
    # accepted as a company ticker fallback.
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", symbol or ""))


COMPANY_ONLY_MESSAGE = (
    "TSRP covers listed company stocks only. Indexes, commodities, currencies, funds, and other instruments are not supported."
)
NON_EQUITY_QUOTE_TYPES = {
    "INDEX",
    "ETF",
    "MUTUALFUND",
    "FUND",
    "COMMODITY",
    "CURRENCY",
    "CRYPTOCURRENCY",
    "FUTURE",
    "OPTION",
    "WARRANT",
    "BOND",
    "CONTRACT",
    "ECNQUOTE",
}


def non_equity_reason(symbol, quote_type=None):
    """Return a reason for rejecting a non-company instrument, if obvious."""
    symbol = normalize_ticker(symbol)
    quote_type = str(quote_type or "").upper().replace(" ", "")
    if quote_type in NON_EQUITY_QUOTE_TYPES:
        return quote_type
    if symbol.startswith("^"):
        return "INDEX"
    if symbol.endswith(("=F", "=X")) or "=" in symbol:
        return "DERIVATIVE"
    if re.search(r"-(USD|EUR|GBP|JPY|BTC|ETH)$", symbol):
        return "CRYPTOCURRENCY"
    return None


def company_symbol_allowed(symbol, quote_type=None):
    return not non_equity_reason(symbol, quote_type)


def yahoo_data_is_valid(yahoo_data, ticker=None):
    if not yahoo_data:
        return False
    info = yahoo_data.get("info") or {}
    fast_info = yahoo_data.get("fast_info") or {}
    symbol = normalize_ticker(ticker) or info.get("symbol") or yahoo_data.get("symbol")
    if symbol and not company_symbol_allowed(symbol, info.get("quoteType")):
        return False
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
    for row in rows or []:
        symbol = str(row.get("symbol") or "").strip()
        quote_type = str(row.get("quoteType") or "").upper()
        if not symbol or symbol in seen or not company_symbol_allowed(symbol, quote_type):
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


LOCAL_SEARCH_NAMES = {
    "AAPL": "Apple Inc.",
    "APLE": "Apple Hospitality REIT, Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "GOOGL": "Alphabet Inc.",
    "META": "Meta Platforms, Inc.",
    "AMZN": "Amazon.com, Inc.",
    "TSLA": "Tesla, Inc.",
    "SAP": "SAP SE",
    "ASML": "ASML Holding N.V.",
    "TM": "Toyota Motor Corporation",
    "SONY": "Sony Group Corporation",
    "BABA": "Alibaba Group Holding Limited",
    "NVS": "Novartis AG",
    "SHEL": "Shell plc",
    "BP": "BP p.l.c.",
    "JPM": "JPMorgan Chase & Co.",
    "V": "Visa Inc.",
    "MA": "Mastercard Incorporated",
    "WMT": "Walmart Inc.",
    "COST": "Costco Wholesale Corporation",
    "KO": "The Coca-Cola Company",
    "PEP": "PepsiCo, Inc.",
    "DIS": "The Walt Disney Company",
    "AMD": "Advanced Micro Devices, Inc.",
    "AVGO": "Broadcom Inc.",
    "ORCL": "Oracle Corporation",
    "CRM": "Salesforce, Inc.",
    "ADBE": "Adobe Inc.",
    "UBER": "Uber Technologies, Inc.",
    "ABNB": "Airbnb, Inc.",
    "PYPL": "PayPal Holdings, Inc.",
    "SHOP": "Shopify Inc.",
    "PLTR": "Palantir Technologies Inc.",
    "BA": "The Boeing Company",
    "IBM": "International Business Machines Corporation",
    "CSCO": "Cisco Systems, Inc.",
    "QCOM": "QUALCOMM Incorporated",
    "TSM": "Taiwan Semiconductor Manufacturing Company Limited",
    "XOM": "Exxon Mobil Corporation",
    "CVX": "Chevron Corporation",
    "JNJ": "Johnson & Johnson",
    "PG": "The Procter & Gamble Company",
    "HD": "The Home Depot, Inc.",
    "MCD": "McDonald's Corporation",
    "NKE": "NIKE, Inc.",
    "SBUX": "Starbucks Corporation",
}


def local_company_search(query):
    """Return name/symbol matches when Yahoo's search endpoint is unavailable."""
    needle = str(query or "").strip().lower()
    if not needle:
        return []
    directory = dict(LOCAL_SEARCH_NAMES)
    for alias, symbol in NAME_ALIASES.items():
        directory.setdefault(symbol, alias.title())
    hits = []
    for symbol, name in directory.items():
        if needle not in name.lower() and needle not in symbol.lower():
            continue
        hits.append(
            {
                "symbol": symbol,
                "name": name,
                "type": "Equity",
                "exchange": "",
                "sector": "",
                "industry": "",
                "quote_type": "EQUITY",
            }
        )
    hits.sort(key=lambda hit: (not hit["symbol"].lower().startswith(needle), not hit["name"].lower().startswith(needle), hit["name"]))
    return hits[:16]


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
    type_rank = {"EQUITY": 0}

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
    live_results = _yahoo_search_live(query)
    if live_results:
        return live_results
    return local_company_search(query)


def resolve_company_query(query):
    raw = str(query or "").strip()
    if not raw:
        return None, "Type a company name or ticker.", []

    alias = lookup_alias(raw)
    if alias:
        if company_symbol_allowed(alias):
            return alias, None, []
        return None, COMPANY_ONLY_MESSAGE, []

    as_ticker = normalize_ticker(raw)
    hits = search_companies(raw)

    symbol_hits = [hit for hit in hits if hit["symbol"].upper() == as_ticker]
    if symbol_hits:
        hit = symbol_hits[0]
        if not company_symbol_allowed(hit["symbol"], hit.get("quote_type")):
            return None, COMPANY_ONLY_MESSAGE, []
        return hit["symbol"], None, []

    if not hits:
        if ticker_format_ok(as_ticker) and company_symbol_allowed(as_ticker):
            return as_ticker, None, []
        if non_equity_reason(as_ticker):
            return None, COMPANY_ONLY_MESSAGE, []
        return None, f"No company found for “{raw}”. Try the company name or ticker.", []

    if len(hits) == 1:
        if not company_symbol_allowed(hits[0]["symbol"], hits[0].get("quote_type")):
            return None, COMPANY_ONLY_MESSAGE, []
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
    sec_period = (analysis.get("sec_provenance") or {}).get("period", "N/A")
    as_of = analysis.get("last_refreshed", "N/A")
    quality = analysis.get("confidence", "N/A")
    rows = []

    def add(metric, value, unit="", currency="", period="Latest available", source="", status="Available", notes=""):
        rows.append([metric, value, unit, currency, period, source or "Unavailable", as_of, status, notes])

    add("Company", company_name, source="Yahoo Finance", notes="Company context")
    add("Ticker", ticker, source="Yahoo Finance", notes="Search result")
    add("Sector", sector, source="Yahoo Finance", notes="May be unavailable for some listings")
    add("Industry", industry, source="Yahoo Finance", notes="May be unavailable for some listings")
    add("Current price", money(analysis["price"], trading_currency, display_currency, display_fx_trading), "currency", display_currency, "Latest quote", "Yahoo Finance", "Available" if analysis.get("price") is not None else "Missing", "Trading currency: " + trading_currency)
    add("Market capitalization", money(analysis["market_cap"], trading_currency, display_currency, display_fx_trading), "currency", display_currency, "Latest quote", "Yahoo Finance", "Available" if analysis.get("market_cap") is not None else "Missing", "Trading currency: " + trading_currency)
    add("Enterprise value", money(analysis["enterprise_value"], reporting_currency, display_currency, display_fx_reporting), "currency", display_currency, "Latest quote", "Yahoo Finance / derived", "Available" if analysis.get("enterprise_value") is not None else "Unavailable", "Requires compatible currency conversion")
    add("Revenue", money(analysis["revenue"], reporting_currency, display_currency, display_fx_reporting), "currency", display_currency, sec_period if analysis.get("has_sec") else "Latest annual", analysis.get("sources", {}).get("Revenue"), "Available" if analysis.get("revenue") is not None else "Missing", "Reporting currency: " + reporting_currency)
    add("Free cash flow", money(analysis["free_cash_flow"], reporting_currency, display_currency, display_fx_reporting), "currency", display_currency, "Latest compatible annual / TTM", analysis.get("sources", {}).get("Free Cash Flow"), "Available" if analysis.get("free_cash_flow") is not None else "Unavailable", "OCF minus capex where compatible")
    add("Cash", money(analysis["cash"], reporting_currency, display_currency, display_fx_reporting), "currency", display_currency, sec_period if analysis.get("has_sec") else "Latest balance sheet", analysis.get("sources", {}).get("Cash"), "Available" if analysis.get("cash") is not None else "Missing", "Reporting currency: " + reporting_currency)
    add("Debt", money(analysis["debt"], reporting_currency, display_currency, display_fx_reporting), "currency", display_currency, sec_period if analysis.get("has_sec") else "Latest balance sheet", analysis.get("sources", {}).get("Debt"), "Available" if analysis.get("debt") is not None else "Missing", "Reporting currency: " + reporting_currency)
    add("Historical revenue growth", percent(analysis["historical_growth"]), "% CAGR", "", f"{analysis.get('historical_growth_years') or 0}-year window", "Yahoo Finance / SEC EDGAR", "Available" if analysis.get("historical_growth") is not None else "Missing", "Historical evidence, not a forecast")
    add("Required revenue growth", required_growth_label(analysis), "% per year", "", "10-year model", "TSRP reverse DCF", "Derived" if analysis.get("required_growth") is not None else "Unavailable", "Not a precise rate when outside solver range")
    add("Analyst consensus growth", percent(analysis.get("consensus_growth")), "% per year", "", "Next fiscal year", "Yahoo Finance estimates", "Estimated" if analysis.get("consensus_growth") is not None else "Unavailable", "Near-term comparison only")
    add("FCF margin", percent(analysis.get("fcf_margin")), "%", "", sec_period if analysis.get("has_sec") else "Latest compatible period", analysis.get("sources", {}).get("Free Cash Flow"), "Available" if analysis.get("fcf_margin") is not None else "Missing", "Free cash flow divided by revenue")
    add("Business Quality", score(analysis["business_quality"]), "0–100 heuristic", "", "Current analysis", "TSRP model", "Derived", "Sector benchmark dashboard")
    add("Financial Strength", score(analysis["financial_strength"]), "0–100 heuristic", "", "Current analysis", "TSRP model", "Derived", "Balance-sheet and cash-flow dashboard")
    add("Evidence Quality", score(analysis["reality_score"]), "0–100 heuristic", "", "Current analysis", "TSRP model", quality, "Not a probability or recommendation")
    add("Discount rate", percent(analysis.get("discount_rate")), "%", reporting_currency, "Model assumption", "TSRP model", "Assumption", "Currency-aware heuristic rate")
    add("Terminal growth", percent(analysis.get("terminal_growth")), "%", reporting_currency, "Model assumption", "TSRP model", "Assumption", "Long-run model assumption")
    add("Data confidence", quality, "label", "", "Current analysis", "TSRP coverage", quality, "Based on field coverage and quality flags")
    add(
        "SEC EDGAR status",
        analysis.get("sec_status", "N/A"),
        "status",
        "",
        sec_period,
        "SEC EDGAR",
        "Available" if analysis.get("has_sec") else "Status disclosed",
        "Live annual company facts are used when coverage is available; unavailable facts remain N/A.",
    )
    add("Data freshness", analysis.get("freshness", "N/A"), "status", "", "Current analysis", "TSRP", "Available", "Cached snapshot status")
    add("Educational disclaimer", EDUCATIONAL_DISCLAIMER, "text", "", "Export", "TSRP", "Required", "Include with any shared output")
    return pd.DataFrame(rows, columns=["Metric", "Value", "Unit", "Currency", "Period", "Data source", "As of", "Status", "Notes"])


def export_payload(analysis, company_name, ticker, sector, industry, display_currency):
    """Build a JSON-safe export with assumptions, provenance, and limitations."""
    fields = {
        "company": company_name,
        "ticker": ticker,
        "sector": sector,
        "industry": industry,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_refreshed": analysis.get("last_refreshed"),
        "freshness": analysis.get("freshness"),
        "display_currency": display_currency,
        "reporting_currency": analysis.get("reporting_currency"),
        "trading_currency": analysis.get("trading_currency"),
        "confidence": analysis.get("confidence"),
        "sec_status": analysis.get("sec_status"),
        "sources": analysis.get("sources", {}),
        "provenance": analysis.get("sec_provenance", {}),
        "model_assumptions": {
            "forecast_years": FORECAST_YEARS,
            "discount_rate": analysis.get("discount_rate"),
            "terminal_growth": analysis.get("terminal_growth"),
            "fcf_margin_used": analysis.get("model_fcf_margin"),
            "solver_status": analysis.get("solver_status"),
            "solver_range": [analysis.get("solver_low"), analysis.get("solver_high")],
        },
        "results": {
            key: analysis.get(key)
            for key in (
                "price", "market_cap", "enterprise_value", "revenue", "free_cash_flow", "cash", "debt",
                "fcf_margin", "historical_growth", "historical_growth_years", "consensus_growth",
                "required_growth", "reality_score", "business_quality", "market_expectations", "financial_strength",
            )
        },
        "quality_flags": analysis.get("quality_flags", []),
        "limitations": [
            "Scores are heuristic dashboards, not calibrated probabilities.",
            "Analyst consensus is a near-term comparison and is not equivalent to the multi-year reverse-DCF path.",
            "Missing or unavailable data is not treated as positive evidence.",
        ],
        "disclaimer": EDUCATIONAL_DISCLAIMER,
    }
    return json.dumps(fields, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def render_data_quality(analysis):
    flags = analysis.get("quality_flags") or []
    flag_text = " · ".join(label for label, _ in flags[:4]) or "No material coverage warnings"
    provenance = analysis.get("sec_provenance") or {}
    period = provenance.get("period", "N/A")
    filed = provenance.get("filed", "N/A")
    render_html(
        f'<div class="panel data-quality-panel">'
        f'<h3>Source and reliability</h3>'
        f'<div class="row"><span>Confidence</span><b>{esc(analysis.get("confidence", "N/A"))}</b></div>'
        f'<div class="row"><span>Market data source</span><b>{esc("Available" if not analysis.get("yahoo_errors") else "Partly unavailable")}</b></div>'
        f'<div class="row"><span>Company filings (SEC EDGAR)</span><b>{esc(analysis.get("sec_status", "N/A"))}</b></div>'
        f'<div class="row"><span>Latest filing period</span><b>{esc(f"{period} · filed {filed}" if provenance else "N/A")}</b></div>'
        f'<div class="row"><span>Freshness</span><b>{esc(analysis.get("freshness", "N/A"))}</b></div>'
        f'<div class="row"><span>Last refresh</span><b>{esc(analysis.get("last_refreshed", "N/A"))}</b></div>'
        f'<div class="source-line">{esc(flag_text)}</div>'
        f'</div>'
    )


def render_sec_status_strip(analysis):
    """Keep SEC provenance visible on every company view without fabricating facts."""
    has_sec = bool(analysis.get("has_sec"))
    status = analysis.get("sec_status", "N/A")
    if has_sec:
        detail = "Annual filing facts are live for this company."
    else:
        detail_by_kind = {
            "not_configured": "Live annual filing facts require a monitored SEC_USER_AGENT contact identity.",
            "not_covered": "SEC EDGAR does not publish company facts for this ticker.",
            "rate_limited": "SEC EDGAR temporarily rate-limited the company-facts request.",
            "timeout": "SEC EDGAR did not respond before the request timed out.",
            "network_error": "SEC EDGAR could not be reached for this request.",
            "parse_error": "SEC EDGAR returned data TSRP could not parse.",
            "http_error": "SEC EDGAR returned an HTTP error for this request.",
        }
        detail = detail_by_kind.get(
            SEC_STATE.get("kind"),
            "No usable SEC EDGAR company facts were returned.",
        )
    dot_class = "status-dot" if has_sec else "status-dot warn"
    render_html(
        f'<div class="sec-status-strip">'
        f'<span class="{dot_class}" aria-hidden="true"></span>'
        f'<span class="sec-status-title">SEC EDGAR</span>'
        f'<span class="sec-status-copy">{esc(status)}</span>'
        f'<span class="sec-status-detail">{esc(detail)}</span>'
        f'</div>'
    )


def render_methodology():
    render_html(
        f'''<div class="panel methodology-panel">
<h3>How TSRP works</h3>
<p><b>The research signal.</b> TSRP compares what the current price appears to require with past performance, analyst estimates, operating quality, and balance-sheet evidence.</p>
<p><b>Growth required by the current price.</b> The detailed price model projects revenue for {FORECAST_YEARS} years, converts revenue into free cash flow using an evidence-based margin, and finds the growth path that matches the current company value. If important inputs are missing or outside the supported range, TSRP shows N/A or a disclosed bound.</p>
<p><b>How to read the score.</b> Business quality, financial strength, and growth comparisons are combined into a 0–100 research signal. Missing evidence lowers confidence; it is never treated as positive evidence. The score is not a probability, recommendation, target price, or expected return.</p>
<p><b>Sources and limits.</b> Yahoo Finance supplies market data, history, estimates, and supplemental fundamentals. SEC EDGAR is shown as the company-filing source; when filing data is unavailable, affected figures remain N/A and the reason stays visible. Reporting and trading currencies are kept separate; unavailable currency conversion blocks affected calculations.</p>
<p><b>Interpretation.</b> Past growth and analyst estimates are comparison points, not guarantees. TSRP cannot determine future returns, business quality beyond the selected evidence, accounting comparability, or whether any security is suitable for a person.</p>
<div class="source-line">{esc(EDUCATIONAL_DISCLAIMER)}</div>
</div>'''
    )


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_compare_analysis(symbol):
    try:
        data = fetch_yahoo_data(symbol)
        if not yahoo_data_is_valid(data, symbol):
            return None
        result = analyze_company(data, fetch_sec_companyfacts(symbol))
        info = data.get("info") or {}
        result["name"] = info.get("longName") or info.get("shortName") or symbol
        return result
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_sec_ticker_map():
    if not SEC_USER_AGENT or "@" not in SEC_USER_AGENT:
        SEC_STATE.update(kind="not_configured", detail="Live SEC EDGAR facts require a monitored contact identity")
        return pd.DataFrame(columns=["ticker", "cik", "company"])
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        response = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=15)
        response.raise_for_status()
    except requests.Timeout:
        SEC_STATE.update(kind="timeout", detail="SEC ticker map request timed out")
        return pd.DataFrame(columns=["ticker", "cik", "company"])
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        SEC_STATE.update(kind="rate_limited" if status == 429 else "http_error", detail=f"SEC ticker map returned HTTP {status or 'error'}")
        return pd.DataFrame(columns=["ticker", "cik", "company"])
    except requests.RequestException:
        SEC_STATE.update(kind="network_error", detail="SEC ticker map network request failed")
        return pd.DataFrame(columns=["ticker", "cik", "company"])

    rows = []
    for item in response.json().values():
        rows.append(
            {
                "ticker": item["ticker"].upper(),
                "cik": str(item["cik_str"]).zfill(10),
                "company": item["title"],
            }
        )

    SEC_STATE.update(kind="available", detail="SEC ticker map available")
    return pd.DataFrame(rows)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_sec_companyfacts(ticker):
    if not SEC_USER_AGENT or "@" not in SEC_USER_AGENT:
        SEC_STATE.update(kind="not_configured", detail="Live SEC EDGAR facts require a monitored contact identity")
        return None
    try:
        ticker_map = fetch_sec_ticker_map()
        match = ticker_map[ticker_map["ticker"] == ticker.upper()]

        if match.empty:
            SEC_STATE.update(kind="not_covered", detail="Ticker is not present in the SEC company map")
            return None

        cik = match.iloc[0]["cik"]
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

        response = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=20)
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("facts"):
            SEC_STATE.update(kind="parse_error", detail="SEC response did not contain company facts")
            return None
        SEC_STATE.update(kind="available", detail="SEC company facts available")
        return payload
    except requests.Timeout:
        SEC_STATE.update(kind="timeout", detail="SEC company facts request timed out")
        return None
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        SEC_STATE.update(kind="rate_limited" if status == 429 else "http_error", detail=f"SEC company facts returned HTTP {status or 'error'}")
        return None
    except requests.RequestException:
        SEC_STATE.update(kind="network_error", detail="SEC company facts network request failed")
        return None
    except (KeyError, TypeError, ValueError):
        SEC_STATE.update(kind="parse_error", detail="SEC response could not be parsed")
        return None


def sec_status(sec_facts):
    """Return a plain-language filing-data state without exposing request internals."""
    if sec_facts:
        return "Available for this company"
    if SEC_STATE["kind"] == "not_configured":
        return "Unavailable — company filing data is not connected"
    labels = {
        "not_covered": "Unavailable — no filing data for this company",
        "rate_limited": "Temporarily unavailable — filing source limit reached",
        "timeout": "Temporarily unavailable — filing source did not respond",
        "network_error": "Temporarily unavailable — filing source could not be reached",
        "parse_error": "Unavailable — filing data could not be read",
        "http_error": "Unavailable — filing source returned an error",
    }
    return labels.get(SEC_STATE["kind"], "Unavailable — no usable company filing data returned")


def sec_fact_provenance(companyfacts, tags, preferred_currencies):
    """Return the latest annual fact's source, unit, period, and filing date."""
    if not companyfacts:
        return {}
    fact_roots = companyfacts.get("facts", {}) or {}
    candidates = []
    for taxonomy, facts in fact_roots.items():
        if not isinstance(facts, dict):
            continue
        for tag in tags:
            item = facts.get(tag) or {}
            units = item.get("units", {}) or {}
            ordered_units = preferred_currencies + [unit for unit in units if unit not in preferred_currencies]
            for unit in ordered_units:
                annual = [
                    row for row in units.get(unit, [])
                    if row.get("form") in {"10-K", "20-F", "40-F"} and row.get("val") is not None
                ]
                if annual:
                    row = max(annual, key=lambda value: (value.get("filed", ""), value.get("end", "")))
                    candidates.append((row.get("filed", ""), row.get("end", ""), {
                        "source": "SEC EDGAR",
                        "taxonomy": taxonomy,
                        "tag": tag,
                        "unit": unit,
                        "currency": unit if unit not in {"USD", "EUR", "GBP", "JPY", "CNY", "KRW", "CAD", "AUD", "CHF", "INR"} else unit,
                        "period": row.get("end") or "N/A",
                        "filed": row.get("filed") or "N/A",
                        "form": row.get("form") or "N/A",
                    }))
                    break
    if not candidates:
        return {}
    return max(candidates, key=lambda value: (value[0], value[1]))[2]


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


def sec_latest_record(companyfacts, tags, preferred_currencies):
    """Return one annual SEC fact with its unit and period metadata."""
    if not companyfacts:
        return None
    fact_roots = companyfacts.get("facts", {}) or {}
    candidates = []
    for taxonomy, facts in fact_roots.items():
        if not isinstance(facts, dict):
            continue
        for tag in tags:
            item = facts.get(tag) or {}
            units = item.get("units", {}) or {}
            unit_order = preferred_currencies + [unit for unit in units if unit not in preferred_currencies]
            for unit in unit_order:
                annual = [
                    row for row in units.get(unit, [])
                    if row.get("form") in {"10-K", "20-F", "40-F"}
                    and row.get("val") is not None
                    and row.get("end")
                ]
                if not annual:
                    continue
                row = max(annual, key=lambda value: (value.get("end", ""), value.get("filed", "")))
                candidates.append({
                    "value": safe_float(row.get("val")),
                    "unit": unit,
                    "period": row.get("end"),
                    "filed": row.get("filed") or "N/A",
                    "fy": row.get("fy"),
                    "form": row.get("form") or "N/A",
                    "taxonomy": taxonomy,
                    "tag": tag,
                })
                break
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row.get("period", ""), row.get("filed", "")))


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
            # Never relabel an unconverted SEC value as the reporting currency.
            if fallback is None:
                return None, None
        else:
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
    yahoo_errors = list(yahoo_data.get("errors") or [])

    reporting_currency, trading_currency = detect_currencies(info, fast_info)
    sec_currencies = sec_currency_candidates(reporting_currency, trading_currency)
    sector = info.get("sector") or "Unknown sector"
    sector_model = get_sector_model(sector)
    rates = model_rates(reporting_currency, sector)
    discount_rate = rates["discount_rate"]
    terminal_growth = rates["terminal_growth"]

    price = get_quote_price(info, fast_info)
    market_cap = get_market_cap(info, fast_info)

    yahoo_revenue = latest_value(financials, ["Total Revenue", "Operating Revenue"]) or safe_float(info.get("totalRevenue"))
    yahoo_gross_profit = latest_value(financials, ["Gross Profit"]) or safe_float(info.get("grossProfit"))
    yahoo_operating_income = latest_value(financials, ["Operating Income"]) or safe_float(info.get("operatingIncome"))
    yahoo_net_income = latest_value(financials, ["Net Income", "Net Income Common Stockholders"]) or safe_float(info.get("netIncomeToCommon"))
    yahoo_ebitda = latest_value(financials, ["EBITDA", "Normalized EBITDA"]) or safe_float(info.get("ebitda"))

    yahoo_operating_cash_flow = latest_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]) or safe_float(info.get("operatingCashflow"))
    yahoo_capex = latest_value(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    yahoo_reported_fcf = latest_value(cashflow, ["Free Cash Flow"])
    yahoo_annual_fcf = compute_fcf(yahoo_operating_cash_flow, yahoo_capex, yahoo_reported_fcf)
    yahoo_info_fcf = safe_float(info.get("freeCashflow"))
    yahoo_ttm_fcf = compute_fcf(
        ttm_sum(quarterly_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]),
        ttm_sum(quarterly_cashflow, ["Capital Expenditure", "Capital Expenditures"]),
        ttm_sum(quarterly_cashflow, ["Free Cash Flow"]),
    )

    yahoo_cash = latest_value(balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]) or safe_float(info.get("totalCash"))
    yahoo_debt = latest_value(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"]) or safe_float(info.get("totalDebt"))
    yahoo_assets = latest_value(balance, ["Total Assets"]) or safe_float(info.get("totalAssets"))
    yahoo_equity = latest_value(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"]) or safe_float(info.get("totalStockholderEquity"))

    sec_revenue, sec_revenue_currency = sec_fact_values(sec_facts, SEC_TAGS["revenue"], sec_currencies)
    sec_revenue_record = sec_latest_record(sec_facts, SEC_TAGS["revenue"], sec_currencies)
    if sec_revenue_record:
        sec_revenue_latest = sec_revenue_record["value"]
        sec_revenue_currency = sec_revenue_record["unit"]
    else:
        sec_revenue_latest = safe_float(sec_revenue.iloc[0]) if not sec_revenue.empty else None

    sec_net_income = sec_latest(sec_facts, SEC_TAGS["net_income"], sec_currencies)
    sec_assets = sec_latest(sec_facts, SEC_TAGS["assets"], sec_currencies)
    sec_equity = sec_latest(sec_facts, SEC_TAGS["equity"], sec_currencies)
    sec_cash = sec_latest(sec_facts, SEC_TAGS["cash"], sec_currencies)
    sec_debt_value = sec_debt(sec_facts, sec_currencies)

    sec_ocf_record = sec_latest_record(sec_facts, SEC_TAGS["operating_cash_flow"], sec_currencies)
    sec_capex_record = sec_latest_record(sec_facts, SEC_TAGS["capex"], sec_currencies)
    sec_ocf = sec_ocf_record["value"] if sec_ocf_record else None
    sec_capex_value = sec_capex_record["value"] if sec_capex_record else None
    sec_fcf_period_matched = bool(
        sec_ocf_record and sec_capex_record
        and sec_ocf_record.get("period") == sec_capex_record.get("period")
    )
    sec_fcf_raw = compute_fcf(sec_ocf, sec_capex_value) if sec_fcf_period_matched else None
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
    if enterprise_value_trading is None and market_cap is not None and debt is not None and cash is not None:
        debt_trading = convert_amount(debt, reporting_currency, trading_currency)
        cash_trading = convert_amount(cash, reporting_currency, trading_currency)
        if debt_trading is not None and cash_trading is not None:
            enterprise_value_trading = market_cap + debt_trading - cash_trading

    enterprise_value = convert_amount(enterprise_value_trading, trading_currency, reporting_currency)
    if enterprise_value is None and market_cap_reporting is not None and debt is not None and cash is not None:
        enterprise_value = market_cap_reporting + debt - cash

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
    solver_status = "unavailable"
    solver_low = None
    solver_high = None
    if model_fcf_margin is not None:
        solver = solve_required_growth_detail(
            enterprise_value,
            revenue,
            model_fcf_margin,
            discount_rate,
            terminal_growth,
            start_margin=fcf_margin if fcf_margin is not None and fcf_margin > 0 else model_fcf_margin,
        )
        required_growth = solver["growth"]
        solver_status = solver["status"]
        solver_low = solver["low"]
        solver_high = solver["high"]
        growth_clamped = solver_status in {"below_range", "above_range"}

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
        quality_flags.append((f"Free cash flow margin is {fcf_margin:.0%} — growth required by price is not calculated because cash flow is negative", "warn"))
    if historical_growth is None:
        quality_flags.append(("No comparable revenue growth history — TSRP does not assume a value", "warn"))
    if (sec_ocf_record or sec_capex_record) and not sec_fcf_period_matched:
        quality_flags.append(("Company filing cash-flow numbers did not match the reported period — filing cash flow not used", "warn"))
    if cash is None or debt is None:
        quality_flags.append(("Balance sheet incomplete — leverage scored as missing evidence", "warn"))
    if growth_clamped:
        quality_flags.append(("Growth required by price is outside the tested range — shown as a bound, not an exact rate", "warn"))
    if sector == "Real Estate":
        quality_flags.append(("Real-estate caveat: this view uses free cash flow and earnings, not property-specific measures", "warn"))
    if rates["used_fallback_currency"]:
        quality_flags.append(("Unknown reporting currency — USD rate world used as a disclosed fallback", "warn"))
    if not SEC_USER_AGENT or "@" not in SEC_USER_AGENT:
        quality_flags.append(("Company filing data is unavailable in this session", "warn"))
    elif not sec_facts:
        quality_flags.append(("SEC EDGAR did not return usable company facts", "warn"))
    if yahoo_errors:
        quality_flags.append(("Some Yahoo Finance endpoints were unavailable", "warn"))
    if market_cap is not None and market_cap_reporting is None:
        quality_flags.append((f"FX unavailable for {trading_currency}→{reporting_currency} — valuation blocked", "bad"))

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
    elif not sec_facts and confidence == "High":
        confidence = "Medium"
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

    final_score = reality_score(
        business_quality,
        financial_strength,
        historical_growth,
        required_growth,
        consensus_growth,
        growth_clamped=growth_clamped or model_fcf_refused,
    )

    revenue_provenance = sec_fact_provenance(sec_facts, SEC_TAGS["revenue"], sec_currencies)
    revenue_source_label = revenue_source
    if revenue_source and str(revenue_source).startswith("SEC") and revenue_provenance:
        revenue_source_label = (
            f"SEC EDGAR · {revenue_provenance.get('form', 'annual')} · "
            f"period {revenue_provenance.get('period', 'N/A')} · filed {revenue_provenance.get('filed', 'N/A')} · "
            f"unit {revenue_provenance.get('unit', 'N/A')}"
        )

    sources = {
        "Revenue": revenue_source_label,
        "Net Income": net_income_source,
        "Assets": assets_source,
        "Equity": equity_source,
        "Cash": cash_source,
        "Debt": debt_source,
        "Free Cash Flow": fcf_source or "Unavailable",
        "Price / Market Data": "Yahoo Finance",
        "Reporting Currency": f"Yahoo Finance ({reporting_currency})",
        "Trading Currency": f"Yahoo Finance ({trading_currency})",
        "SEC EDGAR status": sec_status(sec_facts),
        "Yahoo Finance status": "Available" if not yahoo_errors else "; ".join(yahoo_errors),
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
        "net_income": net_income,
        "assets": total_assets,
        "equity": equity,
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
        "solver_status": solver_status,
        "solver_low": solver_low,
        "solver_high": solver_high,
        "business_quality": business_quality,
        "market_expectations": market_expectations,
        "financial_strength": financial_strength,
        "gap": gap,
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
        "has_sec": bool(sec_facts),
        "sec_status": sec_status(sec_facts),
        "sec_provenance": revenue_provenance,
        "yahoo_errors": yahoo_errors,
        "last_refreshed": yahoo_data.get("last_refreshed"),
        "freshness": "Yahoo Finance snapshot; cached for up to 15 minutes",
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
        ("Evidence Quality", analysis["reality_score"]),
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


def provenance_values(analysis, display_currency, display_fx_reporting, display_fx_trading):
    """Return the displayed value for each provenance row."""
    reporting_currency = analysis.get("reporting_currency") or "USD"
    trading_currency = analysis.get("trading_currency") or reporting_currency
    return {
        "Revenue": money(analysis.get("revenue"), reporting_currency, display_currency, display_fx_reporting),
        "Net Income": money(analysis.get("net_income"), reporting_currency, display_currency, display_fx_reporting),
        "Assets": money(analysis.get("assets"), reporting_currency, display_currency, display_fx_reporting),
        "Equity": money(analysis.get("equity"), reporting_currency, display_currency, display_fx_reporting),
        "Cash": money(analysis.get("cash"), reporting_currency, display_currency, display_fx_reporting),
        "Debt": money(analysis.get("debt"), reporting_currency, display_currency, display_fx_reporting),
        "Free Cash Flow": money(analysis.get("free_cash_flow"), reporting_currency, display_currency, display_fx_reporting),
        "Price / Market Data": money(analysis.get("price"), trading_currency, display_currency, display_fx_trading),
        "Reporting Currency": reporting_currency,
        "Trading Currency": trading_currency,
        "SEC EDGAR status": analysis.get("sec_status", "N/A"),
        "Yahoo Finance status": "Available" if not analysis.get("yahoo_errors") else "Partial / endpoint errors",
    }


def conclusion_text(analysis):
    if analysis.get("model_fcf_refused"):
        return "The growth required by the current price is unavailable because free cash flow is missing or negative. TSRP does not invent a cash-flow margin to fill the gap."
    if analysis.get("growth_clamped"):
        return "The price implies a growth path outside the supported range. Treat the score as a warning, not as a precise growth estimate."
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
            font=dict(color="#5f6d7a", size=12),
            bgcolor="rgba(255,255,255,0)",
        ),
        xaxis=dict(tickfont=dict(color="#5f6d7a", size=12), linecolor="#d9e0e7"),
        yaxis=dict(
            range=[0, 100],
            showgrid=True,
            gridcolor="#e7ebf0",
            zeroline=False,
            tickfont=dict(color="#7a8793", size=11),
        ),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor="#d9e0e7", font_color="#14202b"),
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

if "compare_search" not in st.session_state:
    st.session_state.compare_search = ""

if "suppress_company_suggestions" not in st.session_state:
    st.session_state.suppress_company_suggestions = False

if "detail_section" not in st.session_state:
    st.session_state.detail_section = "Overview"

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
    if incoming and ticker_format_ok(incoming) and company_symbol_allowed(incoming):
        st.session_state.ticker = incoming
        if not st.session_state.company_search:
            st.session_state.company_search = incoming
    elif incoming:
        st.session_state.invalid_ticker = incoming
        st.session_state.ticker_error = COMPANY_ONLY_MESSAGE


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
            if st.button(label, key=f"{key_prefix}_{symbol}", width="stretch", type="secondary"):
                st.session_state.ticker = symbol
                st.session_state.pending_search = label
                st.session_state.search_hits = []
                st.session_state.ticker_error = None
                st.session_state.invalid_ticker = ""
                st.rerun()


def search_hit_label(hit):
    bits = [hit.get("name") or hit.get("symbol"), hit.get("symbol")]
    if hit.get("exchange"):
        bits.append(hit["exchange"])
    return "  ·  ".join(str(bit) for bit in bits if bit)


def render_company_suggestions(hits, key_prefix):
    """Render live, equity-only search matches and return the selected hit."""
    hits = [hit for hit in hits if hit]
    if not hits:
        return None
    render_html('<div class="watch-heading">Companies matching your search <small>Choose a listed stock</small></div>')
    selected = None
    for hit in hits[:8]:
        if st.button(
            search_hit_label(hit),
            key=f"{key_prefix}_{hit['symbol']}",
            width="stretch",
            type="secondary",
        ):
            selected = hit
            break
    st.caption("Select a company to continue. Non-company instruments are excluded.")
    return selected


PRIMARY_SECTIONS = [
    "TSRP Score",
    "Expectations vs Reality",
    "Valuation",
    "Catalysts & Risks",
    "What Changed?",
]
SNAPSHOT_METHODOLOGY_VERSION = "score-v1"


def render_product_system():
    render_html(
        """
<style>
:root {
  --tsrp-bg: #F3EBDD;
  --tsrp-surface: #FBF7EF;
  --tsrp-surface-2: #EAE0D1;
  --tsrp-surface-3: #EFE5D7;
  --tsrp-border: #D7CAB8;
  --tsrp-border-strong: #C5B59F;
  --tsrp-text: #24211D;
  --tsrp-muted: #6F675E;
  --tsrp-faint: #8A8177;
  --tsrp-accent: #70583E;
  --tsrp-accent-soft: rgba(112, 88, 62, .10);
  --tsrp-blue: #70583E;
  --tsrp-green: #416C55;
  --tsrp-yellow: #95692F;
  --tsrp-red: #A34D48;
  --tsrp-focus: #8B6F47;
  --tsrp-shadow: 0 1px 3px rgba(78, 61, 44, .08);
  --tsrp-shadow-soft: 0 1px 2px rgba(78, 61, 44, .06);
  --tsrp-mono: "SFMono-Regular", "Roboto Mono", Consolas, monospace;
}

html, body, [class*="css"], .stApp {
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
  color: var(--tsrp-text) !important;
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], header[data-testid="stHeader"] {
  background: var(--tsrp-bg) !important;
}
[data-testid="stMainBlockContainer"] {
  max-width: 1320px !important;
  padding: 34px 42px 56px !important;
}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"], #MainMenu, footer {
  display: none !important;
}
.app-wrap { max-width: 1240px; margin: 0 auto; color:var(--tsrp-text) !important; }
.app-wrap h1, .app-wrap h2, .app-wrap h3, .app-wrap h4, .app-wrap h5, .app-wrap h6 {
  color:var(--tsrp-text) !important;
}
.stMarkdown, .stMarkdown p, .stMarkdown span, .stMarkdown div { color:var(--tsrp-text); }
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4, .stMarkdown h5, .stMarkdown h6,
.section-intro h2, .score-hero-copy h2, .panel-new h3, .empty-new h3, .evidence-card h4 {
  color:var(--tsrp-text) !important;
}
.stTextInput input {
  background:var(--tsrp-surface-2) !important; color:var(--tsrp-text) !important;
  border-color:var(--tsrp-border-strong) !important;
}
.app-wrap input[type="text"], .app-wrap textarea, .app-wrap [data-baseweb="select"] > div {
  background:var(--tsrp-surface-2) !important; color:var(--tsrp-text) !important;
  border-color:var(--tsrp-border-strong) !important;
}
.product-header {
  display: flex; align-items: center; justify-content: space-between; gap: 22px;
  padding: 4px 0 24px; border-bottom: 1px solid var(--tsrp-border);
}
.brand-lockup-new { display:flex; align-items:center; gap:12px; min-width:190px; }
.brand-mark-new {
  width:34px; height:34px; display:grid; place-items:center; border-radius:10px;
  background:var(--tsrp-accent); color:#081311; font-weight:850; letter-spacing:-.08em;
}
.brand-copy-new { display:flex; flex-direction:column; gap:2px; }
.brand-copy-new strong { font-size:15px; letter-spacing:.08em; }
.brand-copy-new span { color:var(--tsrp-muted); font-size:11px; }
.header-status-new { color:var(--tsrp-muted); font-size:11px; text-align:right; line-height:1.5; }
.search-zone {
  display:flex; align-items:flex-end; gap:10px; margin:24px 0 18px;
  padding:14px; background:var(--tsrp-surface); border:1px solid var(--tsrp-border);
  border-radius:14px;
}
.search-zone > div:first-child { flex:1 1 auto; }
.search-zone [data-testid="stWidgetLabel"] { margin-bottom:5px !important; }
.search-zone [data-testid="stWidgetLabel"] p { color:var(--tsrp-muted) !important; font-size:11px !important; }
.search-zone input, .search-zone [data-baseweb="select"] > div {
  background:var(--tsrp-surface-2) !important; border-color:var(--tsrp-border-strong) !important;
  color:var(--tsrp-text) !important; min-height:42px !important; border-radius:9px !important;
}
.search-zone button, .tsrp-primary button {
  min-height:42px !important; border-radius:9px !important; font-weight:750 !important;
}
.search-zone button[kind="primary"], .tsrp-primary button {
  background:var(--tsrp-accent) !important; color:#071311 !important; border:0 !important;
}
.search-zone button[kind="primary"]:hover, .tsrp-primary button:hover {
  background:#86e8d9 !important; color:#071311 !important;
}
.section-nav-label { color:var(--tsrp-faint); font-size:10px; text-transform:uppercase; letter-spacing:.16em; margin:20px 0 8px; }
.section-nav [data-testid="stSegmentedControl"] { background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important; border-radius:10px !important; padding:4px !important; }
.section-nav [data-testid="stSegmentedControl"] button {
  color:var(--tsrp-muted) !important; background:transparent !important; border-radius:7px !important;
  font-size:12px !important; font-weight:700 !important; min-height:38px !important;
}
.section-nav [data-testid="stSegmentedControl"] button[aria-checked="true"],
.section-nav [data-testid="stSegmentedControl"] button[aria-pressed="true"] {
  background:var(--tsrp-surface-3) !important; color:var(--tsrp-text) !important;
  box-shadow:inset 0 0 0 1px var(--tsrp-border-strong) !important;
}
.section-nav [data-testid="stButtonGroup"] {
  background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important;
  border-radius:10px !important; padding:4px !important;
}
.section-nav [data-testid="stButtonGroup"] button {
  color:var(--tsrp-muted) !important; background:transparent !important; border-radius:7px !important;
  font-size:12px !important; font-weight:700 !important; min-height:38px !important;
}
.section-nav [data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] {
  background:var(--tsrp-surface-3) !important; color:var(--tsrp-text) !important;
  box-shadow:inset 0 0 0 1px var(--tsrp-border-strong) !important;
}
.section-nav-label + div [data-testid="stButtonGroup"] {
  background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important;
  border-radius:10px !important; padding:4px !important;
}
.section-nav-label + div [data-testid="stButtonGroup"] button {
  color:var(--tsrp-muted) !important; background:transparent !important; border-radius:7px !important;
  font-size:12px !important; font-weight:700 !important; min-height:38px !important;
}
.section-nav-label + div [data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] {
  background:var(--tsrp-surface-3) !important; color:var(--tsrp-text) !important;
}
[data-testid="stButtonGroup"] {
  background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important;
  border-radius:10px !important; padding:4px !important;
}
[data-testid="stButtonGroup"] button {
  color:var(--tsrp-muted) !important; background:transparent !important; border-radius:7px !important;
  font-size:12px !important; font-weight:700 !important; min-height:38px !important;
}
[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] {
  background:var(--tsrp-surface-3) !important; color:var(--tsrp-text) !important;
  box-shadow:inset 0 0 0 1px var(--tsrp-border-strong) !important;
}
[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_control"] {
  background:transparent !important; color:var(--tsrp-muted) !important;
}
[data-testid="stButtonGroup"] > div {
  background:transparent !important; color:var(--tsrp-text) !important;
}
.section-nav-fallback button { min-height:38px !important; font-size:11px !important; }
.company-strip {
  display:flex; align-items:center; justify-content:space-between; gap:18px; padding:24px 0 18px;
}
.company-strip h1 { margin:0; font-size:clamp(24px, 3vw, 38px); letter-spacing:-.045em; line-height:1.05; }
.company-strip p { margin:7px 0 0; color:var(--tsrp-muted); font-size:12px; line-height:1.55; }
.company-tags { display:flex; gap:6px; flex-wrap:wrap; margin-top:12px; }
.company-tag, .source-tag, .state-tag {
  display:inline-flex; align-items:center; gap:6px; border:1px solid var(--tsrp-border);
  border-radius:999px; padding:5px 9px; color:var(--tsrp-muted); font-size:10px; line-height:1;
  background:var(--tsrp-surface);
}
.company-tag strong, .source-tag strong { color:var(--tsrp-text); }
.compact-score {
  min-width:112px; padding:11px 13px; border:1px solid var(--tsrp-border-strong);
  border-radius:12px; background:var(--tsrp-surface); text-align:right;
}
.compact-score span { display:block; color:var(--tsrp-faint); font-size:9px; text-transform:uppercase; letter-spacing:.13em; }
.compact-score b { display:block; color:var(--tsrp-accent); font:700 25px/1.1 var(--tsrp-mono); margin-top:3px; }
.section-intro { display:flex; align-items:flex-end; justify-content:space-between; gap:18px; margin:30px 0 16px; }
.section-intro h2 { margin:0; font-size:24px; letter-spacing:-.035em; }
.section-intro p { max-width:560px; margin:5px 0 0; color:var(--tsrp-muted); font-size:12px; line-height:1.55; }
.section-kicker-new { color:var(--tsrp-accent); font-size:10px; text-transform:uppercase; letter-spacing:.16em; font-weight:800; margin-bottom:8px; }
.panel-new, .score-hero, .metric-card-new, .empty-new, .comparison-card, .evidence-card {
  background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important;
  border-radius:14px !important; box-shadow:0 12px 32px rgba(0,0,0,.12) !important;
}
.panel-new { padding:20px; }
.panel-new h3 { margin:0 0 10px; font-size:15px; letter-spacing:-.015em; }
.panel-new p { color:var(--tsrp-muted); font-size:12px; line-height:1.65; }
.score-hero { padding:28px; display:grid; grid-template-columns:minmax(0,1fr) 250px; gap:24px; }
.score-hero-copy h2 { margin:0; font-size:clamp(21px, 3vw, 31px); letter-spacing:-.04em; }
.score-hero-copy p { max-width:640px; color:var(--tsrp-muted); font-size:13px; line-height:1.65; }
.score-emblem { display:flex; flex-direction:column; justify-content:center; align-items:center; border-left:1px solid var(--tsrp-border); padding-left:24px; text-align:center; }
.score-emblem small { color:var(--tsrp-faint); text-transform:uppercase; letter-spacing:.16em; font-size:10px; }
.score-emblem b { color:var(--tsrp-accent); font:700 clamp(52px, 8vw, 76px)/1 var(--tsrp-mono); letter-spacing:-.08em; margin:8px 0; }
.score-emblem span { color:var(--tsrp-text); font-size:12px; font-weight:750; }
.score-note { margin-top:16px; padding:11px 13px; border-left:2px solid var(--tsrp-accent); background:var(--tsrp-accent-soft); color:var(--tsrp-muted); font-size:11px; line-height:1.55; border-radius:0 8px 8px 0; }
.metric-grid-new { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:14px 0; }
.metric-card-new { padding:15px; min-height:94px; }
.metric-card-new .label { color:var(--tsrp-faint); font-size:10px; text-transform:uppercase; letter-spacing:.1em; }
.metric-card-new .value { color:var(--tsrp-text); font:700 20px/1.2 var(--tsrp-mono); margin:9px 0 5px; overflow-wrap:anywhere; }
.metric-card-new .meta { color:var(--tsrp-muted); font-size:10px; line-height:1.45; }
.metric-card-new.accent { border-top:2px solid var(--tsrp-accent) !important; }
.metric-card-new.warn { border-top:2px solid var(--tsrp-yellow) !important; }
.metric-card-new.negative { border-top:2px solid var(--tsrp-red) !important; }
.score-driver-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-top:14px; }
.score-driver { background:var(--tsrp-surface); border:1px solid var(--tsrp-border); border-radius:12px; padding:14px; }
.score-driver .driver-score { display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-bottom:8px; }
.score-driver .driver-score strong { color:var(--tsrp-text); font:700 21px/1.1 var(--tsrp-mono); }
.score-driver .driver-score span { color:var(--tsrp-faint); font-size:9px; text-transform:uppercase; letter-spacing:.1em; }
.score-driver h4 { color:var(--tsrp-text); font-size:12px; margin:0 0 6px; }
.score-driver p { color:var(--tsrp-muted); font-size:10px; line-height:1.5; margin:0; }
.research-answer { background:var(--tsrp-surface); border:1px solid var(--tsrp-border); border-left:3px solid var(--tsrp-accent); border-radius:11px; padding:14px 16px; margin:14px 0; color:var(--tsrp-muted); font-size:12px; line-height:1.6; }
.research-answer b { color:var(--tsrp-text); }
.plain-list { display:grid; gap:0; }
.plain-list .data-line { padding:10px 0; }
.definition { color:var(--tsrp-faint); font-size:10px; line-height:1.5; margin-top:8px; }
.unavailable-note { color:var(--tsrp-muted); font-size:11px; line-height:1.5; }
.two-col-new { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin-top:14px; }
.three-col-new { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
.panel-title-row { display:flex; justify-content:space-between; align-items:baseline; gap:12px; margin-bottom:14px; }
.panel-title-row h3 { margin:0; }
.panel-title-row span { color:var(--tsrp-faint); font-size:10px; }
.score-table, .compare-table-new, .change-table {
  width:100%; border-collapse:collapse; font-size:11px;
}
.score-table th, .score-table td, .compare-table-new th, .compare-table-new td, .change-table th, .change-table td {
  padding:10px 8px; border-bottom:1px solid var(--tsrp-border); text-align:left; vertical-align:top;
}
.score-table th, .compare-table-new th, .change-table th {
  color:var(--tsrp-faint); text-transform:uppercase; letter-spacing:.08em; font-size:9px; font-weight:750;
}
.score-table td, .compare-table-new td, .change-table td { color:var(--tsrp-muted); }
.score-table td strong, .compare-table-new td strong, .change-table td strong { color:var(--tsrp-text); }
.score-table tr:last-child td, .compare-table-new tr:last-child td, .change-table tr:last-child td { border-bottom:0; }
.bar-line { margin:10px 0 14px; }
.bar-head { display:flex; justify-content:space-between; gap:10px; color:var(--tsrp-muted); font-size:11px; }
.bar-head b { color:var(--tsrp-text); font-family:var(--tsrp-mono); }
.bar-track-new { height:7px; border-radius:999px; background:var(--tsrp-surface-3); overflow:hidden; margin-top:7px; }
.bar-fill-new { height:100%; border-radius:999px; background:var(--tsrp-accent); }
.bar-fill-new.warn { background:var(--tsrp-yellow); }
.bar-fill-new.negative { background:var(--tsrp-red); }
.driver-list { display:grid; gap:8px; margin-top:12px; }
.driver-item { display:flex; gap:9px; padding:10px 11px; border:1px solid var(--tsrp-border); background:var(--tsrp-surface-2); border-radius:9px; }
.driver-item i { font-style:normal; font-size:12px; line-height:1.2; }
.driver-item strong { display:block; color:var(--tsrp-text); font-size:11px; }
.driver-item span { display:block; color:var(--tsrp-muted); font-size:10px; line-height:1.45; margin-top:2px; }
.data-line { display:flex; justify-content:space-between; gap:14px; padding:9px 0; border-bottom:1px solid var(--tsrp-border); font-size:11px; }
.data-line:last-child { border-bottom:0; }
.data-line span { color:var(--tsrp-muted); }
.data-line b { color:var(--tsrp-text); text-align:right; overflow-wrap:anywhere; }
.source-foot { color:var(--tsrp-faint); font-size:10px; line-height:1.5; margin-top:12px; }
.source-foot a, .evidence-card a { color:var(--tsrp-accent); text-decoration:none; }
.source-foot a:hover, .evidence-card a:hover { text-decoration:underline; }
.state-strip-new { display:flex; gap:9px; align-items:flex-start; padding:11px 13px; border:1px solid var(--tsrp-border); background:var(--tsrp-surface); border-radius:10px; margin-top:12px; color:var(--tsrp-muted); font-size:11px; line-height:1.5; }
.state-strip-new b { color:var(--tsrp-text); }
.state-dot-new { width:7px; height:7px; border-radius:50%; background:var(--tsrp-accent); margin-top:5px; flex:0 0 auto; }
.state-dot-new.warn { background:var(--tsrp-yellow); }
.empty-new { padding:31px 22px; text-align:center; }
.empty-new h3 { margin:0; font-size:16px; }
.empty-new p { max-width:520px; margin:9px auto 0; color:var(--tsrp-muted); font-size:12px; line-height:1.6; }
.evidence-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
.evidence-card { padding:17px; }
.evidence-card.catalyst { border-top:2px solid var(--tsrp-green) !important; }
.evidence-card.risk { border-top:2px solid var(--tsrp-red) !important; }
.evidence-card h4 { margin:0 0 7px; font-size:14px; }
.evidence-card p { color:var(--tsrp-muted); font-size:11px; line-height:1.55; margin:6px 0; }
.evidence-card .eyebrow-new { color:var(--tsrp-faint); font-size:9px; text-transform:uppercase; letter-spacing:.12em; }
.evidence-card .evidence-meta { margin-top:12px; padding-top:10px; border-top:1px solid var(--tsrp-border); color:var(--tsrp-faint); font-size:10px; line-height:1.5; }
.research-tools { margin-top:18px; }
.research-tools [data-testid="stExpander"] { background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important; border-radius:12px !important; }
.research-tools [data-testid="stExpander"] summary { color:var(--tsrp-text) !important; font-size:12px !important; }
.research-tools [data-testid="stExpander"] > div { border-top:1px solid var(--tsrp-border) !important; }
.stCaption, [data-testid="stCaptionContainer"] { color:var(--tsrp-faint) !important; font-size:10px !important; }
.stAlert { background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important; color:var(--tsrp-muted) !important; }
.stDownloadButton button, [data-testid="stFormSubmitButton"] button, .stButton button {
  border-radius:8px !important; border-color:var(--tsrp-border-strong) !important; color:var(--tsrp-text) !important;
  background:var(--tsrp-surface-2) !important; min-height:38px !important; font-size:11px !important;
}
.stDownloadButton button:hover, .stButton button:hover { border-color:var(--tsrp-accent) !important; background:var(--tsrp-surface-3) !important; }
.stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] {
  background:var(--tsrp-accent) !important; color:#071311 !important; border-color:var(--tsrp-accent) !important;
}
input, textarea, [data-baseweb="select"] > div {
  background:var(--tsrp-surface-2) !important; color:var(--tsrp-text) !important; border-color:var(--tsrp-border-strong) !important;
}
input::placeholder, textarea::placeholder { color:var(--tsrp-faint) !important; }
button:focus-visible, input:focus-visible, textarea:focus-visible, [role="button"]:focus-visible {
  outline:2px solid var(--tsrp-accent) !important; outline-offset:2px !important;
}
.app-footer-new { border-top:1px solid var(--tsrp-border); margin-top:34px; padding-top:15px; color:var(--tsrp-faint); font-size:10px; line-height:1.6; }
/* Warm research system: one palette, one type scale, and one control language. */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], header[data-testid="stHeader"] {
  background:var(--tsrp-bg) !important; color:var(--tsrp-text) !important;
  font-family:Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
}
body, .stApp { font-variant-numeric:tabular-nums; }
[data-testid="stMainBlockContainer"] { max-width:1320px !important; padding:32px 42px 56px !important; }
.stMarkdown, .stMarkdown p, .stMarkdown span, .stMarkdown div { font-family:Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4, .stMarkdown h5, .stMarkdown h6,
.stMarkdown button, .stMarkdown input, .stMarkdown textarea,
h1, h2, h3, h4, h5, h6, button, input, textarea, select {
  font-family:Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
}
.product-header { padding:6px 0 22px; border-bottom-color:var(--tsrp-border); }
.brand-mark-new { background:var(--tsrp-accent); color:var(--tsrp-surface) !important; }
.brand-copy-new strong, .company-strip h1, .section-intro h2, .score-hero-copy h2, .panel-new h3, .empty-new h3, .evidence-card h4 { font-weight:700 !important; }
.brand-copy-new span, .header-status-new, .company-strip p, .section-intro p, .panel-new p, .score-hero-copy p, .metric-card-new .meta, .source-foot, .evidence-card p, .evidence-card .evidence-meta { color:var(--tsrp-muted) !important; }
.search-zone, [data-testid="stExpander"], .research-tools [data-testid="stExpander"] { background:var(--tsrp-surface) !important; border-color:var(--tsrp-border) !important; box-shadow:0 1px 3px rgba(78,61,44,.08) !important; }
.stTextInput input, input, textarea, [data-baseweb="select"] > div {
  min-height:44px !important; height:44px !important; padding:0 14px !important; background:var(--tsrp-surface) !important; color:var(--tsrp-text) !important; border:1px solid var(--tsrp-border-strong) !important; border-radius:10px !important; font-family:Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important; font-size:13px !important; box-shadow:none !important;
}
.stTextInput input::placeholder, input::placeholder, textarea::placeholder { color:var(--tsrp-faint) !important; }
.stTextInput input:focus, input:focus, textarea:focus { border-color:var(--tsrp-focus) !important; box-shadow:0 0 0 3px rgba(139,111,71,.18) !important; outline:none !important; }
.stButton button, .stDownloadButton button, [data-testid="stFormSubmitButton"] button {
  min-height:44px !important; height:auto !important; padding:10px 16px !important; border:1px solid var(--tsrp-border-strong) !important; border-radius:10px !important; background:var(--tsrp-surface) !important; color:var(--tsrp-text) !important; font-family:Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important; font-size:12px !important; font-weight:600 !important; line-height:1.2 !important; white-space:nowrap !important; display:flex !important; align-items:center !important; justify-content:center !important; gap:8px !important; box-shadow:none !important; transform:none !important; transition:background-color .12s ease, border-color .12s ease, color .12s ease !important;
}
.stButton button:hover, .stDownloadButton button:hover { background:var(--tsrp-surface-2) !important; border-color:var(--tsrp-accent) !important; color:var(--tsrp-text) !important; transform:none !important; }
.stButton button:active, .stDownloadButton button:active { background:var(--tsrp-surface-3) !important; border-color:var(--tsrp-accent) !important; transform:none !important; }
.stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] { background:var(--tsrp-accent) !important; border-color:var(--tsrp-accent) !important; color:var(--tsrp-surface) !important; }
.stButton button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button[kind="primary"]:hover { background:#5D4934 !important; border-color:#5D4934 !important; color:var(--tsrp-surface) !important; }
.stButton button[kind="primary"]:active, [data-testid="stFormSubmitButton"] button[kind="primary"]:active { background:#4E3D2C !important; border-color:#4E3D2C !important; }
.stButton button:disabled, .stDownloadButton button:disabled { opacity:.52 !important; cursor:not-allowed !important; }
button:focus-visible, input:focus-visible, textarea:focus-visible, [role="button"]:focus-visible { outline:3px solid var(--tsrp-focus) !important; outline-offset:2px !important; }
[data-testid="stButtonGroup"] { background:var(--tsrp-surface) !important; border-color:var(--tsrp-border) !important; border-radius:12px !important; padding:5px !important; }
[data-testid="stButtonGroup"] > div { background:transparent !important; }
[data-testid="stButtonGroup"] button { min-height:42px !important; padding:10px 14px !important; border:0 !important; border-radius:8px !important; background:transparent !important; color:var(--tsrp-muted) !important; }
[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] { background:var(--tsrp-surface-3) !important; color:var(--tsrp-text) !important; box-shadow:inset 0 0 0 1px var(--tsrp-border-strong) !important; }
.section-nav-label { color:var(--tsrp-faint); margin:24px 0 10px; }
.company-strip { padding:28px 0 20px; }
.compact-score { background:var(--tsrp-surface) !important; border-color:var(--tsrp-border-strong) !important; }
.score-hero, .panel-new, .metric-card-new, .empty-new, .evidence-card { background:var(--tsrp-surface) !important; border-color:var(--tsrp-border) !important; box-shadow:0 1px 3px rgba(78,61,44,.08) !important; }
.score-hero { border-top:3px solid var(--tsrp-accent) !important; }
.score-emblem { background:var(--tsrp-surface-2); border-color:var(--tsrp-border) !important; border-radius:10px; }
.metric-grid-new { gap:12px; margin:18px 0; }
.metric-card-new { min-height:100px; padding:16px; }
.two-col-new, .evidence-grid { gap:16px; margin-top:18px; }
.panel-new { padding:20px; }
.score-table, .compare-table-new, .change-table { font-variant-numeric:tabular-nums; }
.score-table th, .compare-table-new th, .change-table th { color:var(--tsrp-faint) !important; }
.score-table td, .compare-table-new td, .change-table td { color:var(--tsrp-muted) !important; }
.score-table td strong, .compare-table-new td strong, .change-table td strong, .data-line b, .driver-item strong, .state-strip-new b { color:var(--tsrp-text) !important; }
.score-table th, .score-table td, .compare-table-new th, .compare-table-new td, .change-table th, .change-table td { border-bottom-color:var(--tsrp-border) !important; padding:12px 9px; }
.bar-track-new { background:var(--tsrp-surface-2); }
.driver-item, .state-strip-new { background:var(--tsrp-surface-2); border-color:var(--tsrp-border); }
.state-dot-new { background:var(--tsrp-green); }
.state-dot-new.warn { background:var(--tsrp-yellow); }
.stCaption, [data-testid="stCaptionContainer"] { color:var(--tsrp-muted) !important; }
.stAlert { background:var(--tsrp-surface) !important; border:1px solid var(--tsrp-border) !important; color:var(--tsrp-text) !important; border-radius:10px !important; }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p { color:var(--tsrp-text) !important; font-weight:600 !important; }
[data-testid="stExpander"] { border-radius:10px !important; }
@media (max-width:640px) {
  [data-testid="stMainBlockContainer"] { padding:20px 14px 40px !important; }
  .stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] { min-height:48px !important; width:100% !important; }
  .stButton button, .stDownloadButton button { min-height:48px !important; }
  .product-header { padding-bottom:18px; }
  .metric-grid-new { gap:10px; }
  .panel-new { padding:16px; }
  .score-hero { padding:20px 16px; }
  .score-table, .compare-table-new, .change-table { display:block; overflow-x:auto; white-space:nowrap; }
  [data-testid="stButtonGroup"] { width:100% !important; overflow-x:auto !important; }
  [data-testid="stButtonGroup"] button { flex:0 0 auto !important; white-space:nowrap !important; }
}
@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] { padding:26px 22px 46px !important; }
  .metric-grid-new { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .score-hero { grid-template-columns:1fr; }
  .score-emblem { border-left:0; border-top:1px solid var(--tsrp-border); padding:20px 0 0; }
  .score-driver-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding:18px 14px 38px !important; }
  .product-header, .company-strip, .section-intro { align-items:flex-start; flex-direction:column; }
  .header-status-new { text-align:left; }
  .search-zone { flex-direction:column; align-items:stretch; }
  .search-zone > div { width:100% !important; }
  .section-nav [data-testid="stSegmentedControl"] { overflow-x:auto; }
  .section-nav [data-testid="stSegmentedControl"] button { white-space:nowrap; font-size:10px !important; }
  .metric-grid-new, .two-col-new, .three-col-new, .evidence-grid { grid-template-columns:1fr; }
  .score-driver-grid { grid-template-columns:1fr; }
  .score-hero { padding:20px 16px; }
  .panel-new { padding:16px; }
  .score-table, .compare-table-new, .change-table { font-size:10px; }
  .score-table th, .score-table td, .compare-table-new th, .compare-table-new td, .change-table th, .change-table td { padding:8px 5px; }
}
</style>
"""
    )


def new_metric_card(label, value, meta="", tone=""):
    return (
        f'<div class="metric-card-new {esc(tone)}">'
        f'<div class="label">{esc(label)}</div><div class="value">{esc(value)}</div>'
        f'<div class="meta">{esc(meta)}</div></div>'
    )


def new_data_line(label, value):
    return f'<div class="data-line"><span>{esc(label)}</span><b>{esc(value)}</b></div>'


def new_source_link(ticker, source):
    if source == "SEC EDGAR":
        return '<a href="https://www.sec.gov/edgar/search/" target="_blank" rel="noopener">SEC EDGAR</a>'
    return f'<a href="https://finance.yahoo.com/quote/{esc(ticker)}" target="_blank" rel="noopener">Yahoo Finance</a>'


def friendly_exchange(value):
    labels = {
        "NMS": "Nasdaq",
        "NYQ": "New York Stock Exchange",
        "NGM": "Nasdaq",
        "NCM": "Nasdaq",
        "LSE": "London Stock Exchange",
        "TOR": "Toronto Stock Exchange",
        "PAR": "Euronext Paris",
        "GER": "Xetra",
    }
    return labels.get(str(value or "").upper(), str(value or "Exchange unavailable"))


def friendly_currency(value):
    labels = {
        "USD": "US dollars",
        "EUR": "euros",
        "GBP": "pounds sterling",
        "CAD": "Canadian dollars",
        "AUD": "Australian dollars",
        "JPY": "Japanese yen",
        "CNY": "Chinese yuan",
        "CHF": "Swiss francs",
        "INR": "Indian rupees",
    }
    return labels.get(str(value or "").upper(), str(value or "currency unavailable"))


def new_status_strip(analysis):
    has_sec = bool(analysis.get("has_sec"))
    detail = (
        "Annual company filing data is available and used where it matches the reported period."
        if has_sec
        else {
            "not_configured": "Company filing data is not connected, so filing-based figures remain unavailable.",
            "not_covered": "No company filing data was returned for this stock.",
            "rate_limited": "The filing source temporarily limited this request.",
            "timeout": "The filing source did not respond in time.",
            "network_error": "The filing source could not be reached.",
            "parse_error": "The returned filing data could not be read.",
            "http_error": "The filing source returned an error.",
        }.get(SEC_STATE.get("kind"), "No usable company filing data was returned."))
    dot = "" if has_sec else " warn"
    render_html(
        f'<div class="state-strip-new"><span class="state-dot-new{dot}" aria-hidden="true"></span>'
        f'<span><b>Company filings (SEC EDGAR) · {esc(analysis.get("sec_status", "N/A"))}</b><br>{esc(detail)}</span></div>'
    )


def snapshot_payload(analysis, company_name, ticker):
    return {
        "methodology_version": SNAPSHOT_METHODOLOGY_VERSION,
        "ticker": ticker,
        "company": company_name,
        "timestamp": analysis.get("last_refreshed") or datetime.now().astimezone().isoformat(timespec="seconds"),
        "score": safe_float(analysis.get("reality_score")),
        "business_quality": safe_float(analysis.get("business_quality")),
        "financial_strength": safe_float(analysis.get("financial_strength")),
        "market_expectations": safe_float(analysis.get("market_expectations")),
        "historical_growth": safe_float(analysis.get("historical_growth")),
        "consensus_growth": safe_float(analysis.get("consensus_growth")),
        "required_growth": safe_float(analysis.get("required_growth")),
        "revenue": safe_float(analysis.get("revenue")),
        "free_cash_flow": safe_float(analysis.get("free_cash_flow")),
        "fcf_margin": safe_float(analysis.get("fcf_margin")),
        "enterprise_value": safe_float(analysis.get("enterprise_value")),
        "model_fcf_margin": safe_float(analysis.get("model_fcf_margin")),
        "solver_status": analysis.get("solver_status"),
        "sec_status": analysis.get("sec_status"),
        "user_assumptions": {
            "starting_growth": safe_float(st.session_state.get("new_value_growth")),
            "fcf_margin": safe_float(st.session_state.get("new_value_margin")),
            "discount_rate": safe_float(st.session_state.get("new_value_discount")),
            "terminal_growth": safe_float(st.session_state.get("new_value_terminal")),
        },
    }


def snapshot_signature(snapshot):
    return json.dumps(snapshot, sort_keys=True, default=str)


def score_change_text(current, previous, digits=1):
    current = safe_float(current)
    previous = safe_float(previous)
    if current is None or previous is None:
        return "N/A"
    return f"{current - previous:+.{digits}f}"


def pct_change_text(current, previous):
    current = safe_float(current)
    previous = safe_float(previous)
    if current is None or previous is None:
        return "N/A"
    return f"{(current - previous) * 100:+.1f} pts"


def track_snapshot_state(ticker, snapshot):
    saved = st.session_state.setdefault("saved_snapshots", {})
    current = st.session_state.setdefault("current_snapshot", {})
    existing = current.get(ticker)
    if existing and snapshot_signature(existing) == snapshot_signature(snapshot):
        return
    current[ticker] = snapshot
    saved.setdefault(ticker, [])


def save_current_snapshot(ticker, snapshot):
    saved = st.session_state.setdefault("saved_snapshots", {})
    history = saved.setdefault(ticker, [])
    if history and snapshot_signature(history[-1]) == snapshot_signature(snapshot):
        return False
    history.append(snapshot)
    saved[ticker] = history[-6:]
    return True


def build_new_cashflow_trend(yahoo_data):
    financials = yahoo_data.get("financials")
    cashflow = yahoo_data.get("cashflow")
    revenue = get_row(financials, ["Total Revenue", "Operating Revenue"])
    ocf = get_row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = get_row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    reported = get_row(cashflow, ["Free Cash Flow"])
    if revenue is None or (ocf is None and reported is None):
        return pd.DataFrame()
    periods = list(revenue.index)
    rows = []
    for period in periods:
        revenue_value = safe_float(revenue.get(period))
        ocf_value = safe_float(ocf.get(period)) if ocf is not None else None
        capex_value = safe_float(capex.get(period)) if capex is not None else None
        reported_value = safe_float(reported.get(period)) if reported is not None else None
        fcf_value = compute_fcf(ocf_value, capex_value, reported_value)
        if revenue_value is None or fcf_value is None:
            continue
        rows.append(
            {
                "Period": str(period)[:10],
                "Revenue": revenue_value,
                "Free cash flow": fcf_value,
                "FCF margin": fcf_value / revenue_value if revenue_value > 0 else None,
            }
        )
    return pd.DataFrame(rows)


def score_breakdown_html(analysis):
    trace = score_breakdown(
        analysis.get("business_quality"),
        analysis.get("financial_strength"),
        analysis.get("historical_growth"),
        analysis.get("required_growth"),
        analysis.get("consensus_growth"),
        analysis.get("growth_clamped") or analysis.get("model_fcf_refused"),
    )
    rows = []
    for component in trace["components"]:
        rows.append(
            f'<tr><td><strong>{esc(component["label"])}</strong></td>'
            f'<td>{component["value"]:.1f}</td><td>{component["weight"]:.0%}</td>'
            f'<td><strong>{component["contribution"]:.1f}</strong></td></tr>'
        )
    return (
        '<table class="score-table"><thead><tr><th>Area</th><th>Result</th>'
        '<th>Share of score</th><th>Points added</th></tr></thead><tbody>'
        + "".join(rows)
        + f'<tr><td><strong>TSRP Score</strong></td><td></td><td></td><td><strong>{trace["score"]:.1f} / 100</strong></td></tr>'
        + "</tbody></table>"
    ), trace


def score_driver_copy(component):
    labels = {
        "Business quality": "How consistently the business is performing.",
        "Financial strength": "How much financial room the company has.",
        "Historical growth fit": "How past growth compares with what the price requires.",
        "Consensus growth fit": "How the next-year analyst estimate compares with the price.",
    }
    display_labels = {
        "Business quality": "Business quality",
        "Financial strength": "Financial strength",
        "Historical growth fit": "Past growth vs price",
        "Consensus growth fit": "Analysts vs price",
    }
    return display_labels.get(component["label"], component["label"]), labels.get(component["label"], "Evidence used in the overall research signal.")


def render_score_section(analysis, company_name, ticker, display_currency, display_fx_reporting, display_fx_trading, as_of):
    tone = score_tone(analysis.get("reality_score"))
    score_value = score(analysis.get("reality_score"))
    breakdown_markup, trace = score_breakdown_html(analysis)
    render_html(
        f'<div class="section-intro"><div><div class="section-kicker-new">Overall research signal</div>'
        f'<h2>TSRP Score</h2><p>One easy-to-scan signal for how well the company’s evidence supports what the current price appears to require.</p></div>'
        f'<div class="state-tag">0–100 research signal</div></div>'
    )
    render_html(
        f'<div class="score-hero">'
        f'<div class="score-hero-copy"><div class="section-kicker-new">{esc(score_label(analysis.get("reality_score")))}</div>'
        f'<h2>{esc(company_name)} <span style="color:var(--tsrp-muted);font-size:.55em;font-family:var(--tsrp-mono)">{esc(ticker)}</span></h2>'
        f'<p>{esc(conclusion_text(analysis))}</p>'
        f'<div class="score-note">Evidence confidence: <b>{esc(analysis.get("confidence", "N/A"))}</b>. Missing or incompatible information is shown as unavailable, not treated as positive evidence.</div></div>'
        f'<div class="score-emblem" aria-label="TSRP Score {esc(score_value)} out of 100"><small>TSRP Score</small><b>{esc(score_value)}</b><span>{esc(score_label(analysis.get("reality_score")))}</span></div></div>'
    )
    new_status_strip(analysis)
    driver_cards = ""
    for component in trace["components"]:
        display_label, explanation = score_driver_copy(component)
        driver_cards += (
            f'<div class="score-driver"><div class="driver-score"><strong>{component["value"]:.0f}</strong><span>out of 100</span></div>'
            f'<h4>{esc(display_label)}</h4><p>{esc(explanation)}</p></div>'
        )
    render_html(
        f'<div class="research-answer"><b>Why this score?</b> {esc(conclusion_text(analysis))}</div>'
        f'<div class="score-driver-grid">{driver_cards}</div>'
        f'<div class="two-col-new"><div class="panel-new"><div class="panel-title-row"><h3>What is helping or holding it back?</h3><span>Evidence to investigate</span></div>'
        f'<div class="driver-list">'
        f'<div class="driver-item"><i>↗</i><div><strong>What is helping</strong><span>{esc(", ".join(score_driver_copy(c)[0] for c in sorted(trace["components"], key=lambda item: item["value"], reverse=True)[:2]))}</span></div></div>'
        f'<div class="driver-item"><i>↘</i><div><strong>What is holding it back</strong><span>{esc(", ".join(score_driver_copy(c)[0] for c in sorted(trace["components"], key=lambda item: item["value"])[:2]))}</span></div></div>'
        f'<div class="driver-item"><i>!</i><div><strong>Important limits</strong><span>{esc(" · ".join(label for label, _ in analysis.get("quality_flags", [])[:4]) or "No material coverage limits were returned.")}</span></div></div>'
        f'</div></div></div>'
    )
    with st.expander("See score details, sources, and limits", expanded=False):
        render_html('<div class="definition">The detailed view explains how each evidence area contributes to the overall signal. It is provided for review, not because it is needed to use the conclusion.</div>')
        render_html(breakdown_markup)
        render_methodology()
        render_data_quality(analysis)


def comparison_row(metric, expected, actual, difference, period, unit, source, status):
    return (
        f'<tr><td><strong>{esc(metric)}</strong></td><td>{esc(expected)}</td><td>{esc(actual)}</td>'
        f'<td>{esc(difference)}</td><td>{esc(period)}</td><td>{esc(unit)}</td>'
        f'<td>{esc(source)}</td><td>{esc(status)}</td></tr>'
    )


def new_growth_verdict(analysis):
    if analysis.get("model_fcf_refused") or analysis.get("required_growth") is None:
        return "The growth required by the current price is unavailable because the cash-flow or valuation inputs are incomplete."
    if analysis.get("growth_clamped"):
        return "The growth required by the current price is outside the range TSRP can test, so it remains unavailable rather than showing false precision."
    required = analysis.get("required_growth")
    historical = analysis.get("historical_growth")
    consensus = analysis.get("consensus_growth")
    if required is None:
        return "The growth required by the current price could not be calculated from the available inputs."
    comparisons = []
    if historical is not None:
        comparisons.append((required - historical, "historical growth"))
    if consensus is not None:
        comparisons.append((required - consensus, "analyst consensus"))
    if not comparisons:
        return "The growth required by the current price is available, but no comparable growth benchmark was returned."
    gap, benchmark = max(comparisons, key=lambda item: abs(item[0]))
    if abs(gap) < 0.01:
        return f"The growth required by the current price is broadly aligned with {benchmark}."
    direction = "above" if gap > 0 else "below"
    return f"The growth required by the current price is {abs(gap) * 100:.1f} percentage points {direction} {benchmark}; treat this as a comparison, not a forecast."


def render_expectations_section(analysis, yahoo_data, ticker, display_currency, display_fx_reporting):
    historical_years = analysis.get("historical_growth_years") or 0
    required = required_growth_label(analysis)
    consensus = percent(analysis.get("consensus_growth"))
    historical = percent(analysis.get("historical_growth"))
    growth_gap = (
        f"{(analysis['required_growth'] - analysis['consensus_growth']) * 100:+.1f} pts"
        if analysis.get("required_growth") is not None and analysis.get("consensus_growth") is not None
        else "N/A"
    )
    render_html(
        '<div class="section-intro"><div><div class="section-kicker-new">What does the price assume?</div>'
        '<h2>Expectations vs Reality</h2><p>Compare the growth built into today’s price with the company’s past results and the next-year analyst estimate.</p></div></div>'
    )
    render_html(
        '<div class="metric-grid-new">'
        + new_metric_card("Growth required by price", required, "Annual revenue growth implied by today’s price", "accent" if analysis.get("required_growth") is not None else "warn")
        + new_metric_card("Analyst growth estimate", consensus, "Next fiscal year; not a long-term forecast", "accent" if analysis.get("consensus_growth") is not None else "warn")
        + new_metric_card("Past revenue growth", historical, f"Comparable {historical_years}-year history" if historical_years else "No comparable history", "accent" if analysis.get("historical_growth") is not None else "warn")
        + new_metric_card("Required vs analysts", growth_gap, "Difference in percentage points", "warn" if growth_gap != "N/A" and growth_gap.startswith("+") else "")
        + '</div>'
    )
    render_html(f'<div class="research-answer"><b>Bottom line:</b> {esc(new_growth_verdict(analysis))}</div>')
    missing = []
    if analysis.get("required_growth") is None:
        missing.append("the growth required by the current price")
    if analysis.get("consensus_growth") is None:
        missing.append("the analyst growth estimate")
    if analysis.get("historical_growth") is None:
        missing.append("comparable past growth")
    if missing:
        render_html(f'<div class="state-strip-new"><span class="state-dot-new warn" aria-hidden="true"></span><span><b>Some comparisons are unavailable</b><br>{esc("TSRP could not compare " + ", ".join(missing) + ". Missing data is left as N/A.")}</span></div>')
    render_html(
        f'<div class="panel-new"><div class="panel-title-row"><h3>Reported evidence</h3><span>Past results</span></div>'
        f'<div class="plain-list">{new_data_line("Latest reported revenue", money(analysis.get("revenue"), analysis.get("reporting_currency"), display_currency, display_fx_reporting))}{new_data_line("Latest free cash flow", money(analysis.get("free_cash_flow"), analysis.get("reporting_currency"), display_currency, display_fx_reporting))}{new_data_line("Cash-flow margin", percent(analysis.get("fcf_margin")))}</div>'
        f'<div class="definition">Free cash flow is the cash left after operating costs and investment. Cash-flow margin is that amount as a share of revenue.</div>'
        f'<div class="source-foot">Sources: {new_source_link(ticker, "Yahoo Finance")} · {new_source_link(ticker, "SEC EDGAR")} · periods and currencies are retained in the detailed view.</div></div>'
    )
    with st.expander("See the full comparison and annual cash-flow history", expanded=False):
        render_html(
            '<div class="panel-title-row"><h3>Detailed comparison</h3><span>Source and period included</span></div>'
            '<table class="compare-table-new"><thead><tr><th>Evidence</th><th>Estimate or price signal</th><th>Reported result</th><th>Difference</th><th>Period</th><th>Units</th><th>Source</th><th>Status</th></tr></thead><tbody>'
            + comparison_row("Revenue", "No consensus revenue returned", money(analysis.get("revenue"), analysis.get("reporting_currency"), display_currency, display_fx_reporting), "N/A", "Latest annual", analysis.get("reporting_currency", "N/A"), "Yahoo Finance / SEC EDGAR", "Reported result only")
            + comparison_row("Revenue growth", consensus, historical, growth_gap if analysis.get("required_growth") is not None else "N/A", f"Next fiscal year vs {historical_years}-year history", "% per year", "Yahoo Finance estimates / reported history", "Different periods")
            + comparison_row("Earnings per share", "N/A — estimate not returned", "N/A — result not returned", "N/A", "N/A", "per share", "Current data feed", "Unavailable")
            + comparison_row("Company guidance", "N/A — guidance not returned", "N/A", "N/A", "N/A", "N/A", "Current data feed", "Unavailable")
            + comparison_row("Growth required by price", required, historical, growth_gap, "Long-term price signal vs past growth", "% per year", "TSRP calculation / Yahoo Finance / SEC EDGAR", "Calculated comparison")
            + '</tbody></table>'
        )
        trend = build_new_cashflow_trend(yahoo_data)
        if trend.empty:
            st.info("Annual cash-flow history is unavailable for the periods returned by the current data sources.")
        else:
            display = trend.copy()
            display["Revenue"] = display["Revenue"].map(lambda value: money(value, analysis["reporting_currency"], display_currency, display_fx_reporting))
            display["Free cash flow"] = display["Free cash flow"].map(lambda value: money(value, analysis["reporting_currency"], display_currency, display_fx_reporting))
            display["FCF margin"] = display["FCF margin"].map(percent)
            st.dataframe(display, width="stretch", hide_index=True)


def render_valuation_section(analysis, display_currency, display_fx_reporting, display_fx_trading):
    reporting = analysis.get("reporting_currency", "N/A")
    trading = analysis.get("trading_currency", "N/A")
    render_html(
        '<div class="section-intro"><div><div class="section-kicker-new">What must the company deliver?</div>'
        '<h2>Valuation</h2><p>See whether today’s company value asks for a reasonable operating path, and which assumptions affect that conclusion.</p></div></div>'
    )
    required = required_growth_label(analysis)
    if analysis.get("required_growth") is None:
        valuation_answer = "The current price cannot be translated into a growth requirement because a required valuation input is unavailable."
    else:
        valuation_answer = f"At today’s company value, the model requires about {required} annual revenue growth. This is a price signal, not a forecast or a target price."
    render_html(
        '<div class="metric-grid-new">'
        + new_metric_card("Current share price", money(analysis.get("price"), trading, display_currency, display_fx_trading), f"{friendly_currency(trading)} · Yahoo Finance")
        + new_metric_card("Enterprise value", money(analysis.get("enterprise_value"), reporting, display_currency, display_fx_reporting), f"Company value after cash and debt · {friendly_currency(reporting)}", "accent" if analysis.get("enterprise_value") is not None else "warn")
        + new_metric_card("Value / annual sales", multiple(analysis.get("ev_sales")), "Company value compared with annual revenue", "accent" if analysis.get("ev_sales") is not None else "warn")
        + new_metric_card("Growth required by price", required, "Annual revenue growth implied by today’s price", "accent" if analysis.get("required_growth") is not None else "warn")
        + '</div>'
    )
    render_html(f'<div class="research-answer"><b>Valuation readout:</b> {esc(valuation_answer)}</div>')
    solver_status = analysis.get("solver_status", "unavailable")
    status_label = {
        "exact": "Available within the supported range",
        "below_range": "Below the supported range",
        "above_range": "Above the supported range",
        "unavailable": "Unavailable",
    }.get(solver_status, solver_status)
    render_html(
        f'<div class="two-col-new"><div class="panel-new"><div class="panel-title-row"><h3>What the price requires</h3><span>{esc(status_label)}</span></div>'
        f'{new_data_line("Cash flow margin used", percent(analysis.get("model_fcf_margin")))}'
        f'<div class="definition">Cash flow margin is the portion of revenue left after operating costs and investment. It is an important assumption because it affects how much cash the company can produce.</div>'
        f'<div class="source-foot">Sources: Yahoo Finance and company filing data when available. Missing FX or cash-flow evidence keeps dependent results as N/A.</div></div>'
        f'<div class="panel-new"><div class="panel-title-row"><h3>Where company value comes from</h3><span>{esc(friendly_currency(reporting))} reporting currency</span></div>'
        f'{new_data_line("Value of shares in the market", money(analysis.get("market_cap_reporting"), reporting, display_currency, display_fx_reporting))}'
        f'{new_data_line("Debt", money(analysis.get("debt"), reporting, display_currency, display_fx_reporting))}'
        f'{new_data_line("Cash", money(analysis.get("cash"), reporting, display_currency, display_fx_reporting))}'
        f'<div class="definition">Enterprise value, shown above, is a view of the company’s value that includes debt and cash, not just the value of its shares.</div>'
        f'</div></div>'
    )
    with st.expander("Explore different assumptions", expanded=False):
        st.caption("Change these hypothetical inputs to see how the growth required by price moves. They do not change TSRP Score and are not forecasts, target prices, or recommendations.")
        if analysis.get("revenue") is None or analysis.get("revenue") <= 0:
            st.info("Revenue is unavailable, so this growth-required cash-flow estimate cannot run.")
            return
        base_growth = analysis.get("required_growth") if analysis.get("solver_status") == "exact" else 0.0
        base_margin = analysis.get("model_fcf_margin") if analysis.get("model_fcf_margin") and analysis.get("model_fcf_margin") > 0 else 0.10
        left, right = st.columns(2)
        with left:
            growth_default = round(min(max(base_growth * 100, -10), 40) * 2) / 2
            margin_default = round(min(max(base_margin * 100, 1), 50) * 2) / 2
            user_growth = st.slider("Starting annual revenue growth", -10.0, 40.0, growth_default, 0.5, format="%.1f%%", key="new_value_growth") / 100
            user_margin = st.slider("Free cash flow left after investment", 1.0, 50.0, margin_default, 0.5, format="%.1f%%", key="new_value_margin") / 100
        with right:
            discount_default = round(min(max(analysis.get("discount_rate", 0.10) * 100, 3), 20) * 4) / 4
            terminal_default = round(min(max(analysis.get("terminal_growth", 0.02) * 100, 0), 6) * 4) / 4
            user_discount = st.slider("Discount rate · how future cash is valued", 3.0, 20.0, discount_default, 0.25, format="%.2f%%", key="new_value_discount") / 100
            user_terminal = st.slider("Long-run growth after year 10", 0.0, 6.0, terminal_default, 0.25, format="%.2f%%", key="new_value_terminal") / 100
        if user_discount <= user_terminal:
            st.warning("Discount rate must be above terminal growth for the model to converge.")
        else:
            hypothetical_ev = dcf_enterprise_value(analysis["revenue"], user_growth, user_margin, user_discount, user_terminal, start_margin=user_margin)
            if hypothetical_ev is None:
                st.info("These assumptions do not produce a valid DCF output.")
            else:
                implied_equity = None
                if analysis.get("cash") is not None and analysis.get("debt") is not None:
                    implied_equity = hypothetical_ev - analysis["debt"] + analysis["cash"]
                render_html(
                    '<div class="metric-grid-new">'
                    + new_metric_card("Hypothetical enterprise value", money(hypothetical_ev, reporting, display_currency, display_fx_reporting), "Calculated from user inputs", "accent")
                    + new_metric_card("Hypothetical equity value", money(implied_equity, reporting, display_currency, display_fx_reporting), "Only if cash and debt are available")
                    + new_metric_card("Current enterprise value", money(analysis.get("enterprise_value"), reporting, display_currency, display_fx_reporting), "Sourced / reconciled market value")
                    + '</div>'
                )


def evidence_card(kind, title, explanation, why, horizon, confirm, invalidate, ticker):
    source = new_source_link(ticker, "Yahoo Finance")
    return (
        f'<div class="evidence-card {esc(kind)}"><div class="eyebrow-new">{esc("Potential catalyst" if kind == "catalyst" else "Potential risk")}</div>'
        f'<h4>{esc(title)}</h4><p>{esc(explanation)}</p><p><b>Why it matters:</b> {esc(why)}</p>'
        f'<div class="evidence-meta"><b>Watch for:</b> {esc(confirm)}<br><b>When:</b> {esc(horizon)}<br>'
        f'<b>Thesis weakens if:</b> {esc(invalidate)}<br><b>Source:</b> {source} · current analysis</div></div>'
    )


def build_evidence_lists(analysis, ticker):
    catalysts = []
    risks = []
    if analysis.get("historical_growth") is not None and analysis["historical_growth"] > 0:
        catalysts.append(
            evidence_card("catalyst", "Established revenue growth", f"Reported revenue has grown at {percent(analysis['historical_growth'])} over the comparable {analysis.get('historical_growth_years') or 0}-year history.", "A positive operating record gives the current thesis evidence to test.", "Next reported periods", "Revenue growth and margin trajectory", "Reported growth turns negative or coverage becomes non-comparable.", ticker)
        )
    if analysis.get("fcf_margin") is not None and analysis["fcf_margin"] > 0:
        catalysts.append(
            evidence_card("catalyst", "Positive cash conversion", f"Latest compatible free-cash-flow margin is {percent(analysis['fcf_margin'])}.", "Cash conversion can support reinvestment, balance-sheet resilience, or capital returns.", "Next annual or quarterly cash-flow update", "Operating cash flow, capex, and free cash flow", "Free cash flow turns negative or cannot be period-matched.", ticker)
        )
    if analysis.get("cash") is not None and analysis.get("debt") is not None and analysis["cash"] > analysis["debt"]:
        catalysts.append(
            evidence_card("catalyst", "Net cash position", "Reported cash is greater than reported debt in the selected reporting currency.", "A stronger balance sheet can give the company more room to absorb execution volatility.", "Ongoing", "Cash, debt, and interest-bearing obligations", "Debt rises above cash or balance-sheet coverage becomes incomplete.", ticker)
        )
    if analysis.get("required_growth") is not None and analysis["required_growth"] > 0.18:
        risks.append(
            evidence_card("risk", "High growth burden", f"The current company value requires about {required_growth_label(analysis)} starting revenue growth within the range TSRP can test.", "A demanding operating path leaves less room for execution misses.", "Multi-year", "Reported revenue growth versus the growth required by price", "The growth required by price falls materially because the value or inputs change.", ticker)
        )
    if analysis.get("market_expectations") is not None and analysis["market_expectations"] > 70:
        risks.append(
            evidence_card("risk", "Expectation pressure", f"The price already assumes a demanding outcome: the expectations signal is {score(analysis['market_expectations'])}/100.", "Strong results may still disappoint if the price already embeds aggressive assumptions.", "Ongoing", "Value compared with sales, earnings, and required growth", "Valuation pressure normalizes or operating evidence improves.", ticker)
        )
    if analysis.get("fcf_margin") is not None and analysis["fcf_margin"] < 0.05:
        risks.append(
            evidence_card("risk", "Weak cash conversion", f"Latest compatible free-cash-flow margin is {percent(analysis['fcf_margin'])}.", "Revenue quality is less durable when operating cash does not convert into free cash flow.", "Next cash-flow update", "Operating cash flow minus capex", "Cash conversion improves and remains period-aligned.", ticker)
        )
    if analysis.get("debt") is not None and analysis.get("cash") is not None and analysis["debt"] > analysis["cash"] * 2:
        risks.append(
            evidence_card("risk", "Balance-sheet leverage", "Reported debt is more than twice reported cash.", "Less balance-sheet flexibility can amplify an operating or valuation reset.", "Ongoing", "Debt, cash, and enterprise-value bridge", "Debt declines or cash coverage improves.", ticker)
        )
    return catalysts, risks


def render_catalysts_risks_section(analysis, ticker):
    render_html(
        '<div class="section-intro"><div><div class="section-kicker-new">What could change the view?</div>'
        '<h2>Catalysts & Risks</h2><p>Evidence that could strengthen or weaken the research view. These are prompts to monitor, not predictions.</p></div></div>'
    )
    catalysts, risks = build_evidence_lists(analysis, ticker)
    if not catalysts and not risks:
        render_html('<div class="empty-new"><h3>No evidence-backed catalysts or risks yet</h3><p>The current analysis does not contain enough specific, traceable evidence to populate this section. TSRP leaves it empty rather than generating generic narratives.</p></div>')
        return
    left = "".join(catalysts) or '<div class="empty-new"><h3>No catalyst evidence</h3><p>Available data does not support a specific catalyst.</p></div>'
    right = "".join(risks) or '<div class="empty-new"><h3>No specific risk evidence</h3><p>Available data does not support a specific risk beyond the general limitations shown elsewhere.</p></div>'
    render_html(f'<div class="evidence-grid"><div><div class="panel-title-row"><h3>Potential catalysts</h3><span>What could help</span></div>{left}</div><div><div class="panel-title-row"><h3>Potential risks</h3><span>What could hurt</span></div>{right}</div></div>')


def render_changed_section(analysis, company_name, ticker, display_currency, display_fx_reporting):
    render_html(
        '<div class="section-intro"><div><div class="section-kicker-new">Since the last saved analysis</div>'
        '<h2>What Changed?</h2><p>Save an analysis, return after a refresh, and see which research inputs moved. TSRP does not invent a reason for a change that the data cannot support.</p></div></div>'
    )
    current = snapshot_payload(analysis, company_name, ticker)
    history = st.session_state.setdefault("saved_snapshots", {}).setdefault(ticker, [])
    if st.button("Save this analysis", key=f"save_snapshot_{ticker}", type="primary"):
        if save_current_snapshot(ticker, current):
            st.success("Analysis saved for this session.")
        else:
            st.info("This analysis is already the latest saved version.")
        history = st.session_state.setdefault("saved_snapshots", {}).setdefault(ticker, [])
    if not history:
        render_html('<div class="empty-new"><h3>Save this analysis to track change</h3><p>After a later refresh or a changed assumption set, save again to see what moved in the score, expectations, financial evidence, or valuation.</p></div>')
        return
    render_html(f'<div class="state-strip-new"><span class="state-dot-new" aria-hidden="true"></span><span><b>{len(history)} saved analysis{"es" if len(history) != 1 else ""}</b><br>Latest saved: {esc(history[-1].get("timestamp", "N/A"))}</span></div>')
    if len(history) < 2:
        render_html('<div class="empty-new"><h3>One analysis saved</h3><p>Save another version after a refresh or changed assumption to see what is different.</p></div>')
        return
    previous, latest = history[-2], history[-1]
    comparison = snapshot_diff(previous, latest)
    if not comparison["comparable"]:
        st.warning("These saved analyses use different calculation rules, so TSRP will not compare them directly.")
        return
    changed_fields = {item["field"] for item in comparison["changes"]}
    currency = analysis.get("reporting_currency", "N/A")

    def value_for(field, item):
        value = item.get(field)
        if field in {"score", "business_quality", "financial_strength", "market_expectations"}:
            return score(value)
        if field in {"historical_growth", "consensus_growth"}:
            return percent(value)
        if field == "required_growth":
            return f"{percent(value)} /yr" if value is not None else "N/A"
        if field in {"revenue", "free_cash_flow", "enterprise_value"}:
            return money(value, currency, display_currency, display_fx_reporting)
        return "N/A"

    labels = [
        ("score", "TSRP Score", score_change_text),
        ("business_quality", "Business quality", score_change_text),
        ("financial_strength", "Financial strength", score_change_text),
        ("market_expectations", "Price expectations", score_change_text),
        ("historical_growth", "Past revenue growth", pct_change_text),
        ("consensus_growth", "Analyst growth estimate", pct_change_text),
        ("required_growth", "Growth required by price", pct_change_text),
        ("revenue", "Reported revenue", lambda a, b: "Changed"),
        ("free_cash_flow", "Free cash flow", lambda a, b: "Changed"),
        ("enterprise_value", "Enterprise value", lambda a, b: "Changed"),
    ]
    rows = []
    for field, label, movement in labels:
        if field in changed_fields:
            rows.append((label, value_for(field, latest), value_for(field, previous), movement(latest.get(field), previous.get(field))))

    if rows:
        table = '<table class="change-table"><thead><tr><th>Research item</th><th>Now</th><th>Earlier</th><th>Movement</th></tr></thead><tbody>'
        table += "".join(f'<tr><td><strong>{esc(metric)}</strong></td><td>{esc(latest_value)}</td><td>{esc(previous_value)}</td><td>{esc(change)}</td></tr>' for metric, latest_value, previous_value, change in rows)
        table += "</tbody></table>"
        render_html(
            f'<div class="panel-new"><div class="panel-title-row"><h3>What moved</h3><span>{esc(latest.get("timestamp", "N/A"))} vs {esc(previous.get("timestamp", "N/A"))}</span></div>{table}'
            f'<div class="source-foot">A change can come from new market data, a new filing, an updated estimate, or an assumption change. TSRP does not claim a cause unless the saved inputs show it.</div></div>'
        )
    else:
        render_html('<div class="empty-new"><h3>No research inputs changed</h3><p>The latest saved analysis matches the earlier one for the tracked figures.</p></div>')

    if latest.get("user_assumptions") != previous.get("user_assumptions"):
        render_html('<div class="state-strip-new"><span class="state-dot-new warn" aria-hidden="true"></span><span><b>Your valuation assumptions changed</b><br>This can change the growth required by price without changing the company’s reported performance.</span></div>')
        with st.expander("See the assumption changes", expanded=False):
            assumption_labels = {
                "starting_growth": "Starting annual revenue growth",
                "fcf_margin": "Free cash flow left after investment",
                "discount_rate": "Discount rate",
                "terminal_growth": "Long-run growth",
            }
            assumption_rows = []
            for key, label in assumption_labels.items():
                before = percent((previous.get("user_assumptions") or {}).get(key))
                after = percent((latest.get("user_assumptions") or {}).get(key))
                if before != after:
                    assumption_rows.append((label, after, before))
            if assumption_rows:
                table = '<table class="change-table"><thead><tr><th>Assumption</th><th>Now</th><th>Earlier</th></tr></thead><tbody>'
                table += "".join(f'<tr><td><strong>{esc(label)}</strong></td><td>{esc(after)}</td><td>{esc(before)}</td></tr>' for label, after, before in assumption_rows)
                render_html(table + '</tbody></table>')


def select_primary_section():
    current = st.session_state.get("primary_section", PRIMARY_SECTIONS[0])
    if hasattr(st, "segmented_control"):
        selected = st.segmented_control(
            "Primary research section",
            PRIMARY_SECTIONS,
            default=current if current in PRIMARY_SECTIONS else PRIMARY_SECTIONS[0],
            key="primary_section_nav",
            label_visibility="collapsed",
        ) or current
        if selected != current:
            st.session_state.primary_section = selected
            st.rerun()
        return selected
    cols = st.columns(len(PRIMARY_SECTIONS), gap="small")
    for col, name in zip(cols, PRIMARY_SECTIONS):
        with col:
            if st.button(name, key=f"primary_{name}", width="stretch", type="primary" if current == name else "secondary"):
                st.session_state.primary_section = name
                st.rerun()
    return current


st.markdown('<div class="app-wrap">', unsafe_allow_html=True)
render_product_system()
render_html(
    '<div class="product-header"><div class="brand-lockup-new"><div class="brand-mark-new">TS</div>'
    '<div class="brand-copy-new"><strong>TSRP</strong><span>The Saleh Research Project · research before opinion</span></div></div>'
    '<div class="header-status-new">Company stock research<br>Market data and filing status shown with every analysis</div></div>'
)

try:
    search_col, action_col = st.columns([5, 1.15], gap="small", vertical_alignment="bottom")
except TypeError:
    search_col, action_col = st.columns([5, 1.15])
with search_col:
    typed = st.text_input("Company or ticker", placeholder="Search Apple, Microsoft, Samsung, or AAPL", key="company_search")
with action_col:
    submitted = st.button("Analyze company", key="new_analyze_company", width="stretch", type="primary")

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
            st.session_state.primary_section = PRIMARY_SECTIONS[0]
        else:
            st.session_state.ticker = ""
            st.session_state.invalid_ticker = typed
            st.session_state.ticker_error = error

if not submitted:
    st.session_state.search_hits = []

typed_query = str(typed or "").strip()
live_hits = []
if len(typed_query) >= 2 and typed_query.upper() != st.session_state.get("ticker", "").upper():
    live_hits = search_companies(typed_query)
selected_hit = render_company_suggestions(st.session_state.search_hits or live_hits, "new_pick_company")
if selected_hit:
    st.session_state.ticker = selected_hit["symbol"]
    st.session_state.pending_search = selected_hit["symbol"]
    st.session_state.search_hits = []
    st.session_state.ticker_error = None
    st.session_state.invalid_ticker = ""
    st.session_state.primary_section = PRIMARY_SECTIONS[0]
    st.rerun()
if st.session_state.search_hits:
    st.stop()

if st.session_state.ticker_error:
    render_ticker_error(st.session_state.invalid_ticker or "input", st.session_state.ticker_error)
    render_html(f'<div class="app-footer-new">{esc(EDUCATIONAL_DISCLAIMER)}</div></div>')
    st.stop()

if not st.session_state.ticker:
    render_html(
        '<div class="empty-new" style="margin-top:30px"><div class="section-kicker-new">Focused equity research</div>'
        '<h3>Start with the question behind the price</h3>'
        '<p>Enter a company stock to see the TSRP Score first, then trace the expectations, valuation, risks, and changes behind it. Indexes, commodities, currencies, funds, and other non-company instruments are excluded.</p>'
        '<div class="three-col-new" style="text-align:left;margin-top:24px">'
        '<div class="panel-new"><div class="section-kicker-new">01</div><h3>Analyze</h3><p>Search a listed company by name or ticker.</p></div>'
        '<div class="panel-new"><div class="section-kicker-new">02</div><h3>Score</h3><p>See the research signal and the evidence behind it.</p></div>'
        '<div class="panel-new"><div class="section-kicker-new">03</div><h3>Challenge</h3><p>Test expectations, valuation, catalysts, risks, and change.</p></div>'
        '</div></div>'
    )
    recent = [symbol for symbol in st.session_state.get("recent", []) if symbol]
    if recent:
        render_html('<div class="source-foot" style="margin-top:18px">Recent companies</div>')
        render_watch_row(recent[:5], "new_recent")
    render_html(f'<div class="app-footer-new">{esc(EDUCATIONAL_DISCLAIMER)}</div></div>')
    st.stop()

ticker = st.session_state.ticker
if not ticker_format_ok(ticker) or not company_symbol_allowed(ticker):
    st.session_state.ticker = ""
    st.session_state.invalid_ticker = ticker
    st.session_state.ticker_error = COMPANY_ONLY_MESSAGE
    render_ticker_error(ticker, st.session_state.ticker_error)
    render_html(f'<div class="app-footer-new">{esc(EDUCATIONAL_DISCLAIMER)}</div></div>')
    st.stop()

with st.spinner(f"Loading {ticker}..."):
    yahoo_data = fetch_yahoo_data(ticker)
    info = yahoo_data["info"]
if not yahoo_data_is_valid(yahoo_data, ticker):
    st.session_state.ticker = ""
    st.session_state.invalid_ticker = ticker
    st.session_state.ticker_error = COMPANY_ONLY_MESSAGE if non_equity_reason(ticker, (yahoo_data.get("info") or {}).get("quoteType")) else f"“{ticker}” was not found on Yahoo Finance."
    render_ticker_error(ticker, st.session_state.ticker_error)
    render_html(f'<div class="app-footer-new">{esc(EDUCATIONAL_DISCLAIMER)}</div></div>')
    st.stop()

remember_ticker(ticker)
sync_ticker_query(ticker)
with st.spinner("Checking SEC filing coverage..."):
    sec_facts = fetch_sec_companyfacts(ticker)
analysis = analyze_company(yahoo_data, sec_facts)
reporting_currency = analysis["reporting_currency"]
trading_currency = analysis["trading_currency"]
company_name = info.get("longName") or info.get("shortName") or KNOWN_NAMES.get(ticker) or ticker
if str(company_name).upper() == ticker and KNOWN_NAMES.get(ticker):
    company_name = KNOWN_NAMES[ticker]
sector = analysis["sector"]
industry = info.get("industry") or "Unknown industry"

with st.expander("Sources, currency & more", expanded=False):
    display_currency = st.selectbox("Currency for displayed values", DISPLAY_CURRENCIES, key="display_currency", help="This changes how values are displayed. Company reporting currency is kept in the detailed source record.")
    controls_left, controls_right = st.columns(2)
    with controls_left:
        if st.button("↻", key="new_refresh_data", help="Refresh market, FX, and SEC data"):
            fetch_yahoo_data.clear()
            fetch_price_history.clear()
            fetch_sec_companyfacts.clear()
            fetch_sec_ticker_map.clear()
            fx_rate.clear()
            rate_to_usd.clear()
            st.rerun()
    with controls_right:
        export_table = evidence_dataframe(analysis, company_name, ticker, sector, industry, reporting_currency, trading_currency, display_currency, 1.0, 1.0)
        st.download_button("Download source table", export_table.to_csv(index=False).encode("utf-8"), f"tsrp_{ticker}.csv", "text/csv", key="new_export_csv")
        st.download_button("Download full research record", export_payload(analysis, company_name, ticker, sector, industry, display_currency), f"tsrp_{ticker}.json", "application/json", key="new_export_json")
    render_html('<div class="panel-title-row" style="margin-top:18px"><h3>How TSRP works</h3><span>Optional detail</span></div>')
    render_methodology()

raw_fx_reporting = fx_rate(reporting_currency, display_currency)
raw_fx_trading = fx_rate(trading_currency, display_currency)
display_fx_reporting = 1.0 if reporting_currency == display_currency else raw_fx_reporting
display_fx_trading = 1.0 if trading_currency == display_currency else raw_fx_trading
if reporting_currency != display_currency and raw_fx_reporting is None:
    st.warning(f"FX conversion {reporting_currency} → {display_currency} is unavailable. Dependent outputs remain N/A.")
if trading_currency != display_currency and raw_fx_trading is None:
    st.warning(f"FX conversion {trading_currency} → {display_currency} is unavailable. Quote conversion remains N/A.")

as_of = analysis.get("last_refreshed") or "N/A"
previous_price = first_value(info, "previousClose", "regularMarketPreviousClose")
price_change = analysis.get("price") / previous_price - 1 if analysis.get("price") is not None and previous_price not in (None, 0) else None
st.session_state.setdefault("primary_section", PRIMARY_SECTIONS[0])
compact_score_markup = "" if st.session_state.get("primary_section") == "TSRP Score" else (
    f'<div class="compact-score"><span>TSRP Score</span><b>{esc(score(analysis.get("reality_score")))}</b><span>{esc(analysis.get("confidence", "N/A"))} confidence</span></div>'
)
render_html(
    f'<div class="company-strip"><div><h1>{esc(company_name)} <span style="color:var(--tsrp-muted);font:600 .46em var(--tsrp-mono)">{esc(ticker)}</span></h1>'
    f'<p>{esc(friendly_exchange(info.get("exchange")))} · {esc(friendly_currency(trading_currency))} · {esc(money(analysis.get("price"), trading_currency, display_currency, display_fx_trading))} · '
    f'{esc(percent(price_change) if price_change is not None else "N/A")} vs prior close · As of {esc(as_of)}</p>'
    f'<div class="company-tags"><span class="company-tag"><strong>{esc(sector)}</strong></span><span class="company-tag">{esc(industry)}</span>'
    f'<span class="source-tag"><strong>Yahoo Finance</strong> market data</span><span class="source-tag"><strong>SEC EDGAR</strong> {esc("live facts" if analysis.get("has_sec") else "status disclosed")}</span></div></div>'
    f'{compact_score_markup}</div>'
)

render_html('<div class="section-nav-label">Research questions</div><div class="section-nav">')
active_section = select_primary_section()
render_html("</div>")

current_snapshot = snapshot_payload(analysis, company_name, ticker)
track_snapshot_state(ticker, current_snapshot)
if active_section == "TSRP Score":
    render_score_section(analysis, company_name, ticker, display_currency, display_fx_reporting, display_fx_trading, as_of)
elif active_section == "Expectations vs Reality":
    render_expectations_section(analysis, yahoo_data, ticker, display_currency, display_fx_reporting)
elif active_section == "Valuation":
    render_valuation_section(analysis, display_currency, display_fx_reporting, display_fx_trading)
elif active_section == "Catalysts & Risks":
    render_catalysts_risks_section(analysis, ticker)
elif active_section == "What Changed?":
    render_changed_section(analysis, company_name, ticker, display_currency, display_fx_reporting)

render_html(
    f'<div class="app-footer-new">TSRP · Yahoo Finance · SEC EDGAR status disclosed · Data as of {esc(as_of)}<br>{esc(EDUCATIONAL_DISCLAIMER)}</div></div>'
)
st.stop()

st.markdown('<div class="app-wrap">', unsafe_allow_html=True)

render_app_header()

try:
    col_a, col_c = st.columns([5.2, 1], gap="small", vertical_alignment="center")
except TypeError:
    col_a, col_c = st.columns([5.2, 1])
with col_a:
    typed = st.text_input(
        "Search",
        placeholder="Search a company stock or ticker",
        label_visibility="collapsed",
        key="company_search",
    )
with col_c:
    submitted = st.button("Analyze company", key="analyze_company", width="stretch", type="primary")

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

if not submitted:
    st.session_state.search_hits = []

typed_query = str(typed or "").strip()
live_hits = []
show_live_company_search = not st.session_state.pop("suppress_company_suggestions", False)
if show_live_company_search and len(typed_query) >= 2 and typed_query.upper() != st.session_state.get("ticker", "").upper():
    live_hits = search_companies(typed_query)

selected_hit = render_company_suggestions(st.session_state.search_hits or live_hits, "pick_company")
if selected_hit:
    st.session_state.ticker = selected_hit["symbol"]
    st.session_state.pending_search = selected_hit.get("name") or selected_hit["symbol"]
    st.session_state.search_hits = []
    st.session_state.suppress_company_suggestions = True
    st.session_state.ticker_error = None
    st.session_state.invalid_ticker = ""
    st.rerun()

if st.session_state.search_hits:
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
      <div class="hero-copy">Search a company name or stock ticker. Indexes, commodities, currencies, and funds are excluded. The model reverse-solves the sales growth today’s price needs, then sets it next to history and consensus.</div>
    </div>
    <div class="home-steps">
      <div><div class="n">1 · Price</div><p>Start from the latest Yahoo Finance quote available. No fair-value guess first.</p></div>
      <div><div class="n">2 · Required growth</div><p>Solve for the sales path that justifies that price in the reporting currency.</p></div>
      <div><div class="n">3 · Check</div><p>Compare with the company’s history and analyst consensus.</p></div>
    </div>
  </div>
  <div class="source-line">{esc(EDUCATIONAL_DISCLAIMER)}</div>
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

if not ticker_format_ok(ticker) or not company_symbol_allowed(ticker):
    st.session_state.ticker = ""
    st.session_state.invalid_ticker = ticker
    st.session_state.ticker_error = COMPANY_ONLY_MESSAGE
    render_ticker_error(ticker, st.session_state.ticker_error)
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

with st.spinner(f"Loading {ticker}..."):
    yahoo_data = fetch_yahoo_data(ticker)
    info = yahoo_data["info"]

if not yahoo_data_is_valid(yahoo_data, ticker):
    st.session_state.ticker = ""
    st.session_state.invalid_ticker = ticker
    st.session_state.ticker_error = (
        COMPANY_ONLY_MESSAGE
        if non_equity_reason(ticker, (yahoo_data.get("info") or {}).get("quoteType"))
        else f"“{ticker}” was not found on Yahoo Finance."
    )
    render_ticker_error(ticker, st.session_state.ticker_error)
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

remember_ticker(ticker)
sync_ticker_query(ticker)

with st.spinner("Checking SEC filing coverage..."):
    sec_facts = fetch_sec_companyfacts(ticker)
analysis = analyze_company(yahoo_data, sec_facts)
reporting_currency = analysis["reporting_currency"]
trading_currency = analysis["trading_currency"]

_, cur_col, _ = st.columns([1.0, 1.8, 1.0])
with cur_col:
    display_currency = st.selectbox(
        "Show amounts in",
        DISPLAY_CURRENCIES,
        key="display_currency",
        help="Display only. The reverse DCF still uses the company’s reporting currency.",
    )

raw_fx_reporting = fx_rate(reporting_currency, display_currency)
raw_fx_trading = fx_rate(trading_currency, display_currency)
display_fx_reporting = raw_fx_reporting
display_fx_trading = raw_fx_trading
if reporting_currency == display_currency:
    display_fx_reporting = 1.0
if trading_currency == display_currency:
    display_fx_trading = 1.0

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
            "Amounts requiring conversion are shown as N/A; no relabeling is applied."
        )
    else:
        currency_note = f"{reporting_currency} → {display_currency} @ {display_fx_reporting:.4f}"

if trading_currency != display_currency and raw_fx_trading is None:
    st.warning(
        f"Could not fetch FX rate for {trading_currency}/{display_currency}. "
        "Quote prices requiring conversion are shown as N/A."
    )

tone = score_tone(analysis["reality_score"])
active_detail = st.session_state.get("detail_section", "Overview")
previous_price = first_value(info, "previousClose", "regularMarketPreviousClose")
price_change = None
if analysis.get("price") is not None and previous_price not in (None, 0):
    price_change = analysis["price"] / previous_price - 1
price_change_label = percent(price_change) if price_change is not None else "N/A"
exchange = info.get("exchange") or "Exchange unavailable"
as_of = analysis.get("last_refreshed") or "N/A"

render_html(
    f'<div class="result-head">'
    f'<div><div class="hero-title">{esc(company_name)}</div>'
    f'<div class="result-meta">{esc(ticker)} · {esc(exchange)}'
    f'{f" · {esc(sector)}" if sector and sector != "Unknown sector" else ""}'
    f' · {esc(trading_currency)} · {esc(money(analysis.get("price"), trading_currency))}'
    f' · {esc(price_change_label)} vs prior close · As of {esc(as_of)}</div>'
    f'<div class="badge-row">'
    f'<span class="source-chip"><span class="status-dot"></span>Yahoo Finance</span>'
    f'<span class="source-chip">SEC EDGAR · {esc("live facts" if analysis.get("has_sec") else "status disclosed")}</span>'
    f'<span class="source-chip">Reporting currency: {esc(reporting_currency)}</span>'
    f'</div>'
    f'<div class="hero-copy" style="margin-top:10px">{esc(conclusion_text(analysis))}</div></div>'
    f'<div class="result-score {esc(tone)}"><em>Evidence quality</em><b>{esc(score(analysis["reality_score"]))}</b><div class="result-meta">0–100 heuristic signal · {esc(analysis.get("confidence", "N/A"))} confidence</div></div>'
    f"</div>"
)

render_sec_status_strip(analysis)

bad_flags = [label for label, level in analysis["quality_flags"] if level in ("warn", "bad")]
if bad_flags:
    render_html(f'<div class="quality-flags">{esc(" · ".join(bad_flags[:3]))}</div>')

render_html(implied_line_html(analysis))

render_html(
    f'<div class="section-label">Market Snapshot <small>Latest available market and operating data</small></div>'
    f'<div class="metric-grid">'
    f'{metric_card("Current price", money(analysis["price"], trading_currency, display_currency, display_fx_trading), f"{trading_currency} quote · As of {as_of}", "blue")}'
    f'{metric_card("Market capitalization", money(analysis["market_cap"], trading_currency, display_currency, display_fx_trading), f"Equity value · {trading_currency}", "purple")}'
    f'{metric_card("Revenue", money(analysis["revenue"], reporting_currency, display_currency, display_fx_reporting), f"Latest available annual · {reporting_currency}", "green")}'
    f'{metric_card("Free cash flow", money(analysis["free_cash_flow"], reporting_currency, display_currency, display_fx_reporting), f"Period-aligned cash conversion · {reporting_currency}", "cyan")}'
    f"</div>"
)

render_data_quality(analysis)


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


def render_overview_sections(analysis, company_name, ticker, sector, industry, display_currency, display_fx_reporting, display_fx_trading):
    render_html('<div class="section-label">Core research signal <small>Evidence first · heuristic, not a recommendation</small></div>')
    left, right = st.columns([1, 1], gap="large")
    with left:
        render_html(
            f'<div class="panel"><div class="panel-kicker">Price reality</div>'
            f'<div class="signal-copy">{esc(conclusion_text(analysis))}</div>'
            f'{implied_line_html(analysis)}'
            f'<div class="signal-limitations">The signal compares a multi-year price-implied path with historical and near-term analyst evidence. It does not forecast returns.</div></div>'
        )
        render_html(
            f'<div class="panel"><div class="panel-kicker">Market expectations</div>'
            f'{make_rows(pricing_points(analysis, display_currency, display_fx_reporting))}'
            f'</div>'
        )
    with right:
        render_html(
            f'<div class="panel"><div class="panel-kicker">Evidence quality</div>'
            f'{score_bars_html(analysis)}'
            f'<div class="signal-limitations">0–100 heuristic coverage signal. Missing evidence is disclosed and lowers confidence; it is not treated as positive evidence.</div></div>'
        )
        render_html(
            f'<div class="panel"><div class="panel-kicker">Model assumptions</div>'
            f'{make_rows([("Forecast horizon", f"{FORECAST_YEARS} years"), ("Discount rate", percent(analysis.get("discount_rate"))), ("Terminal growth", percent(analysis.get("terminal_growth"))), ("FCF margin used", percent(analysis.get("model_fcf_margin"))), ("Solver status", analysis.get("solver_status", "N/A"))])}'
            f'</div>'
        )

    risks = risk_rows(analysis)
    render_html(
        f'<div class="section-label">Key risks <small>Readable warnings tied to the current evidence</small></div>'
        f'<div class="panel"><div class="panel-kicker">Key risks</div>'
    )
    for risk_index, (title, detail, why) in enumerate(risks):
        risk_cols = st.columns([0.75, 1.8, 0.45], gap="small")
        with risk_cols[0]:
            first_class = " risk-first" if risk_index == 0 else ""
            render_html(f'<div class="risk-cell{first_class}"><strong>{esc(title)}</strong></div>')
        with risk_cols[1]:
            render_html(f'<div class="risk-cell{first_class}"><span>{esc(detail)}<br>{esc(why)}</span></div>')
        with risk_cols[2]:
            review_target = "Expectations" if title in {"Growth burden", "Expectation burden", "Cash conversion"} else "Data"
            if st.button(
                "Review",
                key=f"review_risk_{ticker}_{risk_index}",
                width="stretch",
                type="secondary",
                help=f"Open {review_target} for {title.lower()} evidence",
            ):
                st.session_state.detail_section = review_target
                st.session_state.pending_review = title
                st.rerun()
    render_html('</div></div>')

    source_values = provenance_values(analysis, display_currency, display_fx_reporting, display_fx_trading)
    source_rows = []
    for label, source in analysis.get("sources", {}).items():
        source_label = source or "Unavailable"
        if label == "SEC EDGAR status":
            source_label = "SEC EDGAR"
        elif label == "Yahoo Finance status":
            source_label = "Yahoo Finance"
        value = source_values.get(label, "N/A")
        source_rows.append((label, value if value == source_label else f"{value} · {source_label}"))
    sources_markup = make_rows(source_rows)
    render_html(
        f'<div class="overview-grid">'
        f'<div class="panel"><div class="panel-kicker">Data sources</div>{sources_markup}</div>'
        f'<div class="panel"><div class="panel-kicker">Research notes</div>'
        f'<div class="note-box">{esc(" · ".join(label for label, _ in analysis.get("quality_flags", [])[:4]) or "No material coverage warnings.")}</div>'
        f'<div class="signal-limitations">{esc(company_name)} · {esc(ticker)} · {esc(sector)} · {esc(industry)}</div></div>'
        f'</div>'
    )


if active_detail in {"Overview", "Expectations"} and any(analysis[k] is not None for k in ("required_growth", "consensus_growth", "historical_growth")):
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

if active_detail == "Overview":
    render_overview_sections(analysis, company_name, ticker, sector, industry, display_currency, display_fx_reporting, display_fx_trading)

def pick_detail_section():
    options = ["Overview", "Price", "Expectations", "Scenarios", "Compare", "Data", "Methodology"]
    current = st.session_state.get("detail_section")
    if hasattr(st, "segmented_control"):
        selected = st.segmented_control(
            "Research section",
            options,
            default=current if current in options else "Overview",
            key="detail_nav",
            label_visibility="collapsed",
        ) or current or "Overview"
        if selected != current:
            st.session_state.detail_section = selected
            st.rerun()
        return selected

    keyed_options = [(name, f"nav_{name.lower()}") for name in options]
    cols = st.columns(len(keyed_options), gap="small")
    for col, (name, key) in zip(cols, keyed_options):
        with col:
            if st.button(name, key=key, width="stretch", type="primary" if current == name else "secondary"):
                st.session_state.detail_section = name
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


def cashflow_trend_dataframe(yahoo_data):
    """Build a period-aligned annual cash-flow trend without manufacturing values."""
    financials = yahoo_data.get("financials")
    cashflow = yahoo_data.get("cashflow")
    revenue = get_row(financials, ["Total Revenue", "Operating Revenue"])
    ocf = get_row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = get_row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    reported = get_row(cashflow, ["Free Cash Flow"])
    if revenue is None or (ocf is None and reported is None):
        return pd.DataFrame()

    rows = []
    for period in sorted(set(revenue.index), reverse=True):
        revenue_value = safe_float(revenue.get(period))
        ocf_value = safe_float(ocf.get(period)) if ocf is not None else None
        capex_value = normalize_capex(capex.get(period)) if capex is not None else None
        reported_value = safe_float(reported.get(period)) if reported is not None else None
        fcf_value = compute_fcf(ocf_value, capex_value, reported_value)
        if revenue_value is None or fcf_value is None:
            continue
        rows.append({
            "Period": str(period)[:10],
            "Revenue": revenue_value,
            "Free cash flow": fcf_value,
            "FCF margin": fcf_value / revenue_value if revenue_value > 0 else None,
        })
    return pd.DataFrame(rows)


def render_expectations_view(analysis, yahoo_data, display_currency, display_fx_reporting):
    historical_years = analysis.get("historical_growth_years") or 0
    render_html(
        '<div class="section-label">Expectations <small>What the current price appears to require versus operating evidence</small></div>'
    )
    render_html(
        f'<div class="metric-grid">'
        f'{metric_card("Implied growth", required_growth_label(analysis), "Multi-year price-implied revenue path", "blue")}'
        f'{metric_card("Historical growth", percent(analysis.get("historical_growth")), f"{historical_years}-year revenue CAGR", "green")}'
        f'{metric_card("Analyst consensus", percent(analysis.get("consensus_growth")), "Near-term next-FY estimate", "purple")}'
        f'{metric_card("Current FCF margin", percent(analysis.get("fcf_margin")), "Latest compatible period", "cyan")}'
        f'</div>'
    )
    left, right = st.columns([1, 1], gap="large")
    with left:
        render_html('<div class="panel"><div class="panel-kicker">Growth reality</div><div class="signal-copy">The price is a claim about future operating performance.</div>')
        render_html(implied_line_html(analysis))
        hist_years = analysis.get("historical_growth_years") or 0
        render_html(
            f'{growth_bar("Price-implied", analysis["required_growth"] if not analysis.get("growth_clamped") else None, "req")}'
            f'{growth_bar("Analyst consensus", analysis.get("consensus_growth"), "con")}'
            f'{growth_bar(f"Historical ({hist_years}y)", analysis.get("historical_growth"), "his")}'
            f'{growth_verdict(analysis)}'
        )
        render_html('</div>')
    with right:
        render_html(
            f'<div class="panel"><div class="panel-kicker">Model assumptions</div>'
            f'{make_rows(pricing_points(analysis, display_currency, display_fx_reporting))}'
            f'<div class="note-box">Analyst consensus is a near-term comparison. It is not the same horizon as the multi-year reverse-DCF path.</div></div>'
        )

    trend = cashflow_trend_dataframe(yahoo_data)
    render_html('<div class="section-label">Cash-flow quality <small>Annual operating cash flow and capex are period-aligned where available</small></div>')
    if trend.empty:
        st.info("Cash-flow trend is unavailable for the periods returned by Yahoo Finance.")
    else:
        display = trend.copy()
        display["Revenue"] = display["Revenue"].map(lambda value: money(value, analysis["reporting_currency"], display_currency, display_fx_reporting))
        display["Free cash flow"] = display["Free cash flow"].map(lambda value: money(value, analysis["reporting_currency"], display_currency, display_fx_reporting))
        display["FCF margin"] = display["FCF margin"].map(percent)
        st.dataframe(display, width="stretch", hide_index=True)
    render_html(
        '<div class="panel"><div class="panel-kicker">How to read this page</div>'
        '<p class="hero-copy">A demanding implied growth rate is not a prediction. It is a description of the operating path embedded in the current enterprise value. TSRP places that path beside history and consensus, then shows which evidence is missing.</p></div>'
    )


detail = pick_detail_section()

pending_review = st.session_state.pop("pending_review", None)
if pending_review:
    st.info(f"Reviewing {pending_review}: supporting evidence is shown below.")

if detail == "Price":
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
            if st.button("↻", key="refresh_chart", help="Refresh price history"):
                fetch_yahoo_data.clear()
                fetch_price_history.clear()
                st.rerun()

    period = "max" if chart_tf == "Max" else ("5y" if chart_tf in {"3Y", "5Y"} else "1y")
    history = fetch_price_history(ticker, period)
    if history is None or history.empty:
        st.info("No price history available for this ticker.")
    else:
        render_html(
            f'<div class="section-label">Price history <small>{esc(ticker)} · {esc(display_currency)} · Yahoo Finance · As of {esc(analysis.get("last_refreshed", "N/A"))}</small></div>'
        )
        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        fx = display_fx_trading if trading_currency != display_currency else 1.0
        render_price_chart(history, fx, chart_kind, chart_tf, smas)
        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(f"{ticker} · {chart_tf} · prices in {display_currency} · Yahoo Finance · values may be delayed")

elif detail == "Expectations":
    render_expectations_view(analysis, yahoo_data, display_currency, display_fx_reporting)

elif detail == "Scenarios":
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
            '<div class="whatif-note"><b>Hypothetical scenario inputs.</b> Set your own assumptions and see the enterprise value and share-price equivalent they produce. '
            "These outputs are derived from the model, not targets, recommendations, or expected returns. "
            f"{margin_note}</div>"
        )

        preset_col, reset_col = st.columns([3, 1])
        with preset_col:
            preset = st.selectbox("Assumption set", ["User-defined", "Base", "Cautious", "Optimistic"], key="scenario_preset")
        with reset_col:
            st.write("")
            if st.button("Reset assumptions", key="reset_scenarios", width="stretch"):
                st.session_state.pop("scenario_preset", None)
                for preset_key in ("user-defined", "base", "cautious", "optimistic"):
                    for field in ("growth", "margin", "discount", "terminal"):
                        st.session_state.pop(f"wi_{field}_{preset_key}", None)
                st.rerun()

        preset_delta = {"User-defined": 0.0, "Base": 0.0, "Cautious": -0.05, "Optimistic": 0.05}[preset]
        margin_delta = {"User-defined": 0.0, "Base": 0.0, "Cautious": -0.03, "Optimistic": 0.03}[preset]
        discount_delta = {"User-defined": 0.0, "Base": 0.0, "Cautious": 0.02, "Optimistic": -0.01}[preset]
        terminal_delta = {"User-defined": 0.0, "Base": 0.0, "Cautious": -0.005, "Optimistic": 0.005}[preset]
        preset_note = "User-defined inputs" if preset == "User-defined" else f"{preset} sensitivity around the available evidence; adjust or reset the inputs below."
        render_html(f'<div class="note-box">{esc(preset_note)}</div>')
        widget_suffix = preset.lower().replace(" ", "-")
        growth_key = f"wi_growth_{widget_suffix}"
        margin_key = f"wi_margin_{widget_suffix}"
        discount_key = f"wi_discount_{widget_suffix}"
        terminal_key = f"wi_terminal_{widget_suffix}"

        sl_left, sl_right = st.columns(2)
        with sl_left:
            wi_growth = st.slider("User assumption · starting revenue growth", -10.0, 40.0, round(min(max(base_growth_pct + preset_delta * 100, -10.0), 40.0), 1), 0.5, format="%.1f%%", key=growth_key) / 100
            wi_margin = st.slider("User assumption · FCF margin at maturity", 1.0, 50.0, round(min(max(base_margin_pct + margin_delta * 100, 1.0), 50.0), 1), 0.5, format="%.1f%%", key=margin_key) / 100
        with sl_right:
            wi_discount = st.slider("User assumption · discount rate", 3.0, 20.0, round(min(max((base_discount + discount_delta) * 100, 3.0), 20.0), 2), 0.25, format="%.2f%%", key=discount_key) / 100
            wi_terminal = st.slider("User assumption · terminal growth", 0.0, 6.0, round(min(max((base_terminal + terminal_delta) * 100, 0.0), 6.0), 2), 0.25, format="%.2f%%", key=terminal_key) / 100

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
                implied_equity = None
                ratio = None
                if analysis.get("debt") is not None and analysis.get("cash") is not None and market_cap_reporting:
                    implied_equity = implied_ev - analysis["debt"] + analysis["cash"]
                    ratio = implied_equity / market_cap_reporting
                upside = ratio - 1 if ratio is not None else None
                price = safe_float(analysis["price"])
                implied_price = price * ratio if price is not None and ratio is not None else None
                delta_cls = "up" if upside is not None and upside >= 0 else "down"
                delta_txt = f"{upside:+.1%}" if upside is not None else "N/A"
                price_txt = money(implied_price, trading_currency, display_currency, display_fx_trading) if implied_price is not None else "N/A"
                current_txt = money(price, trading_currency, display_currency, display_fx_trading)

                render_html(
                    f'<div class="metric-grid" style="margin-top:14px">'
                    f'{metric_card("Scenario-implied price", price_txt, "At your assumptions", "blue")}'
                    f'{metric_card("Current price", current_txt, f"{trading_currency} quote", "purple")}'
                    f'{metric_card("Implied enterprise value", money(implied_ev, reporting_currency, display_currency, display_fx_reporting), "DCF at your inputs", "green")}'
                    f'<div class="metric-card accent-cyan"><div class="metric-label">Difference vs current</div>'
                    f'<div class="metric-value"><span class="delta-chip {delta_cls}">{delta_txt}</span></div>'
                    f'<div class="metric-meta">scenario value relative to current quote</div></div>'
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
                if implied_equity is None:
                    st.warning("Scenario enterprise value is available, but a share-price equivalent is unavailable because cash or debt is missing.")

                render_html('<div class="section-label">Scenario sensitivity <small>Derived enterprise value at nearby user-defined assumptions</small></div>')
                sensitivity_growth = [wi_growth - 0.05, wi_growth, wi_growth + 0.05]
                sensitivity_margin = [max(wi_margin - 0.03, 0.01), wi_margin, min(wi_margin + 0.03, 0.50)]
                sensitivity_rows = []
                for growth_value in sensitivity_growth:
                    row = {"Starting growth": percent(growth_value)}
                    for margin_value in sensitivity_margin:
                        value = dcf_enterprise_value(
                            analysis["revenue"], growth_value, margin_value, wi_discount, wi_terminal,
                            start_margin=analysis["fcf_margin"] if analysis.get("fcf_margin") and analysis["fcf_margin"] > 0 else margin_value,
                        )
                        row[f"FCF margin {percent(margin_value)}"] = money(value, reporting_currency, display_currency, display_fx_reporting)
                    sensitivity_rows.append(row)
                st.dataframe(pd.DataFrame(sensitivity_rows), width="stretch", hide_index=True)
                st.caption("Sensitivity outputs are hypothetical model results. They are not target prices, recommendations, or expected returns.")

elif detail == "Compare":
    if st.session_state.pop("clear_compare_search", False):
        st.session_state.compare_search = ""
    cmp_col, btn_col = st.columns([3, 1])
    with cmp_col:
        cmp_input = st.text_input(
            "Peers to compare",
            placeholder="Search a company to add as a peer",
            key="compare_search",
            help="Type a company name or ticker and choose a result. You can also enter comma-separated names.",
        )
    with btn_col:
        st.write("")
        cmp_submit = st.button("Compare", key="compare_submit", width="stretch", type="primary")

    compare_query = str(cmp_input or "").strip()
    compare_hits = []
    if compare_query and "," not in compare_query and ";" not in compare_query and len(compare_query) >= 2:
        compare_hits = search_companies(compare_query)

    selected_peer = render_company_suggestions(compare_hits, "pick_compare")
    if selected_peer:
        selected_symbol = selected_peer["symbol"]
        existing = [symbol for symbol in st.session_state.get("compare_symbols", []) if symbol != ticker]
        if selected_symbol != ticker and selected_symbol not in existing:
            st.session_state.compare_symbols = (existing + [selected_symbol])[:3]
        st.session_state.clear_compare_search = True
        st.rerun()

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
    render_html('<div class="section-label">Data <small>Metric-level provenance, periods, units, and quality status</small></div>')
    st.caption("Unavailable values remain N/A. Converted values identify the display currency; source currency remains in the notes.")
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
    st.dataframe(table, width="stretch", hide_index=True)
    st.download_button(
        "Download CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=f"tsrp_{ticker}_{display_currency}.csv",
        mime="text/csv",
        width="content",
    )
    st.download_button(
        "Download JSON",
        data=export_payload(analysis, company_name, ticker, sector, industry, display_currency),
        file_name=f"tsrp_{ticker}_{display_currency}.json",
        mime="application/json",
        width="content",
    )
    source_values = provenance_values(analysis, display_currency, display_fx_reporting, display_fx_trading)
    source_labels = dict(analysis["sources"])
    source_labels["SEC EDGAR status"] = "SEC EDGAR"
    source_labels["Yahoo Finance status"] = "Yahoo Finance"
    source_table = pd.DataFrame(
        [
            [label, source_values.get(label, "N/A"), source_labels.get(label) or "Unavailable"]
            for label in analysis["sources"]
        ],
        columns=["Data item", "Value", "Source"],
    )
    render_html('<div class="section-label">Data sources <small>Source coverage for this analysis</small></div>')
    st.dataframe(source_table, width="stretch", hide_index=True)
    st.caption(f"Market data: Yahoo Finance. Annual filing facts: {analysis.get('sec_status', 'N/A')}.")
    if reporting_currency != trading_currency:
        st.caption(
            f"Statements are in {reporting_currency}. Price and market cap are in {trading_currency}."
        )

elif detail == "Methodology":
    render_methodology()

render_html(
    f'<div class="app-footer">{esc(APP_SHORT)} · Yahoo Finance · SEC EDGAR status disclosed · {esc(EDUCATIONAL_DISCLAIMER)}</div>'
)

st.markdown("</div>", unsafe_allow_html=True)
