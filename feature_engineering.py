"""
╔══════════════════════════════════════════════════════════════╗
║       MARKETPAL - FEATURE ENGINEERING - GOLD LAYER          ║
║       Phase 2 | Technical Indicators + Signal Columns       ║
╚══════════════════════════════════════════════════════════════╝

PIPELINE:
    Silver (clean OHLCV)
        → Trend indicators    (SMA, EMA, MACD)
        → Momentum indicators (RSI, Stochastic, ROC)
        → Volatility          (ATR, Bollinger Bands, std)
        → Volume              (VWAP, OBV, volume ratio)
        → Price structure     (swing highs/lows, candle patterns)
        → Signal columns      (crossovers, overbought/oversold)
        → Gold (feature-rich Parquet, ready for backtest + DRL)

WHY GOLD LAYER?
    You compute indicators ONCE here, not inside every strategy.
    Your backtester, DRL agent, and live bot all read from the same
    Gold files. Change an indicator param? Rerun this script once.
    Everything downstream gets updated automatically. Clean architecture.

INDICATOR REFERENCE (for when you forget what each does):
    SMA   - Simple Moving Average. Trend direction, support/resistance.
    EMA   - Exponential MA. Reacts faster to recent price than SMA.
    MACD  - Momentum oscillator. Crossover of two EMAs. Trend changes.
    RSI   - Relative Strength Index. 0-100. >70 overbought, <30 oversold.
    ATR   - Average True Range. Volatility measure. Used for stop-loss sizing.
    BB    - Bollinger Bands. Price envelope 2 std dev from SMA. Breakouts.
    VWAP  - Volume Weighted Average Price. Institutional reference price.
    OBV   - On-Balance Volume. Confirms if volume supports price move.
    Stoch - Stochastic Oscillator. Like RSI but uses high/low range.
    ROC   - Rate of Change. Raw momentum. How fast price is moving.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime

# ─── CONFIG ────────────────────────────────────────────────────

INPUT_DIR  = "data/03_SILVER_CLEAN"
OUTPUT_DIR = "data/04_GOLD_FEATURES"

TIMEFRAMES = ["M5", "M15", "H1"]
CATEGORIES = ["forex", "stocks"]

# Indicator parameters — tweak these when searching for edge
# These are the standard "textbook" values used by most traders
PARAMS = {
    "sma_fast":       10,    # Fast SMA — reacts quickly to price
    "sma_slow":       50,    # Slow SMA — trend filter
    "sma_trend":      200,   # Long-term trend direction
    "ema_fast":       12,    # EMA for MACD calculation
    "ema_slow":       26,    # EMA for MACD calculation
    "ema_signal":     9,     # MACD signal line smoothing
    "rsi_period":     14,    # Standard RSI period
    "atr_period":     14,    # ATR for volatility / stop-loss
    "bb_period":      20,    # Bollinger Bands SMA period
    "bb_std":         2.0,   # Bollinger Bands std deviation multiplier
    "stoch_k":        14,    # Stochastic %K period
    "stoch_d":        3,     # Stochastic %D smoothing
    "roc_period":     10,    # Rate of Change period
    "obv_ema":        20,    # OBV smoothing period
    "swing_lookback": 5,     # Bars left/right to confirm swing high/low
}

# ─── INDICATOR FUNCTIONS ───────────────────────────────────────
# Each function takes a DataFrame and returns it with new columns added.
# We never modify a column that already exists — always add new ones.

def add_sma(df):
    """
    Simple Moving Average — average closing price over N periods.
    SMA_fast < price < SMA_slow = potential uptrend.
    Price below SMA_trend (200) = bearish regime overall.
    """
    df[f"sma_{PARAMS['sma_fast']}"]  = df["close"].rolling(PARAMS["sma_fast"]).mean()
    df[f"sma_{PARAMS['sma_slow']}"]  = df["close"].rolling(PARAMS["sma_slow"]).mean()
    df[f"sma_{PARAMS['sma_trend']}"] = df["close"].rolling(PARAMS["sma_trend"]).mean()
    return df


def add_ema(df):
    """
    Exponential Moving Average — like SMA but gives more weight to recent candles.
    Reacts faster to price changes. Better for detecting early trend shifts.
    """
    df[f"ema_{PARAMS['ema_fast']}"] = df["close"].ewm(span=PARAMS["ema_fast"], adjust=False).mean()
    df[f"ema_{PARAMS['ema_slow']}"] = df["close"].ewm(span=PARAMS["ema_slow"], adjust=False).mean()
    return df


def add_macd(df):
    """
    MACD = EMA(12) - EMA(26)
    Signal = EMA(9) of MACD
    Histogram = MACD - Signal

    When MACD crosses above Signal → bullish momentum
    When MACD crosses below Signal → bearish momentum
    Histogram growing = momentum accelerating
    """
    ema_fast   = df["close"].ewm(span=PARAMS["ema_fast"],   adjust=False).mean()
    ema_slow   = df["close"].ewm(span=PARAMS["ema_slow"],   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal     = macd_line.ewm(span=PARAMS["ema_signal"],   adjust=False).mean()

    df["macd"]           = macd_line
    df["macd_signal"]    = signal
    df["macd_histogram"] = macd_line - signal
    return df


def add_rsi(df):
    """
    RSI measures speed and magnitude of price changes.
    Scale: 0-100.
        > 70 = overbought (potential sell)
        < 30 = oversold   (potential buy)
        50   = neutral

    We also add rsi_zone column for easy signal generation:
        'overbought', 'oversold', 'neutral'
    """
    delta  = df["close"].diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)

    avg_gain = gain.ewm(com=PARAMS["rsi_period"] - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=PARAMS["rsi_period"] - 1, adjust=False).mean()

    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    df["rsi"] = rsi

    # Zone classification — useful for DRL state representation
    df["rsi_zone"] = "neutral"
    df.loc[df["rsi"] > 70, "rsi_zone"] = "overbought"
    df.loc[df["rsi"] < 30, "rsi_zone"] = "oversold"

    return df


def add_atr(df):
    """
    ATR = Average True Range. Measures volatility.
    True Range = max of:
        - High - Low
        - |High - Previous Close|
        - |Low  - Previous Close|

    WHY IT MATTERS FOR FTMO:
        Stop-loss sizing. If ATR(14) on EURUSD M15 = 0.0015,
        you know average candle volatility is 15 pips.
        Your stop should be at least 1-2x ATR away to avoid noise.

    atr_pct = ATR as % of price → comparable across instruments
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs()
    ], axis=1).max(axis=1)

    df["atr"]     = tr.ewm(com=PARAMS["atr_period"] - 1, adjust=False).mean()
    df["atr_pct"] = (df["atr"] / df["close"]) * 100   # Volatility as % of price
    return df


def add_bollinger_bands(df):
    """
    Bollinger Bands = SMA ± (2 × std deviation of last 20 closes)

    Price near upper band = overbought / breakout candidate
    Price near lower band = oversold / breakdown candidate
    Bands squeezing together = low volatility, big move incoming

    bb_position: 0 = at lower band, 0.5 = at middle, 1 = at upper band
    bb_squeeze:  True when bands are unusually narrow (volatility contraction)
    """
    sma   = df["close"].rolling(PARAMS["bb_period"]).mean()
    std   = df["close"].rolling(PARAMS["bb_period"]).std()

    df["bb_upper"]  = sma + (PARAMS["bb_std"] * std)
    df["bb_middle"] = sma
    df["bb_lower"]  = sma - (PARAMS["bb_std"] * std)
    df["bb_width"]  = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]

    # Where is price within the bands? 0 = lower, 1 = upper
    band_range = df["bb_upper"] - df["bb_lower"]
    df["bb_position"] = (df["close"] - df["bb_lower"]) / band_range.replace(0, np.nan)

    # Squeeze = band width in bottom 20th percentile of last 100 candles
    bb_width_rolling_min = df["bb_width"].rolling(100).quantile(0.20)
    df["bb_squeeze"] = df["bb_width"] <= bb_width_rolling_min

    return df


def add_stochastic(df):
    """
    Stochastic Oscillator — where is current close relative to recent range?
    %K = (Close - Lowest Low) / (Highest High - Lowest Low) × 100
    %D = 3-period SMA of %K (signal line)

    Like RSI but uses high/low range instead of price momentum.
    Best used in combination with RSI for confirmation.
    """
    low_min  = df["low"].rolling(PARAMS["stoch_k"]).min()
    high_max = df["high"].rolling(PARAMS["stoch_k"]).max()

    stoch_k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    stoch_d = stoch_k.rolling(PARAMS["stoch_d"]).mean()

    df["stoch_k"] = stoch_k
    df["stoch_d"] = stoch_d
    return df


def add_roc(df):
    """
    Rate of Change — how much has price moved in the last N candles, in %.
    Positive = upward momentum, Negative = downward momentum.
    Good for detecting acceleration/deceleration of trends.
    """
    df["roc"] = df["close"].pct_change(periods=PARAMS["roc_period"]) * 100
    return df


def add_vwap(df):
    """
    VWAP = Volume Weighted Average Price.
    The 'fair value' price institutions use as reference.

    Price above VWAP = bullish bias (buyers in control)
    Price below VWAP = bearish bias (sellers in control)

    Note: VWAP is most meaningful intraday. We compute it as
    a rolling VWAP over the last 20 candles — a proxy that works
    across timeframes without session reset logic.

    vwap_distance_pct: how far price is from VWAP in %
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_volume     = typical_price * df["volume"]

    rolling_tpv = tp_volume.rolling(20).sum()
    rolling_vol = df["volume"].rolling(20).sum()

    df["vwap"] = rolling_tpv / rolling_vol.replace(0, np.nan)
    df["vwap_distance_pct"] = ((df["close"] - df["vwap"]) / df["vwap"]) * 100
    return df


def add_obv(df):
    """
    On-Balance Volume — cumulative volume indicator.
    If price closes up → add volume. If closes down → subtract volume.

    Rising OBV with rising price = trend confirmed by volume (strong)
    Rising price but falling OBV = divergence, potential reversal

    obv_ema: smoothed OBV to reduce noise
    obv_trend: is OBV trending up or down vs its own EMA?
    """
    obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    df["obv"]     = obv
    df["obv_ema"] = obv.ewm(span=PARAMS["obv_ema"], adjust=False).mean()
    df["obv_trend"] = np.sign(df["obv"] - df["obv_ema"]).astype(int)
    return df


def add_swing_points(df):
    """
    Detect swing highs and lows — the building blocks of market structure.
    A swing high = candle whose high is higher than N candles left and right.
    A swing low  = candle whose low  is lower  than N candles left and right.

    Why this matters: Support/resistance, trend structure, DRL state.
    swing_high / swing_low = True at the pivot candle.
    """
    n = PARAMS["swing_lookback"]
    highs = df["high"]
    lows  = df["low"]

    # Rolling max/min over the window centered on each candle
    swing_high = (highs == highs.rolling(2 * n + 1, center=True).max())
    swing_low  = (lows  == lows.rolling(2 * n + 1, center=True).min())

    df["swing_high"] = swing_high
    df["swing_low"]  = swing_low
    return df


def add_signal_columns(df):
    """
    Pre-computed signal columns — boolean flags that strategies can use directly.
    These combine multiple indicators into ready-to-use trading signals.

    Think of these as the "observations" your DRL agent will read.
    Having them pre-computed means the agent doesn't need to calculate
    anything — it just reads True/False values. Faster training, cleaner code.
    """
    sma_f = f"sma_{PARAMS['sma_fast']}"
    sma_s = f"sma_{PARAMS['sma_slow']}"

    # SMA Golden Cross: fast crosses above slow (bullish)
    df["signal_golden_cross"] = (
        (df[sma_f] > df[sma_s]) &
        (df[sma_f].shift(1) <= df[sma_s].shift(1))
    )

    # SMA Death Cross: fast crosses below slow (bearish)
    df["signal_death_cross"] = (
        (df[sma_f] < df[sma_s]) &
        (df[sma_f].shift(1) >= df[sma_s].shift(1))
    )

    # MACD bullish crossover
    df["signal_macd_bull"] = (
        (df["macd"] > df["macd_signal"]) &
        (df["macd"].shift(1) <= df["macd_signal"].shift(1))
    )

    # MACD bearish crossover
    df["signal_macd_bear"] = (
        (df["macd"] < df["macd_signal"]) &
        (df["macd"].shift(1) >= df["macd_signal"].shift(1))
    )

    # RSI reversal signals
    df["signal_rsi_oversold_exit"]    = (df["rsi"] > 30) & (df["rsi"].shift(1) <= 30)
    df["signal_rsi_overbought_exit"]  = (df["rsi"] < 70) & (df["rsi"].shift(1) >= 70)

    # Bollinger breakout signals
    df["signal_bb_breakout_up"]   = df["close"] > df["bb_upper"]
    df["signal_bb_breakout_down"] = df["close"] < df["bb_lower"]

    # Price above/below VWAP
    df["signal_above_vwap"] = df["close"] > df["vwap"]

    # Trend regime: is price above the 200 SMA? (long-term bull/bear)
    sma_trend_col = f"sma_{PARAMS['sma_trend']}"
    if sma_trend_col in df.columns:
        df["signal_bull_regime"] = df["close"] > df[sma_trend_col]
    else:
        df["signal_bull_regime"] = np.nan   # Not enough data for 200 SMA

    return df


# ─── PIPELINE ──────────────────────────────────────────────────

def run_feature_pipeline(df, ticker, tf_name):
    """
    Run all indicator functions in order.
    Order matters: some indicators (signals) depend on earlier ones.
    """
    print(f"  ⚙️  Computing indicators for {ticker} ({tf_name})...")

    df = add_sma(df)
    df = add_ema(df)
    df = add_macd(df)
    df = add_rsi(df)
    df = add_atr(df)
    df = add_bollinger_bands(df)
    df = add_stochastic(df)
    df = add_roc(df)
    df = add_vwap(df)
    df = add_obv(df)
    df = add_swing_points(df)
    df = add_signal_columns(df)   # Must be last — uses columns from above

    # How many NaN rows at the start? (indicator warmup period)
    # SMA 200 needs 200 rows before it has a valid value.
    # We report this so you know how much data is "wasted" on warmup.
    warmup_rows = df["rsi"].isna().sum()
    total_cols  = len(df.columns)

    print(f"  ✅ {ticker} ({tf_name}): {total_cols} columns | {len(df)} rows | {warmup_rows} warmup NaN rows")

    return df


# ─── MAIN ──────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║  MARKETPAL FEATURE ENGINEERING - GOLD   ║")
    print(f"║  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                    ║")
    print("╚══════════════════════════════════════════╝\n")

    # Create output folders
    for tf in TIMEFRAMES:
        for cat in CATEGORIES:
            os.makedirs(os.path.join(OUTPUT_DIR, tf, cat), exist_ok=True)
    print(f"✅ Gold layer folders ready at: {OUTPUT_DIR}\n")

    total_ok   = 0
    total_fail = 0
    summary    = []

    for tf_name in TIMEFRAMES:
        for category in CATEGORIES:
            input_folder  = os.path.join(INPUT_DIR,  tf_name, category)
            output_folder = os.path.join(OUTPUT_DIR, tf_name, category)

            if not os.path.exists(input_folder):
                print(f"⚠️  Folder not found, skipping: {input_folder}")
                continue

            parquet_files = sorted([f for f in os.listdir(input_folder) if f.endswith(".parquet")])

            if not parquet_files:
                print(f"⚠️  No files in: {input_folder}")
                continue

            print(f"{'═'*55}")
            print(f"📂 {tf_name} / {category.upper()} — {len(parquet_files)} files")
            print(f"{'═'*55}")

            for filename in parquet_files:
                ticker       = filename.replace(".parquet", "")
                input_path   = os.path.join(input_folder,  filename)
                output_path  = os.path.join(output_folder, filename)

                try:
                    df = pd.read_parquet(input_path)

                    if len(df) < PARAMS["sma_trend"] + 10:
                        print(f"  ⚠️  {ticker} ({tf_name}): only {len(df)} rows — too short for SMA200, skipping")
                        total_fail += 1
                        continue

                    df = run_feature_pipeline(df, ticker, tf_name)
                    df.to_parquet(output_path)

                    summary.append({
                        "ticker":   ticker,
                        "tf":       tf_name,
                        "rows":     len(df),
                        "columns":  len(df.columns),
                        "signals":  len([c for c in df.columns if c.startswith("signal_")])
                    })
                    total_ok += 1

                except Exception as e:
                    print(f"  ❌ Failed: {ticker} ({tf_name}): {e}")
                    total_fail += 1

    # ── SUMMARY ────────────────────────────────────────────────
    print(f"\n{'═'*65}")
    print("📋 GOLD LAYER SUMMARY")
    print(f"{'═'*65}")
    print(f"{'Instrument':<20} {'TF':<6} {'Rows':<8} {'Columns':<10} {'Signals'}")
    print(f"{'─'*65}")
    for r in summary:
        print(f"{r['ticker']:<20} {r['tf']:<6} {r['rows']:<8} {r['columns']:<10} {r['signals']}")
    print(f"{'═'*65}")

    print(f"\n✅ Gold layer complete: {total_ok} files processed")
    if total_fail > 0:
        print(f"⚠️  {total_fail} files skipped (too short for indicators)")
    print(f"📁 Output: {OUTPUT_DIR}")
    print(f"\n💡 Next step: edge_matrix.py — find which signals actually have edge")


if __name__ == "__main__":
    main()