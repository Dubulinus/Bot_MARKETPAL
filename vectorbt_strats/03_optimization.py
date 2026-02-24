import vectorbt as vbt
import pandas as pd
import numpy as np
from pathlib import Path

# 1. NAČTENÍ DAT
cesta = Path("data/03_CLEAN_PARQUET/EURUSD_Yahoo_1h.parquet")
print(f"🚀 Načítám data a startuji Matrix...")
data = pd.read_parquet(cesta)
price = data['close']

# 2. DEFINICE PARAMETRŮ (Tady se děje ta magie)
# Zkoušíme okna:
# Rychlé (fast): od 2 do 30, krok 2 (2, 4, 6... 30)
# Pomalé (slow): od 30 do 100, krok 5 (30, 35, 40... 100)
fast_windows = np.arange(2, 30, 2)
slow_windows = np.arange(30, 100, 5)

print(f"🔄 Testuji {len(fast_windows) * len(slow_windows)} různých strategií najednou...")

# 3. SPOUŠTĚNÍ MŘÍŽKY (VectorBT Power)
# Param_product=True znamená "zkus každé s každým"
fast_ma = vbt.MA.run(price, fast_windows, short_name='fast')
slow_ma = vbt.MA.run(price, slow_windows, short_name='slow')

entries = fast_ma.ma_crossed_above(slow_ma)
exits = fast_ma.ma_crossed_below(slow_ma)

# 4. VYHODNOCENÍ
pf = vbt.Portfolio.from_signals(price, entries, exits, freq='1h', init_cash=10000)

# Najdeme tu nejlepší kombinaci podle celkového výnosu
best_return = pf.total_return().max() * 100
best_settings = pf.total_return().idxmax()

print("\n" + "="*40)
print(f"🏆 VÍTĚZNÁ KOMBINACE NALEZENA")
print("="*40)
print(f"Rychlý průměr (Fast MA): {best_settings[0]}")
print(f"Pomalý průměr (Slow MA): {best_settings[1]}")
print(f"💰 Maximální výnos:      {best_return:.2f} %")
print("-" * 40)
print(f"(Původní strategie 10/20 měla cca 5.47 %)")
print("="*40)

# Bonus: Heatmapa (pokud bys ji chtěl vidět, ale v terminálu to nejde)
# pf.total_return().vbt.heatmap().show()

# ... (kód z minula končí printem vítězné kombinace) ...

print("🎨 Kreslím Heatmapu (otevře se v prohlížeči)...")

# Vytvoření grafu
fig = pf.total_return().vbt.heatmap(
    x_level='fast', 
    y_level='slow',
    title='Optimalizace SMA: Kde jsou prachy? 💰',
    symmetric=True, # Aby nula byla uprostřed barev
    cmap='RdYlGn'   # Červená (prodělek) -> Žlutá -> Zelená (zisk)
)

# Uložení a otevření
heatmap_cesta = "vectorbt_strats/heatmap_vysledky.html"
fig.write_html(heatmap_cesta)

import webbrowser
webbrowser.open(heatmap_cesta)

print("✅ Hotovo. Koukni do prohlížeče!")