"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - DASHBOARD v1                            ║
║         Streamlit vizualizace — equity, signály, trades     ║
╚══════════════════════════════════════════════════════════════╝

JAK SPUSTIT:
    streamlit run dashboard.py

Otevře se prohlížeč na http://localhost:8501
Auto-refresh každých 30 sekund.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ─── CONFIG ────────────────────────────────────────────────────

ST_TITLE      = "🤖 MarketPal Dashboard"
REFRESH_SEC   = 30

TRADES_LOG    = "data/08_PAPER_TRADES/mt5_trades.json"
SCHEDULER_LOG = "data/scheduler_log.json"
FTMO_STATE    = "data/ftmo_state.json"
EDGE_MATRIX   = "data/05_EDGE_MATRIX/edge_matrix_top.csv"
TB_BEST       = "data/07_TRIPLE_BARRIER/triple_barrier_best.csv"
GOLD_DIR      = "data/04_GOLD_FEATURES"

ACCOUNT_SIZE       = 10000
MAX_DAILY_LOSS_PCT = 4.5
MAX_TOTAL_LOSS_PCT = 9.0

# ─── PAGE CONFIG ───────────────────────────────────────────────

st.set_page_config(
    page_title="MarketPal",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Dark theme CSS
st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .metric-card {
        background: #1e2130;
        border-radius: 10px;
        padding: 20px;
        border-left: 4px solid #00d4aa;
    }
    .alert-red   { border-left-color: #ff4b4b !important; }
    .alert-green { border-left-color: #00d4aa !important; }
    .alert-yellow{ border-left-color: #ffd700 !important; }
    h1 { color: #00d4aa; }
    .stMetric label { color: #888; font-size: 12px; }
</style>
""", unsafe_allow_html=True)

# ─── DATA LOADERS ──────────────────────────────────────────────

@st.cache_data(ttl=REFRESH_SEC)
def load_trades():
    if not os.path.exists(TRADES_LOG):
        return []
    try:
        with open(TRADES_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@st.cache_data(ttl=REFRESH_SEC)
def load_ftmo_state():
    if not os.path.exists(FTMO_STATE):
        return {"equity": ACCOUNT_SIZE, "daily_pnl": 0.0, "total_pnl": 0.0}
    try:
        with open(FTMO_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"equity": ACCOUNT_SIZE, "daily_pnl": 0.0, "total_pnl": 0.0}


@st.cache_data(ttl=REFRESH_SEC)
def load_scheduler_log():
    if not os.path.exists(SCHEDULER_LOG):
        return []
    try:
        with open(SCHEDULER_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@st.cache_data(ttl=60)
def load_edge_matrix():
    if not os.path.exists(EDGE_MATRIX):
        return pd.DataFrame()
    try:
        return pd.read_csv(EDGE_MATRIX)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_triple_barrier():
    if not os.path.exists(TB_BEST):
        return pd.DataFrame()
    try:
        return pd.read_csv(TB_BEST)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=REFRESH_SEC)
def load_gold_data(ticker, tf, category):
    path = Path(GOLD_DIR) / tf / category / f"{ticker}.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None

# ─── HEADER ────────────────────────────────────────────────────

st.title(ST_TITLE)
st.caption(f"Auto-refresh každých {REFRESH_SEC}s | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Auto refresh
st_autorefresh = st.empty()
st.markdown(
    f'<meta http-equiv="refresh" content="{REFRESH_SEC}">',
    unsafe_allow_html=True
)

# ─── ROW 1: KLÍČOVÉ METRIKY ────────────────────────────────────

trades    = load_trades()
ftmo      = load_ftmo_state()
sched_log = load_scheduler_log()

equity      = ftmo.get("equity", ACCOUNT_SIZE)
daily_pnl   = ftmo.get("daily_pnl", 0.0)
total_pnl   = ftmo.get("total_pnl", 0.0)

closed_trades = [t for t in trades if t.get("status") == "closed"]
open_trades   = [t for t in trades if t.get("status") == "open"]
wins          = sum(1 for t in closed_trades if t.get("exit_reason") == "tp")
losses        = sum(1 for t in closed_trades if t.get("exit_reason") == "sl")
win_rate      = wins / len(closed_trades) * 100 if closed_trades else 0

daily_limit   = ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT / 100
total_limit   = ACCOUNT_SIZE * MAX_TOTAL_LOSS_PCT / 100
daily_used    = abs(min(daily_pnl, 0)) / daily_limit * 100 if daily_pnl < 0 else 0
total_used    = abs(min(total_pnl, 0)) / total_limit * 100 if total_pnl < 0 else 0

col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    delta_color = "normal" if equity >= ACCOUNT_SIZE else "inverse"
    st.metric("💰 Equity", f"${equity:,.2f}",
              f"{total_pnl:+.2f}",
              delta_color=delta_color)

with col2:
    st.metric("📅 Daily P&L", f"${daily_pnl:+.2f}",
              f"{daily_used:.0f}% limitu použito")

with col3:
    st.metric("📊 Win Rate", f"{win_rate:.1f}%",
              f"{wins}W / {losses}L")

with col4:
    st.metric("📈 Trades celkem", len(closed_trades),
              f"{len(open_trades)} otevřených")

with col5:
    # Pipeline status
    last_run = sched_log[-1] if sched_log else None
    if last_run:
        status = "✅ OK" if last_run["success"] else "❌ CHYBA"
        ts     = last_run["timestamp"][:16].replace("T", " ")
        st.metric("🔄 Pipeline", status, ts)
    else:
        st.metric("🔄 Pipeline", "⚠️ Žádný log", "")

with col6:
    # FTMO status
    if daily_used >= 80:
        ftmo_status = "⚠️ VAROVÁNÍ"
    elif daily_used >= 50:
        ftmo_status = "🟡 POZOR"
    else:
        ftmo_status = "✅ V POŘÁDKU"
    st.metric("🛡️ FTMO Status", ftmo_status,
              f"Daily: {daily_used:.0f}% | Total: {total_used:.0f}%")

st.divider()

# ─── ROW 2: FTMO GAUGES + EQUITY CURVE ────────────────────────

col_left, col_right = st.columns([1, 2])

with col_left:
    st.subheader("🛡️ FTMO Limity")

    # Daily loss gauge
    fig_daily = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=abs(min(daily_pnl, 0)),
        delta={"reference": 0, "valueformat": ".2f"},
        title={"text": "Daily Loss", "font": {"color": "white"}},
        number={"prefix": "$", "font": {"color": "white"}},
        gauge={
            "axis": {"range": [0, daily_limit], "tickcolor": "white"},
            "bar":  {"color": "#ff4b4b" if daily_used > 70 else "#00d4aa"},
            "steps": [
                {"range": [0, daily_limit * 0.5],  "color": "#1e2130"},
                {"range": [daily_limit * 0.5, daily_limit * 0.8], "color": "#2d3548"},
                {"range": [daily_limit * 0.8, daily_limit],       "color": "#3d1e1e"},
            ],
            "threshold": {
                "line": {"color": "red", "width": 4},
                "thickness": 0.75,
                "value": daily_limit
            }
        }
    ))
    fig_daily.update_layout(
        height=200, margin=dict(l=20, r=20, t=30, b=20),
        paper_bgcolor="#0e1117", font_color="white"
    )
    st.plotly_chart(fig_daily, use_container_width=True)

    # Total loss gauge
    fig_total = go.Figure(go.Indicator(
        mode="gauge+number",
        value=abs(min(total_pnl, 0)),
        title={"text": "Total Loss", "font": {"color": "white"}},
        number={"prefix": "$", "font": {"color": "white"}},
        gauge={
            "axis": {"range": [0, total_limit], "tickcolor": "white"},
            "bar":  {"color": "#ff4b4b" if total_used > 70 else "#ffd700"},
            "steps": [
                {"range": [0, total_limit * 0.5],  "color": "#1e2130"},
                {"range": [total_limit * 0.5, total_limit * 0.8], "color": "#2d3548"},
                {"range": [total_limit * 0.8, total_limit],       "color": "#3d1e1e"},
            ],
        }
    ))
    fig_total.update_layout(
        height=200, margin=dict(l=20, r=20, t=30, b=20),
        paper_bgcolor="#0e1117", font_color="white"
    )
    st.plotly_chart(fig_total, use_container_width=True)

with col_right:
    st.subheader("📈 Equity Curve")

    if closed_trades:
        eq_data = []
        running_eq = ACCOUNT_SIZE
        for t in sorted(closed_trades, key=lambda x: x.get("exit_time", "")):
            pnl = t.get("pnl", 0) or 0
            running_eq += pnl
            eq_data.append({
                "time":   t.get("exit_time", "")[:16],
                "equity": round(running_eq, 2),
                "pnl":    pnl,
                "trade":  t.get("name", ""),
                "result": "TP" if t.get("exit_reason") == "tp" else "SL",
            })

        df_eq = pd.DataFrame(eq_data)

        fig_eq = go.Figure()

        # Equity line
        fig_eq.add_trace(go.Scatter(
            x=df_eq["time"], y=df_eq["equity"],
            mode="lines+markers",
            line=dict(color="#00d4aa", width=2),
            marker=dict(
                color=["#00d4aa" if r == "TP" else "#ff4b4b" for r in df_eq["result"]],
                size=8
            ),
            name="Equity",
            hovertemplate="<b>%{x}</b><br>Equity: $%{y:.2f}<br>%{text}",
            text=[f"{r['trade']} {r['result']} ${r['pnl']:+.2f}" for _, r in df_eq.iterrows()]
        ))

        # Initial capital line
        fig_eq.add_hline(y=ACCOUNT_SIZE, line_dash="dash",
                         line_color="gray", annotation_text="Start $10,000")

        fig_eq.update_layout(
            height=420,
            paper_bgcolor="#0e1117",
            plot_bgcolor="#1e2130",
            font_color="white",
            xaxis=dict(gridcolor="#2d3548", title=""),
            yaxis=dict(gridcolor="#2d3548", title="Equity ($)"),
            showlegend=False,
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(fig_eq, use_container_width=True)
    else:
        st.info("Zatím žádné uzavřené obchody. Equity curve se zobrazí po prvním trade.")

st.divider()

# ─── ROW 3: OTEVŘENÉ POZICE + POSLEDNÍCH 10 TRADES ────────────

col_open, col_closed = st.columns(2)

with col_open:
    st.subheader(f"🔴 Otevřené pozice ({len(open_trades)})")
    if open_trades:
        df_open = pd.DataFrame([{
            "Strategie":  t.get("name", ""),
            "Ticker":     t.get("ticker", ""),
            "Směr":       t.get("direction", "").upper(),
            "Entry":      t.get("entry_price", 0),
            "TP":         t.get("tp", 0),
            "SL":         t.get("sl", 0),
            "Čas":        t.get("entry_time", "")[:16],
        } for t in open_trades])
        st.dataframe(df_open, use_container_width=True, hide_index=True)
    else:
        st.info("Žádné otevřené pozice")

with col_closed:
    st.subheader("📋 Posledních 10 obchodů")
    if closed_trades:
        recent = sorted(closed_trades,
                        key=lambda x: x.get("exit_time", ""),
                        reverse=True)[:10]
        df_closed = pd.DataFrame([{
            "Strategie": t.get("name", "")[:20],
            "Ticker":    t.get("ticker", ""),
            "Výsledek":  "✅ TP" if t.get("exit_reason") == "tp" else "❌ SL",
            "P&L":       f"${t.get('pnl', 0):+.2f}",
            "Exit":      t.get("exit_time", "")[:16],
        } for t in recent])
        st.dataframe(df_closed, use_container_width=True, hide_index=True)
    else:
        st.info("Zatím žádné uzavřené obchody")

st.divider()

# ─── ROW 4: TOP SIGNÁLY ────────────────────────────────────────

st.subheader("🎯 Top Signály (Triple Barrier)")

df_tb = load_triple_barrier()
if not df_tb.empty:
    strong = df_tb[df_tb["rating"] == "STRONG"].head(15)
    if not strong.empty:
        fig_pf = px.bar(
            strong.sort_values("profit_factor", ascending=True),
            x="profit_factor", y=strong["signal"] + " " + strong["ticker"] + " " + strong["timeframe"],
            orientation="h",
            color="win_rate",
            color_continuous_scale="RdYlGn",
            labels={"profit_factor": "Profit Factor", "win_rate": "Win Rate %"},
            title="Strong Signals — Profit Factor"
        )
        fig_pf.update_layout(
            height=400,
            paper_bgcolor="#0e1117",
            plot_bgcolor="#1e2130",
            font_color="white",
            margin=dict(l=20, r=20, t=40, b=20),
            yaxis_title="",
        )
        st.plotly_chart(fig_pf, use_container_width=True)
else:
    st.info("Spusť triple_barrier.py pro zobrazení signálů")

st.divider()

# ─── ROW 5: LIVE CHART ─────────────────────────────────────────

st.subheader("📊 Live Chart")

chart_col1, chart_col2, chart_col3 = st.columns(3)
with chart_col1:
    ticker_sel = st.selectbox("Ticker", ["EURUSD", "GBPUSD", "USDJPY", "AMZN", "AAPL", "NVDA"])
with chart_col2:
    tf_sel = st.selectbox("Timeframe", ["M5", "M15", "H1"])
with chart_col3:
    category_sel = "forex" if ticker_sel in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"] else "stocks"
    st.text_input("Category", value=category_sel, disabled=True)

df_chart = load_gold_data(ticker_sel, tf_sel, category_sel)

if df_chart is not None and len(df_chart) > 0:
    df_plot = df_chart.tail(100).copy()

    # Timestamp sloupec
    if "timestamp" in df_plot.columns:
        x_axis = df_plot["timestamp"]
    else:
        x_axis = pd.RangeIndex(len(df_plot))

    fig_chart = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25]
    )

    # Candlestick
    fig_chart.add_trace(go.Candlestick(
        x=x_axis,
        open=df_plot["open"],
        high=df_plot["high"],
        low=df_plot["low"],
        close=df_plot["close"],
        name="Price",
        increasing_line_color="#00d4aa",
        decreasing_line_color="#ff4b4b",
    ), row=1, col=1)

    # Bollinger Bands
    if "bb_upper" in df_plot.columns:
        fig_chart.add_trace(go.Scatter(
            x=x_axis, y=df_plot["bb_upper"],
            line=dict(color="rgba(100,100,255,0.4)", width=1),
            name="BB Upper"
        ), row=1, col=1)
        fig_chart.add_trace(go.Scatter(
            x=x_axis, y=df_plot["bb_lower"],
            line=dict(color="rgba(100,100,255,0.4)", width=1),
            fill="tonexty", fillcolor="rgba(100,100,255,0.05)",
            name="BB Lower"
        ), row=1, col=1)

    # EMA
    if "ema_20" in df_plot.columns:
        fig_chart.add_trace(go.Scatter(
            x=x_axis, y=df_plot["ema_20"],
            line=dict(color="orange", width=1),
            name="EMA 20"
        ), row=1, col=1)

    # Signály — červené trojúhelníky pro short, zelené pro long
    signal_cols = [c for c in df_plot.columns if c.startswith("signal_")]
    for sc in signal_cols[:3]:  # max 3 signály aby nebyl graf přeplněný
        sig_rows = df_plot[df_plot[sc] == True]
        if len(sig_rows) > 0:
            direction = "short" if any(k in sc for k in ["bear", "down", "death", "overbought"]) else "long"
            fig_chart.add_trace(go.Scatter(
                x=sig_rows.index if not "timestamp" in sig_rows.columns else sig_rows["timestamp"],
                y=sig_rows["high"] * 1.001 if direction == "short" else sig_rows["low"] * 0.999,
                mode="markers",
                marker=dict(
                    symbol="triangle-down" if direction == "short" else "triangle-up",
                    size=10,
                    color="#ff4b4b" if direction == "short" else "#00d4aa",
                ),
                name=sc.replace("signal_", ""),
            ), row=1, col=1)

    # Volume
    if "volume" in df_plot.columns:
        colors = ["#00d4aa" if c >= o else "#ff4b4b"
                  for c, o in zip(df_plot["close"], df_plot["open"])]
        fig_chart.add_trace(go.Bar(
            x=x_axis, y=df_plot["volume"],
            marker_color=colors,
            name="Volume",
            opacity=0.7,
        ), row=2, col=1)

    fig_chart.update_layout(
        height=500,
        paper_bgcolor="#0e1117",
        plot_bgcolor="#1e2130",
        font_color="white",
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(bgcolor="#1e2130", font=dict(size=10)),
        margin=dict(l=20, r=20, t=20, b=20),
        xaxis2=dict(gridcolor="#2d3548"),
        yaxis=dict(gridcolor="#2d3548"),
        yaxis2=dict(gridcolor="#2d3548"),
    )
    st.plotly_chart(fig_chart, use_container_width=True)
else:
    st.warning(f"Data pro {ticker_sel} {tf_sel} nenalezena. Spusť scheduler.py --now")

st.divider()

# ─── ROW 6: PIPELINE LOG ───────────────────────────────────────

st.subheader("🔄 Pipeline History")

if sched_log:
    df_log = pd.DataFrame([{
        "Datum":  e["timestamp"][:16].replace("T", " "),
        "Status": "✅ OK" if e["success"] else "❌ CHYBA",
        "Čas":    f"{e['total_duration_s']:.0f}s",
    } for e in reversed(sched_log[-10:])])
    st.dataframe(df_log, use_container_width=True, hide_index=True)
else:
    st.info("Zatím žádný log. Spusť scheduler.py --now")

# ─── FOOTER ────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    f"MarketPal v2 | Phase 3 | "
    f"Trades: {len(trades)} | "
    f"Equity: ${equity:,.2f} | "
    f"Poslední update: {datetime.now().strftime('%H:%M:%S')}"
)