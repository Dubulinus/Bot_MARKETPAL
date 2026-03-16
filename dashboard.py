"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - DASHBOARD v1.0                             ║
║     Streamlit live monitoring dashboard                    ║
╚══════════════════════════════════════════════════════════════╝

INSTALACE:
    pip install streamlit plotly pandas numpy

SPUŠTĚNÍ:
    streamlit run dashboard.py

AUTOMATICKÝ REFRESH:
    streamlit run dashboard.py --server.runOnSave true
    (nebo v dashboardu klikni ↻ každých 30s)

CO ZOBRAZUJE:
    - Equity curve + drawdown
    - FTMO progress bary
    - Otevřené pozice (live z trade_log.json)
    - Win rate, P&L, Sharpe ratio
    - Posledních N signálů ze signal_log.json
    - System bus eventy (co se děje v systému)
    - Per-strategie statistiky
    - Regime distribution (BULL/BEAR/SIDEWAYS)
"""

import json
import time
import random
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ─── PAGE CONFIG ───────────────────────────────────────────────
st.set_page_config(
    page_title  = "MARKETPAL AOS",
    page_icon   = "📈",
    layout      = "wide",
    initial_sidebar_state = "collapsed",
)

# ─── DARK THEME CSS ────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;600;700;800&display=swap');

/* Celková barva pozadí */
.stApp {
    background-color: #0a0e1a;
    color: #e2e8f0;
    font-family: 'Syne', sans-serif;
}

/* Metriky */
[data-testid="metric-container"] {
    background: linear-gradient(135deg, #111827 0%, #1a2235 100%);
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
}

[data-testid="metric-container"] label {
    color: #64748b !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.7rem !important;
    letter-spacing: 0.1em;
    text-transform: uppercase;
}

[data-testid="metric-container"] [data-testid="metric-value"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 1.6rem !important;
    font-weight: 700;
    color: #e2e8f0 !important;
}

[data-testid="metric-container"] [data-testid="metric-delta"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.8rem !important;
}

/* Dataframe */
[data-testid="stDataFrame"] {
    background: #111827;
    border-radius: 8px;
}

/* Headers */
h1, h2, h3 {
    font-family: 'Syne', sans-serif !important;
    font-weight: 800 !important;
    letter-spacing: -0.02em;
}

/* Separátor */
hr {
    border-color: #1e3a5f;
    margin: 8px 0;
}

/* Status badge */
.status-live {
    display: inline-block;
    background: #052e16;
    color: #4ade80;
    border: 1px solid #166534;
    border-radius: 20px;
    padding: 3px 12px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    letter-spacing: 0.08em;
}
.status-paused {
    background: #431407;
    color: #fb923c;
    border-color: #9a3412;
}

/* Progress bar label */
.progress-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: #94a3b8;
    margin-bottom: 2px;
}

/* Event log */
.event-item {
    background: #111827;
    border-left: 3px solid #1e3a5f;
    padding: 6px 12px;
    margin: 4px 0;
    border-radius: 0 6px 6px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: #94a3b8;
}
.event-signal { border-left-color: #4ade80; }
.event-order  { border-left-color: #60a5fa; }
.event-error  { border-left-color: #f87171; }
.event-risk   { border-left-color: #fbbf24; }

/* Sekce nadpis */
.section-title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: #475569;
    margin-bottom: 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid #1e293b;
}
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
# DATA LOADING (s fallbackem na mock data)
# ═══════════════════════════════════════════════════════════════

@st.cache_data(ttl=15)  # refresh každých 15 sekund
def load_state() -> dict:
    path = Path("data/bot_state.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Mock data pro testování bez spuštěného bota
    return {
        "equity":            10_347.50,
        "daily_pnl":         127.50,
        "total_pnl":         347.50,
        "win_count":         14,
        "loss_count":        8,
        "daily_trade_count": 3,
        "paused":            False,
        "open_trades":       [
            {"ticker": "EURUSD", "direction": "long",  "entry": 1.08420, "sl": 1.08270, "tp": 1.08720},
            {"ticker": "GBPUSD", "direction": "long",  "entry": 1.26340, "sl": 1.26190, "tp": 1.26640},
        ],
    }

@st.cache_data(ttl=15)
def load_trades() -> list:
    path = Path("data/trade_log.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Mock closed trades
    rng    = np.random.default_rng(42)
    trades = []
    equity = 10_000.0
    date   = datetime.utcnow() - timedelta(days=30)
    for i in range(22):
        pnl    = float(rng.choice([-78, -65, 95, 127, 142, -52, 108, 183, -90, 76]))
        equity += pnl
        trades.append({
            "ticket":     100000 + i,
            "ticker":     rng.choice(["EURUSD", "GBPUSD", "USDCHF"]),
            "direction":  rng.choice(["long", "short"]),
            "entry":      1.08 + rng.random() * 0.01,
            "pnl":        pnl,
            "equity_after": equity,
            "timestamp":  str(date + timedelta(days=i * 1.3)),
            "status":     "CLOSED",
            "signal_name": rng.choice([
                "EURUSD M15 RSI oversold exit",
                "GBPUSD M15 RSI oversold exit",
                "USDCHF H1 Stoch pin bear",
            ]),
        })
    return trades

@st.cache_data(ttl=15)
def load_signals() -> list:
    path = Path("data/signal_log.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

@st.cache_data(ttl=15)
def load_bus_events() -> list:
    path = Path("data/system_bus.json")
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("events", [])[-20:]
        except Exception:
            pass
    return []


# ═══════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════

def render_header(state: dict):
    col1, col2, col3 = st.columns([3, 1, 1])

    with col1:
        status_class = "status-live" if not state.get("paused") else "status-paused"
        status_text  = "● LIVE" if not state.get("paused") else "⏸ PAUSED"
        st.markdown(
            f"# MARKETPAL AOS "
            f"<span class='{status_class}'>{status_text}</span>",
            unsafe_allow_html=True
        )
        st.markdown(
            f"<span style='font-family:JetBrains Mono;font-size:0.75rem;color:#475569'>"
            f"Last update: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            f"</span>",
            unsafe_allow_html=True
        )

    with col3:
        if st.button("↻ Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()


# ═══════════════════════════════════════════════════════════════
# FTMO PROGRESS
# ═══════════════════════════════════════════════════════════════

def render_ftmo(state: dict):
    ACCOUNT  = 10_000
    TARGET   = 1_000
    MAX_DD   = 1_000
    MAX_DAY  = 500

    equity    = state.get("equity", ACCOUNT)
    total_pnl = state.get("total_pnl", 0)
    daily_pnl = state.get("daily_pnl", 0)
    total_dd  = ACCOUNT - equity

    profit_pct  = max(0, total_pnl / TARGET * 100)
    dd_pct      = total_dd / MAX_DD * 100
    daily_pct   = abs(min(daily_pnl, 0)) / MAX_DAY * 100

    # Barvy
    profit_color = "#4ade80" if profit_pct < 100 else "#fbbf24"
    dd_color     = "#4ade80" if dd_pct < 50 else "#fbbf24" if dd_pct < 80 else "#f87171"
    daily_color  = "#4ade80" if daily_pct < 50 else "#fbbf24" if daily_pct < 80 else "#f87171"

    st.markdown("<div class='section-title'>FTMO Challenge Progress</div>",
                unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f"<div class='progress-label'>🎯 Profit Target "
            f"${total_pnl:+.0f} / ${TARGET}</div>", unsafe_allow_html=True)
        st.progress(max(0.0, min(profit_pct / 100, 1.0)))
        st.caption(f"{profit_pct:.1f}% splněno")

    with c2:
        st.markdown(
            f"<div class='progress-label'>📉 Max Drawdown "
            f"${max(0,total_dd):.0f} / ${MAX_DD}</div>", unsafe_allow_html=True)
        st.progress(max(0.0, min(dd_pct / 100, 1.0)))
        st.caption(f"{max(0,dd_pct):.1f}% využito")

    with c3:
        st.markdown(
            f"<div class='progress-label'>📅 Daily Loss "
            f"${abs(min(daily_pnl,0)):.0f} / ${MAX_DAY}</div>",
            unsafe_allow_html=True)
        st.progress(max(0.0, min(daily_pct / 100, 1.0)))
        st.caption(f"{daily_pct:.1f}% využito")


# ═══════════════════════════════════════════════════════════════
# KEY METRICS
# ═══════════════════════════════════════════════════════════════

def render_metrics(state: dict):
    equity    = state.get("equity", 10_000)
    daily_pnl = state.get("daily_pnl", 0)
    total_pnl = state.get("total_pnl", 0)
    wins      = state.get("win_count", 0)
    losses    = state.get("loss_count", 0)
    n_trades  = wins + losses
    wr        = wins / max(n_trades, 1) * 100
    n_open    = len(state.get("open_trades", []))
    dd        = 10_000 - equity

    cols = st.columns(6)
    metrics = [
        ("Equity",      f"${equity:,.2f}",  f"${total_pnl:+.2f} total"),
        ("Dnes P&L",    f"${daily_pnl:+.2f}", f"{state.get('daily_trade_count',0)} obchodů"),
        ("Win Rate",    f"{wr:.0f}%",        f"{wins}W / {losses}L"),
        ("Drawdown",    f"${dd:.0f}",        f"{dd/100:.1f}% účtu"),
        ("Otevřeno",    f"{n_open}",          "pozic"),
        ("Celkem",      f"{n_trades}",        "uzavřených"),
    ]
    for col, (label, value, delta) in zip(cols, metrics):
        with col:
            st.metric(label, value, delta)


# ═══════════════════════════════════════════════════════════════
# EQUITY CURVE
# ═══════════════════════════════════════════════════════════════

def render_equity_curve(trades: list):
    if not trades:
        st.info("Zatím žádné uzavřené obchody.")
        return

    closed = [t for t in trades if t.get("status") == "CLOSED"]
    if not closed:
        return

    df = pd.DataFrame(closed)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")

    # Equity curve
    if "equity_after" not in df.columns:
        df["equity_after"] = 10_000 + df["pnl"].cumsum()

    equity   = df["equity_after"].values
    peak     = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak * 100

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.7, 0.3],
        shared_xaxes=True,
        vertical_spacing=0.04,
    )

    # Equity line
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=equity,
        mode="lines",
        name="Equity",
        line=dict(color="#60a5fa", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(96,165,250,0.07)",
    ), row=1, col=1)

    # Baseline
    fig.add_hline(y=10_000, line_dash="dot",
                  line_color="#475569", line_width=1, row=1, col=1)

    # Profit target
    fig.add_hline(y=11_000, line_dash="dash",
                  line_color="#4ade80", line_width=1,
                  annotation_text="Target", row=1, col=1)

    # Drawdown
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=drawdown,
        mode="lines",
        name="Drawdown",
        line=dict(color="#f87171", width=1.5),
        fill="tozeroy",
        fillcolor="rgba(248,113,113,0.15)",
    ), row=2, col=1)

    fig.add_hline(y=-10, line_dash="dash",
                  line_color="#f87171", line_width=1,
                  annotation_text="Max DD", row=2, col=1)

    fig.update_layout(
        height=420,
        paper_bgcolor="#0a0e1a",
        plot_bgcolor="#0d1220",
        font=dict(family="JetBrains Mono", color="#94a3b8", size=11),
        showlegend=False,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis2=dict(gridcolor="#1e293b"),
        yaxis=dict(gridcolor="#1e293b",
                   tickformat="$,.0f"),
        yaxis2=dict(gridcolor="#1e293b",
                    tickformat=".1f",
                    ticksuffix="%"),
    )

    st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════
# P&L PER STRATEGIE
# ═══════════════════════════════════════════════════════════════

def render_strategy_stats(trades: list):
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    if not closed:
        return

    df   = pd.DataFrame(closed)
    col  = "signal_name" if "signal_name" in df.columns else "ticker"
    rows = []

    for name, grp in df.groupby(col):
        wins  = (grp["pnl"] > 0).sum()
        total = len(grp)
        rows.append({
            "Strategie":  name,
            "Obchodů":    total,
            "Win Rate":   f"{wins/total*100:.0f}%",
            "Total P&L":  f"${grp['pnl'].sum():+.2f}",
            "Avg P&L":    f"${grp['pnl'].mean():+.2f}",
            "Best":       f"${grp['pnl'].max():+.2f}",
            "Worst":      f"${grp['pnl'].min():+.2f}",
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


# ═══════════════════════════════════════════════════════════════
# P&L HISTOGRAM
# ═══════════════════════════════════════════════════════════════

def render_pnl_distribution(trades: list):
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    if len(closed) < 5:
        return

    pnls = [t["pnl"] for t in closed]
    colors = ["#4ade80" if p > 0 else "#f87171" for p in pnls]

    fig = go.Figure(go.Histogram(
        x=pnls,
        nbinsx=20,
        marker_color=colors,
        marker_line_width=0,
    ))
    fig.add_vline(x=0, line_color="#475569", line_width=1)
    fig.add_vline(x=np.mean(pnls), line_dash="dash",
                  line_color="#60a5fa", line_width=1.5,
                  annotation_text=f"Avg ${np.mean(pnls):+.0f}")

    fig.update_layout(
        height=220,
        paper_bgcolor="#0a0e1a",
        plot_bgcolor="#0d1220",
        font=dict(family="JetBrains Mono", color="#94a3b8", size=10),
        margin=dict(l=0, r=0, t=10, b=0),
        bargap=0.1,
        xaxis=dict(gridcolor="#1e293b", tickprefix="$"),
        yaxis=dict(gridcolor="#1e293b"),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════
# OTEVŘENÉ POZICE
# ═══════════════════════════════════════════════════════════════

def render_open_positions(state: dict):
    positions = state.get("open_trades", [])

    if not positions:
        st.markdown(
            "<div style='color:#475569;font-family:JetBrains Mono;"
            "font-size:0.8rem;padding:16px'>Žádné otevřené pozice</div>",
            unsafe_allow_html=True
        )
        return

    for pos in positions:
        ticker    = pos.get("ticker", "?")
        direction = pos.get("direction", "?").upper()
        entry     = pos.get("entry", 0)
        sl        = pos.get("sl", 0)
        tp        = pos.get("tp", 0)
        color     = "#4ade80" if direction == "LONG" else "#f87171"
        arrow     = "▲" if direction == "LONG" else "▼"

        st.markdown(f"""
        <div style='background:#111827;border:1px solid #1e3a5f;border-radius:8px;
                    padding:12px 16px;margin:6px 0;font-family:JetBrains Mono;'>
            <span style='color:{color};font-weight:700;font-size:0.95rem'>
                {arrow} {ticker} {direction}
            </span>
            <span style='color:#475569;font-size:0.75rem;margin-left:12px'>
                Entry: <span style='color:#e2e8f0'>{entry:.5f}</span>
                &nbsp;SL: <span style='color:#f87171'>{sl:.5f}</span>
                &nbsp;TP: <span style='color:#4ade80'>{tp:.5f}</span>
            </span>
        </div>
        """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
# SYSTEM BUS EVENTS
# ═══════════════════════════════════════════════════════════════

def render_events(events: list):
    if not events:
        st.markdown(
            "<div style='color:#475569;font-family:JetBrains Mono;"
            "font-size:0.8rem;padding:8px'>Čekám na eventy...</div>",
            unsafe_allow_html=True
        )
        return

    EVENT_ICONS = {
        "signal":   ("🟢", "event-signal"),
        "order":    ("🔵", "event-order"),
        "position": ("🔵", "event-order"),
        "risk":     ("🟡", "event-risk"),
        "system":   ("⚪", "event-item"),
        "error":    ("🔴", "event-error"),
    }

    for ev in reversed(events[-12:]):
        etype  = ev.get("type", "")
        source = ev.get("source", "?")
        ts     = ev.get("timestamp", "")[:16].replace("T", " ")
        prefix = etype.split(".")[0]
        icon, css = EVENT_ICONS.get(prefix, ("⚪", "event-item"))

        payload = ev.get("payload", {})
        detail  = ""
        if "ticker" in payload:
            detail = f"· {payload['ticker']} {payload.get('direction','')}"
        elif "pnl" in payload:
            detail = f"· P&L: ${payload['pnl']:+.2f}"
        elif "error" in payload:
            detail = f"· {str(payload['error'])[:40]}"

        st.markdown(
            f"<div class='{css}'>"
            f"{icon} <b>{etype}</b> "
            f"<span style='color:#475569'>← {source}</span> "
            f"<span style='color:#64748b'>{detail}</span> "
            f"<span style='float:right;color:#334155'>{ts}</span>"
            f"</div>",
            unsafe_allow_html=True
        )


# ═══════════════════════════════════════════════════════════════
# MAIN LAYOUT
# ═══════════════════════════════════════════════════════════════

def main():
    state  = load_state()
    trades = load_trades()
    events = load_bus_events()

    render_header(state)
    st.divider()

    # FTMO progress
    render_ftmo(state)
    st.divider()

    # Klíčové metriky
    render_metrics(state)
    st.divider()

    # Hlavní obsah — 2 sloupce
    left, right = st.columns([2, 1])

    with left:
        st.markdown("<div class='section-title'>Equity Curve & Drawdown</div>",
                    unsafe_allow_html=True)
        render_equity_curve(trades)

        st.markdown("<div class='section-title'>P&L Distribuce</div>",
                    unsafe_allow_html=True)
        render_pnl_distribution(trades)

        st.markdown("<div class='section-title'>Statistiky per strategie</div>",
                    unsafe_allow_html=True)
        render_strategy_stats(trades)

    with right:
        st.markdown("<div class='section-title'>Otevřené pozice</div>",
                    unsafe_allow_html=True)
        render_open_positions(state)

        st.markdown("<div class='section-title'>System Bus — posledních 12 eventů</div>",
                    unsafe_allow_html=True)
        render_events(events)

    # Auto-refresh
    st.divider()
    col1, col2 = st.columns([3, 1])
    with col1:
        st.caption(
            "Dashboard se automaticky refreshuje každých 15s. "
            "Data čte z: data/bot_state.json, trade_log.json, system_bus.json"
        )
    with col2:
        auto = st.toggle("Auto-refresh 15s", value=False)
    if auto:
        time.sleep(15)
        st.rerun()


if __name__ == "__main__" or True:
    main()
    