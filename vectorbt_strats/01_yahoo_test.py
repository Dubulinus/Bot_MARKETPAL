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
    
    # Řekneme mu: "Počítej s tím, že svíčky jsou hodinové (1h)"
    stats = pf.stats(freq='1h')
    
    print(f"Celkový výnos: {stats['Total Return'] * 100:.2f} %")
    print(f"Počet obchodů: {stats['Total Trades']}")
    print(f"Sharpe Ratio:  {stats['Sharpe Ratio']:.2f}")
    print(f"Win Rate:      {stats['Win Rate [%]']:.2f} %")
    print(f"Max Drawdown:  {stats['Max Drawdown [%]']:.2f} %")

except Exception as e:
    # Teď už nám to vypíše skutečnou chybu, ne moji vymyšlenou hlášku
    print(f"❌ SKUTEČNÁ CHYBA: {e}")
    import traceback
    traceback.print_exc()