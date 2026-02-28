import vectorbt as vbt
import yfinance as yf
import pandas as pd

print("⏳ Stahuji historická data Bitcoinu (poslední 1 rok, 1h svíčky)...")
# Stáhneme data
btc_data = yf.download("BTC-USD", period="1y", interval="1h", progress=False, auto_adjust=False)

# Ořízneme jen na uzavírací ceny a vyhladíme strukturu pro VectorBT
close_price = btc_data['Close']
if isinstance(close_price, pd.DataFrame):
    close_price = close_price.iloc[:, 0]

print("🧠 Počítám indikátory (SMA 26 a SMA 90)...")
# VectorBT v mžiku spočítá všechny klouzavé průměry
fast_ma = vbt.MA.run(close_price, 26)
slow_ma = vbt.MA.run(close_price, 90)

# Kde rychlá překříží pomalou nahoru = NÁKUP
entries = fast_ma.ma_crossed_above(slow_ma)
# Kde rychlá překříží pomalou dolů = PRODEJ
exits = fast_ma.ma_crossed_below(slow_ma)

print("🚀 Spouštím hyper-rychlou simulaci trhu...")
# Spustíme backtest: Start se 100 000 USD a poplatek burzy 0.1 % za obchod
portfolio = vbt.Portfolio.from_signals(
    close_price, 
    entries, 
    exits, 
    init_cash=100000, 
    fees=0.001,
    freq='1h'
)

# Vypíšeme tvrdá data
print("\n" + "="*40)
print("🏆 VÝSLEDKY BACKTESTU (1 ROK)")
print("="*40)
# VectorBT vyhodí obrovskou tabulku statistik, vezmeme ty nejdůležitější
stats = portfolio.stats()
print(stats[['Start Value', 'End Value', 'Total Return [%]', 'Win Rate [%]', 'Max Drawdown [%]', 'Total Trades']])
print("="*40)

print("📊 Vykresluji graf. Zkontroluj nové okno prohlížeče!")
# Otevře interaktivní HTML graf v prohlížeči
print("📊 Generuji HTML graf, moment...")
fig = portfolio.plot()
# Místo .show() to uložíme rovnou do souboru a prohlížeč si to z disku načte sám
fig.write_html("backtest_vysledek.html", auto_open=True)
print("✅ Graf uložen jako 'backtest_vysledek.html' a měl by se otevřít.")