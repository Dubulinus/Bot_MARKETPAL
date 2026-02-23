import yfinance as yf
import pandas as pd
from pathlib import Path

# === NASTAVENÍ ===
SYMBOL = "EURUSD=X"  # Yahoo kód pro EuroDollar
# Yahoo dává 1h data až 730 dní dozadu. Pro začátek super.
PERIODA = "2y"       # Stáhneme poslední 2 roky
INTERVAL = "1h"      # Hodinové svíčky (stačí pro vývoj)

# Cesta pro uložení
CLEAN_DIR = Path("data/03_CLEAN_PARQUET")
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

def tezba_yahoo():
    print(f"⛏️  Těžím data z Yahoo Finance: {SYMBOL}...")
    
    # Stažení dat (magie na jeden řádek)
    df = yf.download(SYMBOL, period=PERIODA, interval=INTERVAL, progress=False)
    
    if df.empty:
        print("❌ CHYBA: Yahoo nic nevrátilo. Jsi online?")
        return

    # Úprava dat pro VectorBT
    # Yahoo vrací sloupce s velkými písmeny, VectorBT má rád malá
    df.columns = [c.lower() for c in df.columns]
    
    # Přejmenování indexu na 'time' (pro pořádek)
    df.index.name = 'time'
    
    # Yahoo občas vrací časovou zónu, VectorBT má radši čistý čas
    df.index = df.index.tz_localize(None)

    print(f"✅ Staženo {len(df)} svíček.")
    print("   (Prvních 5 řádků:)\n", df.head())

    # Uložení do Parquetu
    nazev_souboru = f"{SYMBOL.replace('=X', '')}_Yahoo_1h.parquet"
    cesta = CLEAN_DIR / nazev_souboru
    
    df.to_parquet(cesta)
    print(f"\n💾 ULOŽENO: {cesta}")
    print("🏁 Mise splněna. Jdi spát.")

if __name__ == "__main__":
    tezba_yahoo()