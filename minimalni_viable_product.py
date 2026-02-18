import MetaTrader5 as mt5
import pandas as pd
import time
import logging
import os
import urllib.request
import urllib.parse
from datetime import datetime

# ==========================================
# NASTAVENÍ BOTA A TELEGRAMU
# ==========================================
TELEGRAM_TOKEN = "7750206963:AAF3495CpRGrmS3XQ2ECXzveFpMq4ICvlVI"
TELEGRAM_CHAT_ID = "8544333240" # Příklad: "123456789"

# LOGOVÁNÍ (Nyní s UTF-8 pro správnou češtinu)
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
    # 1. Zápis do logu a konzole
    if uroven == "FATAL":
        logging.error(f"💀 FATAL: {zprava}")
        print(f"💀 FATAL: {zprava}")
        poslat_telegram = True # Fatal chceme na mobil vždy
    elif uroven == "HEARTBEAT":
        logging.info(f"💓 HEARTBEAT: {zprava}")
        print(f"💓 HEARTBEAT: {zprava}")
    else:
        logging.info(zprava)
        print(f"ℹ️ {zprava}")

    # 2. Odeslání na mobil přes Telegram
    if poslat_telegram and TELEGRAM_CHAT_ID != "VLOZ_SEM_SVOJE_CHAT_ID_Z_PROHLIZECE":
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({'chat_id': TELEGRAM_CHAT_ID, 'text': zprava}).encode('utf-8')
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
# HLAVNÍ FUNKCE (Sběrač)
# ==========================================
def sberac_dat():
    posli_notifikaci("🚀 MARKETPAL_sentinel se probouzí a startuje motory...", "INFO", poslat_telegram=True)
    
    if not mt5.initialize():
        posli_notifikaci("Nepodařilo se připojit k MT5! Zkontroluj Ghetto-Server.", "FATAL")
        return

    symbol = "EURUSD"
    posli_notifikaci(f"✅ MT5 připojeno. Začínám potichu sbírat {symbol}.", "INFO", poslat_telegram=True)

    pocitadlo_cyklu = 0

    while True:
        zkontroluj_kill_switch()
        
        try:
            # INDEX 1: Stahuje vždy jen poslední UZAVŘENOU svíčku (bez duplicit)
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, 1)
            
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                
                # ZAOKROUHLENÍ: Čistá data bez nesmyslných desetinných míst
                df = df.round({'open': 5, 'high': 5, 'low': 5, 'close': 5})
                
                df.to_csv(f'data_{symbol}.csv', mode='a', header=not os.path.exists(f'data_{symbol}.csv'), index=False)
            else:
                logging.warning(f"Nedostal jsem data pro {symbol}.")

        except Exception as e:
            posli_notifikaci(f"Chyba při stahování: {e}", "FATAL")

        # Heartbeat na mobil každých 60 minut (aby tě nespamoval, ale věděl jsi, že žije)
        pocitadlo_cyklu += 1
        if pocitadlo_cyklu >= 60:
            posli_notifikaci(f"Sentinel hlásí: Jsem naživu, servery běží, sbírám {symbol}.", "HEARTBEAT", poslat_telegram=True)
            pocitadlo_cyklu = 0

        time.sleep(60)

if __name__ == "__main__":
    sberac_dat()