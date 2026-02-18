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
TELEGRAM_CHAT_ID = "8544333240"

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

    if poslat_telegram and TELEGRAM_CHAT_ID:
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

    # TADY JE TA MAGIE: Seznam trhů (Bitcoin jede i o víkendu!)
    symboly = ["EURUSD", "XAUUSD", "AAPL"]
    pocitadlo_cyklu = 0
    
    posli_notifikaci(f"✅ MT5 připojeno. Začínám potichu sbírat trhy: {', '.join(symboly)}.", "INFO", poslat_telegram=True)

    while True:
        zkontroluj_kill_switch()
        
        # Bot projde všechny zadané trhy jeden po druhém
        for symbol in symboly:
            try:
                rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, 1)
                
                if rates is not None and len(rates) > 0:
                    df = pd.DataFrame(rates)
                    df['time'] = pd.to_datetime(df['time'], unit='s')
                    
                    # OPRAVA ČASU O HODINU (EET -> CET)
                    df['time'] = df['time'] - pd.Timedelta(hours=1)
                    df = df.round({'open': 5, 'high': 5, 'low': 5, 'close': 5})
                    
                    # DENNÍ SOUBORY A SLOŽKY
                    dnesni_datum = datetime.now().strftime('%Y_%m_%d')
                    slozka_data = f"data/1_bronze_raw/{symbol}"
                    os.makedirs(slozka_data, exist_ok=True)
                    
                    cesta_k_souboru = f"{slozka_data}/{dnesni_datum}.csv"
                    df.to_csv(cesta_k_souboru, mode='a', header=not os.path.exists(cesta_k_souboru), index=False)
                    
                    # ČISTIČ (365 DNÍ)
                    nyni = time.time()
                    for f in os.listdir(slozka_data):
                        f_cesta = os.path.join(slozka_data, f)
                        if os.path.isfile(f_cesta):
                            if os.stat(f_cesta).st_mtime < nyni - (365 * 86400):
                                os.remove(f_cesta)
                                logging.info(f"🧹 Smazán starý soubor: {f}")

                else:
                    logging.warning(f"Nedostal jsem data pro {symbol} (Trh je možná zavřený).")

            except Exception as e:
                logging.error(f"Chyba při stahování {symbol}: {e}")

        # HEARTBEAT KAŽDOU HODINU
        pocitadlo_cyklu += 1
        if pocitadlo_cyklu >= 60:
            posli_notifikaci(f"Sentinel hlásí: Jsem naživu, servery běžící, těžím {len(symboly)} trhů najednou.", "HEARTBEAT", poslat_telegram=True)
            pocitadlo_cyklu = 0

        # Počká 60 vteřin a jde zkontrolovat všechny trhy znovu
        time.sleep(60)

# ==========================================
# SPUŠTĚNÍ BOTA
# ==========================================
if __name__ == "__main__":
    sberac_dat()