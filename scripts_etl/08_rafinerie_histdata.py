import pandas as pd
from pathlib import Path

# === NASTAVENÍ CEST ===
# Sem nám předchozí skript (Miner) uložil to ošklivé CSV
VSTUPNI_SLOZKA = Path("data_raw") 
# Společný sklad pro všechna čistá data (stejný jako u tvé staré rafinerie)
VYSTUPNI_SLOZKA = Path("data/03_CLEAN_PARQUET")

# Vytvoří výstupní složku, pokud náhodou zmizela
VYSTUPNI_SLOZKA.mkdir(parents=True, exist_ok=True)

print("🏭 START RAFINERIE: LINKA HISTDATA (1M Svíčky)")
print(f"➡️  Nasávám surovinu z: {VSTUPNI_SLOZKA}")
print(f"➡️  Lisuji do: {VYSTUPNI_SLOZKA}\n")

# Najdeme všechny CSV ve složce data_raw
soubory = list(VSTUPNI_SLOZKA.glob("*.csv"))

if not soubory:
    print("❌ CHYBA: Žádná surová data nenalezena. Spustil jsi vůbec Minera?")
    exit()

for cesta in soubory:
    nazev = cesta.name
    print(f"⚙️ Zpracovávám agresivní chemickou lázní: {nazev}")
    
    try:
        # 1. NAČTENÍ (HistData formát je specifický: bez hlavičky, oddělený středníkem)
        df = pd.read_csv(
            cesta, 
            sep=';', 
            header=None, 
            names=['time', 'open', 'high', 'low', 'close', 'volume']
        )
        
        # 2. ČIŠTĚNÍ ČASU (Největší bolest)
        # HistData čas vypadá jako "20230101 235959", musíme to přeložit pro VectorBT
        print("   ⏳ Překládám mimozemský časový formát na Datetime...")
        df['time'] = pd.to_datetime(df['time'], format='%Y%m%d %H%M%S')
        
        # Seřadíme, pro jistotu
        df = df.sort_values('time')
        
        # 3. EXPORT (Lisování do Parquet cihličky)
        df = df.set_index('time')
        
        novy_nazev = nazev.replace(".csv", "_1M.parquet")
        vystupni_cesta = VYSTUPNI_SLOZKA / novy_nazev
        
        # Uložíme. Compression 'snappy' už znáš ze své staré linky.
        df.to_parquet(vystupni_cesta, compression='snappy')
        
        print(f"✅ HOTOVO: {len(df):,} minutových svíček čistých jako křišťál uložených do {novy_nazev}\n")
        
    except Exception as e:
        print(f"💀 SELHALO u souboru {nazev}: {e}")

print("🏁 LINKA HISTDATA DOKONČENA. Můžeme jít backtestovat.")