from datetime import datetime
import html
import math
import re

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
                increasing_line_color=UP_COLOR,
                increasing_fillcolor=UP_COLOR,
                decreasing_line_color=DOWN_COLOR,
                decreasing_fillcolor=DOWN_COLOR,
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
                line=dict(color="#2962ff", width=2.2),
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
            UP_COLOR if c >= o else DOWN_COLOR
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
    markup = markup.strip()
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def esc(value):
    return html.escape("" if value is None else str(value))


def brand_logo_svg(size="sm"):
    # Abstract mark: rising bars + pulse node (Apple-clean + TradingView terminal vibe)
    return (
        f'<div class="logo-mark{" logo-lg" if size == "lg" else ""}" aria-hidden="true">'
        '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="4" y="18" width="4" height="8" rx="1.2" fill="white" opacity="0.55"/>'
        '<rect x="11" y="12" width="4" height="14" rx="1.2" fill="white" opacity="0.75"/>'
        '<rect x="18" y="7" width="4" height="19" rx="1.2" fill="white"/>'
        '<path d="M5 15.5 L12 11 L19 13.5 L27 6" stroke="#7CFFB2" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<circle cx="27" cy="6" r="2.2" fill="#7CFFB2"/>'
        "</svg></div>"
    )


def brand_lockup_html(subtitle="Expectation Reality Check"):
    return (
        f'<div class="brand-lockup">{brand_logo_svg("sm")}'
        f"<div><div class='brand-title'>TSRP</div>"
        f"<div class='brand-sub'>{esc(subtitle)}</div></div></div>"
    )


st.markdown(
    """
    <style>
    :root {
        --bg: #030405;
        --bg-elevated: #080a0f;
        --card: #0c0f14;
        --card-hover: #11151c;
        --surface: #10141b;
        --surface-2: #161b24;
        --text: #f2f4f8;
        --text-secondary: #9aa0ab;
        --text-tertiary: #5c6370;
        --blue: #2962ff;
        --blue-bright: #5b8cff;
        --blue-soft: rgba(41, 98, 255, 0.16);
        --purple: #7c5cff;
        --green: #089981;
        --green-soft: rgba(8, 153, 129, 0.18);
        --orange: #f7931a;
        --orange-soft: rgba(247, 147, 26, 0.16);
        --red: #f23645;
        --red-soft: rgba(242, 54, 69, 0.16);
        --border: rgba(255, 255, 255, 0.06);
        --border-strong: rgba(255, 255, 255, 0.11);
        --fill: rgba(255, 255, 255, 0.04);
        --grid: rgba(255, 255, 255, 0.025);
        --shadow: 0 12px 40px rgba(0, 0, 0, 0.55);
        --shadow-soft: 0 4px 20px rgba(0, 0, 0, 0.35);
        --radius-xl: 20px;
        --radius-lg: 14px;
        --radius-md: 10px;
        --radius-sm: 7px;
        --mono: "SF Mono", "JetBrains Mono", "Menlo", "Consolas", monospace;
    }

    header[data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"],
    #MainMenu,
    footer { display: none !important; }

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", sans-serif !important;
        -webkit-font-smoothing: antialiased;
    }

    .stApp {
        background-color: var(--bg);
        background-image:
            linear-gradient(var(--grid) 1px, transparent 1px),
            linear-gradient(90deg, var(--grid) 1px, transparent 1px),
            radial-gradient(ellipse 55% 45% at 0% -5%, rgba(41, 98, 255, 0.14), transparent 55%),
            radial-gradient(ellipse 45% 35% at 100% 0%, rgba(8, 153, 129, 0.1), transparent 50%),
            radial-gradient(ellipse 70% 45% at 50% 110%, rgba(41, 98, 255, 0.06), transparent 60%);
        background-size: 56px 56px, 56px 56px, auto, auto, auto;
        color: var(--text);
    }

    .block-container {
        max-width: 1240px;
        padding-top: 1.25rem;
        padding-bottom: 2.5rem;
    }

    .app-wrap { animation: fadeIn .5s ease both; }

    .terminal-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 16px 20px;
        margin-bottom: 16px;
        background: rgba(12, 15, 20, 0.82);
        border: 1px solid var(--border-strong);
        border-radius: var(--radius-xl);
        backdrop-filter: blur(24px) saturate(180%);
        -webkit-backdrop-filter: blur(24px) saturate(180%);
        box-shadow: var(--shadow-soft), inset 0 1px 0 rgba(255, 255, 255, 0.04);
    }

    .topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
        margin-bottom: 18px;
    }

    .brand-lockup { display: flex; align-items: center; gap: 14px; }

    .brand-mark, .logo-mark {
        width: 44px;
        height: 44px;
        border-radius: 12px;
        display: grid;
        place-items: center;
        background: linear-gradient(145deg, #3d7bff 0%, #1a4fd6 55%, #0f2d8a 100%);
        color: #fff;
        font-size: .92rem;
        font-weight: 800;
        box-shadow: 0 6px 24px rgba(41, 98, 255, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.2);
        flex-shrink: 0;
        overflow: hidden;
    }

    .brand-mark svg, .logo-mark svg { width: 26px; height: 26px; display: block; }

    .logo-mark.logo-lg {
        width: 64px;
        height: 64px;
        border-radius: 18px;
        margin-bottom: 18px;
        box-shadow: 0 10px 36px rgba(41, 98, 255, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.22);
    }

    .logo-mark.logo-lg svg { width: 36px; height: 36px; }

    .brand-title {
        font-size: 1.22rem;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.035em;
    }

    .brand-sub {
        color: var(--text-tertiary);
        font-size: .8rem;
        margin-top: 2px;
        letter-spacing: 0.02em;
    }

    .try-section {
        margin: 14px 0 6px;
        padding: 16px 16px 12px;
        border: 1px solid var(--border-strong);
        border-radius: var(--radius-lg);
        background: linear-gradient(180deg, rgba(16, 20, 27, 0.95), rgba(8, 10, 14, 0.95));
        box-shadow: var(--shadow-soft);
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

    /* Quick-start / try-instead ticker pills in the main pane */
    [data-testid="stMain"] [data-testid="stHorizontalBlock"] .stButton button[kind="secondary"] {
        background: rgba(41, 98, 255, 0.08) !important;
        border: 1px solid rgba(41, 98, 255, 0.28) !important;
        color: #c9d8ff !important;
        border-radius: 12px !important;
        font-family: var(--mono) !important;
        font-weight: 700 !important;
        letter-spacing: 0.03em !important;
        box-shadow: none !important;
        min-height: 48px !important;
    }

    [data-testid="stMain"] [data-testid="stHorizontalBlock"] .stButton button[kind="secondary"]:hover {
        background: rgba(41, 98, 255, 0.18) !important;
        border-color: rgba(41, 98, 255, 0.5) !important;
        color: #fff !important;
    }

    .header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

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
        gap: 7px;
        padding: 6px 12px;
        border-radius: 999px;
        background: var(--fill);
        border: 1px solid var(--border);
        color: var(--text-secondary);
        font-size: .72rem;
        font-weight: 600;
        letter-spacing: .04em;
        text-transform: uppercase;
    }

    .live-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--green);
        box-shadow: 0 0 8px rgba(38, 166, 154, 0.7);
        animation: pulse 2s ease infinite;
    }

    .card, .hero-card, .panel, .metric-card, .learn-card, .risk-item, .info-strip, .empty-state, .score-panel {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        box-shadow: var(--shadow-soft);
    }

    .hero-card, .panel, .score-panel, .empty-state, .learn-card {
        background: linear-gradient(165deg, rgba(16, 20, 27, 0.98) 0%, rgba(10, 12, 16, 0.98) 100%);
        box-shadow: var(--shadow), inset 0 1px 0 rgba(255, 255, 255, 0.03);
    }

    .empty-state {
        padding: 36px 32px;
        border-color: var(--border-strong);
        position: relative;
        overflow: hidden;
        text-align: left;
    }

    .empty-state .hero-title {
        max-width: 18ch;
    }

    .empty-state .hero-copy {
        max-width: 42rem;
    }

    .empty-state.error-state {
        border-color: rgba(242, 54, 69, 0.45);
        box-shadow: var(--shadow), 0 0 48px rgba(242, 54, 69, 0.1);
    }

    .empty-state.error-state .eyebrow { color: var(--red); }

    .empty-state.error-state::before {
        content: "";
        position: absolute;
        inset: 0;
        background: radial-gradient(circle at 20% 0%, rgba(242, 54, 69, 0.14), transparent 45%);
        pointer-events: none;
    }

    .empty-state::before {
        content: "";
        position: absolute;
        inset: 0;
        background: radial-gradient(circle at 20% 0%, rgba(41, 98, 255, 0.12), transparent 45%);
        pointer-events: none;
    }

    .hero-card, .panel, .score-panel { padding: 24px 26px; }

    .info-strip {
        padding: 11px 16px;
        margin-bottom: 14px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
        box-shadow: none;
        background: var(--surface);
        border-color: var(--border-strong);
        font-family: var(--mono);
        font-size: .76rem;
    }

    .info-strip span { color: var(--text-secondary); }
    .info-strip b { color: var(--text); font-weight: 600; }

    .eyebrow {
        color: var(--blue);
        font-size: .7rem;
        font-weight: 700;
        letter-spacing: .08em;
        text-transform: uppercase;
    }

    .hero-title {
        font-size: clamp(1.65rem, 3vw, 2.35rem);
        line-height: 1.1;
        font-weight: 650;
        letter-spacing: -0.03em;
        margin: 8px 0 10px;
        color: var(--text);
    }

    .hero-copy, .panel p, .learn-card p, .risk-item p {
        color: var(--text-secondary);
        line-height: 1.58;
        font-size: .95rem;
        margin: 0;
    }

    .results-grid, .two-col, .learn-grid, .feature-grid, .metric-grid {
        display: grid;
        gap: 14px;
    }

    .results-grid { grid-template-columns: 1.25fr .75fr; margin-bottom: 14px; }
    .two-col { grid-template-columns: 1fr 1fr; margin-bottom: 14px; }
    .learn-grid, .feature-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .metric-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 14px; }

    .score-panel {
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
        min-height: 280px;
        border: 1px solid var(--border-strong);
        position: relative;
        overflow: hidden;
    }

    .score-panel::before {
        content: "";
        position: absolute;
        inset: 0;
        background: radial-gradient(circle at 50% 0%, rgba(41, 98, 255, 0.1), transparent 60%);
        pointer-events: none;
    }

    .score-panel.good { border-color: rgba(8, 153, 129, 0.35); box-shadow: var(--shadow), 0 0 60px rgba(8, 153, 129, 0.1); }
    .score-panel.mid { border-color: rgba(247, 147, 26, 0.35); box-shadow: var(--shadow), 0 0 60px rgba(247, 147, 26, 0.08); }
    .score-panel.low { border-color: rgba(242, 54, 69, 0.35); box-shadow: var(--shadow), 0 0 60px rgba(242, 54, 69, 0.08); }

    .score-ring-wrap {
        position: relative;
        width: 148px;
        height: 148px;
        margin: 6px auto 10px;
        z-index: 1;
    }

    .score-ring { width: 100%; height: 100%; filter: drop-shadow(0 0 12px rgba(41, 98, 255, 0.25)); }
    .score-panel.good .score-ring { filter: drop-shadow(0 0 14px rgba(8, 153, 129, 0.35)); }
    .score-panel.mid .score-ring { filter: drop-shadow(0 0 14px rgba(247, 147, 26, 0.3)); }
    .score-panel.low .score-ring { filter: drop-shadow(0 0 14px rgba(242, 54, 69, 0.3)); }

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
        padding: 18px 20px 16px;
        position: relative;
        overflow: hidden;
        transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
        animation: fadeUp .55s ease both;
    }

    .metric-card::before {
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: var(--blue);
        opacity: 0.85;
    }

    .metric-card.accent-purple::before { background: var(--purple); }
    .metric-card.accent-green::before { background: var(--green); }
    .metric-card.accent-cyan::before { background: #00bcd4; }

    .metric-card:nth-child(1) { animation-delay: .04s; }
    .metric-card:nth-child(2) { animation-delay: .08s; }
    .metric-card:nth-child(3) { animation-delay: .12s; }
    .metric-card:nth-child(4) { animation-delay: .16s; }

    .metric-card:hover {
        transform: translateY(-2px);
        background: var(--card-hover);
        border-color: var(--border-strong);
        box-shadow: var(--shadow-soft), 0 0 24px rgba(41, 98, 255, 0.06);
    }

    .metric-label {
        color: var(--text-tertiary);
        font-size: .68rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .06em;
    }

    .metric-value {
        font-family: var(--mono);
        color: var(--text);
        font-size: 1.32rem;
        font-weight: 700;
        letter-spacing: -0.03em;
        margin-top: 10px;
        font-variant-numeric: tabular-nums;
    }

    .panel-pricing { border-top: 2px solid rgba(41, 98, 255, 0.5); }
    .panel-reality { border-top: 2px solid rgba(8, 153, 129, 0.5); }

    .panel h3::before, .learn-card h4::before {
        content: "";
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--blue);
        margin-right: 10px;
        box-shadow: 0 0 10px rgba(41, 98, 255, 0.5);
        vertical-align: middle;
        transform: translateY(-1px);
    }

    .panel-reality h3::before { background: var(--green); box-shadow: 0 0 10px rgba(8, 153, 129, 0.5); }
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
        border-radius: 999px;
        overflow: hidden;
    }

    .score-fill {
        height: 100%;
        border-radius: 999px;
        background: var(--blue);
        box-shadow: 0 0 12px rgba(41, 98, 255, 0.45);
    }

    .score-fill.good { background: var(--green); box-shadow: 0 0 12px rgba(38, 166, 154, 0.4); }
    .score-fill.mid { background: var(--orange); box-shadow: 0 0 12px rgba(247, 147, 26, 0.35); }
    .score-fill.low { background: var(--red); box-shadow: 0 0 12px rgba(239, 83, 80, 0.35); }

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
        background: linear-gradient(180deg, var(--surface) 0%, rgba(12, 15, 20, 0.95) 100%);
        border: 1px solid var(--border-strong);
        border-radius: var(--radius-xl);
        padding: 18px 20px 8px;
        margin-bottom: 16px;
        box-shadow: var(--shadow-soft), inset 0 1px 0 rgba(255, 255, 255, 0.03);
    }

    .stTextInput input, .stSelectbox > div > div {
        background: var(--bg-elevated) !important;
        border: 1px solid var(--border-strong) !important;
        border-radius: var(--radius-md) !important;
        color: var(--text) !important;
        min-height: 44px !important;
        font-family: var(--mono) !important;
        font-size: .9rem !important;
    }

    .stTextInput input::placeholder { color: var(--text-tertiary) !important; }

    .stTextInput input:focus {
        border-color: var(--blue) !important;
        box-shadow: 0 0 0 3px rgba(41, 98, 255, 0.2) !important;
    }

    .stSelectbox label, .stTextInput label {
        color: var(--text-secondary) !important;
        font-size: .72rem !important;
        font-weight: 700 !important;
        letter-spacing: .05em !important;
        text-transform: uppercase !important;
    }

    .stSelectbox svg { fill: var(--text-secondary) !important; }

    .stButton button {
        background: linear-gradient(180deg, #3d7bff 0%, var(--blue) 100%) !important;
        color: #fff !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        border-radius: var(--radius-md) !important;
        min-height: 44px !important;
        font-weight: 650 !important;
        letter-spacing: -0.01em !important;
        box-shadow: 0 4px 14px rgba(41, 98, 255, 0.35) !important;
    }

    .stButton button:hover {
        background: linear-gradient(180deg, #5088ff 0%, #3470ff 100%) !important;
        box-shadow: 0 6px 20px rgba(41, 98, 255, 0.45) !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        border-bottom: 1px solid var(--border);
        background: transparent;
        padding: 0 2px;
    }

    .stTabs [data-baseweb="tab"] {
        background: transparent;
        border: none;
        border-radius: var(--radius-sm) var(--radius-sm) 0 0;
        color: var(--text-tertiary);
        padding: 10px 14px;
        font-weight: 600;
        font-size: .84rem;
        border-bottom: 2px solid transparent;
    }

    .stTabs [data-baseweb="tab"]:hover { color: var(--text-secondary); background: var(--fill); }

    .stTabs [aria-selected="true"] {
        color: var(--text) !important;
        border-bottom: 2px solid var(--blue) !important;
        background: rgba(41, 98, 255, 0.08) !important;
    }

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
        text-align: center;
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
        margin-top: 28px;
        padding: 14px 18px;
        border-radius: var(--radius-lg);
        border: 1px solid var(--border);
        background: rgba(8, 10, 14, 0.75);
        color: var(--text-tertiary);
        font-size: .74rem;
        text-align: center;
        letter-spacing: 0.02em;
    }

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

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #06080b 0%, #04050a 100%) !important;
        border-right: 1px solid var(--border-strong);
        min-width: 300px !important;
    }

    [data-testid="stSidebar"] .block-container,
    [data-testid="stSidebar"] > div {
        background: transparent !important;
    }

    .watch-heading {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: .72rem;
        font-weight: 700;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: .08em;
        margin: 4px 0 4px;
    }

    .watch-heading::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: var(--blue);
        box-shadow: 0 0 8px rgba(41, 98, 255, 0.6);
    }

    [data-testid="stSidebar"] .stButton button {
        background: rgba(255, 255, 255, 0.03) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius-md) !important;
        color: var(--text) !important;
        font-family: var(--mono) !important;
        font-size: .8rem !important;
        font-weight: 700 !important;
        min-height: 38px !important;
        box-shadow: none !important;
        letter-spacing: .01em !important;
        transition: border-color .15s ease, background .15s ease !important;
    }

    [data-testid="stSidebar"] .stButton button:hover {
        background: var(--blue-soft) !important;
        border-color: rgba(41, 98, 255, 0.45) !important;
        box-shadow: none !important;
    }

    [data-testid="stSidebar"] [data-testid="stForm"] {
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 12px 12px 2px;
        box-shadow: none;
    }

    [data-testid="stSidebar"] [data-testid="stForm"] .stButton button {
        background: linear-gradient(180deg, #3d7bff 0%, var(--blue) 100%) !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        color: #fff !important;
        font-family: -apple-system, BlinkMacSystemFont, sans-serif !important;
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
        gap: 7px;
        margin: -6px 0 14px;
    }

    .flag-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 999px;
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
        border-radius: 999px;
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
        border-radius: 999px;
        overflow: hidden;
    }

    .gbar-fill { height: 100%; border-radius: 999px; }
    .gbar-fill.req { background: var(--blue); box-shadow: 0 0 12px rgba(41, 98, 255, 0.4); }
    .gbar-fill.con { background: var(--purple); box-shadow: 0 0 12px rgba(124, 92, 255, 0.4); }
    .gbar-fill.his { background: var(--green); box-shadow: 0 0 12px rgba(8, 153, 129, 0.4); }
    .gbar-fill.neg { background: var(--red); box-shadow: 0 0 12px rgba(242, 54, 69, 0.4); }

    .gbar-verdict {
        margin-top: 16px;
        padding: 12px 14px;
        border-radius: var(--radius-md);
        border: 1px solid var(--border);
        background: var(--fill);
        color: var(--text-secondary);
        font-size: .86rem;
        line-height: 1.5;
    }

    .gbar-verdict b { color: var(--text); }

    .cmp-table { width: 100%; border-collapse: collapse; }

    .cmp-table th {
        text-align: right;
        padding: 10px 12px;
        color: var(--text);
        font-size: .82rem;
        font-weight: 700;
        border-bottom: 1px solid var(--border-strong);
        font-family: var(--mono);
    }

    .cmp-table th:first-child { text-align: left; color: var(--text-tertiary); font-family: inherit; font-weight: 600; }

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
        border-radius: 999px;
        font-family: var(--mono);
        font-size: .8rem;
        font-weight: 700;
    }

    .delta-chip.up { color: var(--green); background: var(--green-soft); }
    .delta-chip.down { color: var(--red); background: var(--red-soft); }

    .stSlider [data-baseweb="slider"] [role="slider"] {
        background: var(--blue) !important;
        border-color: var(--blue) !important;
        box-shadow: 0 0 10px rgba(41, 98, 255, 0.5) !important;
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

    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(12px); }
        to { opacity: 1; transform: translateY(0); }
    }

    @keyframes fadeUp {
        from { opacity: 0; transform: translateY(14px); }
        to { opacity: 1; transform: translateY(0); }
    }

    @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.55; transform: scale(0.92); }
    }

    @media (max-width: 900px) {
        .block-container {
            padding-top: 0.75rem !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            padding-bottom: 2rem !important;
            max-width: 100% !important;
        }

        .results-grid, .two-col, .learn-grid, .feature-grid { grid-template-columns: 1fr; }
        .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
        .topbar, .terminal-header { flex-direction: column; align-items: flex-start; }
        .topbar-note { text-align: left; max-width: none; }
        .header-actions { width: 100%; }

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
            padding: 12px 14px;
            border-radius: 14px;
            margin-bottom: 12px;
        }

        .brand-mark, .logo-mark { width: 36px; height: 36px; border-radius: 10px; font-size: .8rem; }
        .brand-mark svg, .logo-mark svg { width: 20px; height: 20px; }
        .logo-mark.logo-lg { width: 52px; height: 52px; border-radius: 14px; margin-bottom: 14px; }
        .logo-mark.logo-lg svg { width: 28px; height: 28px; }
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
            padding: 12px 12px 4px;
            border-radius: 14px;
            margin-bottom: 12px;
        }

        .stTextInput input, .stSelectbox > div > div {
            min-height: 42px !important;
            font-size: .86rem !important;
        }

        .stButton button {
            min-height: 42px !important;
            border-radius: 10px !important;
        }

        .chart-wrap { padding: 8px 4px 2px; border-radius: 12px; }
        .app-footer { font-size: .68rem; padding: 12px; line-height: 1.45; }

        .gbar-head { font-size: .78rem; }
        .whatif-note { font-size: .8rem; padding: 10px 12px; }

        div[data-testid="stHorizontalBlock"] {
            gap: 0.4rem !important;
        }

        /* Keep search usable: stack form fields more tightly */
        [data-testid="stForm"] [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
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
        "business_weights": {"growth": 0.25, "gross": 0.20, "operating": 0.15, "fcf": 0.20, "roe": 0.10, "balance": 0.10},
        "business_benchmarks": {"gross": 0.70, "operating": 0.30, "fcf": 0.25, "roe": 0.25},
        "expectation_weights": {"required_growth": 0.50, "ev_sales": 0.25, "pe": 0.10, "ev_ebitda": 0.15},
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

    try:
        revenue_estimate = stock.revenue_estimate
    except Exception:
        revenue_estimate = None

    return {
        "info": info or {},
        "fast_info": fast_info or {},
        "financials": financials,
        "balance": balance,
        "cashflow": cashflow,
        "history": history,
        "revenue_estimate": revenue_estimate,
    }


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
    if info.get("shortName") or info.get("longName") or info.get("symbol"):
        history = yahoo_data.get("history")
        if history is not None and not getattr(history, "empty", True):
            return True
    history = yahoo_data.get("history")
    return history is not None and not getattr(history, "empty", True)


@st.cache_data(ttl=600, show_spinner=False)
def ticker_exists(symbol):
    symbol = normalize_ticker(symbol)
    if not ticker_format_ok(symbol):
        return False
    try:
        data = fetch_yahoo_data(symbol)
        return yahoo_data_is_valid(data)
    except Exception:
        return False


def render_ticker_error(symbol, reason=None):
    detail = reason or "Yahoo Finance did not return usable company data for that symbol."
    st.error(f"Invalid ticker: {symbol}")
    render_html(
        f"""
<div class="empty-state error-state">
  {brand_logo_svg("lg")}
  <div class="eyebrow">Error</div>
  <div class="hero-title">Invalid ticker · {esc(symbol)}</div>
  <div class="hero-copy">{esc(detail)} Try a valid symbol like AAPL, SAP.DE, or 7203.T.</div>
</div>
"""
    )
    render_html(
        """
<div class="try-section">
  <div class="try-label">Try instead</div>
  <div class="try-hint">Pick a known symbol to jump back in.</div>
</div>
"""
    )
    tips = st.columns(4)
    for col, tip in zip(tips, ["AAPL", "MSFT", "NVDA", "SAP.DE"]):
        with col:
            if st.button(tip, key=f"err_tip_{tip}", use_container_width=True, type="secondary"):
                st.session_state.ticker = tip
                st.session_state.ticker_error = None
                st.session_state.invalid_ticker = ""
                st.rerun()
    if st.button("Dismiss error", key="dismiss_ticker_error"):
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
            ["Historical Revenue Growth", percent(analysis["historical_growth"])],
            ["Required Revenue Growth", percent(analysis["required_growth"])],
            ["Analyst Consensus Growth", percent(analysis.get("consensus_growth"))],
            ["Analyst Target Price", money(analysis.get("target_mean_price"), trading_currency, display_currency, display_fx_trading)],
            ["EV/Sales", multiple(analysis["ev_sales"])],
            ["EV/EBITDA", multiple(analysis["ev_ebitda"])],
            ["P/E", multiple(analysis["pe"])],
            ["Data Confidence", analysis.get("confidence", "—")],
        ],
        columns=["Metric", "Value"],
    )


@st.cache_data(ttl=300, show_spinner=False)
def fetch_watch_quote(symbol):
    try:
        fast_info = dict(yf.Ticker(symbol).fast_info)
    except Exception:
        return {"price": None, "change": None, "currency": ""}

    price = first_value(fast_info, "lastPrice", "last_price", "regularMarketPrice")
    prev = first_value(fast_info, "previousClose", "previous_close", "regularMarketPreviousClose")
    change = None
    if price is not None and prev not in (None, 0):
        change = (price - prev) / prev * 100
    return {"price": price, "change": change, "currency": str(fast_info.get("currency") or "")}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_watch_score(symbol):
    try:
        data = fetch_yahoo_data(symbol)
        if not data["info"]:
            return None
        return analyze_company(data, None)["reality_score"]
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_compare_analysis(symbol):
    try:
        data = fetch_yahoo_data(symbol)
        if not data["info"]:
            return None
        sec = fetch_sec_companyfacts(symbol)
        result = analyze_company(data, sec)
        result["name"] = data["info"].get("shortName") or data["info"].get("longName") or symbol
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
            ]

            if annual:
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


def dcf_enterprise_value(revenue, growth, fcf_margin, discount_rate=DISCOUNT_RATE, terminal_growth=TERMINAL_GROWTH, years=FORECAST_YEARS):
    revenue = safe_float(revenue)
    fcf_margin = safe_float(fcf_margin)
    if revenue is None or fcf_margin is None or revenue <= 0 or fcf_margin <= 0:
        return None
    if discount_rate <= terminal_growth:
        return None

    present_value = 0
    projected_revenue = revenue
    final_fcf = 0
    for year in range(1, years + 1):
        projected_revenue *= 1 + growth
        final_fcf = projected_revenue * fcf_margin
        present_value += final_fcf / ((1 + discount_rate) ** year)

    terminal_value = final_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    present_value += terminal_value / ((1 + discount_rate) ** years)
    return present_value


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

    low = -0.20
    high = 0.80

    for _ in range(80):
        mid = (low + high) / 2
        if dcf_enterprise_value(revenue, mid, fcf_margin) < enterprise_value:
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
    if required_growth is None:
        quality_flags.append(("Required growth not solvable", "bad"))
    if free_cash_flow is None:
        quality_flags.append(("Free cash flow unavailable", "warn"))
    if fcf_margin is None or fcf_margin <= 0:
        quality_flags.append((f"FCF margin defaulted to {DEFAULT_FCF_MARGIN:.0%}", "warn"))
    if historical_growth is None:
        quality_flags.append(("No revenue growth history", "warn"))
    if cash is None or debt is None:
        quality_flags.append(("Balance sheet incomplete", "warn"))
    if ev_ebitda is None or ev_ebitda <= 0:
        quality_flags.append(("EV/EBITDA unavailable", "info"))
    if pe is None:
        quality_flags.append(("P/E unavailable or negative", "info"))
    if consensus_growth is None:
        quality_flags.append(("No analyst estimates", "info"))
    if sec_facts is None:
        quality_flags.append(("Yahoo data only, no SEC facts", "info"))

    bad_count = sum(1 for _, level in quality_flags if level == "bad")
    warn_count = sum(1 for _, level in quality_flags if level == "warn")
    if bad_count:
        confidence = "Low"
    elif warn_count >= 2:
        confidence = "Medium"
    else:
        confidence = "High"

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
        "consensus_growth": consensus_growth,
        "target_mean_price": target_mean_price,
        "analyst_count": analyst_count,
        "quality_flags": quality_flags,
        "confidence": confidence,
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


def pricing_points(analysis, display_currency=None, display_fx=1.0):
    points = [
        ("Required revenue growth", f"{percent(analysis['required_growth'])} per year"),
        ("FCF margin used in model", percent(analysis["model_fcf_margin"])),
        ("EV/Sales pressure", multiple(analysis["ev_sales"])),
        ("Probability estimate", f"{score(analysis['probability'])}/100"),
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


def score_tone(value):
    value = safe_float(value, 0)
    if value >= 70:
        return "good"
    if value >= 50:
        return "mid"
    return "low"


def score_ring_svg(value, tone):
    pct = clamp(value)
    radius = 54
    circumference = 2 * math.pi * radius
    offset = circumference * (1 - pct / 100)
    colors = {"good": "#089981", "mid": "#f7931a", "low": "#f23645"}
    color = colors.get(tone, "#2962ff")
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
        f'<div class="score-caption">How well current company evidence supports the expectations implied by the market price.</div>'
        f"</div>"
    )


def badges_html(sector, industry, ticker):
    return (
        f'<div class="badge-row">'
        f'<span class="badge badge-ticker">{esc(ticker)}</span>'
        f'<span class="badge badge-muted">{esc(sector)}</span>'
        f'<span class="badge badge-muted">{esc(industry)}</span>'
        f"</div>"
    )


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


def render_compare_chart(results):
    try:
        import plotly.graph_objects as go
    except ImportError:
        return

    symbols = list(results)
    categories = [
        ("Reality Score", "reality_score", "#2962ff"),
        ("Business Quality", "business_quality", "#089981"),
        ("Market Expectations", "market_expectations", "#f7931a"),
        ("Financial Strength", "financial_strength", "#7c5cff"),
    ]
    fig = go.Figure()
    for label, key, color in categories:
        fig.add_trace(
            go.Bar(
                name=label,
                x=symbols,
                y=[results[s][key] for s in symbols],
                marker_color=color,
                marker_line_width=0,
                hovertemplate="%{y:.0f}<extra>" + label + "</extra>",
            )
        )
    fig.update_layout(
        barmode="group",
        bargap=0.3,
        bargroupgap=0.08,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=4, r=4, t=8, b=4),
        height=300,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(color="#9aa0ab", size=11),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis=dict(tickfont=dict(color="#e8eaed", size=13), linecolor="rgba(255,255,255,0.06)"),
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
        ("Required growth /yr", lambda a: plain_cell(percent(a["required_growth"]))),
        ("Analyst consensus (next FY)", lambda a: plain_cell(percent(a.get("consensus_growth")))),
        ("Historical growth (3y)", lambda a: plain_cell(percent(a["historical_growth"]))),
        ("Operating margin", lambda a: plain_cell(percent(a["operating_margin"]))),
        ("FCF margin", lambda a: plain_cell(percent(a["fcf_margin"]))),
        ("EV/Sales", lambda a: plain_cell(multiple(a["ev_sales"]))),
        ("EV/EBITDA", lambda a: plain_cell(multiple(a["ev_ebitda"]))),
        ("P/E", lambda a: plain_cell(multiple(a["pe"]))),
        ("Sector", lambda a: plain_cell(a["sector"])),
        ("Data confidence", lambda a: plain_cell(a.get("confidence", "—"))),
    ]

    header = "<tr><th>Metric</th>" + "".join(f"<th>{esc(s)}</th>" for s in symbols) + "</tr>"
    body = ""
    for label, cell_fn in rows:
        body += f"<tr><td>{esc(label)}</td>" + "".join(cell_fn(results[s]) for s in symbols) + "</tr>"
    return f'<table class="cmp-table">{header}{body}</table>'


if "ticker" not in st.session_state:
    st.session_state.ticker = ""

if "display_currency" not in st.session_state:
    st.session_state.display_currency = "USD"

if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["AAPL", "NVDA", "MSFT", "TSLA"]

if "ticker_error" not in st.session_state:
    st.session_state.ticker_error = None

if "invalid_ticker" not in st.session_state:
    st.session_state.invalid_ticker = ""

if "recent" not in st.session_state:
    st.session_state.recent = []

if st.session_state.display_currency not in DISPLAY_CURRENCIES:
    st.session_state.display_currency = "USD"


def watch_quote_html(quote):
    price = quote["price"]
    change = quote["change"]
    price_txt = f"{price:,.2f}" if price is not None else "—"
    if change is None:
        chg_cls, chg_txt = "flat", ""
    elif change >= 0.005:
        chg_cls, chg_txt = "up", f"+{change:.2f}%"
    elif change <= -0.005:
        chg_cls, chg_txt = "down", f"{change:.2f}%"
    else:
        chg_cls, chg_txt = "flat", "0.00%"
    return (
        f'<div class="watch-quote"><span class="watch-price">{price_txt}</span>'
        f'<span class="watch-chg {chg_cls}">{chg_txt}</span></div>'
    )


with st.sidebar:
    st.markdown('<div class="watch-heading">Watchlist</div>', unsafe_allow_html=True)

    with st.form("watch_add_form", clear_on_submit=True):
        add_col, btn_col = st.columns([2.2, 1])
        with add_col:
            new_symbol = st.text_input("Add ticker", placeholder="Ticker", label_visibility="collapsed")
        with btn_col:
            add_clicked = st.form_submit_button("Add", use_container_width=True)

    if add_clicked:
        new_symbol = normalize_ticker(new_symbol)
        if not new_symbol:
            st.sidebar.error("Enter a ticker symbol.")
        elif not ticker_format_ok(new_symbol):
            st.sidebar.error(f"Invalid ticker format: {new_symbol}")
        elif new_symbol in st.session_state.watchlist:
            st.sidebar.warning(f"{new_symbol} is already on the watchlist.")
        elif not ticker_exists(new_symbol):
            st.sidebar.error(f"Invalid ticker: {new_symbol}")
        else:
            st.session_state.watchlist.append(new_symbol)
            st.sidebar.success(f"Added {new_symbol}")

    if not st.session_state.watchlist:
        st.caption("Watchlist is empty. Add a ticker above.")

    show_watch_scores = st.toggle("Reality scores", value=True, key="watch_show_scores")

    for symbol in list(st.session_state.watchlist):
        quote = fetch_watch_quote(symbol)
        if show_watch_scores:
            sym_col, quote_col, score_col, rm_col = st.columns([1.15, 1.0, 0.62, 0.4])
        else:
            sym_col, quote_col, rm_col = st.columns([1.3, 1.3, 0.5])
        with sym_col:
            if st.button(symbol, key=f"watch_{symbol}", use_container_width=True):
                st.session_state.ticker = symbol
                st.session_state.ticker_error = None
                st.rerun()
        with quote_col:
            st.markdown(watch_quote_html(quote), unsafe_allow_html=True)
        if show_watch_scores:
            with score_col:
                watch_score = fetch_watch_score(symbol)
                if watch_score is None:
                    st.markdown('<div class="watch-score">—</div>', unsafe_allow_html=True)
                else:
                    st.markdown(
                        f'<div class="watch-score {score_tone(watch_score)}">{score(watch_score)}</div>',
                        unsafe_allow_html=True,
                    )
        with rm_col:
            if st.button("✕", key=f"watch_rm_{symbol}", use_container_width=True):
                st.session_state.watchlist.remove(symbol)
                st.rerun()

    current = st.session_state.ticker
    if current and current not in st.session_state.watchlist and not st.session_state.ticker_error:
        st.divider()
        if st.button(f"☆ Watch {current}", key="watch_current", use_container_width=True):
            if ticker_exists(current):
                st.session_state.watchlist.append(current)
                st.rerun()
            else:
                st.sidebar.error(f"Invalid ticker: {current}")

    if st.session_state.recent:
        st.divider()
        st.markdown('<div class="watch-heading">Recent</div>', unsafe_allow_html=True)
        recent_cols = st.columns(min(4, len(st.session_state.recent)))
        for col, symbol in zip(recent_cols, st.session_state.recent[:4]):
            with col:
                if st.button(symbol, key=f"recent_{symbol}", use_container_width=True):
                    st.session_state.ticker = symbol
                    st.session_state.ticker_error = None
                    st.session_state.invalid_ticker = ""
                    st.rerun()


st.markdown('<div class="app-wrap">', unsafe_allow_html=True)

render_html(
    f"""
<div class="terminal-header">
  {brand_lockup_html()}
  <div class="header-actions">
    <div class="live-pill"><span class="live-dot"></span>Live market data</div>
    <div class="live-pill badge-muted">Not investment advice</div>
  </div>
</div>
"""
)

with st.form("search_form"):
    col_a, col_b, col_c = st.columns([3, 1.2, 1])
    with col_a:
        form_default = st.session_state.ticker or st.session_state.invalid_ticker
        typed = st.text_input("Company ticker", value=form_default, placeholder="AAPL, SAP.DE, 7203.T").upper().strip()
    with col_b:
        display_currency = st.selectbox("Display currency", DISPLAY_CURRENCIES, index=DISPLAY_CURRENCIES.index(st.session_state.display_currency))
    with col_c:
        st.write("")
        submitted = st.form_submit_button("Analyze", use_container_width=True)

if submitted:
    typed = normalize_ticker(typed)
    st.session_state.display_currency = display_currency
    if not typed:
        st.session_state.ticker = ""
        st.session_state.invalid_ticker = ""
        st.session_state.ticker_error = "Enter a ticker symbol before analyzing."
    elif not ticker_format_ok(typed):
        st.session_state.ticker = ""
        st.session_state.invalid_ticker = typed
        st.session_state.ticker_error = f"“{typed}” is not a valid ticker format. Use letters/numbers like AAPL, BRK-B, or SAP.DE."
    elif not ticker_exists(typed):
        st.session_state.ticker = ""
        st.session_state.invalid_ticker = typed
        st.session_state.ticker_error = f"“{typed}” was not found on Yahoo Finance."
    else:
        st.session_state.ticker = typed
        st.session_state.invalid_ticker = ""
        st.session_state.ticker_error = None
else:
    display_currency = st.session_state.display_currency

if st.session_state.ticker_error:
    render_ticker_error(st.session_state.invalid_ticker or "input", st.session_state.ticker_error)
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

if not st.session_state.ticker:
    render_html(
        f"""
<div class="empty-state">
  {brand_logo_svg("lg")}
  <div class="eyebrow">Expectation Reality Check</div>
  <div class="hero-title">See what the price is asking for</div>
  <div class="hero-copy">Enter a ticker to translate market price into implied expectations, then compare those against revenue growth, margins, cash flow, and balance sheet evidence.</div>
</div>
"""
    )
    render_html(
        """
<div class="try-section">
  <div class="try-label">Quick start</div>
  <div class="try-hint">No ticker yet? Try one of these.</div>
</div>
"""
    )
    tip_cols = st.columns(4)
    for col, tip in zip(tip_cols, ["AAPL", "MSFT", "NVDA", "BABA"]):
        with col:
            if st.button(tip, key=f"empty_tip_{tip}", use_container_width=True, type="secondary"):
                st.session_state.ticker = tip
                st.session_state.ticker_error = None
                st.session_state.invalid_ticker = ""
                st.rerun()
    render_html(
        """
<div class="feature-grid">
  <div class="panel panel-pricing"><h3>What it does</h3><p>Turns valuation into a simple question: does the business evidence support what the price appears to require?</p></div>
  <div class="panel panel-reality"><h3>Where data comes from</h3><p>Yahoo Finance for market data. SEC EDGAR for official U.S. filing facts when available.</p></div>
</div>
"""
    )

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


ticker = st.session_state.ticker

with st.spinner(f"Loading {ticker}..."):
    yahoo_data = fetch_yahoo_data(ticker)
    info = yahoo_data["info"]
    sec_facts = fetch_sec_companyfacts(ticker)

if not yahoo_data_is_valid(yahoo_data):
    st.session_state.ticker = ""
    st.session_state.invalid_ticker = ticker
    st.session_state.ticker_error = f"“{ticker}” was not found on Yahoo Finance."
    render_ticker_error(ticker, st.session_state.ticker_error)
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

remember_ticker(ticker)

analysis = analyze_company(yahoo_data, sec_facts)
reporting_currency = analysis["reporting_currency"]
trading_currency = analysis["trading_currency"]
raw_fx_reporting = fx_rate(reporting_currency, display_currency)
raw_fx_trading = fx_rate(trading_currency, display_currency)
display_fx_reporting = raw_fx_reporting or 1.0
display_fx_trading = raw_fx_trading or 1.0

company_name = info.get("longName") or info.get("shortName") or ticker
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
        "Quote prices may be unconverted."
    )

render_html(
    f'<div class="info-strip"><span>Reporting <b>{esc(reporting_currency)}</b> · Trading <b>{esc(trading_currency)}</b></span>'
    f'<span>Display <b>{esc(display_currency)}</b>'
    f'{f" · FX {esc(currency_note)}" if reporting_currency != display_currency else ""}</span></div>'
)

flag_chips = "".join(
    f'<span class="flag-chip {level}">{esc(label)}</span>' for label, level in analysis["quality_flags"]
)
render_html(
    f'<div class="flag-strip">'
    f'<span class="conf-chip {esc(analysis["confidence"])}">Data confidence: {esc(analysis["confidence"])}</span>'
    f"{flag_chips}</div>"
)

tone = score_tone(analysis["reality_score"])

summary = (
    f"{company_name} ({ticker}) · Reality {score(analysis['reality_score'])}/100 · "
    f"{score_label(analysis['reality_score'])} · Required growth {percent(analysis['required_growth'])} · "
    f"Consensus {percent(analysis.get('consensus_growth'))} · Confidence {analysis.get('confidence')}"
)
with st.expander("Copy summary", expanded=False):
    st.code(summary, language=None)

render_html(
    f'<div class="results-grid">'
    f'<div class="hero-card"><div class="eyebrow">Company overview</div>'
    f'<div class="hero-title">{esc(company_name)}</div>{badges_html(sector, industry, ticker)}'
    f'<div class="hero-copy">{esc(conclusion_text(analysis))}</div></div>'
    f'{score_panel_html(analysis, tone)}'
    f"</div>"
)

render_html(
    f'<div class="metric-grid">'
    f'{metric_card("Price", money(analysis["price"], trading_currency, display_currency, display_fx_trading), f"{trading_currency} quote · Yahoo Finance", "blue")}'
    f'{metric_card("Market cap", money(analysis["market_cap"], trading_currency, display_currency, display_fx_trading), f"{trading_currency} · Yahoo Finance", "purple")}'
    f'{metric_card("Revenue", money(analysis["revenue"], reporting_currency, display_currency, display_fx_reporting), analysis["sources"]["Revenue"], "green")}'
    f'{metric_card("Free cash flow", money(analysis["free_cash_flow"], reporting_currency, display_currency, display_fx_reporting), analysis["sources"]["Free Cash Flow"], "cyan")}'
    f"</div>"
)

render_html(
    f'<div class="two-col">'
    f'<div class="panel panel-pricing"><h3>What the market is pricing</h3>{make_rows(pricing_points(analysis, display_currency, display_fx_trading))}</div>'
    f'<div class="panel panel-reality"><h3>What the business shows</h3>{make_rows(reality_points(analysis, display_currency, display_fx_reporting))}</div>'
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
    render_html(
        f'<div class="panel" style="margin-bottom:14px"><h3>Growth: required vs expected</h3>'
        f'{growth_bar("Market requires (10y, implied)", analysis["required_growth"], "req")}'
        f'{growth_bar("Analyst consensus (next FY)", analysis["consensus_growth"], "con")}'
        f'{growth_bar("Historical (3y CAGR)", analysis["historical_growth"], "his")}'
        f"{growth_verdict(analysis)}"
        f"</div>"
    )

render_html(f'<div class="panel"><h3>Score breakdown</h3>{score_bars_html(analysis)}</div>')

tab_chart, tab_whatif, tab_compare, tab_learn, tab_evidence, tab_sources, tab_model, tab_risk = st.tabs(
    ["Chart", "What-If", "Compare", "Learn", "Evidence", "Data Sources", "Sector Model", "Expectation Breakers"]
)


def chart_control(label, options, default, key):
    if hasattr(st, "segmented_control"):
        selected = st.segmented_control(label, options, default=default, key=key, label_visibility="collapsed")
        return selected or default
    return st.radio(label, options, index=options.index(default), horizontal=True, key=key, label_visibility="collapsed")


def chart_control_multi(label, options, default, key):
    if hasattr(st, "pills"):
        return st.pills(label, options, selection_mode="multi", default=default, key=key, label_visibility="collapsed") or []
    return st.multiselect(label, options, default=default, key=key, label_visibility="collapsed")


with tab_chart:
    history = yahoo_data["history"]
    if history is None or history.empty:
        st.info("No price history available for this ticker.")
    else:
        ctrl_kind, ctrl_tf, ctrl_ma = st.columns([1, 1.7, 1.5])
        with ctrl_kind:
            chart_kind = chart_control("Chart type", ["Candles", "Line"], "Candles", "chart_kind")
        with ctrl_tf:
            chart_tf = chart_control("Timeframe", CHART_TIMEFRAMES, "1Y", "chart_tf")
        with ctrl_ma:
            ma_selected = chart_control_multi("Moving averages", ["SMA 20", "SMA 50", "SMA 200"], ["SMA 50", "SMA 200"], "chart_ma")
        smas = tuple(int(label.split()[1]) for label in ma_selected if str(label).startswith("SMA "))

        refresh_col, _ = st.columns([1, 5])
        with refresh_col:
            if st.button("↻ Refresh chart data", key="refresh_chart"):
                fetch_yahoo_data.clear()
                st.rerun()

        st.markdown('<div class="chart-wrap">', unsafe_allow_html=True)
        fx = display_fx_trading if trading_currency != display_currency else 1.0
        render_price_chart(history, fx, chart_kind, chart_tf, smas)
        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(f"{ticker} · {chart_tf} · prices in {display_currency} · Yahoo Finance")

with tab_whatif:
    market_cap_reporting = safe_float(analysis["market_cap_reporting"])
    if not analysis["revenue"] or not market_cap_reporting or market_cap_reporting <= 0:
        st.info("Revenue or market cap is unavailable, so the what-if model cannot run for this ticker.")
    else:
        base_growth = analysis["required_growth"]
        if base_growth is None:
            base_growth = analysis["historical_growth"] if analysis["historical_growth"] is not None else 0.08
        base_growth_pct = float(min(max(base_growth * 100, -10.0), 40.0))
        base_margin_pct = float(min(max(analysis["model_fcf_margin"] * 100, 1.0), 50.0))

        render_html(
            '<div class="whatif-note">Set your own assumptions and see the price they justify. '
            "The sliders start at the assumptions currently baked into the market price, so the initial "
            "result is roughly the price today. Push growth or margin to what <b>you</b> believe and see the gap.</div>"
        )

        sl_left, sl_right = st.columns(2)
        with sl_left:
            wi_growth = st.slider("Revenue growth per year (10 yrs)", -10.0, 40.0, round(base_growth_pct, 1), 0.5, format="%.1f%%") / 100
            wi_margin = st.slider("FCF margin at maturity", 1.0, 50.0, round(base_margin_pct, 1), 0.5, format="%.1f%%") / 100
        with sl_right:
            wi_discount = st.slider("Discount rate", 6.0, 15.0, DISCOUNT_RATE * 100, 0.25, format="%.2f%%") / 100
            wi_terminal = st.slider("Terminal growth", 0.0, 4.0, TERMINAL_GROWTH * 100, 0.25, format="%.2f%%") / 100

        if wi_discount <= wi_terminal:
            st.warning("Discount rate must be above terminal growth for the model to converge.")
        else:
            implied_ev = dcf_enterprise_value(analysis["revenue"], wi_growth, wi_margin, wi_discount, wi_terminal)
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
                        f"<b>{percent(req)}</b> annual revenue growth at a {percent(analysis['model_fcf_margin'])} FCF margin, "
                        f"{DISCOUNT_RATE:.0%} discount rate, and {TERMINAL_GROWTH:.0%} terminal growth.</div>"
                    )

with tab_compare:
    with st.form("compare_form"):
        cmp_col, btn_col = st.columns([3, 1])
        with cmp_col:
            cmp_input = st.text_input(
                "Peers to compare",
                placeholder="MSFT, GOOGL",
                help="Up to 3 tickers, comma separated. The current ticker is always included.",
            )
        with btn_col:
            st.write("")
            cmp_submit = st.form_submit_button("Compare", use_container_width=True)

    if cmp_submit:
        peers = [normalize_ticker(s) for s in cmp_input.replace(";", ",").split(",") if s.strip()]
        peers = list(dict.fromkeys(peers))[:3]
        valid_peers = []
        for symbol in peers:
            if not ticker_format_ok(symbol):
                st.error(f"Invalid ticker format: {symbol}")
            elif not ticker_exists(symbol):
                st.error(f"Invalid ticker: {symbol}")
            else:
                valid_peers.append(symbol)
        st.session_state.compare_symbols = valid_peers

    peer_symbols = [s for s in st.session_state.get("compare_symbols", []) if s != ticker]

    if not peer_symbols:
        st.info("Enter one to three peer tickers above to compare them against the current company.")
    else:
        compare_results = {ticker: analysis}
        with st.spinner("Analyzing peers..."):
            for symbol in peer_symbols:
                peer = fetch_compare_analysis(symbol)
                if peer is None:
                    st.error(f"Invalid ticker: {symbol}")
                else:
                    compare_results[symbol] = peer

        if len(compare_results) > 1:
            render_compare_chart(compare_results)
            render_html(f'<div class="panel">{compare_table_html(compare_results)}</div>')
            st.caption("Scores use each company's own sector model. Absolute values are omitted because reporting currencies differ.")

with tab_learn:
    render_html(
        """
<div class="learn-grid">
  <div class="learn-card"><h4>Market expectations</h4><p>The future performance that appears necessary to support the current valuation.</p></div>
  <div class="learn-card"><h4>Business reality</h4><p>The company evidence today: growth, margins, cash flow, cash, debt, and profitability.</p></div>
  <div class="learn-card"><h4>Reality gap</h4><p>Business Quality minus Market Expectations. It shows whether evidence is ahead of or behind the expectation burden.</p></div>
  <div class="learn-card"><h4>Expectation Reality Score</h4><p>The final score summarizing whether company evidence supports what the market appears to expect.</p></div>
</div>
"""
    )

with tab_evidence:
    st.markdown('<div class="section-heading">Company evidence</div>', unsafe_allow_html=True)
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
    st.markdown(f'<div class="section-heading">Sector framework · {sector}</div>', unsafe_allow_html=True)
    st.caption("Weights and benchmarks are selected from Yahoo Finance sector classification.")

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
    risk_html = "".join(
        f'<div class="risk-item"><b>{esc(title)}</b><p>{esc(signal)}. {esc(meaning)}</p></div>'
        for title, signal, meaning in risks
    )
    render_html(f'<div class="risk-list">{risk_html}</div>')

render_html(
    f'<div class="app-footer">Yahoo Finance · SEC EDGAR · {reporting_currency} reporting · '
    f'{display_currency} display · Not investment advice · '
    f'Refreshed {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>'
)

st.markdown("</div>", unsafe_allow_html=True)
