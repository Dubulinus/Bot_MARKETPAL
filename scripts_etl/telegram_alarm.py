import asyncio
import os
from dotenv import load_dotenv
from telegram import Bot

# 1. Načte tajemství z .env
load_dotenv()

# 2. Vytáhne hodnoty (když tam nebudou, hodí None)
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 3. Kontrola (pro jistotu, abys nehledal chybu hodinu)
if not TOKEN or not CHAT_ID:
    raise ValueError("❌ CHYBA: V souboru .env chybí TOKEN nebo CHAT_ID!")      

async def posli_zpravu():
    bot = Bot(token=TOKEN)
    print("📡 Pokouším se navázat spojení se základnou...")
    
    try:
        await bot.send_message(
            chat_id=CHAT_ID, 
            text="🚀 PROJEKT MARKETPAL ŽIJE!\n\nPrávě jsi propojil svůj kód s realitou. Zítra budeme pálit grafy! 💸"
        )
        print("✅ ZPRÁVA ODESLÁNA! Zkontroluj mobil.")
    except Exception as e:
        print(f"❌ CHYBA: {e}")

if __name__ == "__main__":
    asyncio.run(posli_zpravu())
    
    # ... zbytek tvého kódu ...
bot = Bot(token=TOKEN)  