"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - STRATEGY RUNNER v1                      ║
║         Paper trading top signálů automaticky               ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    1. Načte nejnovější Gold data
    2. Zkontroluje všechny top signály na poslední svíčce
    3. Aplikuje FTMO risk management (can_trade?)
    4. PAPER MODE: zaloguje obchod + Telegram alert
    5. LIVE MODE:  pošle order do MT5 (přidat až bude MT5 ready)

JAK SPUSTIT:
    Paper trading (bezpečné, žádné reálné obchody):
        python strategy_runner.py

    Live trading (az bude MT5 připojeno):
        python strategy_runner.py --live

    Jeden průchod (pro scheduler):
        python strategy_runner.py --once

    Nepřetržitý běh (kontrola každých N minut):
        python strategy_runner.py --loop

TOP SIGNÁLY (z Triple Barrier analýzy):
    Přidávej/odebírej v ACTIVE_STRATEGIES níže.
    Každá strategie má: ticker, tf, signal, direction, pt, sl, hold

ARCHITEKTURA:
    Runner
      ├── DataLoader     → načte parquet, zkontroluje čerstvost
      ├── SignalChecker  → zkontroluje signal na poslední svíčce
      ├── RiskGate       → ftmo_risk.py can_trade() + position size
      ├── PaperExecutor  → zaloguje obchod (paper mode)
      └── LiveExecutor   → MT5 order (live mode, TODO)
"""

import os
import sys
import json
import time
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

# Telegram
try:
    import requests
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

from dotenv import load_dotenv
load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────

GOLD_DIR    = "data/04_GOLD_FEATURES"
TRADES_LOG  = "data/08_PAPER_TRADES/paper_trades.json"
EQUITY_LOG  = "data/08_PAPER_TRADES/equity_curve.json"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Jak dlouho smí být data stará (v minutách) aby byl signál platný
MAX_DATA_AGE = {
    "M5":  15,    # M5 data nesmí být starší než 15 minut
    "M15": 30,
    "H1":  90,
}

# Interval kontroly v --loop módu (minuty)
LOOP_INTERVAL_MINUTES = 5

# Paper trading počáteční kapitál
PAPER_CAPITAL = 10_000

# ─── AKTIVNÍ STRATEGIE ─────────────────────────────────────────
# Výsledky z Triple Barrier + Backtester v2
# Přidávej/odebírej podle aktuální analýzy
#
# Formát:
# {
#   "name":      název pro logy,
#   "ticker":    ticker symbol,
#   "tf":        timeframe ("M5", "M15", "H1"),
#   "category":  "stocks" nebo "forex",
#   "signal":    název signal_ sloupce,
#   "direction": "long" nebo "short",
#   "pt_atr":    profit target v násobcích ATR,
#   "sl_atr":    stop loss v násobcích ATR,
#   "hold":      max svíček držení,
#   "active":    True/False — rychle vypni bez mazání
# }

ACTIVE_STRATEGIES = [
    {
        "name":      "AMZN RSI OB Exit",
        "ticker":    "AMZN",
        "tf":        "M5",
        "category":  "stocks",
        "signal":    "signal_rsi_overbought_exit",
        "direction": "short",
        "pt_atr":    3.0,
        "sl_atr":    3.0,
        "hold":      12,
        "active":    True,
    },
    {
        "name":      "AAPL BB Breakdown M15",
        "ticker":    "AAPL",
        "tf":        "M15",
        "category":  "stocks",
        "signal":    "signal_bb_breakout_down",
        "direction": "short",
        "pt_atr":    3.0,
        "sl_atr":    3.0,
        "hold":      12,
        "active":    True,
    },
    {
        "name":      "EURUSD Death Cross M15",
        "ticker":    "EURUSD",
        "tf":        "M15",
        "category":  "forex",
        "signal":    "signal_death_cross",
        "direction": "short",
        "pt_atr":    1.5,
        "sl_atr":    1.5,
        "hold":      24,
        "active":    True,
    },
    {
        "name":      "USDCHF Stoch Pin Bear",
        "ticker":    "USDCHF",
        "tf":        "M5",
        "category":  "forex",
        "signal":    "signal_stoch_pin_bear",
        "direction": "short",
        "pt_atr":    2.0,
        "sl_atr":    1.0,
        "hold":      24,
        "active":    True,
    },
    {
        "name":      "AAPL Volume Spike Bear H1",
        "ticker":    "AAPL",
        "tf":        "H1",
        "category":  "stocks",
        "signal":    "signal_volume_spike_bear",
        "direction": "short",
        "pt_atr":    2.0,
        "sl_atr":    1.0,
        "hold":      24,
        "active":    True,
    },
    {
        "name":      "NVDA RSI OB Exit M15",
        "ticker":    "NVDA",
        "tf":        "M15",
        "category":  "stocks",
        "signal":    "signal_rsi_overbought_exit",
        "direction": "short",
        "pt_atr":    1.5,
        "sl_atr":    1.5,
        "hold":      24,
        "active":    True,
    },
]

# ─── TELEGRAM ──────────────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_OK or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception:
        pass

# ─── DATA LOADER ───────────────────────────────────────────────

def load_latest_data(ticker, tf, category):
    """
    Načte Gold parquet pro daný instrument.
    Vrátí (df, is_fresh) — is_fresh = data jsou dostatečně nová.
    """
    path = Path(GOLD_DIR) / tf / category / f"{ticker}.parquet"
    if not path.exists():
        return None, False

    df = pd.read_parquet(path)

    # Zkontroluj čerstvost dat
    if "timestamp" in df.columns:
        last_ts = pd.to_datetime(df["timestamp"].iloc[-1])
    elif df.index.dtype == "datetime64[ns]":
        last_ts = df.index[-1]
    else:
        # Nemáme timestamp — předpokládej čerstvé
        return df, True

    age_minutes = (datetime.now() - last_ts.replace(tzinfo=None)).total_seconds() / 60
    max_age     = MAX_DATA_AGE.get(tf, 60)
    is_fresh    = age_minutes <= max_age

    return df, is_fresh

# ─── SIGNAL CHECKER ────────────────────────────────────────────

def check_signal(df, signal_col):
    """
    Zkontroluje jestli je signál aktivní na POSLEDNÍ svíčce.
    Vrátí (signal_active, entry_price, atr, last_row)
    """
    if signal_col not in df.columns:
        return False, None, None, None

    last = df.iloc[-1]
    signal_active = bool(last.get(signal_col, False))
    entry_price   = float(last.get("close", 0))
    atr           = float(last.get("atr", 0))

    return signal_active, entry_price, atr, last

# ─── RISK GATE ─────────────────────────────────────────────────

class SimpleRiskGate:
    """
    Zjednodušená FTMO risk gate bez závislosti na ftmo_risk.py.
    Načte stav z JSON, zkontroluje limity.
    """
    STATE_FILE     = "data/ftmo_state.json"
    ACCOUNT_SIZE   = PAPER_CAPITAL
    MAX_DAILY_PCT  = 4.5   # Buffer pod FTMO 5%
    MAX_TOTAL_PCT  = 9.0   # Buffer pod FTMO 10%
    RISK_PER_TRADE = 1.0   # % kapitálu na obchod

    def load_state(self):
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"equity": self.ACCOUNT_SIZE, "daily_pnl": 0, "total_pnl": 0}

    def can_trade(self):
        state      = self.load_state()
        equity     = state.get("equity", self.ACCOUNT_SIZE)
        daily_pnl  = state.get("daily_pnl", 0)
        total_pnl  = state.get("total_pnl", 0)

        max_daily  = self.ACCOUNT_SIZE * self.MAX_DAILY_PCT / 100
        max_total  = self.ACCOUNT_SIZE * self.MAX_TOTAL_PCT / 100

        if daily_pnl <= -max_daily:
            return False, f"Daily loss limit dosažen ({daily_pnl:.2f})"
        if total_pnl <= -max_total:
            return False, f"Total loss limit dosažen ({total_pnl:.2f})"

        return True, "OK"

    def position_size(self, entry_price, atr, sl_atr):
        """Volatility-adjusted position sizing — 1% risk per trade."""
        state       = self.load_state()
        equity      = state.get("equity", self.ACCOUNT_SIZE)
        risk_amount = equity * self.RISK_PER_TRADE / 100
        stop_dist   = atr * sl_atr
        if stop_dist <= 0:
            return 0
        return round(risk_amount / stop_dist, 4)

risk_gate = SimpleRiskGate()

# ─── PAPER EXECUTOR ────────────────────────────────────────────

def load_trades():
    if os.path.exists(TRADES_LOG):
        try:
            with open(TRADES_LOG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_trades(trades):
    os.makedirs(os.path.dirname(TRADES_LOG), exist_ok=True)
    with open(TRADES_LOG, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)


def paper_execute(strategy, entry_price, atr, pos_size):
    """Zaloguje paper trade."""
    sl_atr = strategy["sl_atr"]
    pt_atr = strategy["pt_atr"]
    direction = strategy["direction"]

    if direction == "long":
        stop_price   = round(entry_price - sl_atr * atr, 6)
        target_price = round(entry_price + pt_atr * atr, 6)
    else:
        stop_price   = round(entry_price + sl_atr * atr, 6)
        target_price = round(entry_price - pt_atr * atr, 6)

    trade = {
        "id":           f"{strategy['ticker']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "strategy":     strategy["name"],
        "ticker":       strategy["ticker"],
        "timeframe":    strategy["tf"],
        "direction":    direction,
        "entry_price":  entry_price,
        "stop_price":   stop_price,
        "target_price": target_price,
        "atr":          round(atr, 6),
        "pos_size":     pos_size,
        "entry_time":   datetime.now().isoformat(),
        "status":       "open",
        "exit_price":   None,
        "exit_time":    None,
        "pnl":          None,
        "exit_reason":  None,
        "hold_candles": strategy["hold"],
        "mode":         "paper",
    }

    trades = load_trades()
    trades.append(trade)
    save_trades(trades)

    return trade


def update_open_trades(df_map):
    """
    Zkontroluje otevřené paper trades jestli byl zasažen TP nebo SL.
    df_map = {"AMZN_M5": df, ...}
    """
    trades   = load_trades()
    updated  = 0

    for trade in trades:
        if trade["status"] != "open":
            continue

        key = f"{trade['ticker']}_{trade['timeframe']}"
        df  = df_map.get(key)
        if df is None:
            continue

        # Zkontroluj poslední svíčky od vstupu
        entry_time = datetime.fromisoformat(trade["entry_time"])
        last       = df.iloc[-1]
        high       = float(last.get("high", 0))
        low        = float(last.get("low", 0))

        tp  = trade["target_price"]
        sl  = trade["stop_price"]
        dir = trade["direction"]

        hit_tp = (dir == "long"  and high >= tp) or (dir == "short" and low <= tp)
        hit_sl = (dir == "long"  and low  <= sl) or (dir == "short" and high >= sl)

        if hit_tp or hit_sl:
            exit_price  = tp if hit_tp else sl
            exit_reason = "tp" if hit_tp else "sl"

            if dir == "long":
                pnl = (exit_price - trade["entry_price"]) * trade["pos_size"]
            else:
                pnl = (trade["entry_price"] - exit_price) * trade["pos_size"]

            trade["status"]      = "closed"
            trade["exit_price"]  = exit_price
            trade["exit_time"]   = datetime.now().isoformat()
            trade["pnl"]         = round(pnl, 2)
            trade["exit_reason"] = exit_reason
            updated += 1

            icon = "✅" if hit_tp else "❌"
            send_telegram(
                f"{icon} <b>Paper Trade Closed</b>\n"
                f"{trade['strategy']}\n"
                f"Exit: {exit_reason.upper()} @ {exit_price}\n"
                f"PnL: ${pnl:+.2f}"
            )

    if updated:
        save_trades(trades)

    return updated

# ─── LIVE EXECUTOR (TODO) ───────────────────────────────────────

def live_execute(strategy, entry_price, atr, pos_size):
    """
    TODO: Pošle order do MT5.
    Implementovat až bude MT5 připojeno.
    """
    print(f"  [LIVE] TODO: MT5 order pro {strategy['ticker']}")
    print(f"         Implementovat v Phase 3 po MT5 připojení")
    return None

# ─── HLAVNÍ RUN LOOP ───────────────────────────────────────────

def run_once(live_mode=False):
    """
    Jeden průchod — zkontroluj všechny strategie a proveď paper/live obchody.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  STRATEGY RUNNER | {now} | {'LIVE' if live_mode else 'PAPER'}")
    print(f"{'='*55}")

    # Zkontroluj FTMO limity
    allowed, reason = risk_gate.can_trade()
    if not allowed:
        print(f"  RISK GATE: {reason} — neobchoduji")
        send_telegram(f"⛔ <b>Risk Gate</b>\n{reason}")
        return

    signals_found = 0
    df_map        = {}   # cache načtených dat pro update_open_trades

    for strat in ACTIVE_STRATEGIES:
        if not strat["active"]:
            continue

        ticker    = strat["ticker"]
        tf        = strat["tf"]
        category  = strat["category"]
        signal_col = strat["signal"]

        # Načti data
        df, is_fresh = load_latest_data(ticker, tf, category)

        if df is None:
            print(f"  {ticker:8} {tf:4} — soubor nenalezen")
            continue

        if not is_fresh:
            print(f"  {ticker:8} {tf:4} — data nejsou čerstvá, přeskakuji")
            continue

        # Cache pro update_open_trades
        df_map[f"{ticker}_{tf}"] = df

        # Zkontroluj signál
        active, entry_price, atr, last_row = check_signal(df, signal_col)

        if not active:
            print(f"  {ticker:8} {tf:4} {signal_col.replace('signal_',''):<25} — žádný signál")
            continue

        if atr is None or atr <= 0:
            print(f"  {ticker:8} {tf:4} — ATR není k dispozici")
            continue

        # Position sizing
        pos_size = risk_gate.position_size(entry_price, atr, strat["sl_atr"])

        print(f"\n  SIGNAL: {strat['name']}")
        print(f"  Entry:  {entry_price} | ATR: {atr:.6f} | Size: {pos_size}")

        # Execute
        if live_mode:
            trade = live_execute(strat, entry_price, atr, pos_size)
        else:
            trade = paper_execute(strat, entry_price, atr, pos_size)

        if trade:
            signals_found += 1
            direction_icon = "SHORT" if strat["direction"] == "short" else "LONG"
            send_telegram(
                f"{'📄' if not live_mode else '🚀'} <b>{'Paper' if not live_mode else 'LIVE'} Trade Opened</b>\n"
                f"<b>{strat['name']}</b>\n"
                f"{direction_icon} @ {entry_price}\n"
                f"TP: {trade['target_price']} | SL: {trade['stop_price']}\n"
                f"Size: {pos_size} | ATR: {atr:.5f}"
            )

    # Aktualizuj otevřené obchody
    closed = update_open_trades(df_map)

    # Souhrn
    trades    = load_trades()
    open_t    = sum(1 for t in trades if t["status"] == "open")
    closed_t  = sum(1 for t in trades if t["status"] == "closed")
    total_pnl = sum(t["pnl"] for t in trades if t["pnl"] is not None)
    wins      = sum(1 for t in trades if t.get("exit_reason") == "tp")
    losses    = sum(1 for t in trades if t.get("exit_reason") == "sl")

    print(f"\n  --- SOUHRN ---")
    print(f"  Nové signály:      {signals_found}")
    print(f"  Otevřené trades:   {open_t}")
    print(f"  Uzavřené trades:   {closed_t} (TP: {wins} | SL: {losses})")
    print(f"  Celkový paper PnL: ${total_pnl:+.2f}")

    return signals_found

# ─── ENTRY POINT ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MarketPal Strategy Runner")
    parser.add_argument("--live",  action="store_true", help="Live mode (MT5)")
    parser.add_argument("--once",  action="store_true", help="Jeden průchod a konec")
    parser.add_argument("--loop",  action="store_true", help="Nepřetržitý běh")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL STRATEGY RUNNER v1       ║")
    mode = "LIVE" if args.live else "PAPER"
    print(f"║      Mode: {mode:<30}║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝")

    if args.live:
        print("\n  ⚠️  LIVE MODE — reálné obchody!")
        print("  MT5 executor zatím není implementován.")
        print("  Spusť bez --live pro paper trading.\n")

    os.makedirs(os.path.dirname(TRADES_LOG), exist_ok=True)

    if args.loop:
        print(f"\n  Loop mode — kontrola každých {LOOP_INTERVAL_MINUTES} minut")
        print("  Zastav: Ctrl+C\n")
        send_telegram(
            f"🤖 <b>Strategy Runner spuštěn</b>\n"
            f"Mode: {mode} | Interval: {LOOP_INTERVAL_MINUTES} min\n"
            f"Strategie: {sum(1 for s in ACTIVE_STRATEGIES if s['active'])}"
        )
        while True:
            try:
                run_once(live_mode=args.live)
                print(f"\n  Další kontrola za {LOOP_INTERVAL_MINUTES} minut...")
                time.sleep(LOOP_INTERVAL_MINUTES * 60)
            except KeyboardInterrupt:
                print("\n  Zastaven (Ctrl+C)")
                send_telegram("⏹️ <b>Strategy Runner zastaven</b>")
                break
    else:
        # Jeden průchod (default i --once)
        run_once(live_mode=args.live)
        print("\n  Hotovo. Pro nepřetržitý běh: python strategy_runner.py --loop")


if __name__ == "__main__":
    main()