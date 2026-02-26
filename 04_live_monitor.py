import os
import time
import pandas as pd
import yfinance as yf
import telebot
from datetime import datetime
import serial
from dotenv import load_dotenv
import threading
from fastapi import FastAPI
import uvicorn
from fastapi.middleware.cors import CORSMiddleware

# Inicializace webového API
app = FastAPI()

# Povolíme Reactu, aby mohl číst naše data (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # V produkci to omezíme, teď necháme otevřené
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Globální paměť pro webový dashboard
web_data = {
    "btc_price": 0,
    "status": "Skenování trhu...",
    "osint_score": 50
}

@app.get("/api/status")
def get_api_status():
    """Tohle url si bude volat tvůj React dashboard každou vteřinu"""
    # Spojíme data o trhu a stav peněženky do jednoho balíku
    return {
        "market": web_data,
        "portfolio": pm.get_status() # Tvůj bankéř z portfolio_manager
    }

# === HARDWARE DASHBOARD SETUP ===
try:
    # ⚠️ ZMĚŇ 'COM3' NA PORT, KTERÝ VIDÍŠ V ARDUINO IDE! (např. COM4 nebo /dev/ttyUSB0)
    arduino = serial.Serial('COM3', 9600, timeout=1)
    time.sleep(2) # Arduino se po připojení restartuje, musíme počkat 2 sekundy
    print("📺 Fyzický God-Mode LCD připojen!")
except Exception as e:
    print(f"⚠️ Arduino nenalezeno (pojede to bez něj): {e}")
    arduino = None

def odesli_na_arduino(cena, skore, signal):
    """Pošle data do Arduina v přesném formátu pro displej"""
    if arduino:
        try:
            # Ořízneme cenu na celé číslo, ať se to vejde na displej
            zprava = f"{int(cena)},{skore},{signal}\n"
            arduino.write(zprava.encode())
        except Exception as e:
            print(f"Chyba odesílání na displej: {e}")
# ================================


# --- IMPORTUJEME BANKÉŘE ---
# (Musí být ve stejné složce soubor portfolio_manager.py)
from portfolio_manager import PortfolioManager


# --- NAČTENÍ KONFIGURACE Z .ENV ---
load_dotenv()

# Pro nový MarketPal bot VŽDY používáme TELEGRAM_TOKEN02!
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN02")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Bezpečnostní pojistka
if not TELEGRAM_TOKEN or not CHAT_ID:
    print("❌ CHYBA: Nemůžu najít TELEGRAM_TOKEN02 nebo CHAT_ID v .env souboru!")
    print("   Zkontroluj, jestli tam ten soubor je a jestli jsi nezapomněl na číslo 02 na konci názvu tokenu.")
    exit()

# --- INICIALIZACE TELEGRAMU ---
try:
    bot = telebot.TeleBot(TELEGRAM_TOKEN)
    print("📡 Telegramový modul načten z prostředí .env (Token 02 aktivní).")
except Exception as e:
    print(f"❌ Chyba při startu Telegram bota: {e}")
    exit()
SYMBOLS = ["BTC-USD", "NVDA", "AAPL", "EURUSD=X", "GC=F"] # Přidal jsem Zlato a Euro

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
        df = yf.download(symbol, period="5d", interval="15m", progress=False, auto_adjust=False)
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
        sma_fast = close_series.rolling(window=26).mean()
        sma_slow = close_series.rolling(window=90).mean()
        
        # Získáme poslední dvě hodnoty (včera/teď) pro detekci křížení
        # Musíme ošetřit, jestli máme dost dat
        if len(sma_fast) < 90: return

        last_fast = sma_fast.iloc[-1]
        prev_fast = sma_fast.iloc[-2]
        
        last_slow = sma_slow.iloc[-1]
        prev_slow = sma_slow.iloc[-2]
        
        current_price = close_series.iloc[-1]
        
        # Budeme aktualizovat displej jen pro první symbol (BTC), ať se to nepřepisuje všemi
        if symbol == "BTC-USD":
            odesli_na_arduino(current_price, 50, "SCANNING")
            web_data["btc_price"] = float(current_price) # Uložíme pro React
        
        # --- LOGIKA KŘÍŽENÍ (CROSSOVER) ---

        # 1. GOLDEN CROSS (Rychlá jde NAHORU přes Pomalou) -> BUY
        if prev_fast < prev_slow and last_fast > last_slow:
            msg = f"🚀 {symbol}: GOLDEN CROSS (BUY) @ {current_price:.2f}"
            print(msg)
            send_telegram_message(msg)
            
            # --> BANKÉŘ OTEVÍRÁ POZICI <--
            # size=0.1 je napevno, časem to vypočítáme podle risku
            pm.otevrit_pozici(symbol, current_price, "BUY", size=0.1)
            
            odesli_na_arduino(current_price, 86, "BUY")
            
            web_data["status"] = f"🚀 NÁKUP {symbol} @ {current_price:.2f}"
            web_data["osint_score"] = 86

        # 2. DEATH CROSS (Rychlá jde DOLŮ přes Pomalou) -> SELL (Exit)
        elif prev_fast > prev_slow and last_fast < last_slow:
            msg = f"📉 {symbol}: DEATH CROSS (SELL) @ {current_price:.2f}"
            print(msg)
            send_telegram_message(msg)
            
            # --> BANKÉŘ ZAVÍRÁ POZICI <--
            zisk = pm.zavrit_pozici(symbol, current_price)
            
            # Při prodeji dáme skóre nízko.
            odesli_na_arduino(current_price, 25, "SELL")
            
            web_data["status"] = f"📉 PRODEJ {symbol} @ {current_price:.2f}"
            web_data["osint_score"] = 25
            
            if zisk is not None:
                balance_info = pm.get_status()
                profit_msg = f"💰 OBCHOD UZAVŘEN!\nZisk: ${zisk:.2f}\n{balance_info}"
                send_telegram_message(profit_msg)
                print(profit_msg)

    except Exception as e:
        print(f"Chyba výpočtu u {symbol}: {e}")

def bot_loop():
    """Tohle je původní smyčka, teď běží ve vedlejším vlákně"""
    print("✅ Bot smyčka spuštěna na pozadí...")
    
    # 👇 TADY JE TVŮJ TELEGRAM ZPÁTKY 👇
    send_telegram_message(f"🤖 MarketPal (God-Mode) online.\n{pm.get_status()}")
    
    while True:
        print(f"\n--- SKENOVÁNÍ: {datetime.now().strftime('%H:%M:%S')} ---")
        for symbol in SYMBOLS:
            process_symbol(symbol)
            time.sleep(2)
        print("💤 Čekám 60 sekund...")
        time.sleep(60)

if __name__ == "__main__":
    import threading # Pro jistotu, kdybys to nahoře zapomněl importovat
    import uvicorn
    
    # 1. Odstartujeme bota do tajného vlákna na pozadí
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    
    # 2. Na hlavním vlákně spustíme API server pro React
    print("🌐 Startuji API Server na adrese: http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")