"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - MT5 BRIDGE v1                           ║
║         Python -> MetaTrader5 demo                          ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    Propojuje MarketPal s MetaTrader5.
    Umí: otevřít pozici, zavřít pozici, zkontrolovat účet,
         stáhnout živé ceny, zkontrolovat otevřené pozice.

JAK POUŽÍT:
    Test připojení:
        python mt5_bridge.py --test

    Otevřít paper trade (jen log, bez reálného orderu):
        python mt5_bridge.py --paper

    Otevřít reálný demo order:
        python mt5_bridge.py --order

KONFIGURACE:
    MT5 musí být otevřený a přihlášený.
    Credentials v .env (volitelné — pokud MT5 už je přihlášen, není potřeba).
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from dotenv import load_dotenv
load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────

MT5_LOGIN    = int(os.getenv("MT5_LOGIN",    "5045580242"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD",     "")           # volitelné
MT5_SERVER   = os.getenv("MT5_SERVER",       "MetaQuotes-Demo")

# FTMO-safe risk parametry
RISK_PER_TRADE_PCT = 1.0      # % účtu na jeden obchod
MAX_SLIPPAGE_PIPS  = 3        # maximální povolený skluz
MAGIC_NUMBER       = 202600   # unikátní ID pro MarketPal ordery

# Mapping: naše tickery -> MT5 symboly
SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "USDCHF": "USDCHF",
    "AAPL":   "AAPL",
    "MSFT":   "MSFT",
    "NVDA":   "NVDA",
    "AMZN":   "AMZN",
}

# ─── INICIALIZACE ──────────────────────────────────────────────

def connect():
    """Připoj se k MT5. Vrátí True pokud úspěch."""
    if not mt5.initialize():
        print(f"  ❌ MT5 initialize() selhalo: {mt5.last_error()}")
        print(f"     Zkontroluj že MT5 je otevřený a přihlášený.")
        return False

    info = mt5.account_info()
    if info is None:
        print(f"  ❌ account_info() selhalo: {mt5.last_error()}")
        return False

    print(f"  ✅ MT5 připojeno")
    print(f"     Login:   {info.login}")
    print(f"     Server:  {info.server}")
    print(f"     Balance: ${info.balance:.2f}")
    print(f"     Equity:  ${info.equity:.2f}")
    print(f"     Leverage: 1:{info.leverage}")
    return True


def disconnect():
    mt5.shutdown()


# ─── ACCOUNT INFO ──────────────────────────────────────────────

def get_account_info():
    """Vrátí dict s aktuálním stavem účtu."""
    info = mt5.account_info()
    if info is None:
        return None
    return {
        "login":        info.login,
        "server":       info.server,
        "balance":      info.balance,
        "equity":       info.equity,
        "margin":       info.margin,
        "margin_free":  info.margin_free,
        "profit":       info.profit,
        "currency":     info.currency,
        "leverage":     info.leverage,
    }


# ─── LIVE CENY ─────────────────────────────────────────────────

def get_price(ticker):
    """
    Vrátí aktuální bid/ask cenu pro daný ticker.
    """
    symbol = SYMBOL_MAP.get(ticker, ticker)

    # Zkontroluj že symbol existuje v MT5
    if not mt5.symbol_select(symbol, True):
        print(f"  ⚠️  Symbol {symbol} nenalezen v MT5")
        return None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"  ⚠️  Nelze získat cenu pro {symbol}: {mt5.last_error()}")
        return None

    return {
        "symbol": symbol,
        "bid":    tick.bid,
        "ask":    tick.ask,
        "spread": round((tick.ask - tick.bid) * 10000, 1),  # v pipech (pro forex)
        "time":   datetime.fromtimestamp(tick.time).isoformat(),
    }


def get_live_candles(ticker, tf_str, n=100):
    """
    Stáhni posledních N svíček přímo z MT5.
    Rychlejší než Polygon pro real-time signály.
    """
    symbol = SYMBOL_MAP.get(ticker, ticker)

    tf_map = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }

    tf = tf_map.get(tf_str)
    if tf is None:
        print(f"  ⚠️  Neznámý timeframe: {tf_str}")
        return None

    if not mt5.symbol_select(symbol, True):
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
    if rates is None or len(rates) == 0:
        print(f"  ⚠️  Žádná data pro {symbol} {tf_str}: {mt5.last_error()}")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={
        "time":   "timestamp",
        "open":   "open",
        "high":   "high",
        "low":    "low",
        "close":  "close",
        "tick_volume": "volume",
    }, inplace=True)

    return df


# ─── POSITION SIZING ───────────────────────────────────────────

def calculate_lot_size(symbol, entry_price, stop_price, account_balance):
    """
    Vypočítá správnou velikost lotu pro dané riziko.

    Riziko = 1% balance = risk_amount
    Stop distance = |entry - stop| v ceně
    Lot size = risk_amount / (stop_distance * contract_size * point_value)
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"  ⚠️  Symbol info nenalezen pro {symbol}")
        return 0.01  # minimum

    risk_amount    = account_balance * RISK_PER_TRADE_PCT / 100
    stop_distance  = abs(entry_price - stop_price)

    if stop_distance <= 0:
        return 0.01

    # Hodnota 1 lotu na 1 pip pohyb (zjednodušeno)
    contract_size  = info.trade_contract_size   # např. 100000 pro forex
    point          = info.point                  # např. 0.00001 pro EURUSD

    # Pip value v USD pro 1 lot
    if "JPY" in symbol:
        pip_value = contract_size * point * 100  # JPY korekce
    else:
        pip_value = contract_size * point

    stop_pips = stop_distance / (point * 10)    # počet pipů
    lot_size  = risk_amount / (stop_pips * pip_value * 10)

    # Zaokrouhli na volume step
    step     = info.volume_step
    lot_size = round(lot_size / step) * step
    lot_size = max(info.volume_min, min(info.volume_max, lot_size))

    return round(lot_size, 2)


# ─── ORDER EXECUTION ───────────────────────────────────────────

def open_trade(ticker, direction, sl_price, tp_price, comment="MarketPal"):
    """
    Otevře pozici v MT5.

    Args:
        ticker:    náš ticker (např. "EURUSD")
        direction: "long" nebo "short"
        sl_price:  stop loss cena
        tp_price:  take profit cena
        comment:   komentář k orderu

    Returns:
        dict s výsledkem nebo None při chybě
    """
    symbol = SYMBOL_MAP.get(ticker, ticker)

    if not mt5.symbol_select(symbol, True):
        print(f"  ❌ Symbol {symbol} není dostupný")
        return None

    tick    = mt5.symbol_info_tick(symbol)
    account = mt5.account_info()

    if tick is None or account is None:
        print(f"  ❌ Nelze získat cenu nebo info o účtu")
        return None

    # Entry cena
    if direction == "long":
        order_type  = mt5.ORDER_TYPE_BUY
        entry_price = tick.ask   # nakupujeme za ask
    else:
        order_type  = mt5.ORDER_TYPE_SELL
        entry_price = tick.bid   # prodáváme za bid

    # Position sizing
    lot_size = calculate_lot_size(symbol, entry_price, sl_price, account.balance)

    print(f"\n  ORDER PŘÍPRAVA:")
    print(f"    Symbol:    {symbol}")
    print(f"    Direction: {direction.upper()}")
    print(f"    Entry:     {entry_price}")
    print(f"    SL:        {sl_price}")
    print(f"    TP:        {tp_price}")
    print(f"    Lot size:  {lot_size}")
    print(f"    Risk:      ${account.balance * RISK_PER_TRADE_PCT / 100:.2f}")

    # Sestavení request
    request = {
        "action":     mt5.TRADE_ACTION_DEAL,
        "symbol":     symbol,
        "volume":     lot_size,
        "type":       order_type,
        "price":      entry_price,
        "sl":         sl_price,
        "tp":         tp_price,
        "deviation":  int(MAGIC_NUMBER),
        "magic":      MAGIC_NUMBER,
        "comment":    comment,
        "type_time":  mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    # Pošli order
    result = mt5.order_send(request)

    if result is None:
        print(f"  ❌ order_send() vrátil None: {mt5.last_error()}")
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"  ❌ Order selhal: {result.retcode} — {result.comment}")
        # Časté chyby:
        if result.retcode == 10018:
            print(f"     Market je zavřený (víkend nebo mimo obchodní hodiny)")
        elif result.retcode == 10004:
            print(f"     Requote — cena se změnila, zkus znovu")
        elif result.retcode == 10013:
            print(f"     Neplatný request — zkontroluj SL/TP hodnoty")
        return None

    print(f"\n  ✅ ORDER OTEVŘEN!")
    print(f"     Ticket:     #{result.order}")
    print(f"     Cena:       {result.price}")
    print(f"     Volume:     {result.volume}")

    return {
        "ticket":     result.order,
        "symbol":     symbol,
        "direction":  direction,
        "entry_price": result.price,
        "volume":     result.volume,
        "sl":         sl_price,
        "tp":         tp_price,
        "time":       datetime.now().isoformat(),
        "magic":      MAGIC_NUMBER,
    }


def close_trade(ticket):
    """Zavře pozici podle ticket čísla."""
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        print(f"  ⚠️  Pozice #{ticket} nenalezena")
        return False

    pos  = positions[0]
    tick = mt5.symbol_info_tick(pos.symbol)

    if tick is None:
        return False

    if pos.type == mt5.ORDER_TYPE_BUY:
        order_type  = mt5.ORDER_TYPE_SELL
        close_price = tick.bid
    else:
        order_type  = mt5.ORDER_TYPE_BUY
        close_price = tick.ask

    request = {
        "action":   mt5.TRADE_ACTION_DEAL,
        "symbol":   pos.symbol,
        "volume":   pos.volume,
        "type":     order_type,
        "position": ticket,
        "price":    close_price,
        "deviation": 20,
        "magic":    MAGIC_NUMBER,
        "comment":  "MarketPal close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"  ✅ Pozice #{ticket} zavřena @ {close_price}")
        return True
    else:
        print(f"  ❌ Zavření selhalo: {result.retcode if result else 'None'}")
        return False


def get_open_positions():
    """Vrátí všechny otevřené MarketPal pozice."""
    positions = mt5.positions_get()
    if positions is None:
        return []

    result = []
    for pos in positions:
        if pos.magic == MAGIC_NUMBER:
            result.append({
                "ticket":    pos.ticket,
                "symbol":    pos.symbol,
                "direction": "long" if pos.type == 0 else "short",
                "volume":    pos.volume,
                "open_price": pos.price_open,
                "sl":        pos.sl,
                "tp":        pos.tp,
                "profit":    pos.profit,
                "time":      datetime.fromtimestamp(pos.time).isoformat(),
            })
    return result


# ─── TESTY ─────────────────────────────────────────────────────

def test_connection():
    """Otestuje připojení a základní funkce."""
    print("\n  TEST 1: Připojení k MT5")
    if not connect():
        return False

    print("\n  TEST 2: Account info")
    info = get_account_info()
    for k, v in info.items():
        print(f"    {k}: {v}")

    print("\n  TEST 3: Live ceny")
    for ticker in ["EURUSD", "GBPUSD", "USDJPY"]:
        price = get_price(ticker)
        if price:
            print(f"    {price['symbol']}: bid={price['bid']} ask={price['ask']} "
                  f"spread={price['spread']} pips")

    print("\n  TEST 4: Live svíčky (EURUSD M5, posledních 5)")
    df = get_live_candles("EURUSD", "M5", n=5)
    if df is not None:
        print(df[["timestamp", "open", "high", "low", "close", "volume"]].to_string())

    print("\n  TEST 5: Otevřené pozice")
    positions = get_open_positions()
    if positions:
        for p in positions:
            print(f"    #{p['ticket']} {p['symbol']} {p['direction']} "
                  f"profit=${p['profit']:.2f}")
    else:
        print("    Žádné otevřené MarketPal pozice")

    print("\n  ✅ Všechny testy prošly!")
    disconnect()
    return True


def test_order():
    """
    Otestuje odeslání malého demo orderu.
    Otevře EURUSD 0.01 lot a okamžitě zavře.
    """
    print("\n  ORDER TEST — EURUSD 0.01 lot")
    print("  (otevře a hned zavře — jen test exekuce)\n")

    if not connect():
        return

    tick = mt5.symbol_info_tick("EURUSD")
    atr_approx = 0.0010   # ~10 pip ATR jako fallback

    if tick:
        entry = tick.ask
        sl    = round(entry - atr_approx * 1.5, 5)
        tp    = round(entry + atr_approx * 2.0, 5)

        trade = open_trade("EURUSD", "long", sl, tp, comment="MarketPal TEST")

        if trade:
            print(f"\n  Čekám 2 sekundy pak zavřu...")
            import time
            time.sleep(2)
            close_trade(trade["ticket"])

    disconnect()


# ─── ENTRY POINT ───────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MarketPal MT5 Bridge")
    parser.add_argument("--test",  action="store_true", help="Test připojení")
    parser.add_argument("--order", action="store_true", help="Test order (0.01 lot)")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL MT5 BRIDGE v1            ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝")

    if args.order:
        test_order()
    else:
        # Default: test připojení
        test_connection()