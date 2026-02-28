import vectorbt as vbt
import yfinance as yf
import numpy as np

print("⏳ Stahuji data pro optimalizaci (Bitcoin, 1 rok, 1h svíčky)...")
# Můžeš změnit na cokoliv, ale pro testy nechme BTC
data = yf.download("BTC-USD", period="1y", interval="1h")
close_price = data['Close']

# Ošetření struktury dat z yfinance
if isinstance(close_price, type(data)):
    close_price = close_price.iloc[:, 0]

print("🧠 Generuji stovky kombinací pro SMA (Fast a Slow)...")
# Rychlé SMA budeme testovat od 10 do 50 (po krocích 5)
fast_mas = np.arange(10, 55, 5)
# Pomalé SMA budeme testovat od 50 do 200 (po krocích 10)
slow_mas = np.arange(50, 210, 10)

# VBT magie: vytvoří indikátory pro VŠECHNY kombinace naráz
fast_ma, slow_ma = vbt.MA.run_combs(close_price, window=fast_mas, short_names=['fast', 'slow'])

print("🚀 Křížím průměry a simuluji tisíce paralelních vesmírů...")
entries = fast_ma.ma_crossed_above(slow_ma)
exits = fast_ma.ma_crossed_below(slow_ma)

# Spuštění masivního backtestu
portfolio = vbt.Portfolio.from_signals(
    close_price, 
    entries, 
    exits, 
    init_cash=100000, 
    fees=0.001,
    freq='1h'
)

print("📊 Počítám výsledky...")
# Získáme Total Return pro každou kombinaci a najdeme tu nejlepší
returns = portfolio.total_return()
best_combo = returns.idxmax()
best_return = returns.max()

print("\n" + "="*50)
print("🏆 NEJLEPŠÍ NALEZENÁ STRATEGIE")
print("="*50)
print(f"Rychlá SMA: {best_combo[0]}")
print(f"Pomalá SMA: {best_combo[1]}")
print(f"Zhodnocení: {best_return * 100:.2f} %")
print("="*50)

# Uložení grafu pro tu JEDNU nejlepší kombinaci
print("\n📊 Generuji HTML graf pro vítěznou kombinaci...")
fig = portfolio[best_combo].plot()
fig.write_html("nejlepsi_sma_strategie.html", auto_open=True)
print("✅ Graf uložen jako 'nejlepsi_sma_strategie.html' a měl by se otevřít.")