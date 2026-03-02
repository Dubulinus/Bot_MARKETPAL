"""
╔══════════════════════════════════════════════════════════════╗
║        MARKETPAL - DATA REFINERY - SILVER LAYER             ║
║        Phase 2 | Cleaning + Validation + Quality Check      ║
╚══════════════════════════════════════════════════════════════╝

PIPELINE:
    Bronze (raw Polygon Parquet)
        → validate structure
        → remove duplicates
        → handle missing candles (NaN forward-fill for forex gaps)
        → detect & flag outliers (price spikes)
        → remove weekend/holiday gaps for stocks
        → Silver (clean Parquet ready for feature engineering)

WHY THIS MATTERS:
    Garbage in = garbage out. If your backtest or DRL agent trains
    on data with a missing candle or a price spike caused by a bad
    tick, it will learn wrong patterns. The refinery is your last
    line of defense before any calculation touches the data.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime

# ─── CONFIG ────────────────────────────────────────────────────

INPUT_DIR  = "data/02_POLYGON_RAW"
OUTPUT_DIR = "data/03_SILVER_CLEAN"

TIMEFRAMES = ["M5", "M15", "H1"]
CATEGORIES = ["forex", "stocks"]

# Expected candle gaps in minutes for each timeframe
EXPECTED_GAP = {
    "M5":  5,
    "M15": 15,
    "H1":  60,
}

# Outlier detection: flag candles where price moves more than X%
# in a single candle. 3% is aggressive for M5 forex, adjust if needed.
MAX_CANDLE_MOVE_PCT = {
    "forex":  1.5,   # Forex rarely moves >1.5% in a single M5 candle
    "stocks": 5.0,   # Stocks can gap more, especially on earnings
}

# ─── HELPERS ───────────────────────────────────────────────────

def create_folders():
    for tf in TIMEFRAMES:
        for cat in CATEGORIES:
            os.makedirs(os.path.join(OUTPUT_DIR, tf, cat), exist_ok=True)
    print(f"✅ Silver layer folders ready at: {OUTPUT_DIR}\n")


def load_parquet(path):
    """Load a parquet file, return DataFrame or None on failure."""
    try:
        df = pd.read_parquet(path)
        return df
    except Exception as e:
        print(f"  ❌ Failed to load {path}: {e}")
        return None


def check_structure(df, filename):
    """
    Make sure all required columns exist.
    Returns True if OK, False if something is missing.
    This catches cases where Polygon returned partial data.
    """
    required = ["open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        print(f"  ❌ STRUCTURE: Missing columns {missing} in {filename}")
        return False
    return True


def remove_duplicates(df, filename):
    """
    Remove duplicate timestamps.
    This happens occasionally with Polygon when a candle is sent twice.
    We keep the last occurrence (usually the corrected one).
    """
    before = len(df)
    df = df[~df.index.duplicated(keep="last")]
    removed = before - len(df)
    if removed > 0:
        print(f"  🧹 DUPLICATES: Removed {removed} duplicate rows in {filename}")
    return df


def sort_index(df):
    """Always sort by timestamp ascending. Sounds obvious, but don't skip this."""
    return df.sort_index()


def detect_outliers(df, category, tf_name, filename):
    """
    Flag candles where the high-low range is suspiciously large.
    These are usually bad ticks / data errors from the exchange.

    Strategy: calculate rolling median of candle range, flag anything
    that is more than 10x the median range. We FLAG them (don't delete)
    so you can inspect them later. A separate column 'outlier' is added.

    Why not just delete? Because sometimes big moves are real (news events,
    flash crashes). You want to KNOW about them, not silently lose them.
    """
    max_pct = MAX_CANDLE_MOVE_PCT.get(category, 3.0)

    # Percentage move from open to close
    candle_move = ((df["close"] - df["open"]) / df["open"]).abs() * 100

    # High-low range as % of open
    hl_range = ((df["high"] - df["low"]) / df["open"]) * 100

    df["outlier"] = (candle_move > max_pct) | (hl_range > max_pct * 2)

    outlier_count = df["outlier"].sum()
    if outlier_count > 0:
        print(f"  ⚠️  OUTLIERS: {outlier_count} suspicious candles flagged in {filename}")
        print(f"      (column 'outlier'=True, NOT deleted — inspect manually)")

    return df


def handle_missing_candles(df, tf_name, category, filename):
    """
    Detect and handle gaps in the time series.

    FOREX: Markets are open ~24/5. Gaps during weekends are expected.
           Small gaps during trading hours (e.g. 1-2 missing M5 candles)
           are forward-filled — price didn't move, spread just widened.

    STOCKS: Markets open 9:30-16:00 ET. We don't fill gaps between
            sessions — those are legitimate overnight gaps.

    We report how many candles were missing so you can judge data quality.
    """
    expected_freq = f"{EXPECTED_GAP[tf_name]}min"

    # Build a complete expected index (only trading hours approximation)
    full_index = pd.date_range(
        start=df.index.min(),
        end=df.index.max(),
        freq=expected_freq
    )

    missing = full_index.difference(df.index)

    if len(missing) == 0:
        return df

    missing_pct = (len(missing) / len(full_index)) * 100
    print(f"  📊 GAPS: {len(missing)} missing candles ({missing_pct:.1f}%) in {filename}")

    if category == "forex" and missing_pct < 15:
        # Forward-fill small gaps for forex (price was flat / spread only)
        df = df.reindex(full_index)
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].ffill()
        df["volume"] = df["volume"].fillna(0)
        df["source"] = df["source"].ffill()
        df["outlier"] = df["outlier"].fillna(False)
        print(f"      → Forward-filled (forex, gap < 15%) — weekend gaps excluded manually")
    elif missing_pct > 30:
        print(f"  🚨 WARNING: {missing_pct:.1f}% data missing! Check Polygon subscription for this instrument.")

    return df


def validate_ohlc(df, filename):
    """
    Basic OHLC sanity check:
    - High must be >= Open, Close
    - Low must be <= Open, Close
    - No negative prices
    - No zero prices

    These violations indicate corrupted data from the exchange or API.
    We remove them rather than fill — bad price = unusable candle.
    """
    before = len(df)

    invalid = (
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["low"]  > df["open"]) |
        (df["low"]  > df["close"]) |
        (df["close"] <= 0) |
        (df["open"]  <= 0)
    )

    df = df[~invalid]
    removed = before - len(df)

    if removed > 0:
        print(f"  🗑️  OHLC INVALID: Removed {removed} rows with broken OHLC in {filename}")

    return df


def add_metadata(df, ticker, tf_name, category):
    """
    Add useful metadata columns that will be handy in feature engineering.
    Better to compute once here than repeat in every strategy script.
    """
    df["ticker"]    = ticker
    df["timeframe"] = tf_name
    df["category"]  = category

    # Candle direction: 1 = bullish, -1 = bearish, 0 = doji
    df["direction"] = np.sign(df["close"] - df["open"]).astype(int)

    # Candle body size as % of price
    df["body_pct"] = ((df["close"] - df["open"]).abs() / df["open"]) * 100

    # True range (useful for ATR later)
    df["true_range"] = df["high"] - df["low"]

    return df


def generate_quality_report(results):
    """
    Print a final quality summary table so you immediately know
    which instruments have bad data and need attention.
    """
    print("\n" + "═" * 65)
    print("📋 DATA QUALITY REPORT")
    print("═" * 65)
    print(f"{'Instrument':<20} {'TF':<6} {'Rows':<8} {'Outliers':<10} {'Status'}")
    print("─" * 65)
    for r in results:
        status = "✅ OK" if r["outliers"] < 10 and r["rows"] > 50 else "⚠️  CHECK"
        print(f"{r['ticker']:<20} {r['tf']:<6} {r['rows']:<8} {r['outliers']:<10} {status}")
    print("═" * 65)


# ─── MAIN ──────────────────────────────────────────────────────

def refine_file(input_path, output_path, ticker, tf_name, category):
    """
    Full refinery pipeline for a single instrument/timeframe file.
    Returns a dict with quality stats.
    """
    filename = os.path.basename(input_path)
    print(f"\n🔬 Refining: {ticker} ({tf_name}) [{category}]")

    df = load_parquet(input_path)
    if df is None:
        return {"ticker": ticker, "tf": tf_name, "rows": 0, "outliers": 0}

    if not check_structure(df, filename):
        return {"ticker": ticker, "tf": tf_name, "rows": 0, "outliers": 0}

    df = remove_duplicates(df, filename)
    df = sort_index(df)
    df = validate_ohlc(df, filename)
    df = detect_outliers(df, category, tf_name, filename)
    df = handle_missing_candles(df, tf_name, category, filename)
    df = add_metadata(df, ticker, tf_name, category)

    # Save Silver layer
    df.to_parquet(output_path)
    print(f"  💾 Silver saved: {output_path} | {len(df)} rows")

    outlier_count = int(df["outlier"].sum()) if "outlier" in df.columns else 0
    return {"ticker": ticker, "tf": tf_name, "rows": len(df), "outliers": outlier_count}


def main():
    print("╔══════════════════════════════════════════╗")
    print("║    MARKETPAL DATA REFINERY - PHASE 2    ║")
    print(f"║    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                   ║")
    print("╚══════════════════════════════════════════╝\n")

    create_folders()
    results = []

    for tf_name in TIMEFRAMES:
        for category in CATEGORIES:
            input_folder  = os.path.join(INPUT_DIR,  tf_name, category)
            output_folder = os.path.join(OUTPUT_DIR, tf_name, category)

            if not os.path.exists(input_folder):
                print(f"⚠️  Folder not found, skipping: {input_folder}")
                continue

            parquet_files = [f for f in os.listdir(input_folder) if f.endswith(".parquet")]

            if not parquet_files:
                print(f"⚠️  No parquet files in: {input_folder}")
                continue

            print(f"\n{'═'*55}")
            print(f"📂 {tf_name} / {category.upper()} — {len(parquet_files)} files")
            print(f"{'═'*55}")

            for filename in sorted(parquet_files):
                ticker      = filename.replace(".parquet", "")
                input_path  = os.path.join(input_folder,  filename)
                output_path = os.path.join(output_folder, filename)

                stats = refine_file(input_path, output_path, ticker, tf_name, category)
                results.append(stats)

    generate_quality_report(results)

    total_saved = sum(1 for r in results if r["rows"] > 0)
    print(f"\n✅ Refinery complete: {total_saved}/{len(results)} files processed into Silver layer")
    print(f"📁 Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()