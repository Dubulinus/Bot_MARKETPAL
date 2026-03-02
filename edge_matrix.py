"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - EDGE MATRIX                             ║
║         Phase 2 | Which signals actually make money?        ║
╚══════════════════════════════════════════════════════════════╝

WHAT IS "EDGE"?
    Edge = your strategy wins more than random chance, consistently.
    A coin flip wins 50% of the time. If your signal wins 55% with
    a good risk/reward ratio — that's edge. That's your job as a quant.

HOW THIS WORKS:
    For each signal column (golden_cross, rsi_oversold_exit etc.):
    1. Find every candle where signal = True
    2. Look forward N candles (the "holding period")
    3. Calculate: did price go up or down?
    4. Aggregate: win rate, avg return, Sharpe-like ratio

    Output: a ranked table showing which signals have real edge
    and which are just noise. This tells you WHERE TO FOCUS.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime

# ─── CONFIG ────────────────────────────────────────────────────

INPUT_DIR  = "data/04_GOLD_FEATURES"
OUTPUT_DIR = "data/05_EDGE_MATRIX"

TIMEFRAMES = ["M5", "M15", "H1"]
CATEGORIES = ["forex", "stocks"]

# After a signal fires, how many candles forward do we measure the result?
HOLDING_PERIODS = [3, 6, 12, 24]

# Minimum number of signal occurrences to be statistically meaningful
MIN_OCCURRENCES = 20

# Signal columns to test (must match feature_engineering.py output)
SIGNAL_COLS = [
    "signal_golden_cross",
    "signal_death_cross",
    "signal_macd_bull",
    "signal_macd_bear",
    "signal_rsi_oversold_exit",
    "signal_rsi_overbought_exit",
    "signal_bb_breakout_up",
    "signal_bb_breakout_down",
    "signal_above_vwap",
    "signal_bull_regime",
]

# ─── EDGE CALCULATION ──────────────────────────────────────────

def calculate_edge(df, signal_col, holding_period, ticker, tf_name):
    """
    Core edge calculation for a single signal + holding period combo.

    Logic:
    - Find all candles where signal fired (True)
    - For each: compute forward return over holding_period candles
    - A bullish signal (golden_cross, macd_bull etc.) should predict UP moves
    - A bearish signal (death_cross, macd_bear etc.) should predict DOWN moves
    - We flip bearish signals so that "win" always means "signal was right"

    Returns a dict with all the stats, or None if not enough data.
    """
    if signal_col not in df.columns:
        return None

    # Forward return: % change from signal candle close to N candles later
    df["_fwd_return"] = df["close"].shift(-holding_period) / df["close"] - 1

    # Get all rows where signal fired and we have a valid forward return
    signal_rows = df[df[signal_col] == True].dropna(subset=["_fwd_return"])

    if len(signal_rows) < MIN_OCCURRENCES:
        return None

    returns = signal_rows["_fwd_return"].values

    # Bearish signals: expected direction is DOWN, so we flip the return
    # so that "positive return" always means "signal was correct"
    bearish_signals = ["signal_death_cross", "signal_macd_bear",
                       "signal_rsi_overbought_exit", "signal_bb_breakout_down"]
    if signal_col in bearish_signals:
        returns = -returns

    wins     = returns > 0
    win_rate = wins.mean() * 100
    avg_ret  = returns.mean() * 100
    std_ret  = returns.std() * 100

    # Edge Ratio: avg_return / std_return (higher = more consistent edge)
    # Think of it as a simplified Sharpe ratio per signal
    edge_ratio = avg_ret / std_ret if std_ret > 0 else 0

    # Profit Factor: sum of wins / sum of losses (>1.5 is decent, >2 is good)
    gross_profit = returns[returns > 0].sum()
    gross_loss   = abs(returns[returns < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf

    return {
        "ticker":         ticker,
        "timeframe":      tf_name,
        "signal":         signal_col.replace("signal_", ""),
        "hold_candles":   holding_period,
        "occurrences":    len(signal_rows),
        "win_rate_pct":   round(win_rate, 1),
        "avg_return_pct": round(avg_ret, 4),
        "std_return_pct": round(std_ret, 4),
        "edge_ratio":     round(edge_ratio, 3),
        "profit_factor":  round(profit_factor, 2),
    }


def rate_edge(row):
    """
    Human-readable edge rating based on multiple factors.
    Don't chase signals with <50 occurrences — sample size too small.
    """
    if row["occurrences"] < MIN_OCCURRENCES:
        return "❓ LOW SAMPLE"
    if row["win_rate_pct"] >= 55 and row["profit_factor"] >= 1.5 and row["edge_ratio"] > 0.05:
        return "🔥 STRONG"
    if row["win_rate_pct"] >= 52 and row["profit_factor"] >= 1.2:
        return "✅ DECENT"
    if row["win_rate_pct"] >= 50:
        return "⚖️  MARGINAL"
    return "❌ NO EDGE"


# ─── MAIN ──────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL EDGE MATRIX - PHASE 2    ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_results = []

    for tf_name in TIMEFRAMES:
        for category in CATEGORIES:
            folder = os.path.join(INPUT_DIR, tf_name, category)
            if not os.path.exists(folder):
                continue

            files = sorted([f for f in os.listdir(folder) if f.endswith(".parquet")])
            print(f"{'═'*55}")
            print(f"🔍 {tf_name} / {category.upper()}")
            print(f"{'═'*55}")

            for filename in files:
                ticker = filename.replace(".parquet", "")
                path   = os.path.join(folder, filename)
                df     = pd.read_parquet(path).copy()

                print(f"  📊 Testing {ticker}...")

                for signal_col in SIGNAL_COLS:
                    for hold in HOLDING_PERIODS:
                        result = calculate_edge(df, signal_col, hold, ticker, tf_name)
                        if result:
                            all_results.append(result)

    if not all_results:
        print("❌ No results — check that Gold layer files exist.")
        return

    # Build results DataFrame
    results_df = pd.DataFrame(all_results)
    results_df["edge_rating"] = results_df.apply(rate_edge, axis=1)

    # Sort by edge_ratio descending — best signals first
    results_df = results_df.sort_values("edge_ratio", ascending=False)

    # Save full matrix
    full_path = os.path.join(OUTPUT_DIR, "edge_matrix_full.csv")
    results_df.to_csv(full_path, index=False)

    # Save only strong + decent signals
    top_df = results_df[results_df["edge_rating"].isin(["🔥 STRONG", "✅ DECENT"])]
    top_path = os.path.join(OUTPUT_DIR, "edge_matrix_top.csv")
    top_df.to_csv(top_path, index=False)

    # ── PRINT TOP 20 ───────────────────────────────────────────
    print(f"\n{'═'*90}")
    print("🏆 TOP SIGNALS BY EDGE RATIO (top 20)")
    print(f"{'═'*90}")
    print(f"{'Signal':<28} {'TF':<6} {'Ticker':<8} {'Hold':<6} {'WinRate':<10} {'AvgRet':<10} {'PF':<8} {'Rating'}")
    print(f"{'─'*90}")

    for _, row in results_df.head(20).iterrows():
        print(
            f"{row['signal']:<28} "
            f"{row['timeframe']:<6} "
            f"{row['ticker']:<8} "
            f"{row['hold_candles']:<6} "
            f"{row['win_rate_pct']:<10} "
            f"{row['avg_return_pct']:<10} "
            f"{row['profit_factor']:<8} "
            f"{row['edge_rating']}"
        )

    print(f"{'═'*90}")
    print(f"\n📁 Full matrix saved: {full_path}")
    print(f"📁 Top signals saved: {top_path}")
    print(f"\n📊 Total signal/ticker combos tested: {len(all_results)}")
    print(f"🔥 Strong signals found: {len(results_df[results_df['edge_rating'] == '🔥 STRONG'])}")
    print(f"✅ Decent signals found: {len(results_df[results_df['edge_rating'] == '✅ DECENT'])}")
    print(f"❌ No-edge signals:      {len(results_df[results_df['edge_rating'] == '❌ NO EDGE'])}")


if __name__ == "__main__":
    main()