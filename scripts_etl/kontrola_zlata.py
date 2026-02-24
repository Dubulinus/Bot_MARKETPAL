import pandas as pd
from pathlib import Path

# Cesta k pokladu
cesta_k_pokladu = Path("data/03_CLEAN_PARQUET")
soubory = list(cesta_k_pokladu.glob("*.parquet"))

if not soubory:
    print("❌ CHYBA: Složka je prázdná! Rafinerie asi selhala.")
    exit()

# Vezmeme první soubor, co nám přijde pod ruku (nebo si vyber konkrétní)
pokusny_kralik = soubory[0]

print(f"🕵️ Otevírám trezor: {pokusny_kralik.name}")

# Načtení Parquetu (všimni si, jak je to bleskové oproti CSV)
df = pd.read_parquet(pokusny_kralik)

print("\n=== 📊 DATA INFO ===")
print(df.info())

print("\n=== ⏱️ PRVNÍCH 5 TICKŮ ===")
print(df.head())

print("\n=== ⏱️ POSLEDNÍCH 5 TICKŮ ===")
print(df.tail())

print("\n=== 🧠 TEST REALITY ===")
# Kontrola, jestli jde čas dopředu
if df.index.is_monotonic_increasing:
    print("✅ Časová osa je v pořádku. Žádné cestování do minulosti.")
else:
    print("❌ FATÁLNÍ CHYBA: Čas je stále rozbitý!")

# Kontrola cen
if (df['bid'] <= 0).any():
    print("❌ FATÁLNÍ CHYBA: Našli jsme nulové ceny!")
else:
    print("✅ Ceny vypadají reálně.")