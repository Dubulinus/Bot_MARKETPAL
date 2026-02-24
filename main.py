import os
import asyncio
import vectorbt as vbt
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from telegram import Bot

# === 1. NAČTENÍ TREZORU (Bezpečnost především) ===
load_dotenv() # Hledá soubor .env

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Nastavení dat
SYMBOL = "EURUSD"
DATA_FILE = "data/03_CLEAN_PARQUET/EURUSD_Yahoo_1h.parquet"

# === 2. FUNKCE: STRATEGIE ===
def spustit_strategii():
    print(f"🚀 Načítám data: {DATA_FILE}")
    
    # Kontrola, jestli soubor existuje
    if not Path(DATA_FILE).exists():
        return "❌ CHYBA: Nemůžu najít soubor s daty! Pustil jsi těžbu?"

    data = pd.read_parquet(DATA_FILE)
    price = data['close']
    
    # --- TADY JE TVOJE STRATEGIE (SMA Cross) ---
    fast_ma = vbt.MA.run(price, 10)
    slow_ma = vbt.MA.run(price, 20)
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    
    # Simulace portfolia (10 000 USD start)
    pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=10000, freq='1h')
    
    # Statistiky
    total_return = pf.total_return() * 100
    win_rate = pf.trades.win_rate() * 100
    trades = pf.trades.count()
    sharpe = pf.sharpe_ratio()
    
    # Sestavení zprávy pro šéfa
    report = (
        f"📊 *MARKETPAL STATUS REPORT*\n"
        f"-----------------------------\n"
        f"Instrument: {SYMBOL}\n"
        f"Strategie: SMA Cross (10/20)\n\n"
        f"💰 Zisk: *{total_return:.2f} %*\n"
        f"📈 Win Rate: {win_rate:.2f} %\n"
        f"🤝 Obchody: {trades}\n"
        f"⚖️ Sharpe Ratio: {sharpe:.2f}\n"
        f"-----------------------------\n"
        f"🤖 *Systém je připraven k nasazení.*"
    )
    return report

# === 3. FUNKCE: ODESLÁNÍ ===
async def odeslat_report():
    if not TOKEN or not CHAT_ID:
        print("❌ CHYBA: V souboru .env chybí TOKEN nebo CHAT_ID!")
        return

    print("📨 Generuji report...")
    zprava = spustit_strategii()
    
    print("📲 Posílám na Telegram...")
    bot = Bot(token=TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=zprava, parse_mode='Markdown')
    print("✅ ODESLÁNO! Zkontroluj si mobil.")

# === 4. SPOUŠTĚČ ===
if __name__ == "__main__":
    try:
        asyncio.run(odeslat_report())
    except Exception as e:
        print(f"💀 KRITICKÁ CHYBA: {e}")