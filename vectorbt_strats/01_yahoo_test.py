import vectorbt as vbt
import pandas as pd
from pathlib import Path

# 1. NAČTENÍ DAT
cesta = Path("data/03_CLEAN_PARQUET/EURUSD_Yahoo_1h.parquet")
print(f"🚀 Načítám data: {cesta}")

try:
    data = pd.read_parquet(cesta)
    price = data['close']
    
    # 2. STRATEGIE (SMA Cross)
    fast_ma = vbt.MA.run(price, 10)
    slow_ma = vbt.MA.run(price, 20)
    
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    
    # freq='1h' je klíčové pro správný výpočet Sharpe Ratio
    pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=10000, freq='1h')
    
    # 3. VÝSLEDEK (Opravený výpis)
    print("\n" + "="*30)
    print("📊 VÝSLEDKY STRATEGIE")
    print("="*30)
    
    # Místo stats['Total Return'] použijeme přímo metody portfolia
    # To je nejjistější cesta, jak se vyhnout KeyError
    total_return = pf.total_return() * 100
    total_trades = pf.trades.count()
    sharpe_ratio = pf.sharpe_ratio()
    win_rate = pf.trades.win_rate() * 100
    max_dd = pf.max_drawdown() * 100

    print(f"💰 Celkový výnos:  {total_return:.2f} %")
    print(f"🤝 Počet obchodů:  {total_trades}")
    print(f"🏆 Win Rate:       {win_rate:.2f} %")
    print(f"📉 Max Drawdown:   {max_dd:.2f} %")
    print(f"⚖️  Sharpe Ratio:   {sharpe_ratio:.2f}")
    print("="*30)

except Exception as e:
    print(f"❌ CHYBA: {e}")