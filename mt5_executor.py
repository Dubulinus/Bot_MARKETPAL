"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - MT5 EXECUTOR v1                         ║
║         Strategy Runner + MT5 Bridge = živý bot             ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    Tohle je mozek živého bota.
    Kombinuje strategy_runner (signály) + mt5_bridge (exekuce).

    1. Načte Gold data
    2. Zkontroluje signály na poslední svíčce
    3. Zkontroluje FTMO limity
    4. Pošle order do MT5
    5. Sleduje otevřené pozice (TP/SL hit?)
    6. Telegram report

JAK SPUSTIT:
    Demo trading (reálné ordery na demo účtu):
        python mt5_executor.py

    Jeden průchod:
        python mt5_executor.py --once

    Nepřetržitý běh každých 5 minut:
        python mt5_executor.py --loop

    Pouze monitoring otevřených pozic (bez nových orderů):
        python mt5_executor.py --monitor
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from dotenv import load_dotenv
load_dotenv()

# Importuj naše moduly
try:
    from mt5_bridge import (
        connect, disconnect, get_account_info,
        get_price, get_live_candles, open_trade,
        close_trade, get_open_positions, calculate_lot_size,
        SYMBOL_MAP, MAGIC_NUMBER
    )
    MT5_OK = True
except ImportError as e:
    print(f"⚠️  mt5_bridge.py nenalezen: {e}")
    MT5_OK = False

# ─── CONFIG ────────────────────────────────────────────────────

GOLD_DIR       = "data/04_GOLD_FEATURES"
TRADES_LOG     = "data/08_PAPER_TRADES/mt5_trades.json"
FTMO_STATE     = "data/ftmo_state.json"
LOOP_INTERVAL  = 5   # minuty

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# FTMO limity (s bufferem)
ACCOUNT_SIZE       = 10000
MAX_DAILY_LOSS_PCT = 4.5
MAX_TOTAL_LOSS_PCT = 9.0
RISK_PER_TRADE_PCT = 1.0
MAX_OPEN_TRADES    = 3     # max současně otevřených pozic

# ─── AKTIVNÍ STRATEGIE ─────────────────────────────────────────

ACTIVE_STRATEGIES = [
    {
        "name":      "AMZN RSI OB Exit M5",
        "ticker":    "AMZN",
        "tf":        "M5",
        "category":  "stocks",
        "signal":    "signal_rsi_overbought_exit",
        "direction": "short",
        "pt_atr":    3.0,
        "sl_atr":    3.0,
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
        "active":    True,
    },
    {
        "name":      "USDCHF Stoch Pin Bear M5",
        "ticker":    "USDCHF",
        "tf":        "M5",
        "category":  "forex",
        "signal":    "signal_stoch_pin_bear",
        "direction": "short",
        "pt_atr":    2.0,
        "sl_atr":    1.0,
        "active":    True,
    },
]

# ─── TELEGRAM ──────────────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception:
        pass

# ─── FTMO RISK GATE ────────────────────────────────────────────

def load_ftmo_state():
    if os.path.exists(FTMO_STATE):
        try:
            with open(FTMO_STATE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"equity": ACCOUNT_SIZE, "daily_pnl": 0.0, "total_pnl": 0.0}


def save_ftmo_state(state):
    os.makedirs(os.path.dirname(FTMO_STATE), exist_ok=True)
    with open(FTMO_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def check_ftmo_limits():
    """Zkontroluje FTMO limity. Vrátí (allowed, reason)."""
    state     = load_ftmo_state()
    daily_pnl = state.get("daily_pnl", 0.0)
    total_pnl = state.get("total_pnl", 0.0)

    max_daily = ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT / 100
    max_total = ACCOUNT_SIZE * MAX_TOTAL_LOSS_PCT / 100

    if daily_pnl <= -max_daily:
        return False, f"Daily loss limit: ${daily_pnl:.2f} (max -${max_daily:.2f})"
    if total_pnl <= -max_total:
        return False, f"Total loss limit: ${total_pnl:.2f} (max -${max_total:.2f})"

    # Warning při 70%
    if daily_pnl <= -max_daily * 0.7:
        send_telegram(
            f"⚠️ <b>FTMO Warning</b>\n"
            f"Daily PnL: ${daily_pnl:.2f} — blíží se limitu ${-max_daily:.2f}"
        )

    return True, "OK"


def update_ftmo_state(pnl):
    """Aktualizuj FTMO state po uzavřeném obchodu."""
    state = load_ftmo_state()
    state["daily_pnl"]  = round(state.get("daily_pnl", 0) + pnl, 2)
    state["total_pnl"]  = round(state.get("total_pnl", 0) + pnl, 2)
    state["equity"]     = round(state.get("equity", ACCOUNT_SIZE) + pnl, 2)
    state["last_update"] = datetime.now().isoformat()
    save_ftmo_state(state)

# ─── TRADES LOG ────────────────────────────────────────────────

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


def log_trade(trade_data):
    trades = load_trades()
    trades.append(trade_data)
    save_trades(trades)

# ─── SIGNAL CHECK ──────────────────────────────────────────────

def check_signal_on_gold(ticker, tf, category, signal_col):
    """Zkontroluj signál na Gold datech (z ranní pipeline)."""
    path = Path(GOLD_DIR) / tf / category / f"{ticker}.parquet"
    if not path.exists():
        return False, None, None

    df   = pd.read_parquet(path)
    last = df.iloc[-1]

    active      = bool(last.get(signal_col, False))
    entry_price = float(last.get("close", 0))
    atr         = float(last.get("atr", 0))

    return active, entry_price, atr


def already_in_trade(ticker):
    """Zkontroluj jestli už máme otevřenou pozici pro tento ticker."""
    positions = get_open_positions()
    symbol    = SYMBOL_MAP.get(ticker, ticker)
    return any(p["symbol"] == symbol for p in positions)

# ─── MONITORING OTEVŘENÝCH POZIC ───────────────────────────────

def monitor_positions():
    """
    Zkontroluje všechny otevřené MarketPal pozice.
    MT5 sám hlídá TP/SL — tato funkce jen loguje stav.
    """
    positions = get_open_positions()

    if not positions:
        return 0

    print(f"\n  OTEVŘENÉ POZICE ({len(positions)}):")
    total_profit = 0

    for p in positions:
        icon   = "📈" if p["profit"] >= 0 else "📉"
        profit = p["profit"]
        total_profit += profit
        print(f"  {icon} #{p['ticket']} {p['symbol']:8} {p['direction']:5} "
              f"@ {p['open_price']} | P&L: ${profit:+.2f}")

    print(f"  Celkový float P&L: ${total_profit:+.2f}")
    return len(positions)


def check_closed_positions():
    """
    Zkontroluje history pro nově uzavřené pozice.
    Aktualizuje FTMO state.
    """
    from datetime import timedelta
    history = mt5.history_deals_get(
        datetime.now() - timedelta(hours=24),
        datetime.now()
    )

    if history is None:
        return

    trades     = load_trades()
    open_tickets = {t.get("ticket") for t in trades if t.get("status") == "open"}

    for deal in history:
        if deal.magic != MAGIC_NUMBER:
            continue
        if deal.entry != mt5.DEAL_ENTRY_OUT:
            continue

        ticket = deal.position_id
        if ticket not in open_tickets:
            continue

        # Najdi odpovídající open trade
        for trade in trades:
            if trade.get("ticket") == ticket and trade.get("status") == "open":
                pnl    = deal.profit
                reason = "tp" if pnl > 0 else "sl"

                trade["status"]      = "closed"
                trade["exit_price"]  = deal.price
                trade["exit_time"]   = datetime.fromtimestamp(deal.time).isoformat()
                trade["pnl"]         = round(pnl, 2)
                trade["exit_reason"] = reason

                update_ftmo_state(pnl)

                icon = "✅" if pnl > 0 else "❌"
                print(f"\n  {icon} UZAVŘENO: {trade['name']}")
                print(f"     Exit: {reason.upper()} @ {deal.price} | P&L: ${pnl:+.2f}")

                send_telegram(
                    f"{icon} <b>Trade Closed</b>\n"
                    f"{trade['name']}\n"
                    f"{reason.upper()} @ {deal.price}\n"
                    f"P&L: ${pnl:+.2f}"
                )

    save_trades(trades)

# ─── HLAVNÍ RUN ────────────────────────────────────────────────

def run_once():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  MT5 EXECUTOR | {now}")
    print(f"{'='*55}")

    # 1. Zkontroluj FTMO limity
    allowed, reason = check_ftmo_limits()
    if not allowed:
        msg = f"⛔ Risk Gate: {reason}"
        print(f"  {msg}")
        send_telegram(f"⛔ <b>Risk Gate</b>\n{reason}")
        monitor_positions()
        return

    # 2. Zkontroluj uzavřené pozice
    check_closed_positions()

    # 3. Monitoring otevřených pozic
    open_count = monitor_positions()

    if open_count >= MAX_OPEN_TRADES:
        print(f"\n  Max otevřených pozic ({MAX_OPEN_TRADES}) dosaženo — nové signály přeskakuji")
        return

    # 4. Zkontroluj signály
    print(f"\n  KONTROLA SIGNÁLŮ:")
    new_trades = 0

    for strat in ACTIVE_STRATEGIES:
        if not strat["active"]:
            continue

        ticker     = strat["ticker"]
        tf         = strat["tf"]
        category   = strat["category"]
        signal_col = strat["signal"]

        # Přeskoč pokud už máme pozici v tomto instrumentu
        if already_in_trade(ticker):
            print(f"  {ticker:8} {tf:4} — již otevřená pozice, přeskakuji")
            continue

        # Zkontroluj signál na Gold datech
        active, entry_price, atr = check_signal_on_gold(
            ticker, tf, category, signal_col
        )

        if not active:
            print(f"  {ticker:8} {tf:4} {signal_col.replace('signal_',''):<25} — žádný signál")
            continue

        if not atr or atr <= 0:
            print(f"  {ticker:8} {tf:4} — ATR nedostupné")
            continue

        # Získej live cenu z MT5 (přesnější než Gold close)
        price_info = get_price(ticker)
        if price_info:
            if strat["direction"] == "short":
                entry_price = price_info["bid"]
            else:
                entry_price = price_info["ask"]

        # Vypočítej TP/SL
        if strat["direction"] == "long":
            sl_price = round(entry_price - strat["sl_atr"] * atr, 6)
            tp_price = round(entry_price + strat["pt_atr"] * atr, 6)
        else:
            sl_price = round(entry_price + strat["sl_atr"] * atr, 6)
            tp_price = round(entry_price - strat["pt_atr"] * atr, 6)

        print(f"\n  SIGNAL: {strat['name']}")

        # Pošli order do MT5
        trade = open_trade(
            ticker      = ticker,
            direction   = strat["direction"],
            sl_price    = sl_price,
            tp_price    = tp_price,
            comment     = f"MP_{strat['name'][:12]}"
        )

        if trade:
            trade["name"]     = strat["name"]
            trade["signal"]   = signal_col
            trade["status"]   = "open"
            trade["strategy"] = strat
            log_trade(trade)
            new_trades += 1

            send_telegram(
                f"🚀 <b>MT5 Order Opened</b>\n"
                f"<b>{strat['name']}</b>\n"
                f"{strat['direction'].upper()} @ {trade['entry_price']}\n"
                f"TP: {tp_price} | SL: {sl_price}\n"
                f"Vol: {trade['volume']} lot"
            )

    # 5. Souhrn
    state      = load_ftmo_state()
    trades_all = load_trades()
    closed     = [t for t in trades_all if t.get("status") == "closed"]
    wins       = sum(1 for t in closed if t.get("exit_reason") == "tp")
    losses     = sum(1 for t in closed if t.get("exit_reason") == "sl")
    total_pnl  = sum(t.get("pnl", 0) for t in closed)

    print(f"\n  --- SOUHRN ---")
    print(f"  Nové ordery:       {new_trades}")
    print(f"  Otevřené pozice:   {open_count + new_trades}")
    print(f"  Uzavřené:          {len(closed)} (TP:{wins} SL:{losses})")
    print(f"  Celkový P&L:       ${total_pnl:+.2f}")
    print(f"  Daily P&L:         ${state.get('daily_pnl', 0):+.2f}")
    print(f"  FTMO limit:        ${-ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT / 100:.2f}/den")


# ─── ENTRY POINT ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MarketPal MT5 Executor")
    parser.add_argument("--once",    action="store_true")
    parser.add_argument("--loop",    action="store_true")
    parser.add_argument("--monitor", action="store_true")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL MT5 EXECUTOR v1          ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝")

    if not MT5_OK:
        print("❌ mt5_bridge.py nenalezen. Dej oba soubory do stejné složky.")
        sys.exit(1)

    if not connect():
        print("❌ MT5 připojení selhalo. Je MT5 otevřený?")
        sys.exit(1)

    if args.monitor:
        print("\n  Monitor mode — jen sledování pozic\n")
        monitor_positions()
        check_closed_positions()
        disconnect()
        return

    if args.loop:
        print(f"\n  Loop mode — každých {LOOP_INTERVAL} minut")
        print("  Zastav: Ctrl+C\n")
        send_telegram(
            f"🤖 <b>MT5 Executor spuštěn</b>\n"
            f"Demo účet: {mt5.account_info().login}\n"
            f"Strategie: {sum(1 for s in ACTIVE_STRATEGIES if s['active'])}\n"
            f"Interval: {LOOP_INTERVAL} min"
        )
        while True:
            try:
                run_once()
                print(f"\n  Další kontrola za {LOOP_INTERVAL} minut...")
                time.sleep(LOOP_INTERVAL * 60)
            except KeyboardInterrupt:
                print("\n  Zastaven (Ctrl+C)")
                send_telegram("⏹️ <b>MT5 Executor zastaven</b>")
                disconnect()
                break
    else:
        run_once()
        disconnect()


if __name__ == "__main__":
    main()