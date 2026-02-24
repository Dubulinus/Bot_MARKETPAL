import os
import glob
import pandas as pd
from pathlib import Path
from datetime import date
from duka.app import app
from duka.core.utils import TimeFrame

# === KONFIGURACE ===
SYMBOL = "EURUSD"
START = date(2023, 3, 1)    # Datum musí být objekt date, ne text!
KONEC = date(2023, 3, 7)

# Cesty
RAW_DIR = "data/02_RAW_DUKASCOPY"  # Duka chce string, ne Path objekt
CLEAN_DIR = Path("data/03_CLEAN_PARQUET")

# Vytvoření složek
Path(RAW_DIR).mkdir(parents=True, exist_ok=True)
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

def stahni_a_zpracuj():
    print(f"\n==========================================")
    print(f"⛏️  START TĚŽBY: {SYMBOL} ({START} -> {KONEC})")
    print(f"==========================================\n")

    # 1. PŘÍMÁ TĚŽBA (Python volá Python)
    print(f"🚀 Připojuji se do Švýcarska...")
    try:
        # Voláme přímo funkci app() z knihovny Duka
        # threads=4 znamená, že to pojede 4x rychleji (využije 4 jádra)
        app(symbols=[SYMBOL], start=START, end=KONEC, 
            threads=1, timeframe=TimeFrame.TICK, 
            folder=RAW_DIR, header=True)
            
    except Exception as e:
        print(f"❌ KRYTICKÁ CHYBA PŘI STAHOVÁNÍ: {e}")
        return

    # 2. HLEDÁNÍ ÚLOVKU
    print("\n🔍 Hledám stažený soubor...")
    cesta_raw = Path(RAW_DIR)
    # Duka to pojmenuje např. "EURUSD-2023_01_01-2023_12_31.csv"
    seznam_csv = list(cesta_raw.glob(f"*{SYMBOL}*.csv"))
    
    if not seznam_csv:
        print("❌ CHYBA: CSV soubor nikde není. Duka asi nic nestáhla.")
        return
    
    # Vezmeme ten nejnovější
    stazeny_soubor = max(seznam_csv, key=os.path.getctime)
    velikost_mb = stazeny_soubor.stat().st_size / (1024 * 1024)
    print(f"✅ Nalezeno: {stazeny_soubor.name} ({velikost_mb:.2f} MB)")

    # 3. RAFINERIE
    print(f"⚙️  Startuji kompresi do Parquetu (Tohle chvíli potrvá)...")
    
    try:
        # Použijeme chunksize, aby ti nevybuchla RAM u obřího souboru
        # (Ale pro jednoduchost to teď načteme celé, pokud máš 16GB RAM, bude to OK)
        df = pd.read_csv(stazeny_soubor)
        
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')
        df = df.sort_index()
        
        nazev_parquet = f"{SYMBOL}_{START}_{KONEC}.parquet"
        cesta_parquet = CLEAN_DIR / nazev_parquet
        
        df.to_parquet(cesta_parquet, compression='snappy')
        
        print(f"💾 ULOŽENO: {cesta_parquet}")
        
        # 4. ÚKLID
        os.remove(stazeny_soubor)
        print("🗑️  Surové CSV smazáno.")
        print("\n✅ HOTOVO. Dobrou noc, šéfe.")
        
    except Exception as e:
        print(f"💀 CHYBA PŘI ZPRACOVÁNÍ: {e}")

if __name__ == "__main__":
    stahni_a_zpracuj()