import MetaTrader5 as mt5
import time

print("Startuji AOS MVP v1.0...")

# 1. PŘIPOJENÍ (Otevření trubek)
# MT5 musí být na tvém počítači zapnutý a musíš v něm být přihlášený na Demo účtu!
if not mt5.initialize():
    print("❌ FATÁLNÍ CHYBA: Nepodařilo se připojit k MT5.")
    print("Zkontroluj, jestli je MetaTrader 5 zapnutý.")
    mt5.shutdown()
    quit()

print("✅ Úspěšně připojeno k brokerovi!")

# 2. AUDIT ÚČTU (Základ pro tvého budoucího Gatekeepera)
account_info = mt5.account_info()
if account_info is not None:
    print("\n--- STAV ÚČTU ---")
    print(f"💰 Zůstatek:  {account_info.balance} {account_info.currency}")
    print(f"🛡️ Volná marže: {account_info.margin_free} {account_info.currency}")
else:
    print("❌ Nepodařilo se načíst data o účtu.")

# 3. ZÍSKÁNÍ ŽIVÝCH DAT (Zkouška spojení s trhem)
# Zvolíme si symbol, např. EURUSD (nebo "GOLD", "AAPL" podle tvého brokera)
symbol = "EURUSD"

# Řekneme MT5, že tento symbol chceme sledovat
mt5.symbol_select(symbol, True)

tick = mt5.symbol_info_tick(symbol)
if tick is not None:
    print(f"\n--- ŽIVÁ DATA: {symbol} ---")
    print(f"📈 Nákupní cena (Ask): {tick.ask}")
    print(f"📉 Prodejní cena (Bid): {tick.bid}")
    print(f"↔️ Spread: {round(tick.ask - tick.bid, 5)}")
else:
    print(f"❌ Nepodařilo se stáhnout tick data pro {symbol}. (Zkontroluj název symbolu v MT5)")

# 4. BEZPEČNÉ ODPOJENÍ
mt5.shutdown()
print("\nSpojení bezpečně ukončeno. Kód doběhl do konce.")