import vectorbt as vbt
import yfinance as yf
import pandas as pd

print("⏳ Stahuji data pro matematický soudní dvůr...")
# Stáhneme Bitcoin za poslední 2 roky, denní data pro čistší signál v backtestu
data = yf.download("BTC-USD", period="2y", interval="1d", progress=False)

if isinstance(data.columns, pd.MultiIndex):
    close_price = data['Close'].iloc[:, 0]
else:
    close_price = data['Close']

print("🧮 Počítám SMA (26 a 90) a generuji signály...")
# VectorBT umí počítat indikátory bleskově nad celým datasetem
fast_ma = vbt.MA.run(close_price, 26)
slow_ma = vbt.MA.run(close_price, 90)

# Křížení (Entries = Golden Cross, Exits = Death Cross)
entries = fast_ma.ma_crossed_above(slow_ma)
exits = fast_ma.ma_crossed_below(slow_ma)

print("🚀 Spouštím VectorBT Portfolio simulaci...")
# Vytvoříme portfolio se 100 000 USD
pf = vbt.Portfolio.from_signals(
    close_price, 
    entries, 
    exits, 
    init_cash=100000, 
    fees=0.001, # Počítáme s 0.1% poplatkem burzy, ať jsme realisté!
    freq='1D'
)

print("\n" + "="*40)
print("📊 VÝSLEDKY BACKTESTU (SMA 26/90)")
print("="*40)
# Tohle ti vyhodí tvrdá čísla: Win Rate, Max Drawdown, Sharpe Ratio
print(pf.stats())

# Zobrazíme graf
pf.plot().show()