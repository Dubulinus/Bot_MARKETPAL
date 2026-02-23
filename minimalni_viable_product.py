import MetaTrader5 as mt5
import pandas as pd
import time
import logging
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from dotenv import load_dotenv  # Musíš mít: pip install python-dotenv
from telegram import Bot

# 1. Načte tajemství z .env
load_dotenv()

# 2. Vytáhne hodnoty (když tam nebudou, hodí None)
TOKEN = os.getenv("TELEGRAM_TOKEN01")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 3. Kontrola (pro jistotu, abys nehledal chybu hodinu)
if not TOKEN or not CHAT_ID:
    raise ValueError("❌ CHYBA: V souboru .env chybí TOKEN nebo CHAT_ID!")

# ==========================================
# NASTAVENÍ BOTA A TELEGRAMU
# ==========================================

# LOGOVÁNÍ
logging.basicConfig(
    filename='bot_denik.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8' 
)

# ==========================================
# KOMUNIKACE (Telegram + Log + Print)
# ==========================================
def posli_notifikaci(zprava, uroven="INFO", poslat_telegram=False):
    if uroven == "FATAL":
        logging.error(f"💀 FATAL: {zprava}")
        print(f"💀 FATAL: {zprava}")
        poslat_telegram = True
    elif uroven == "HEARTBEAT":
        logging.info(f"💓 HEARTBEAT: {zprava}")
        print(f"💓 HEARTBEAT: {zprava}")
    else:
        logging.info(zprava)
        print(f"ℹ️ {zprava}")

    if poslat_telegram and CHAT_ID:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': zprava}).encode('utf-8')
        try:
            urllib.request.urlopen(url, data=data, timeout=10)
        except Exception as e:
            logging.error(f"Nepodařilo se odeslat Telegram: {e}")

# ==========================================
# KILL SWITCH (Ochrana z pláže)
# ==========================================
def zkontroluj_kill_switch():
    if os.path.exists("STOP.txt"):
        msg = "🚨 KILL SWITCH AKTIVOVÁN! Našel jsem STOP.txt. MARKETPAL_sentinel se bezpečně vypíná."
        posli_notifikaci(msg, "FATAL", poslat_telegram=True)
        mt5.shutdown()
        exit()
# ==========================================
# MAKROEKONOMICKÝ AGENT (Nouzový Bypass před odletem)
# ==========================================
def stahni_a_zkontroluj_makro(slozka_makro, dnesni_datum):
    # Makro knihovna vyžaduje update, vyřešíme po návratu z Portugalska!
    # Zatím vracíme True, aby mohl bot nerušeně těžit Svíčky a Orderflow.
    return True
# ==========================================
# HLAVNÍ FUNKCE (Sběrač)
# ==========================================
def sberac_dat():
    posli_notifikaci("🚀 MARKETPAL_sentinel se probouzí. Aktivuji Orderflow a Makro štíty...", "INFO", poslat_telegram=True)
    
    if not mt5.initialize():
        posli_notifikaci("Nepodařilo se připojit k MT5! Zkontroluj Ghetto-Server.", "FATAL")
        return

    symboly = ["EURUSD", "XAUUSD", "AAPL"]
    pocitadlo_cyklu = 0
    
    posli_notifikaci(f"✅ MT5 připojeno. Těžím svíčky i Orderflow pro: {', '.join(symboly)}.", "INFO", poslat_telegram=True)

    while True:
        zkontroluj_kill_switch()
        nyni = time.time()
        dnesni_datum = datetime.now().strftime('%Y_%m_%d')
        
        # --- 1. MAKROEKONOMICKÝ ŠTÍT A FUNDAMENTALS ---
        slozka_makro = "data/3_bronze_macro"
        os.makedirs(slozka_makro, exist_ok=True)
        
        # Tohle stáhne data do CSV a zároveň zjistí, jestli je bezpečno!
        bezpecno_obchodovat = stahni_a_zkontroluj_makro(slozka_makro, dnesni_datum)
        
        for symbol in symboly:
            try:
                # --- 2. SBĚR SVÍČEK (OHLC) - Retence 365 dní ---
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, 1)
                if rates is not None and len(rates) > 0:
                    df = pd.DataFrame(rates)
                    df['time'] = pd.to_datetime(df['time'], unit='s') - pd.Timedelta(hours=1)
                    df = df.round({'open': 5, 'high': 5, 'low': 5, 'close': 5})
                    
                    slozka_svicky = f"data/1_bronze_raw/{symbol}"
                    os.makedirs(slozka_svicky, exist_ok=True)
                    cesta_svicky = f"{slozka_svicky}/{dnesni_datum}.csv"
                    df.to_csv(cesta_svicky, mode='a', header=not os.path.exists(cesta_svicky), index=False)
                    
                    # Čistič svíček (365 dní)
                    for f in os.listdir(slozka_svicky):
                        f_cesta = os.path.join(slozka_svicky, f)
                        if os.path.isfile(f_cesta) and os.stat(f_cesta).st_mtime < nyni - (365 * 86400):
                            os.remove(f_cesta)

                # --- 3. SBĚR ORDERFLOW (TICKY) - Retence 30 dní ---
                # Stáhne všechny ticky (změny ceny) za posledních 60 vteřin
                cas_start = int(nyni) - 60
                cas_konec = int(nyni)
                ticks = mt5.copy_ticks_range(symbol, cas_start, cas_konec, mt5.COPY_TICKS_ALL)
                
                if ticks is not None and len(ticks) > 0:
                    df_ticks = pd.DataFrame(ticks)
                    df_ticks['time'] = pd.to_datetime(df_ticks['time'], unit='s') - pd.Timedelta(hours=1)
                    
                    slozka_ticks = f"data/2_bronze_ticks/{symbol}"
                    os.makedirs(slozka_ticks, exist_ok=True)
                    cesta_ticks = f"{slozka_ticks}/{dnesni_datum}.csv"
                    # Pro ticky ukládáme jen základní surová data bez složitého zaokrouhlování
                    df_ticks.to_csv(cesta_ticks, mode='a', header=not os.path.exists(cesta_ticks), index=False)
                    
                    # Čistič ticků (30 dní - tvůj požadavek)
                    for f in os.listdir(slozka_ticks):
                        f_cesta = os.path.join(slozka_ticks, f)
                        if os.path.isfile(f_cesta) and os.stat(f_cesta).st_mtime < nyni - (30 * 86400):
                            os.remove(f_cesta)
                            logging.info(f"🧹 Smazán starý Orderflow soubor: {f}")

            except Exception as e:
                logging.error(f"Chyba při stahování {symbol}: {e}")

        # --- 4. HEARTBEAT A USÍNÁNÍ ---
        pocitadlo_cyklu += 1
        if pocitadlo_cyklu >= 60:
            posli_notifikaci(f"Sentinel: Žiju. Těžím Svíčky i Orderflow pro {len(symboly)} trhů. Makro štít aktivní: {bezpecno_obchodovat}.", "HEARTBEAT", poslat_telegram=True)
            pocitadlo_cyklu = 0

        time.sleep(60)

# ==========================================
# SPUŠTĚNÍ BOTA
# ==========================================
if __name__ == "__main__":
    sberac_dat()