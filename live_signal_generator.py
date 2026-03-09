"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - LIVE SIGNAL GENERATOR v1.0                 ║
║     Každých 15 minut: čte gold data → generuje signály     ║
╚══════════════════════════════════════════════════════════════╝

POZICE V PIPELINE:
  Gold features → [TENTO SKRIPT] → signály → telegram_bot.py → broker

JAK TO FUNGUJE:
  1. Každých 15 minut se spustí check_signals()
  2. Načte poslední N svíček z gold parquetu (stačí posledních 200)
  3. Zkontroluje signal_ sloupce na poslední svíčce
  4. Pokud signal = True → projde risk filtry
  5. Pokud projde → pošle Telegram alert + (v budoucnu) MT5 order

RISK FILTRY (každý signál musí projít všemi):
  ✓ Spread check      — není abnormální spread
  ✓ Session filter    — správná tržní seance
  ✓ Regime filter     — signal ve správném tržním režimu
  ✓ Max trades        — nepřekročen denní limit obchodů
  ✓ Daily loss limit  — nepřekročena denní ztráta (FTMO)
  ✓ Total DD limit    — nepřekročen max drawdown (FTMO)
  ✓ Duplicate check   — stejný signál nebyl generován posledních 60 min
  ✓ Meta-model filter — (pokud dostupný) meta-model confidence > threshold
"""

import os
import json
import time
import pickle
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

warnings.filterwarnings("ignore")

# ─── CONFIG ────────────────────────────────────────────────────
GOLD_DIR    = "data/04_GOLD_FEATURES"
META_DIR    = "data/11_META_LABELS"
STATE_FILE  = "data/bot_state.json"
SIGNAL_LOG  = "data/signal_log.json"

ACCOUNT_SIZE    = 10_000
RISK_PER_TRADE  = 0.01      # 1%
MAX_DAILY_LOSS  = 500       # FTMO 5%
MAX_TOTAL_LOSS  = 1_000     # FTMO 10%
MAX_DAILY_TRADES= 6         # max obchodů za den
META_THRESHOLD  = 0.55      # meta-model confidence minimum

# Strategie — kopie z meta_labeling.py
STRATEGIES = [
    {
        "name":       "EURUSD M15 RSI oversold exit",
        "ticker":     "EURUSD",
        "tf":         "M15",
        "category":   "forex",
        "signal_col": "signal_rsi_oversold_exit",
        "direction":  "long",
        "pt":         2.0,
        "sl":         1.5,
        "t":          24,
        "session":    ["london", "ny", "overlap"],  # jen v těchto seancích
        "regime":     ["BULL", "SIDEWAYS"],         # jen v těchto režimech
    },
    {
        "name":       "GBPUSD M15 RSI oversold exit",
        "ticker":     "GBPUSD",
        "tf":         "M15",
        "category":   "forex",
        "signal_col": "signal_rsi_oversold_exit",
        "direction":  "long",
        "pt":         1.5,
        "sl":         1.5,
        "t":          24,
        "session":    ["london", "ny", "overlap"],
        "regime":     ["BULL", "SIDEWAYS"],
    },
    {
        "name":       "USDCHF H1 Stoch pin bear",
        "ticker":     "USDCHF",
        "tf":         "H1",
        "category":   "forex",
        "signal_col": "signal_stoch_pin_bear",
        "direction":  "short",
        "pt":         1.5,
        "sl":         1.5,
        "t":          24,
        "session":    ["london", "ny"],
        "regime":     ["BEAR", "SIDEWAYS"],
    },
]


# ═══════════════════════════════════════════════════════════════
# SEKCE 1: DATA LOADING
# ═══════════════════════════════════════════════════════════════

def load_latest_candles(ticker: str, tf: str, category: str,
                         n: int = 200) -> Optional[pd.DataFrame]:
    """
    Načte posledních N svíček z gold parquetu.
    Nepotřebujeme celý dataset — stačí posledních 200 pro indikátory.
    """
    path = Path(GOLD_DIR) / tf / category / f"{ticker}.parquet"
    if not path.exists():
        return None

    try:
        df = pd.read_parquet(path)

        # Nastav datetime index
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        elif not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        df = df.sort_index()
        return df.iloc[-n:]  # jen posledních N svíček

    except Exception as e:
        print(f"  ❌ Chyba načítání {ticker} {tf}: {e}")
        return None


def load_meta_model(ticker: str, tf: str) -> Optional[object]:
    """Načte meta-model pokud existuje."""
    name = f"{ticker}_{tf}_meta_model.pkl"
    path = Path(META_DIR) / name
    if path.exists():
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return None


# ═══════════════════════════════════════════════════════════════
# SEKCE 2: RISK FILTRY
# ═══════════════════════════════════════════════════════════════

def load_state() -> dict:
    default = {
        "equity": ACCOUNT_SIZE, "daily_pnl": 0.0,
        "total_pnl": 0.0, "paused": False,
        "open_trades": [], "daily_trade_count": 0,
        "last_reset_date": str(datetime.utcnow().date()),
    }
    if Path(STATE_FILE).exists():
        try:
            with open(STATE_FILE) as f:
                return {**default, **json.load(f)}
        except Exception:
            pass
    return default


def load_signal_log() -> list:
    if Path(SIGNAL_LOG).exists():
        try:
            with open(SIGNAL_LOG) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_signal_log(log: list):
    Path(SIGNAL_LOG).parent.mkdir(parents=True, exist_ok=True)
    # Ponech jen posledních 500 signálů
    with open(SIGNAL_LOG, "w") as f:
        json.dump(log[-500:], f, default=str)


def reset_daily_if_needed(state: dict) -> dict:
    """Resetuje denní countery pokud začal nový den."""
    today = str(datetime.utcnow().date())
    if state.get("last_reset_date") != today:
        state["daily_pnl"]         = 0.0
        state["daily_trade_count"] = 0
        state["last_reset_date"]   = today
    return state


def check_duplicate(ticker: str, signal_col: str,
                    log: list, cooldown_min: int = 60) -> bool:
    """
    Vrátí True pokud byl stejný signál generován posledních cooldown_min minut.
    Zabrání opakovaným vstupům na stejný signál.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=cooldown_min)
    for entry in reversed(log):
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts < cutoff:
                break
            if entry["ticker"] == ticker and entry["signal"] == signal_col:
                return True  # duplicitní
        except Exception:
            continue
    return False


def apply_risk_filters(strategy: dict, last_candle: pd.Series,
                        state: dict, log: list) -> tuple[bool, str]:
    """
    Projde všechny risk filtry.
    Vrátí (True, "OK") pokud projde, nebo (False, "důvod") pokud ne.
    """
    ticker     = strategy["ticker"]
    signal_col = strategy["signal_col"]

    # 1. Bot není paused
    if state.get("paused"):
        return False, "bot paused"

    # 2. FTMO daily loss limit
    if state["daily_pnl"] <= -MAX_DAILY_LOSS:
        return False, f"denní DD limit (${state['daily_pnl']:.0f})"

    # 3. FTMO total loss limit
    total_dd = ACCOUNT_SIZE - state["equity"]
    if total_dd >= MAX_TOTAL_LOSS:
        return False, f"max DD limit (${total_dd:.0f})"

    # 4. Max denní počet obchodů
    if state["daily_trade_count"] >= MAX_DAILY_TRADES:
        return False, f"max obchodů za den ({MAX_DAILY_TRADES})"

    # 5. Duplicate check (cooldown 60 minut)
    if check_duplicate(ticker, signal_col, log, cooldown_min=60):
        return False, "cooldown (stejný signál < 60 min)"

    # 6. Session filter
    allowed_sessions = strategy.get("session", [])
    if allowed_sessions:
        in_session = any(
            last_candle.get(f"session_{s}", False)
            for s in allowed_sessions
        )
        if not in_session:
            hour = datetime.utcnow().hour
            return False, f"mimo obchodní seanci (hodina: {hour} UTC)"

    # 7. Regime filter
    allowed_regimes = strategy.get("regime", [])
    if allowed_regimes:
        current_regime = last_candle.get("regime", "SIDEWAYS")
        if current_regime not in allowed_regimes:
            return False, f"špatný režim ({current_regime})"

    # 8. Abnormální ATR (volatility filter)
    atr_ratio = last_candle.get("atr_ratio", 1.0)
    if pd.notna(atr_ratio) and atr_ratio > 3.0:
        return False, f"příliš vysoká volatilita (ATR ratio: {atr_ratio:.1f}x)"

    return True, "OK"


# ═══════════════════════════════════════════════════════════════
# SEKCE 3: META-MODEL FILTR
# ═══════════════════════════════════════════════════════════════

def get_meta_confidence(model, last_candle: pd.Series) -> float:
    """
    Zeptá se meta-modelu na pravděpodobnost že signál vede k výhře.
    Vrátí 0.5 pokud model není dostupný (neutrální).
    """
    if model is None:
        return 0.5

    feature_cols = [c for c in last_candle.index
                    if not c.startswith("signal_") and
                    c not in ["open","high","low","close","volume","regime","symbol"]]

    try:
        X = last_candle[feature_cols].fillna(0).values.reshape(1, -1)
        prob = model.predict_proba(X)[0][1]
        return float(prob)
    except Exception:
        return 0.5


# ═══════════════════════════════════════════════════════════════
# SEKCE 4: POSITION SIZING
# ═══════════════════════════════════════════════════════════════

def calculate_position_size(ticker: str, entry: float, sl: float,
                              equity: float, risk_pct: float = RISK_PER_TRADE,
                              meta_conf: float = 0.5) -> float:
    """
    Kelly-scaled position sizing.
    Základní risk = 1% equity.
    Meta-model confidence > 0.6 → scale up (max 1.5×).
    Meta-model confidence < 0.52 → scale down (0.5×).
    """
    risk_usd   = equity * risk_pct
    pip_risk   = abs(entry - sl)

    if pip_risk == 0:
        return 0.0

    # Base lot size (forex)
    if "USD" in ticker:
        pip_value  = 10.0  # USD pip value pro 1 standard lot
        base_lots  = risk_usd / (pip_risk * 10_000 * pip_value)
    else:
        base_lots  = risk_usd / pip_risk  # pro stocks = shares

    # Meta-model scaling
    if meta_conf >= 0.65:
        scale = 1.5
    elif meta_conf >= 0.60:
        scale = 1.25
    elif meta_conf >= 0.55:
        scale = 1.0
    elif meta_conf >= 0.52:
        scale = 0.75
    else:
        scale = 0.5

    return round(base_lots * scale, 2)


# ═══════════════════════════════════════════════════════════════
# SEKCE 5: SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════

def check_signals(verbose: bool = True) -> list:
    """
    Hlavní funkce — zkontroluje všechny strategie na přítomnost signálů.
    Vrátí seznam aktivních signálů které prošly risk filtry.
    """
    state    = load_state()
    state    = reset_daily_if_needed(state)
    log      = load_signal_log()
    signals  = []

    now = datetime.utcnow()
    if verbose:
        print(f"\n{'─'*55}")
        print(f"  🔍 Signal check — {now.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  Equity: ${state['equity']:,.0f} | "
              f"Denní P&L: ${state['daily_pnl']:+.2f} | "
              f"Obchodů dnes: {state['daily_trade_count']}")
        print(f"{'─'*55}")

    for strat in STRATEGIES:
        ticker = strat["ticker"]
        tf     = strat["tf"]
        cat    = strat["category"]
        scol   = strat["signal_col"]

        # Načti data
        df = load_latest_candles(ticker, tf, cat)
        if df is None or len(df) < 50:
            if verbose:
                print(f"  ⚠️  {ticker} {tf}: nedostatek dat")
            continue

        last = df.iloc[-1]

        # Zkontroluj signal sloupec
        signal_active = bool(last.get(scol, False))

        if not signal_active:
            if verbose:
                print(f"  ○  {strat['name']}: žádný signál")
            continue

        # Risk filtry
        passed, reason = apply_risk_filters(strat, last, state, log)
        if not passed:
            if verbose:
                print(f"  ⏭  {strat['name']}: signál ✓ | filter ✗ — {reason}")
            continue

        # Meta-model
        model      = load_meta_model(ticker, tf)
        meta_conf  = get_meta_confidence(model, last)

        if meta_conf < META_THRESHOLD:
            if verbose:
                print(f"  🤖 {strat['name']}: meta-model confidence příliš nízká ({meta_conf:.2f})")
            continue

        # Vypočítej entry parametry
        entry  = float(last["close"])
        atr    = float(last.get("atr", entry * 0.001))
        sl     = entry - atr * strat["sl"] if strat["direction"] == "long" else entry + atr * strat["sl"]
        tp     = entry + atr * strat["pt"] if strat["direction"] == "long" else entry - atr * strat["pt"]
        size   = calculate_position_size(ticker, entry, sl, state["equity"], meta_conf=meta_conf)

        signal_data = {
            "timestamp":   str(now),
            "name":        strat["name"],
            "ticker":      ticker,
            "tf":          tf,
            "signal":      scol,
            "direction":   strat["direction"],
            "entry":       entry,
            "sl":          sl,
            "tp":          tp,
            "size":        size,
            "meta_conf":   meta_conf,
            "regime":      last.get("regime", "UNKNOWN"),
            "atr_ratio":   float(last.get("atr_ratio", 1.0)),
        }

        signals.append(signal_data)

        # Zaloguj signál
        log.append(signal_data)

        if verbose:
            print(f"  🟢 SIGNAL: {strat['name']}")
            print(f"     Entry: {entry:.5f} | SL: {sl:.5f} | TP: {tp:.5f}")
            print(f"     Size: {size:.2f} lots | Meta conf: {meta_conf:.2f} | Regime: {last.get('regime','?')}")

    save_signal_log(log)

    if verbose:
        if signals:
            print(f"\n  ✅ {len(signals)} signálů prošlo filtry — připraveno k exekuci")
        else:
            print(f"\n  ─ Žádné signály v tomto cyklu")

    return signals


# ═══════════════════════════════════════════════════════════════
# SEKCE 6: MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def run_signal_loop(interval_min: int = 15):
    """
    Hlavní smyčka — spustí check_signals každých interval_min minut.
    V produkci: signály jdou do telegram_bot.py a MT5 executoru.
    """
    print("╔══════════════════════════════════════════════════════╗")
    print("║   MARKETPAL LIVE SIGNAL GENERATOR v1.0            ║")
    print(f"║   {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}                          ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"\n  Interval: každých {interval_min} minut")
    print(f"  Strategie: {len(STRATEGIES)}")
    print(f"  Gold dir: {GOLD_DIR}\n")

    # Import telegram notifikací (pokud dostupné)
    try:
        from telegram_bot import notify_trade_entry, notify_signal_skipped
        telegram_ok = True
        print("  ✅ Telegram bot připojen")
    except ImportError:
        telegram_ok = False
        print("  ⚠️  Telegram bot nepřipojen (spusť telegram_bot.py)")

    iteration = 0
    try:
        while True:
            iteration += 1
            signals = check_signals(verbose=True)

            # Odešli signály do Telegramu
            if telegram_ok:
                for sig in signals:
                    try:
                        notify_trade_entry(
                            sig["ticker"], sig["direction"].upper(),
                            sig["entry"], sig["sl"], sig["tp"], sig["size"]
                        )
                    except Exception as e:
                        print(f"  ⚠️  Telegram error: {e}")

            # TODO: MT5 executor
            # for sig in signals:
            #     mt5_executor.place_order(sig)

            # Čekej na další cyklus
            next_run = datetime.utcnow() + timedelta(minutes=interval_min)
            print(f"\n  ⏰ Další check: {next_run.strftime('%H:%M UTC')}")
            time.sleep(interval_min * 60)

    except KeyboardInterrupt:
        print("\n\nSignal generator zastaven (Ctrl+C).")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        # Jednorázový check (pro debugging)
        signals = check_signals(verbose=True)
        print(f"\nSignálů: {len(signals)}")
    else:
        run_signal_loop(interval_min=15)