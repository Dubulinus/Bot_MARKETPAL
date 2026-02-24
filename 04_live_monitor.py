import yfinance as yf
import pandas as pd
import ta
import os
import time
import asyncio
from dotenv import load_dotenv
from telegram import Bot
from marketpal_logger import Denik 

# 1. NAČTENÍ HESEL
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN02")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 2. NASTAVENÍ (Sledujeme toho víc!)
SYMBOLS = ["EURUSD=X", "BTC-USD", "GC=F"] # Forex, Krypto, Zlato
TIMEFRAME = "1h"
FAST_MA = 26 
SLOW_MA = 90
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70 # Moc drahé (nepokupovat)
RSI_OVERSOLD = 30   # Moc levné (neprodávat)

async def posli_telegram(zprava):
    if not TOKEN: return
    try:
        bot = Bot(token=TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=zprava)
    except Exception as e:
        print(f"⚠️ Chyba Telegramu: {e}")

def analyzuj_symbol(symbol):
    print(f"🔍 Skenuji: {symbol}...")
    try:
        # Stáhneme data
        df = yf.download(symbol, period="7d", interval=TIMEFRAME, progress=False)
        if df.empty: return

        # Oprava pro Yahoo
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # --- INDIKÁTORY ---
        # 1. SMA (Trend)
        df['SMA_FAST'] = ta.trend.sma_indicator(df['Close'], window=FAST_MA)
        df['SMA_SLOW'] = ta.trend.sma_indicator(df['Close'], window=SLOW_MA)
        # 2. RSI (Síla) - NOVINKA
        df['RSI'] = ta.momentum.rsi(df['Close'], window=RSI_PERIOD)

        # Poslední svíčky
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Výpis pro kontrolu
        print(f"   💰 Cena: {last['Close']:.2f} | RSI: {last['RSI']:.1f}")

        # --- LOGIKA VSTUPU (SMA + RSI Filtr) ---
        signal = None
        duvod = ""

        # BUY: Golden Cross AND RSI není přepálené (< 70)
        if (prev['SMA_FAST'] < prev['SMA_SLOW'] and last['SMA_FAST'] > last['SMA_SLOW']):
            if last['RSI'] < RSI_OVERBOUGHT:
                signal = "BUY"
                duvod = f"Golden Cross + RSI OK ({last['RSI']:.1f})"
            else:
                print(f"   ⚠️ Golden Cross zamítnut! RSI je moc vysoko ({last['RSI']:.1f})")

        # SELL: Death Cross AND RSI není na dně (> 30)
        elif (prev['SMA_FAST'] > prev['SMA_SLOW'] and last['SMA_FAST'] < last['SMA_SLOW']):
            if last['RSI'] > RSI_OVERSOLD:
                signal = "SELL"
                duvod = f"Death Cross + RSI OK ({last['RSI']:.1f})"
            else:
                print(f"   ⚠️ Death Cross zamítnut! RSI je moc nízko ({last['RSI']:.1f})")

        # --- AKCE ---
        if signal:
            print(f"   🚨 SIGNÁL: {signal}!")
            # Zápis do deníku
            denik = Denik()
            denik.zapis_obchod(signal, symbol, last['Close'], duvod, "SMA_RSI_V2")
            
            # Telegram
            msg = (
                f"🤖 **MARKETPAL BOT**\n"
                f"-------------------\n"
                f"Instrument: {symbol}\n"
                f"Akce: **{signal}** 🚀\n"
                f"Cena: {last['Close']:.4f}\n"
                f"Důvod: {duvod}\n"
                f"-------------------"
            )
            asyncio.run(posli_telegram(msg))

    except Exception as e:
        print(f"❌ Chyba u {symbol}: {e}")

# --- HLAVNÍ SMYČKA ---
if __name__ == "__main__":
    print("🤖 BOT NASTARTOVÁN. (Ukonči pomocí Ctrl+C)")
    print(f"Používám TOKEN02 (MarketPal).")
    
    # --- NOVINKA: ODESLÁNÍ STARTUP ZPRÁVY ---
    try:
        # Tohle pošle zprávu hned po startu
        uvitaci_zprava = "✅ **MarketPal ONLINE**\nSleduji: EURUSD, BTC, GOLD.\nJdu lovit příležitosti. 🦅"
        asyncio.run(posli_telegram(uvitaci_zprava))
        print("✅ Startup zpráva odeslána na Telegram.")
    except Exception as e:
        print(f"⚠️ Nepodařilo se poslat startup zprávu: {e}")
    # ----------------------------------------

    print("-" * 30)

    try:
        while True:
            # 1. Projedeme všechny symboly
            for sym in SYMBOLS:
                analyzuj_symbol(sym)
            
            print("💤 Jdu spát na 60 sekund...")
            print("-" * 30)
            
            # 2. Počkáme (na testování dáme 60 sekund, v reálu 3600 = hodina)
            time.sleep(60) 

    except KeyboardInterrupt:
        print("\n👋 Bot ukončen uživatelem. Jdu se učit Zeměpis.")