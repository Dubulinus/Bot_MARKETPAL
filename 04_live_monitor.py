import os
import time
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import telebot
from datetime import datetime

# --- IMPORTUJEME BANKÉŘE ---
# (Musí být ve stejné složce soubor portfolio_manager.py)
from portfolio_manager import PortfolioManager

# --- KONFIGURACE ---
TELEGRAM_TOKEN = "TVUJ_TOKEN_ZDE"  # <-- DOPLŇ SVŮJ TOKEN (Sentinel nebo ten nový)
CHAT_ID = "TVUJE_CHAT_ID"          # <-- DOPLŇ SVŮJ ID
SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "EURUSD=X", "GC=F"] # Přidal jsem Zlato a Euro

# --- INICIALIZACE ---
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# !!! DŮLEŽITÉ: Inicializujeme bankéře TADY NAHOŘE, aby byl globální !!!
print("🏦 Startuji Portfolio Managera...")
pm = PortfolioManager() 
print(pm.get_status()) # Vypíše aktuální stav při startu

def send_telegram_message(message):
    try:
        bot.send_message(CHAT_ID, message)
    except Exception as e:
        print(f"Chyba Telegramu: {e}")

def download_data(symbol):
    try:
        df = yf.download(symbol, period="5d", interval="15m", progress=False)
        if df.empty:
            print(f"⚠️ Žádná data pro {symbol}")
            return None
        return df
    except Exception as e:
        print(f"Chyba stahování {symbol}: {e}")
        return None

def process_symbol(symbol):
    """
    Hlavní mozek. Stáhne data, spočítá SMA, rozhodne o obchodu.
    """
    df = download_data(symbol)
    if df is None: return

    # Výpočet SMA (26 a 90)
    try:
        # yfinance vrací MultiIndex sloupce, musíme zploštit nebo přistoupit správně
        # Pro jistotu bereme 'Close' a pokud je to DataFrame, vezmeme první sloupec
        close_series = df['Close']
        if isinstance(close_series, pd.DataFrame):
            close_series = close_series.iloc[:, 0]
            
        # Výpočet indikátorů
        sma_fast = ta.sma(close_series, length=26)
        sma_slow = ta.sma(close_series, length=90)
        
        # Získáme poslední dvě hodnoty (včera/teď) pro detekci křížení
        # Musíme ošetřit, jestli máme dost dat
        if len(sma_fast) < 90: return

        last_fast = sma_fast.iloc[-1]
        prev_fast = sma_fast.iloc[-2]
        
        last_slow = sma_slow.iloc[-1]
        prev_slow = sma_slow.iloc[-2]
        
        current_price = close_series.iloc[-1]
        
        # --- LOGIKA KŘÍŽENÍ (CROSSOVER) ---

        # 1. GOLDEN CROSS (Rychlá jde NAHORU přes Pomalou) -> BUY
        if prev_fast < prev_slow and last_fast > last_slow:
            msg = f"🚀 {symbol}: GOLDEN CROSS (BUY) @ {current_price:.2f}"
            print(msg)
            send_telegram_message(msg)
            
            # --> BANKÉŘ OTEVÍRÁ POZICI <--
            # size=0.1 je napevno, časem to vypočítáme podle risku
            pm.otevrit_pozici(symbol, current_price, "BUY", size=0.1)

        # 2. DEATH CROSS (Rychlá jde DOLŮ přes Pomalou) -> SELL (Exit)
        elif prev_fast > prev_slow and last_fast < last_slow:
            msg = f"📉 {symbol}: DEATH CROSS (SELL) @ {current_price:.2f}"
            print(msg)
            send_telegram_message(msg)
            
            # --> BANKÉŘ ZAVÍRÁ POZICI <--
            zisk = pm.zavrit_pozici(symbol, current_price)
            
            if zisk is not None:
                balance_info = pm.get_status()
                profit_msg = f"💰 OBCHOD UZAVŘEN!\nZisk: ${zisk:.2f}\n{balance_info}"
                send_telegram_message(profit_msg)
                print(profit_msg)

    except Exception as e:
        print(f"Chyba výpočtu u {symbol}: {e}")

# --- HLAVNÍ SMYČKA ---
def main():
    print("✅ Bot spuštěn. Sleduji trhy...")
    send_telegram_message(f"🤖 Bot online.\n{pm.get_status()}")
    
    while True:
        print(f"\n--- SKENOVÁNÍ: {datetime.now().strftime('%H:%M:%S')} ---")
        for symbol in SYMBOLS:
            process_symbol(symbol)
            time.sleep(2) # Abychom nespamovali Yahoo a nedostali ban
        
        print("💤 Čekám 60 sekund...")
        time.sleep(60)

if __name__ == "__main__":
    main()