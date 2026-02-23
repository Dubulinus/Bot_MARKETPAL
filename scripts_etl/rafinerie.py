import pandas as pd
from pathlib import Path
import os

# === NASTAVENÍ CEST (Uprav, pokud se složky jmenují jinak) ===
# Kde leží ten toxický odpad (relativně od tohoto skriptu nebo absolutně)
# Pokud spouštíš skript z Bot_MARKETPAL, cesty jsou takto:
VSTUPNI_SLOZKA = Path("data/01_portugal_karantena") 
VYSTUPNI_SLOZKA = Path("data/03_CLEAN_PARQUET")

# Vytvoří výstupní složku, pokud neexistuje
VYSTUPNI_SLOZKA.mkdir(parents=True, exist_ok=True)

print(f"🏭 START RAFINERIE")
print(f"➡️  Nasávám z: {VSTUPNI_SLOZKA}")
print(f"➡️  Ukládám do: {VYSTUPNI_SLOZKA}\n")

# Najde všechny CSV v karanténě
soubory = list(VSTUPNI_SLOZKA.rglob("*.csv"))

if not soubory:
    print("❌ CHYBA: V karanténě nic není! Zkontroluj cestu.")
    exit()

for cesta in soubory:
    nazev = cesta.name
    print(f"🔧 Zpracovávám: {nazev}", end=" ... ")
    
    try:
        # 1. NAČTENÍ (Surová ropa)
        df = pd.read_csv(cesta)
        
        # Pojistka: Je to vůbec tickové data? (Má bid/ask?)
        if 'bid' not in df.columns:
            print("🚫 PŘESKAKUJI (Nejsou to ticky)")
            continue

        puvodni_radky = len(df)
        
        # 2. ČIŠTĚNÍ (Chemická lázeň)
        # a) Převod času na datetime
        df['time'] = pd.to_datetime(df['time'])
        
        # b) OPRAVA STROJE ČASU (Seřadit chronologicky)
        df = df.sort_values('time')
        
        # c) VYHOZENÍ DUCHŮ (Cena <= 0)
        df = df[(df['bid'] > 0) & (df['ask'] > 0)]
        
        # d) ODSTRANĚNÍ DUPLICIT (Stejný čas, stejná cena)
        df = df.drop_duplicates(subset=['time', 'bid', 'ask'])
        
        novy_pocet = len(df)
        smazano = puvodni_radky - novy_pocet
        
        # 3. EXPORT (Lisování do cihličky)
        # Nastavíme čas jako index (VectorBT to miluje)
        df = df.set_index('time')
        
        # Změníme příponu z .csv na .parquet
        novy_nazev = nazev.replace(".csv", ".parquet")
        vystupni_cesta = VYSTUPNI_SLOZKA / novy_nazev
        
        # Uložení (komprese 'snappy' je rychlá a efektivní)
        df.to_parquet(vystupni_cesta, compression='snappy')
        
        print(f"✅ HOTOVO (Smazáno {smazano} vadných ticků)")
        
    except Exception as e:
        print(f"💀 SELHALO: {e}")

print("\n🏁 RAFINERIE DOKONČENA. Čistá data jsou připravena.")