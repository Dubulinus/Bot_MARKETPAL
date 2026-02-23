import os
import glob
import pandas as pd
from datetime import date, timedelta
from pathlib import Path

# === KONFIGURACE TĚŽBY ===
SYMBOL = "EURUSD"  # Měnový pár (zkus třeba GBPUSD, USDJPY, XAUUSD)
START = "2024-01-01" # Od kdy (YYYY-MM-DD)
KONEC = "2024-01-05" # Do kdy (třeba jen týden na zkoušku)

# Kam to budeme sypat
RAW_DIR = Path("data/02_RAW_DUKASCOPY")
CLEAN_DIR = Path("data/03_CLEAN_PARQUET")

# Vytvoření složek, pokud nejsou
RAW_DIR.mkdir(parents=True, exist_ok=True)
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

def stahni_a_zpracuj():
    print(f"⛏️  ZAČÍNÁM TĚŽBU: {SYMBOL} ({START} -> {KONEC})")
    print(f"📂 Dočasné úložiště: {RAW_DIR}")

    # 1. SPUŠTĚNÍ DUKA PŘÍKAZU (Voláme systémový příkaz z Pythonu)
    # Parametr -d (den), -s (symbol), -f (folder)
    prikaz = f"duka {SYMBOL} -d {START} -t {KONEC} -f {RAW_DIR} -c tick --header"
    
    print(f"🚀 Odesílám příkaz do Švýcarska...")
    exit_code = os.system(prikaz)
    
    if exit_code != 0:
        print("❌ CHYBA: Těžba selhala. Zkontroluj internet nebo název symbolu.")
        return

    # 2. NAJÍT STAŽENÝ SOUBOR (Duka generuje divné názvy, musíme ho najít)
    # Hledáme nejnovější CSV v té složce
    seznam_csv = list(RAW_DIR.glob(f"*{SYMBOL}*.csv"))
    if not seznam_csv:
        print("❌ CHYBA: Duka prý stáhla data, ale CSV nikde není.")
        return
    
    # Vezmeme ten nejnovější (kdyby tam ležely staré)
    stazeny_soubor = max(seznam_csv, key=os.path.getctime)
    print(f"✅ Staženo: {stazeny_soubor.name}")

    # 3. RAFINERIE (Okamžitý převod na Parquet)
    print("⚙️  Spouštím rafinerii (CSV -> Parquet)...")
    
    try:
        df = pd.read_csv(stazeny_soubor)
        
        # Čištění (Dukascopy data jsou kvalitní, ale jistota je jistota)
        df['time'] = pd.to_datetime(df['time'])
        df = df.set_index('time')
        df = df.sort_index()
        
        # Výstupní název
        nazev_parquet = f"{SYMBOL}_{START}_{KONEC}.parquet"
        cesta_parquet = CLEAN_DIR / nazev_parquet
        
        # Uložení
        df.to_parquet(cesta_parquet, compression='snappy')
        print(f"💾 Uloženo čisté zlato: {cesta_parquet}")
        
        # 4. ÚKLID (Smazání CSV)
        os.remove(stazeny_soubor)
        print("🗑️  Surové CSV smazáno. Disk je čistý.")
        
    except Exception as e:
        print(f"💀 CHYBA PŘI ZPRACOVÁNÍ: {e}")

if __name__ == "__main__":
    stahni_a_zpracuj()