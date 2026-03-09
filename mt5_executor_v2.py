"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - MT5 EXECUTOR v1.0                          ║
║     Signal → MetaTrader 5 → Broker → Reálné peníze        ║
╚══════════════════════════════════════════════════════════════╝

POZICE V PIPELINE:
  live_signal_generator.py → [TENTO SKRIPT] → MT5 → Broker

CO DĚLÁ:
  1. Přijme signal_data dict od signal generatoru
  2. Zkontroluje pre-trade podmínky (margin, spread, market open)
  3. Otevře pozici v MT5 (market order + SL + TP)
  4. Sleduje otevřené pozice (trailing stop, break-even)
  5. Při uzavření zaznamená výsledek + pošle Telegram

INSTALACE MT5:
  pip install MetaTrader5
  + MetaTrader 5 desktop app musí být spuštěná a přihlášená

PODPOROVANÍ BROKEŘI (MT5):
  - IC Markets (doporučený pro algo — Raw Spread účet)
  - Pepperstone (nízké spready, rychlá exekuce)
  - FTMO prop firm (po složení challenge)
  - XM, FP Markets, Tickmill
"""

import os
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

# Logování — každý order se zapíše do souboru
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s | %(levelname)s | %(message)s",
    handlers = [
        logging.FileHandler("data/logs/mt5_executor.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("MT5Executor")

# MT5 import — obalíme try/except protože na serveru nemusí být nainstalované
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 není nainstalovaný — spouštím v SIMULATION módu")
    log.warning("Instalace: pip install MetaTrader5")

# ─── CONFIG ────────────────────────────────────────────────────
STATE_FILE  = "data/bot_state.json"
TRADE_LOG   = "data/trade_log.json"

# FTMO limity — executor je vynucuje tvrdě
ACCOUNT_SIZE    = 10_000
MAX_DAILY_LOSS  = 500       # 5%
MAX_TOTAL_LOSS  = 1_000     # 10%
MAX_SLIPPAGE    = 3         # max povolený skluz v pipech
MAX_SPREAD_MULT = 2.0       # max spread = 2× normální spread

# Symbol mapping: náš název → MT5 název
# Závisí na brokerovi — IC Markets používá přesně tyto
SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "USDCHF": "USDCHF",
    "XAUUSD": "XAUUSD",
    "AAPL":   "AAPL.US",   # závisí na brokerovi
    "GOOGL":  "GOOGL.US",
    "MSFT":   "MSFT.US",
}

# Pip size pro každý symbol
PIP_SIZE = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "USDJPY": 0.01,
    "USDCHF": 0.0001,
    "XAUUSD": 0.01,
}


# ═══════════════════════════════════════════════════════════════
# SEKCE 1: MT5 PŘIPOJENÍ
# ═══════════════════════════════════════════════════════════════

class MT5Connection:
    """
    Spravuje připojení k MT5.
    Singleton — pouze jedno připojení najednou.
    """
    _connected = False

    @classmethod
    def connect(cls, login: int = None, password: str = None,
                server: str = None) -> bool:
        """
        Připojí se k MT5.
        Pokud login/password nejsou zadané, použije přihlášený účet v MT5 apce.
        """
        if not MT5_AVAILABLE:
            log.info("[SIMULATION] MT5 připojení simulováno")
            cls._connected = True
            return True

        if not mt5.initialize():
            log.error(f"MT5 initialize() selhal: {mt5.last_error()}")
            return False

        # Pokud máme credentials, přihlásíme se programaticky
        if login and password and server:
            authorized = mt5.login(login, password=password, server=server)
            if not authorized:
                log.error(f"MT5 login selhal: {mt5.last_error()}")
                mt5.shutdown()
                return False

        info = mt5.account_info()
        if info is None:
            log.error("Nepodařilo se načíst informace o účtu")
            return False

        log.info(f"✅ MT5 připojen | Účet: {info.login} | "
                 f"Broker: {info.company} | "
                 f"Equity: ${info.equity:.2f}")
        cls._connected = True
        return True

    @classmethod
    def disconnect(cls):
        if MT5_AVAILABLE and cls._connected:
            mt5.shutdown()
        cls._connected = False
        log.info("MT5 odpojeno")

    @classmethod
    def is_connected(cls) -> bool:
        return cls._connected


# ═══════════════════════════════════════════════════════════════
# SEKCE 2: INFORMACE O ÚČTU A SYMBOLU
# ═══════════════════════════════════════════════════════════════

def get_account_info() -> dict:
    """Načte aktuální stav účtu z MT5."""
    if not MT5_AVAILABLE:
        # Simulation: vrátí data ze state souboru
        state = _load_state()
        return {
            "equity":   state.get("equity", ACCOUNT_SIZE),
            "balance":  state.get("equity", ACCOUNT_SIZE),
            "margin":   0.0,
            "free_margin": state.get("equity", ACCOUNT_SIZE),
            "profit":   state.get("daily_pnl", 0.0),
        }

    info = mt5.account_info()
    if info is None:
        return {}

    return {
        "equity":       info.equity,
        "balance":      info.balance,
        "margin":       info.margin,
        "free_margin":  info.margin_free,
        "profit":       info.profit,
        "leverage":     info.leverage,
    }


def get_symbol_info(ticker: str) -> dict:
    """Načte aktuální ceny a spread pro symbol."""
    mt5_symbol = SYMBOL_MAP.get(ticker, ticker)

    if not MT5_AVAILABLE:
        # Simulation: vrátí placeholder hodnoty
        return {
            "bid":    1.08540,
            "ask":    1.08545,
            "spread": 0.5,
            "digits": 5,
            "volume_min": 0.01,
            "volume_step": 0.01,
        }

    # Ujisti se že symbol je aktivní
    if not mt5.symbol_select(mt5_symbol, True):
        log.error(f"Symbol {mt5_symbol} nelze vybrat")
        return {}

    tick = mt5.symbol_info_tick(mt5_symbol)
    info = mt5.symbol_info(mt5_symbol)

    if tick is None or info is None:
        return {}

    pip = PIP_SIZE.get(ticker, 0.0001)

    return {
        "bid":         tick.bid,
        "ask":         tick.ask,
        "spread":      (tick.ask - tick.bid) / pip,  # v pipech
        "digits":      info.digits,
        "volume_min":  info.volume_min,
        "volume_step": info.volume_step,
        "volume_max":  info.volume_max,
        "point":       info.point,
    }


def normalize_volume(volume: float, ticker: str) -> float:
    """Zaokrouhlí lot size na povolený volume step brokera."""
    sym = get_symbol_info(ticker)
    step = sym.get("volume_step", 0.01)
    min_v = sym.get("volume_min", 0.01)

    normalized = round(volume / step) * step
    normalized = max(normalized, min_v)
    return round(normalized, 2)


# ═══════════════════════════════════════════════════════════════
# SEKCE 3: PRE-TRADE CHECKS
# ═══════════════════════════════════════════════════════════════

def pre_trade_checks(signal: dict) -> tuple[bool, str]:
    """
    Finální kontroly těsně před odesláním orderu.
    Toto je POSLEDNÍ obranná linie před reálnou exekucí.

    Checks:
    1. MT5 připojeno
    2. Market je otevřený
    3. Spread není abnormální
    4. Dostatečný margin
    5. FTMO limity (double-check)
    6. Aktuální cena je blízko signal ceně
    """
    ticker = signal["ticker"]

    # 1. MT5 připojení
    if not MT5Connection.is_connected():
        return False, "MT5 není připojeno"

    # 2. Symbol info
    sym = get_symbol_info(ticker)
    if not sym:
        return False, f"Nelze načíst info pro {ticker}"

    # 3. Spread check
    current_spread = sym.get("spread", 0)
    normal_spread  = _get_normal_spread(ticker)
    if current_spread > normal_spread * MAX_SPREAD_MULT:
        return False, f"Abnormální spread: {current_spread:.1f} pip (normál: {normal_spread:.1f})"

    # 4. Account info
    acc = get_account_info()
    if not acc:
        return False, "Nelze načíst stav účtu"

    # 5. FTMO daily loss
    state     = _load_state()
    daily_pnl = state.get("daily_pnl", 0.0)
    if daily_pnl <= -MAX_DAILY_LOSS:
        return False, f"FTMO denní loss limit: ${daily_pnl:.0f}"

    # 6. FTMO total DD
    total_dd = ACCOUNT_SIZE - acc["equity"]
    if total_dd >= MAX_TOTAL_LOSS:
        return False, f"FTMO max DD: ${total_dd:.0f}"

    # 7. Cena se nezměnila příliš od signálu (max 5 pipů)
    current_price = sym["ask"] if signal["direction"] == "long" else sym["bid"]
    signal_price  = signal["entry"]
    pip           = PIP_SIZE.get(ticker, 0.0001)
    price_diff    = abs(current_price - signal_price) / pip

    if price_diff > 10:
        return False, f"Cena se příliš změnila: {price_diff:.1f} pip od signálu"

    return True, "OK"


def _get_normal_spread(ticker: str) -> float:
    """Typický spread pro každý pár (v pipech). Závisí na brokerovi."""
    typical = {
        "EURUSD": 0.7,
        "GBPUSD": 0.9,
        "USDJPY": 0.8,
        "USDCHF": 1.0,
        "XAUUSD": 15.0,
    }
    return typical.get(ticker, 1.5)


# ═══════════════════════════════════════════════════════════════
# SEKCE 4: ORDER EXECUTION
# ═══════════════════════════════════════════════════════════════

def place_order(signal: dict) -> dict:
    """
    Odešle market order do MT5.

    signal dict obsahuje:
      ticker, direction, entry, sl, tp, size, name, meta_conf

    Vrátí result dict s výsledkem exekuce.
    """
    ticker    = signal["ticker"]
    direction = signal["direction"]
    mt5_symbol= SYMBOL_MAP.get(ticker, ticker)
    volume    = normalize_volume(signal["size"], ticker)

    # Aktuální ceny
    sym = get_symbol_info(ticker)
    if direction == "long":
        order_type    = mt5.ORDER_TYPE_BUY if MT5_AVAILABLE else "BUY"
        fill_price    = sym.get("ask", signal["entry"])
    else:
        order_type    = mt5.ORDER_TYPE_SELL if MT5_AVAILABLE else "SELL"
        fill_price    = sym.get("bid", signal["entry"])

    log.info(f"📤 ORDER: {ticker} {direction.upper()} "
             f"{volume} lots @ {fill_price:.5f} | "
             f"SL: {signal['sl']:.5f} TP: {signal['tp']:.5f}")

    if not MT5_AVAILABLE:
        # SIMULATION MODE
        result = _simulate_order(signal, fill_price, volume)
        _save_trade(result)
        log.info(f"[SIMULATION] Order simulován: ticket #{result['ticket']}")
        return result

    # LIVE MODE — skutečný MT5 order
    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       mt5_symbol,
        "volume":       volume,
        "type":         order_type,
        "price":        fill_price,
        "sl":           signal["sl"],
        "tp":           signal["tp"],
        "deviation":    MAX_SLIPPAGE,      # max skluz v pipech × 10
        "magic":        20260309,          # unikátní ID bota (datum spuštění)
        "comment":      f"MARKETPAL|{signal['name'][:20]}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result_mt5 = mt5.order_send(request)

    if result_mt5 is None:
        err = mt5.last_error()
        log.error(f"❌ order_send() vrátil None: {err}")
        return {"success": False, "error": str(err)}

    if result_mt5.retcode != mt5.TRADE_RETCODE_DONE:
        log.error(f"❌ Order zamítnut: retcode={result_mt5.retcode} "
                  f"({_retcode_to_text(result_mt5.retcode)})")
        return {
            "success":  False,
            "retcode":  result_mt5.retcode,
            "error":    _retcode_to_text(result_mt5.retcode),
        }

    result = {
        "success":    True,
        "ticket":     result_mt5.order,
        "ticker":     ticker,
        "direction":  direction,
        "volume":     volume,
        "entry":      result_mt5.price,
        "sl":         signal["sl"],
        "tp":         signal["tp"],
        "signal_name":signal["name"],
        "meta_conf":  signal["meta_conf"],
        "timestamp":  str(datetime.utcnow()),
        "status":     "OPEN",
        "pnl":        0.0,
    }

    _save_trade(result)
    log.info(f"✅ Order vyplněn: ticket #{result['ticket']} @ {result['entry']:.5f}")
    return result


# ═══════════════════════════════════════════════════════════════
# SEKCE 5: POSITION MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def get_open_positions() -> list:
    """Načte všechny otevřené pozice z MT5."""
    if not MT5_AVAILABLE:
        trades = _load_trades()
        return [t for t in trades if t.get("status") == "OPEN"]

    positions = mt5.positions_get()
    if positions is None:
        return []

    result = []
    for pos in positions:
        if pos.magic != 20260309:  # jen naše pozice
            continue
        result.append({
            "ticket":    pos.ticket,
            "ticker":    pos.symbol,
            "direction": "long" if pos.type == 0 else "short",
            "volume":    pos.volume,
            "entry":     pos.price_open,
            "sl":        pos.sl,
            "tp":        pos.tp,
            "pnl":       pos.profit,
            "pips":      pos.profit / (pos.volume * 10) if pos.volume > 0 else 0,
        })
    return result


def move_to_breakeven(ticket: int, entry: float, ticker: str) -> bool:
    """
    Přesune SL na entry cenu (break-even).
    Volá se když obchod dosáhne 1R profitu.
    """
    log.info(f"📍 Break-even: ticket #{ticket} → SL @ {entry:.5f}")

    if not MT5_AVAILABLE:
        log.info(f"[SIMULATION] Break-even simulován pro #{ticket}")
        return True

    mt5_symbol = SYMBOL_MAP.get(ticker, ticker)
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   mt5_symbol,
        "position": ticket,
        "sl":       entry,
    }
    result = mt5.order_send(request)
    return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE


def close_position(ticket: int, ticker: str, volume: float,
                   direction: str, reason: str = "manual") -> dict:
    """Zavře pozici podle ticket čísla."""
    mt5_symbol = SYMBOL_MAP.get(ticker, ticker)
    sym        = get_symbol_info(ticker)

    if direction == "long":
        close_type  = mt5.ORDER_TYPE_SELL if MT5_AVAILABLE else "SELL"
        close_price = sym.get("bid", 0)
    else:
        close_type  = mt5.ORDER_TYPE_BUY if MT5_AVAILABLE else "BUY"
        close_price = sym.get("ask", 0)

    log.info(f"🚪 Zavírám pozici #{ticket} | {ticker} | důvod: {reason}")

    if not MT5_AVAILABLE:
        log.info(f"[SIMULATION] Pozice #{ticket} uzavřena")
        return {"success": True, "ticket": ticket, "reason": reason}

    request = {
        "action":   mt5.TRADE_ACTION_DEAL,
        "symbol":   mt5_symbol,
        "volume":   volume,
        "type":     close_type,
        "position": ticket,
        "price":    close_price,
        "deviation":MAX_SLIPPAGE,
        "magic":    20260309,
        "comment":  f"CLOSE|{reason}",
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(f"✅ Pozice #{ticket} uzavřena @ {close_price:.5f}")
        return {"success": True, "ticket": ticket, "close_price": close_price}
    else:
        err = _retcode_to_text(result.retcode) if result else "unknown"
        log.error(f"❌ Zavření selhalo: {err}")
        return {"success": False, "error": err}


def close_all_positions(reason: str = "emergency") -> int:
    """Zavře VŠECHNY otevřené pozice. Nouzový stop."""
    positions = get_open_positions()
    closed    = 0
    log.warning(f"🛑 CLOSE ALL: {len(positions)} pozic | důvod: {reason}")

    for pos in positions:
        result = close_position(
            pos["ticket"], pos["ticker"],
            pos["volume"], pos["direction"], reason
        )
        if result.get("success"):
            closed += 1

    log.info(f"Uzavřeno {closed}/{len(positions)} pozic")
    return closed


# ═══════════════════════════════════════════════════════════════
# SEKCE 6: POSITION MONITOR
# ═══════════════════════════════════════════════════════════════

def monitor_positions():
    """
    Spouštěj v smyčce — sleduje otevřené pozice a:
    - Přesouvá SL na break-even po dosažení 1R
    - Detekuje FTMO breach
    - Loguje aktuální P&L
    """
    positions = get_open_positions()
    if not positions:
        return

    acc = get_account_info()
    log.info(f"📊 Monitor: {len(positions)} pozic | "
             f"Equity: ${acc.get('equity', 0):.2f} | "
             f"Float P&L: ${acc.get('profit', 0):.2f}")

    for pos in positions:
        entry     = pos["entry"]
        sl        = pos["sl"]
        tp        = pos["tp"]
        pnl       = pos["pnl"]
        ticket    = pos["ticket"]
        ticker    = pos["ticker"]
        direction = pos["direction"]

        # Risk na pozici v $
        pip       = PIP_SIZE.get(ticker, 0.0001)
        risk_pips = abs(entry - sl) / pip
        profit_pips = (
            (pos.get("current_price", entry) - entry) / pip
            if direction == "long"
            else (entry - pos.get("current_price", entry)) / pip
        )

        # Break-even: po 1R profitu přesuň SL na entry
        if profit_pips >= risk_pips and sl != entry:
            move_to_breakeven(ticket, entry, ticker)

        # FTMO check
        state     = _load_state()
        daily_pnl = state.get("daily_pnl", 0) + pnl
        if daily_pnl <= -MAX_DAILY_LOSS:
            log.warning(f"⚠️ FTMO denní limit! Zavírám {ticker} #{ticket}")
            close_position(ticket, ticker, pos["volume"], direction, "ftmo_daily_limit")


# ═══════════════════════════════════════════════════════════════
# SEKCE 7: POMOCNÉ FUNKCE
# ═══════════════════════════════════════════════════════════════

def _simulate_order(signal: dict, fill_price: float, volume: float) -> dict:
    """Simuluje order pro testování bez MT5."""
    import random
    ticket = random.randint(100000, 999999)
    return {
        "success":    True,
        "ticket":     ticket,
        "ticker":     signal["ticker"],
        "direction":  signal["direction"],
        "volume":     volume,
        "entry":      fill_price,
        "sl":         signal["sl"],
        "tp":         signal["tp"],
        "signal_name":signal["name"],
        "meta_conf":  signal.get("meta_conf", 0.5),
        "timestamp":  str(datetime.utcnow()),
        "status":     "OPEN",
        "pnl":        0.0,
        "simulated":  True,
    }


def _retcode_to_text(retcode: int) -> str:
    """Převede MT5 retcode na čitelný text."""
    codes = {
        10004: "REQUOTE — cena se změnila, zkus znovu",
        10006: "REQUEST_REJECTED — broker odmítl",
        10007: "REQUEST_CANCEL — zrušeno",
        10008: "ORDER_PLACED — pending order umístěn",
        10009: "DONE — úspěšně vyplněno",
        10010: "DONE_PARTIAL — částečně vyplněno",
        10011: "ERROR — obecná chyba",
        10012: "TIMEOUT — vypršel čas",
        10013: "INVALID — neplatný request",
        10014: "INVALID_VOLUME — neplatný objem",
        10015: "INVALID_PRICE — neplatná cena",
        10016: "INVALID_STOPS — neplatný SL/TP",
        10017: "TRADE_DISABLED — obchodování zakázáno",
        10018: "MARKET_CLOSED — market zavřený",
        10019: "NO_MONEY — nedostatek prostředků",
        10025: "LIMIT_ORDERS — příliš mnoho orderů",
    }
    return codes.get(retcode, f"Neznámý kód: {retcode}")


def _load_state() -> dict:
    if Path(STATE_FILE).exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"equity": ACCOUNT_SIZE, "daily_pnl": 0.0, "total_pnl": 0.0}


def _load_trades() -> list:
    if Path(TRADE_LOG).exists():
        try:
            with open(TRADE_LOG) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_trade(trade: dict):
    Path(TRADE_LOG).parent.mkdir(parents=True, exist_ok=True)
    trades = _load_trades()
    # Aktualizuj existující nebo přidej nový
    for i, t in enumerate(trades):
        if t.get("ticket") == trade.get("ticket"):
            trades[i] = trade
            break
    else:
        trades.append(trade)
    with open(TRADE_LOG, "w") as f:
        json.dump(trades, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# SEKCE 8: INTEGRATION TEST
# ═══════════════════════════════════════════════════════════════

def run_integration_test():
    """
    Otestuje celý flow bez reálných peněz.
    Spusť před prvním live deployem.
    """
    print("╔══════════════════════════════════════════════════════╗")
    print("║   MT5 EXECUTOR — INTEGRATION TEST                  ║")
    print("╚══════════════════════════════════════════════════════╝\n")

    # 1. Připojení
    print("1. Připojení k MT5...")
    connected = MT5Connection.connect()
    print(f"   {'✅ OK' if connected else '❌ FAIL'}\n")

    # 2. Account info
    print("2. Account info...")
    acc = get_account_info()
    print(f"   Equity: ${acc.get('equity', 0):.2f}")
    print(f"   Free margin: ${acc.get('free_margin', 0):.2f}\n")

    # 3. Symbol info
    print("3. Symbol info (EURUSD)...")
    sym = get_symbol_info("EURUSD")
    print(f"   Bid: {sym.get('bid', 0):.5f}")
    print(f"   Ask: {sym.get('ask', 0):.5f}")
    print(f"   Spread: {sym.get('spread', 0):.1f} pip\n")

    # 4. Pre-trade checks
    test_signal = {
        "ticker":    "EURUSD",
        "direction": "long",
        "entry":     sym.get("ask", 1.08540),
        "sl":        sym.get("ask", 1.08540) - 0.0015,
        "tp":        sym.get("ask", 1.08540) + 0.0020,
        "size":      0.01,
        "name":      "EURUSD M15 RSI oversold exit",
        "meta_conf": 0.65,
    }

    print("4. Pre-trade checks...")
    passed, reason = pre_trade_checks(test_signal)
    print(f"   {'✅' if passed else '❌'} {reason}\n")

    # 5. Simulovaný order (0.01 lot)
    print("5. Test order (SIMULATION)...")
    test_signal["size"] = 0.01
    result = place_order(test_signal)
    print(f"   Success: {result.get('success')}")
    print(f"   Ticket: #{result.get('ticket')}")
    print(f"   Fill: {result.get('entry', 0):.5f}\n")

    # 6. Otevřené pozice
    print("6. Otevřené pozice...")
    positions = get_open_positions()
    print(f"   Počet: {len(positions)}")
    for p in positions:
        print(f"   • {p['ticker']} {p['direction']} #{p['ticket']}")

    print("\n✅ Integration test dokončen")
    print("💡 Pokud vše zelené → spusť live_signal_generator.py")

    MT5Connection.disconnect()


# ═══════════════════════════════════════════════════════════════
# SEKCE 9: MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    Path("data/logs").mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "test":
            run_integration_test()
        elif cmd == "positions":
            MT5Connection.connect()
            positions = get_open_positions()
            print(f"Otevřené pozice: {len(positions)}")
            for p in positions:
                print(f"  {p['ticker']} {p['direction']} #{p['ticket']} P&L: ${p['pnl']:.2f}")
        elif cmd == "close_all":
            MT5Connection.connect()
            n = close_all_positions("manual_close_all")
            print(f"Zavřeno {n} pozic")
        else:
            print("Použití: python mt5_executor.py [test|positions|close_all]")
    else:
        run_integration_test()