import vectorbt as vbt
import pandas as pd
import asyncio
from telegram import Bot
from pathlib import Path

# === KONFIGURACE ===
TOKEN = '8752286962:AAEbe6ck1VeNqQxPiHnzNhRD8pbRmAY1dcE'
CHAT_ID = '8544333240' # To číslo, co ti to vypsalo minule
CESTA_DATA = "data/03_CLEAN_PARQUET/EURUSD_Yahoo_1h.parquet"

async def posli_report(text):
    bot = Bot(token=TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=text)

def spust_backtest():
    print("📊 Spouštím analýzu pro Telegram...")
    data = pd.read_parquet(CESTA_DATA)
    price = data['close']
    
    # Strategie: SMA Cross (10 vs 20)
    fast_ma = vbt.MA.run(price, 10)
    slow_ma = vbt.MA.run(price, 20)
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    
    pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=10000, freq='1h')
    
    # Příprava zprávy
    vratnost = pf.total_return() * 100
    obchody = pf.trades.count()
    sharpe = pf.sharpe_ratio()
    
    report = (
        "📈 *MARKETPAL REPORT*\n"
        "--------------------------\n"
        f"Symbol: EURUSD (1h)\n"
        f"Strategie: SMA Cross 10/20\n\n"
        f"💰 Výnos: {vratnost:.2f} %\n"
        f"🤝 Obchody: {obchody}\n"
        f"⚖️ Sharpe: {sharpe:.2f}\n"
        "--------------------------\n"
        "🤖 Backtest úspěšně dokončen!"
    )
    return report

async def main():
    try:
        zprava = spust_backtest()
        await posli_report(zprava)
        print("✅ Report odeslán na tvůj mobil!")
    except Exception as e:
        print(f"❌ Chyba: {e}")

if __name__ == "__main__":
    asyncio.run(main())