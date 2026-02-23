import asyncio
from telegram import Bot

# === KONFIGURACE ===
TOKEN = '8752286962:AAEbe6ck1VeNqQxPiHnzNhRD8pbRmAY1dcE'  # Sem dej ten dlouhý kód
CHAT_ID = '8544333240'           # Sem dej to číslo, co jsi zjistil

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