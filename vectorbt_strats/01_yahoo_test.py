import vectorbt as vbt
import pandas as pd
from pathlib import Path

# 1. NAČTENÍ DAT (Yahoo Parquet)
# Pozor na název souboru, zkopíruj ho přesně z tvé složky!
cesta = "data/03_CLEAN_PARQUET/EURUSD_Yahoo_1h.parquet"
print(f"🚀 Načítám data: {cesta}")

try:
    data = pd.read_parquet(cesta)
    print(f"✅ Načteno {len(data)} řádků.")
    
    # VectorBT potřebuje cenu. Yahoo má 'close'.
    price = data['close']
    
    # 2. RYCHLÝ BACKTEST (SMA Cross na hodinovém grafu)
    # Když 10-hodinový průměr překříží 20-hodinový
    fast_ma = vbt.MA.run(price, 10)
    slow_ma = vbt.MA.run(price, 20)
    
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    
    pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=10000)
    
    # 3. VÝSLEDEK
    print("\n=== 📊 VÝSLEDEK YAHOO TESTU ===")
    print(f"Celkový výnos: {pf.total_return() * 100:.2f} %")
    print(f"Počet obchodů: {pf.stats()['Total Trades']}")
    print(f"Sharpe Ratio:  {pf.stats()['Sharpe Ratio']:.2f}")
    
except Exception as e:
    print(f"❌ CHYBA: {e}")
    print("Zkontroluj, jestli se soubor jmenuje přesně takhle!")