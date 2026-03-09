"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - REGIME FIX v1.0                            ║
║     Opraví forex gold parquety: přidá ema_20, ema_50       ║
╚══════════════════════════════════════════════════════════════╝

PROBLÉM:
    backtest_v3.py → regime_analysis() ukazuje jen SIDEWAYS pro forex
    protože v gold parquetech chybí ema_20 a ema_50.

    Kód v backtest_v3.py (řádek ~180):
        if "ema_20" not in df.columns or "ema_50" not in df.columns:
            df["regime"] = "SIDEWAYS"  ← toto se vždy spustí pro forex

ŘEŠENÍ:
    Tento skript dočasně přidá EMA do existujících gold souborů.
    Trvalé řešení: feature_engineering.py by měl EMA přidávat vždy.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

GOLD_DIR   = "data/04_GOLD_FEATURES"
TIMEFRAMES = ["M5", "M15", "H1"]
CATEGORIES = {
    "forex":  ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"],
    "stocks": ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "GOOGL", "AMD"],
}

def add_ema_if_missing(df: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """Přidá EMA sloupce pokud chybí. Vrátí (df, seznam přidaných)."""
    added = []
    close = df["close"] if "close" in df.columns else df.iloc[:, 3]

    for span, col in [(20, "ema_20"), (50, "ema_50"), (200, "ema_200")]:
        if col not in df.columns:
            df[col] = close.ewm(span=span, adjust=False).mean()
            added.append(col)

    # ATR — potřebný pro regime amplitude výpočet
    if "atr" not in df.columns and all(c in df.columns for c in ["high","low","close"]):
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"]  - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.ewm(span=14, adjust=False).mean()
        added.append("atr")

    return df, added


def regime_label(df: pd.DataFrame) -> pd.DataFrame:
    """
    Přidá regime sloupec přímo do gold souboru.
    Logika:
      BULL     — ema_20 > ema_50 AND close > ema_20
      BEAR     — ema_20 < ema_50 AND close < ema_20
      SIDEWAYS — jinak
    """
    if "ema_20" not in df.columns or "ema_50" not in df.columns:
        df["regime"] = "SIDEWAYS"
        return df

    bull = (df["ema_20"] > df["ema_50"]) & (df["close"] > df["ema_20"])
    bear = (df["ema_20"] < df["ema_50"]) & (df["close"] < df["ema_20"])

    df["regime"] = "SIDEWAYS"
    df.loc[bull, "regime"] = "BULL"
    df.loc[bear, "regime"] = "BEAR"

    return df


def main():
    print("╔══════════════════════════════════════════════════╗")
    print("║   MARKETPAL REGIME FIX v1.0                    ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                      ║")
    print("╚══════════════════════════════════════════════════╝\n")

    total_fixed = 0
    total_files = 0

    for tf in TIMEFRAMES:
        print(f"⏱️  {tf}")
        for category, tickers in CATEGORIES.items():
            for ticker in tickers:
                path = Path(GOLD_DIR) / tf / category / f"{ticker}.parquet"
                if not path.exists():
                    continue

                total_files += 1
                df = pd.read_parquet(path)

                df, added = add_ema_if_missing(df)
                df = regime_label(df)

                # Regime distribuce
                dist = df["regime"].value_counts()
                bull_pct = dist.get("BULL", 0) / len(df) * 100
                bear_pct = dist.get("BEAR", 0) / len(df) * 100
                side_pct = dist.get("SIDEWAYS", 0) / len(df) * 100

                if added:
                    df.to_parquet(path, index=False)
                    total_fixed += 1
                    print(f"  ✅ {ticker:<8} přidáno: {added}")
                else:
                    print(f"  ✓  {ticker:<8} EMA už existuje")

                print(f"     Regime: BULL {bull_pct:.0f}% | BEAR {bear_pct:.0f}% | SIDEWAYS {side_pct:.0f}%")

    print(f"\n{'═'*50}")
    print(f"  Opraveno: {total_fixed}/{total_files} souborů")
    print(f"\n  ⚠️  Trvalá oprava: přidej do feature_engineering.py:")
    print(f"     df['ema_20'] = df['close'].ewm(span=20).mean()")
    print(f"     df['ema_50'] = df['close'].ewm(span=50).mean()")
    print(f"\n  💡 Teď spusť backtest_v3.py — regime analysis bude fungovat")


if __name__ == "__main__":
    main()