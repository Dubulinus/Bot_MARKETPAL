import yfinance as yf
import pandas as pd
from pathlib import Path

# === NASTAVENÍ ===
SYMBOL = "EURUSD=X"
PERIODA = "1y"
INTERVAL = "1h"

# Cesty
CLEAN_DIR = Path("data/03_CLEAN_PARQUET")
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

def tezba_yahoo():
    print(f"⛏️  Yahoo Express: Těžím {SYMBOL}...")
    
    # Stažení dat
    # multi_level_index=False říká Yahoo, ať nevymýšlí blbosti se sloupci
    try:
        df = yf.download(SYMBOL, period=PERIODA, interval=INTERVAL, progress=False, multi_level_index=False)
    except TypeError:
        # Kdyby náhodou tvá verze neuměla ten parametr nahoře, uděláme to ručně:
        df = yf.download(SYMBOL, period=PERIODA, interval=INTERVAL, progress=False)
        if isinstance(df.columns[0], tuple):
            df.columns = [c[0] for c in df.columns]

    if df.empty:
        print("❌ CHYBA: Yahoo nic nedalo. Jsi online?")
        return

    # === ČIŠTĚNÍ PRO VECTORBT ===
    # 1. Pokud tam zůstaly nějaké divné sloupce (tuples), srovnáme je
    if isinstance(df.columns[0], tuple):
        df.columns = [c[0] for c in df.columns]
    
    # 2. Všechno na malá písmena (Open -> open)
    df.columns = [c.lower() for c in df.columns]
    
    # 3. Časová osa
    df.index.name = 'time'
    df.index = df.index.tz_localize(None) # Odstranění časových zón

    # Uložení
    nazev = f"EURUSD_Yahoo_1h.parquet"
    cesta = CLEAN_DIR / nazev
    df.to_parquet(cesta)
    
    print(f"\n✅ ÚSPĚCH! Staženo {len(df)} svíček.")
    print(f"💾 Uloženo v: {cesta}")
    print("🎯 Dobrou noc, šéfe.")

if __name__ == "__main__":
    tezba_yahoo()