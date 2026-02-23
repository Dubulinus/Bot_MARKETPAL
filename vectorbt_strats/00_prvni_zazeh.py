import vectorbt as vbt
import pandas as pd
import numpy as np
from pathlib import Path

# 1. NAČTENÍ DAT (Z naší nové čisté složky)
cesta = "data/03_CLEAN_PARQUET/2026_02_18.parquet"  # Zkontroluj, jestli název sedí!
print(f"🚀 Načítám data z: {cesta}")

df = pd.read_parquet(cesta)

# VectorBT potřebuje jen jeden sloupec ceny (obvykle Close, my použijeme Bid)
price_ticks = df['bid']

print(f"📊 Načteno {len(price_ticks)} ticků.")

# 2. RESAMPLING (Magie: Ticky -> Minutové svíčky)
# '1min' = 1 minuta. Můžeš zkusit '5min', '1h' atd.
print("🔄 Převádím ticky na 1-minutové svíčky...")
price_1m = price_ticks.resample('1min').ohlc() # Vytvoří Open, High, Low, Close

# Vezmeme jen Close cenu pro strategii
close_price = price_1m['close']

# Ošetření prázdných míst (kdyby v nějaké minutě nebyl obchod)
close_price = close_price.ffill() 

print(f"🕯️ Vytvořeno {len(close_price)} minutových svíček.")

# 3. STRATEGIE "HELLO WORLD" (Rychlý SMA Cross)
# Koupíme, když je cena nad průměrem za 10 minut. Prodáme, když je pod.
fast_ma = vbt.MA.run(close_price, 10)
entries = close_price > fast_ma.ma
exits = close_price < fast_ma.ma

# 4. BACKTEST (Simulace)
pf = vbt.Portfolio.from_signals(close_price, entries, exits, init_cash=10000)

# 5. VÝSLEDEK
print("\n=== 📈 VÝSLEDKY PRVNÍHO TESTU ===")
print(f"Celkový výnos: {pf.total_return() * 100:.2f} %")
print(f"Počet obchodů: {pf.stats()['Total Trades']}")
print(f"Win Rate: {pf.stats()['Win Rate [%]']:.2f} %")

# Pokud máš Jupyter Notebook, tohle ti vykreslí graf. 
# V terminálu to neuvidíš, ale aspoň uvidíš čísla nahoře.
# pf.plot().show()