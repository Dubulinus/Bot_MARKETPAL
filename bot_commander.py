import os
import asyncio
import logging
import yfinance as yf
import vectorbt as vbt
import pandas as pd
import matplotlib.pyplot as plt # <--- NOVÝ IMPORT PRO GRAFY
import io
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# ... (Tvoje nastavení LOGOVÁNÍ a TOKENU zůstává stejné) ...
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN02")

# ... (Funkce start a get_price nech stejné) ...
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    msg = (
        f"🤖 *Vítej, {user}!*\n"
        "Jsem připraven kreslit grafy.\n"
        "📉 `/price` - Cena\n"
        "📊 `/report` - Backtest + GRAF\n"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (Tady nech to, co ti fungovalo naposledy) ...
    await context.bot.send_message(chat_id=update.effective_chat.id, text="🔍 Sahám na trh...")
    try:
        df = yf.download("EURUSD=X", period="1d", interval="1m", progress=False)
        raw_price = df['Close'].iloc[-1]
        
        if hasattr(raw_price, 'item'):
            current_price = raw_price.item()
        else:
            current_price = float(raw_price)
            
        last_time = df.index[-1].strftime('%H:%M:%S')
        msg = f"💶 *EUR/USD UPDATE*\nCena: `{current_price:.5f}`\nČas: {last_time}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Chyba dat: {str(e)}")

# === TOTO JE TA NOVÁ VYMAZLENÁ FUNKCE S GRAFEM ===
async def run_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="🎨 Kreslím graf výdělků, vydrž...")
    
    try:
        # 1. Načtení dat a strategie
        # Pokud ti to padá na cestě, zkontroluj, jestli jsi ve složce Bot_MARKETPAL
        data = pd.read_parquet("data/03_CLEAN_PARQUET/EURUSD_Yahoo_1h.parquet")
        price = data['close']
        
        fast_ma = vbt.MA.run(price, 10)
        slow_ma = vbt.MA.run(price, 20)
        entries = fast_ma.ma_crossed_above(slow_ma)
        exits = fast_ma.ma_crossed_below(slow_ma)
        
        pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=10000, freq='1h')
        
        # 2. Vytvoření grafu (Equity Curve - jak rostou prachy)
        plt.figure(figsize=(10, 6)) # Velikost obrázku
        
        # Vykreslíme hodnotu portfolia
        pf.value().plot(title='Vývoj kapitálu (Equity Curve)', color='green')
        
        plt.xlabel("Čas")
        plt.ylabel("Hodnota portfolia ($)")
        plt.grid(True, alpha=0.3)
        
        # 3. Uložení do paměti (ne na disk, ať neděláme bordel)
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close() # Zavřeme graf, ať nežere paměť
        
        # 4. Statistiky
        ret = pf.total_return() * 100
        sharpe = pf.sharpe_ratio()
        
        caption = (
            f"📊 *BACKTEST REPORT*\n"
            f"💰 Zisk: {ret:.2f} %\n"
            f"⚖️ Sharpe: {sharpe:.2f}\n"
            f"📈 Koukej na tu křivku!"
        )
        
        # 5. Odeslání fotky
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=buf, caption=caption, parse_mode='Markdown')
        
    except Exception as e:
        print(f"CHYBA REPORTU: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Nepovedlo se nakreslit graf: {e}")

# ... (Zbytek kódu s FIX PRO WINDOWS a main blokem zůstává stejný) ...
if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == '__main__':
    if not TOKEN:
        print("❌ CHYBA: Nemám TOKEN! Zkontroluj .env")
        exit()

    print("🤖 Bot Commander (Picasso Edition) startuje...")
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('price', get_price))
    application.add_handler(CommandHandler('report', run_report))
    
    application.run_polling()