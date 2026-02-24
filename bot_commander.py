import os
import asyncio
import logging
import yfinance as yf
import vectorbt as vbt
import pandas as pd
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# === 1. NASTAVENÍ A LOGOVÁNÍ ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN02")

# === 2. PŘÍKAZY BOTA ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Odpoví na příkaz /start"""
    user = update.effective_user.first_name
    msg = (
        f"🤖 *Vítej v centrále, {user}!*\n\n"
        "Jsem připraven. Zadej rozkaz:\n"
        "📈 `/price` - Aktuální cena EURUSD\n"
        "📊 `/report` - Spustit rychlý backtest\n"
        "💀 `/kill` - Vypnout bota"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stáhne aktuální cenu z Yahoo - OPRAVENÁ VERZE"""
    await context.bot.send_message(chat_id=update.effective_chat.id, text="🔍 Sahám na trh...")
    
    try:
        # Stáhneme poslední data
        df = yf.download("EURUSD=X", period="1d", interval="1m", progress=False)
        
        # === ZMĚNA ZDE ===
        # Vezmeme poslední zavírací cenu
        raw_price = df['Close'].iloc[-1]
        
        # Trik: Pokud je to Pandas Series (tabulka), vytáhneme z ní hodnotu pomocí .item()
        # Pokud je to už číslo, float() to pojistí.
        if hasattr(raw_price, 'item'):
            current_price = raw_price.item()
        else:
            current_price = float(raw_price)
            
        # Zformátování času
        last_time = df.index[-1].strftime('%H:%M:%S')
        
        msg = f"💶 *EUR/USD UPDATE*\nCena: `{current_price:.5f}`\nČas: {last_time}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')
        
    except Exception as e:
        print(f"CHYBA PŘI PRICE: {e}") # Vypíše chybu i tobě do terminálu
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Chyba dat: {str(e)}")

async def run_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Spustí ten tvůj backtest na požádání"""
    await context.bot.send_message(chat_id=update.effective_chat.id, text="📊 Počítej, chroustám data...")
    
    # Tady voláme logiku z main.py (zkrácená verze)
    try:
        # Načteme data (pro demo použijeme ta stažená, v reálu bys stáhl nová)
        # Pokud nemáš main.py importovatelný, hodíme to sem "natvrdo" pro rychlost
        data = pd.read_parquet("data/03_CLEAN_PARQUET/EURUSD_Yahoo_1h.parquet")
        price = data['close']
        fast_ma = vbt.MA.run(price, 10)
        slow_ma = vbt.MA.run(price, 20)
        entries = fast_ma.ma_crossed_above(slow_ma)
        exits = fast_ma.ma_crossed_below(slow_ma)
        pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=10000, freq='1h')
        
        ret = pf.total_return() * 100
        sharpe = pf.sharpe_ratio()
        
        msg = (
            f"📊 *BACKTEST DOKONČEN*\n"
            f"Výnos: {ret:.2f} %\n"
            f"Sharpe: {sharpe:.2f}\n"
            f"Trades: {pf.trades.count()}"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')
        
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Chyba backtestu: {e}")

# === 3. FIX PRO WINDOWS (Aby to neřvalo RuntimeError) ===
# Tohle je ta magie, co opraví červené chyby na konci
if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# === 4. HLAVNÍ SPOUŠTĚČ ===
if __name__ == '__main__':
    if not TOKEN:
        print("❌ CHYBA: Nemám TOKEN! Zkontroluj .env")
        exit()

    print("🤖 Bot Commander startuje... (Ctrl+C pro ukončení)")
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Registrace příkazů
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('price', get_price))
    application.add_handler(CommandHandler('report', run_report))
    
    # Spuštění nekonečné smyčky
    application.run_polling()