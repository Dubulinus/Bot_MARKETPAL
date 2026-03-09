"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - FEATURE ENGINEERING v2                  ║
║         Nové indikátory: Volume, Momentum, Volatility        ║
╚══════════════════════════════════════════════════════════════╝

CO PŘIBÝVÁ oproti v1 (52 featur):
    Volume:     OBV, MFI, Volume Z-score, Volume spike
    Momentum:   Stochastic %K/%D, Williams %R, CCI, ROC, CMO
    Volatility: Keltner Channels, Donchian Channels, ATR ratio
    Pattern:    Inside bar, Engulfing, Pin bar, Doji
    Composite:  RSI + Volume kombinace, BB + Stoch kombinace

NOVÉ SIGNÁLY (k testování v edge matrix):
    signal_stoch_oversold_exit   — Stoch < 20 pak kříží nahoru
    signal_stoch_overbought_exit — Stoch > 80 pak kříží dolů
    signal_cci_oversold          — CCI < -100 reversal
    signal_cci_overbought        — CCI > +100 reversal
    signal_volume_spike_bull     — objem > 2x průměr + zelená svíčka
    signal_volume_spike_bear     — objem > 2x průměr + červená svíčka
    signal_mfi_oversold          — MFI < 20 (RSI s volume)
    signal_mfi_overbought        — MFI > 80
    signal_keltner_breakout_up   — cena probíjí horní Keltner band
    signal_keltner_breakout_down — cena probíjí dolní Keltner band
    signal_donchian_break_up     — nové N-denní high (momentum)
    signal_donchian_break_down   — nové N-denní low
    signal_pin_bar_bull          — pin bar se spodním knitem (reversal)
    signal_pin_bar_bear          — pin bar s horním knitem (reversal)
    signal_engulfing_bull        — bullish engulfing pattern
    signal_engulfing_bear        — bearish engulfing pattern
    signal_inside_bar_break_up   — inside bar probití nahoru
    signal_inside_bar_break_down — inside bar probití dolů
    signal_roc_bull              — Rate of Change překračuje 0 zdola
    signal_williams_oversold     — Williams %R < -80 reversal
    signal_williams_overbought   — Williams %R > -20 reversal
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# ─── CONFIG ────────────────────────────────────────────────────

INPUT_DIR  = "data/04_GOLD_FEATURES"   # čteme existující Gold data
OUTPUT_DIR = "data/04_GOLD_FEATURES"   # přepisujeme na místě (přidáváme sloupce)

TIMEFRAMES  = ["M5", "M15", "H1"]
CATEGORIES  = {"forex": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"],
               "stocks": ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "GOOGL", "AMD"]}

ALT_DIR = "data/12_ALTERNATIVE"

def load_alternative_data():
    """Načte FRED makro + COT forex data pro merge do každého tickeru."""
    alt = {}

    # FRED makro
    fred_path = Path(ALT_DIR) / "fred_macro.parquet"
    if fred_path.exists():
        df_fred = pd.read_parquet(fred_path)
        df_fred.index = pd.to_datetime(df_fred.index).normalize()  # jen datum, bez času
        alt["fred"] = df_fred
        print(f"  📊 FRED načten: {len(df_fred.columns)} features")
    else:
        print(f"  ⚠️  FRED nenalezen: {fred_path}")

    # COT forex
    cot_path = Path(ALT_DIR) / "cot_forex.parquet"
    if cot_path.exists():
        df_cot = pd.read_parquet(cot_path)
        df_cot.index = pd.to_datetime(df_cot.index).normalize()
        alt["cot"] = df_cot
        print(f"  📊 COT načten: {len(df_cot.columns)} features")
    else:
        print(f"  ⚠️  COT nenalezen: {cot_path}")

    return alt


def merge_alternative_data(df, alt, ticker, category):
    """
    Mergne FRED + COT do tickerového DataFrame.

    Logika:
    - df má datetime index (intraday svíčky)
    - FRED/COT má denní index
    - Merge přes normalize() → left join → forward fill
    """
    if not alt:
        return df

    # Vytvoř denní klíč ze svíčkového indexu
    df_dates = pd.to_datetime(df.index).normalize()

    # FRED — pro všechny tickery
    if "fred" in alt:
        df_fred = alt["fred"].reindex(df_dates).values
        fred_cols = alt["fred"].columns.tolist()
        for i, col in enumerate(fred_cols):
            df[f"fred_{col}"] = df_fred[:, i]

    # COT — jen pro forex
    if "cot" in alt and category == "forex":
        df_cot = alt["cot"].reindex(df_dates).values
        cot_cols = alt["cot"].columns.tolist()
        for i, col in enumerate(cot_cols):
            df[f"cot_{col}"] = df_cot[:, i]

        # Přidej COT signály jako boolean signal_ sloupce
        # (triple_barrier je bude testovat automaticky)
        for pair in ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"]:
            long_col  = f"cot_{pair}_cot_long"
            short_col = f"cot_{pair}_cot_short"
            if long_col in df.columns:
                df[f"signal_cot_{pair}_long"]  = df[long_col].fillna(0).astype(bool)
                df[f"signal_cot_{pair}_short"] = df[short_col].fillna(0).astype(bool)

    # Forward fill pro NaN (víkend gaps atd.)
    alt_cols = [c for c in df.columns if c.startswith("fred_") or c.startswith("cot_")]
    if alt_cols:
        df[alt_cols] = df[alt_cols].ffill()

    return df

# Parametry indikátorů
STOCH_K     = 14    # Stochastic %K perioda
STOCH_D     = 3     # Stochastic %D vyhlazení
CCI_PERIOD  = 20    # CCI perioda
MFI_PERIOD  = 14    # Money Flow Index perioda
ROC_PERIOD  = 10    # Rate of Change perioda
KELTNER_EMA = 20    # Keltner Channel EMA
KELTNER_ATR = 2.0   # Keltner Channel ATR multiplikátor
DONCHIAN_N  = 20    # Donchian Channel perioda
VOL_MA      = 20    # Volume moving average pro spike detekci
VOL_SPIKE   = 2.0   # Kolikrát musí objem překročit průměr

# ─── VOLUME INDIKÁTORY ─────────────────────────────────────────

def add_obv(df):
    """On-Balance Volume — kumulativní volume podle směru svíčky."""
    direction = np.sign(df["close"] - df["close"].shift(1))
    obv = (direction * df["volume"]).fillna(0).cumsum()
    df["obv"]          = obv
    df["obv_ma"]       = obv.rolling(20).mean()
    df["obv_rising"]   = obv > obv.shift(3)   # OBV roste → akumulace
    return df


def add_mfi(df, period=MFI_PERIOD):
    """
    Money Flow Index — RSI ale s objemem.
    MFI < 20 = oversold, MFI > 80 = overbought.
    """
    tp  = (df["high"] + df["low"] + df["close"]) / 3   # typical price
    rmf = tp * df["volume"]                              # raw money flow

    pos_mf = rmf.where(tp > tp.shift(1), 0)
    neg_mf = rmf.where(tp < tp.shift(1), 0)

    pos_sum = pos_mf.rolling(period).sum()
    neg_sum = neg_mf.rolling(period).sum()

    mfr = pos_sum / neg_sum.replace(0, np.nan)
    df["mfi"] = 100 - (100 / (1 + mfr))
    return df


def add_volume_features(df):
    """Volume spike detekce a Z-score."""
    vol_ma  = df["volume"].rolling(VOL_MA).mean()
    vol_std = df["volume"].rolling(VOL_MA).std()

    df["volume_ma"]      = vol_ma
    df["volume_zscore"]  = (df["volume"] - vol_ma) / vol_std.replace(0, np.nan)
    df["volume_ratio"]   = df["volume"] / vol_ma.replace(0, np.nan)

    is_spike = df["volume_ratio"] > VOL_SPIKE
    is_green  = df["close"] > df["open"]
    is_red    = df["close"] < df["open"]

    df["signal_volume_spike_bull"] = (is_spike & is_green).astype(bool)
    df["signal_volume_spike_bear"] = (is_spike & is_red).astype(bool)
    return df


# ─── MOMENTUM INDIKÁTORY ───────────────────────────────────────

def add_stochastic(df, k=STOCH_K, d=STOCH_D):
    """
    Stochastic Oscillator %K a %D.
    Klasický mean-reversion indikátor.
    """
    lowest_low   = df["low"].rolling(k).min()
    highest_high = df["high"].rolling(k).max()
    hl_range     = (highest_high - lowest_low).replace(0, np.nan)

    stoch_k = 100 * (df["close"] - lowest_low) / hl_range
    stoch_d = stoch_k.rolling(d).mean()

    df["stoch_k"] = stoch_k
    df["stoch_d"] = stoch_d

    # Signály: kříží ze zóny
    prev_k = stoch_k.shift(1)
    prev_d = stoch_d.shift(1)

    # Oversold exit: byl pod 20, %K kříží %D nahoru
    df["signal_stoch_oversold_exit"]   = (
        (prev_k < 20) & (stoch_k > stoch_d) & (prev_k <= prev_d)
    ).astype(bool)

    # Overbought exit: byl nad 80, %K kříží %D dolů
    df["signal_stoch_overbought_exit"] = (
        (prev_k > 80) & (stoch_k < stoch_d) & (prev_k >= prev_d)
    ).astype(bool)

    return df


def add_williams_r(df, period=14):
    """
    Williams %R — podobný Stochastics ale invertovaný.
    -80 až -100 = oversold, -0 až -20 = overbought.
    """
    highest_high = df["high"].rolling(period).max()
    lowest_low   = df["low"].rolling(period).min()
    hl_range     = (highest_high - lowest_low).replace(0, np.nan)

    wr = -100 * (highest_high - df["close"]) / hl_range
    df["williams_r"] = wr

    df["signal_williams_oversold"]   = (
        (wr.shift(1) < -80) & (wr > -80)
    ).astype(bool)

    df["signal_williams_overbought"] = (
        (wr.shift(1) > -20) & (wr < -20)
    ).astype(bool)

    return df


def add_cci(df, period=CCI_PERIOD):
    """
    Commodity Channel Index.
    CCI > +100 = overbought, CCI < -100 = oversold.
    """
    tp      = (df["high"] + df["low"] + df["close"]) / 3
    tp_ma   = tp.rolling(period).mean()
    # Mean Absolute Deviation
    mad     = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci     = (tp - tp_ma) / (0.015 * mad.replace(0, np.nan))

    df["cci"] = cci

    df["signal_cci_oversold"]   = (
        (cci.shift(1) < -100) & (cci > -100)
    ).astype(bool)

    df["signal_cci_overbought"] = (
        (cci.shift(1) > 100) & (cci < 100)
    ).astype(bool)

    return df


def add_roc(df, period=ROC_PERIOD):
    """Rate of Change — momentum indikátor."""
    roc = 100 * (df["close"] - df["close"].shift(period)) / df["close"].shift(period)
    df["roc"] = roc

    df["signal_roc_bull"] = (
        (roc.shift(1) < 0) & (roc > 0)
    ).astype(bool)

    df["signal_roc_bear"] = (
        (roc.shift(1) > 0) & (roc < 0)
    ).astype(bool)

    return df


# ─── VOLATILITY INDIKÁTORY ─────────────────────────────────────

def add_keltner_channels(df, ema_period=KELTNER_EMA, atr_mult=KELTNER_ATR):
    """
    Keltner Channels — ATR-based pasma.
    Podobné Bollinger Bands ale používají ATR místo std.
    """
    ema  = df["close"].ewm(span=ema_period).mean()
    atr  = df.get("atr", (df["high"] - df["low"]).rolling(14).mean())

    upper = ema + atr_mult * atr
    lower = ema - atr_mult * atr

    df["keltner_upper"]  = upper
    df["keltner_middle"] = ema
    df["keltner_lower"]  = lower

    # Breakout signály
    df["signal_keltner_breakout_up"]   = (
        (df["close"].shift(1) <= upper.shift(1)) & (df["close"] > upper)
    ).astype(bool)

    df["signal_keltner_breakout_down"] = (
        (df["close"].shift(1) >= lower.shift(1)) & (df["close"] < lower)
    ).astype(bool)

    # Squeeze: Keltner užší než BB → exploze čeká
    if "bb_upper" in df.columns and "bb_lower" in df.columns:
        bb_width      = df["bb_upper"] - df["bb_lower"]
        kelt_width    = upper - lower
        df["squeeze"] = (bb_width < kelt_width).astype(bool)

    return df


def add_donchian_channels(df, period=DONCHIAN_N):
    """
    Donchian Channels — highest high / lowest low za N svíček.
    Probití = momentum breakout signál.
    """
    upper = df["high"].rolling(period).max()
    lower = df["low"].rolling(period).min()
    mid   = (upper + lower) / 2

    df["donchian_upper"]  = upper
    df["donchian_lower"]  = lower
    df["donchian_middle"] = mid

    # Breakout = nové N-perioda high/low
    df["signal_donchian_break_up"]   = (
        df["high"] >= upper.shift(1)
    ).astype(bool)

    df["signal_donchian_break_down"] = (
        df["low"] <= lower.shift(1)
    ).astype(bool)

    return df


def add_atr_ratio(df):
    """ATR ratio — aktuální volatilita vs průměrná."""
    if "atr" not in df.columns:
        return df
    atr_ma = df["atr"].rolling(50).mean()
    df["atr_ratio"] = df["atr"] / atr_ma.replace(0, np.nan)
    df["high_volatility"] = (df["atr_ratio"] > 1.5).astype(bool)
    df["low_volatility"]  = (df["atr_ratio"] < 0.7).astype(bool)
    return df


# ─── PATTERN RECOGNITION ───────────────────────────────────────

def add_candlestick_patterns(df):
    """
    Základní svíčkové patterny.
    Marcos: nepouž je samotné, kombinuj s potvrzením (volume, trend).
    """
    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]

    body      = (c - o).abs()
    candle_range = h - l
    upper_wick = h - c.where(c > o, o)
    lower_wick = c.where(c > o, o) - l

    # Pin bar bull — dlouhý dolní knítek, malé tělo nahoře
    df["signal_pin_bar_bull"] = (
        (lower_wick > 2 * body) &
        (lower_wick > 0.6 * candle_range) &
        (body > 0)
    ).astype(bool)

    # Pin bar bear — dlouhý horní knítek, malé tělo dole
    df["signal_pin_bar_bear"] = (
        (upper_wick > 2 * body) &
        (upper_wick > 0.6 * candle_range) &
        (body > 0)
    ).astype(bool)

    # Engulfing bull — červená pak větší zelená
    prev_red   = o.shift(1) > c.shift(1)
    curr_green = c > o
    engulf_b   = c > o.shift(1)
    engulf_b2  = o < c.shift(1)
    df["signal_engulfing_bull"] = (prev_red & curr_green & engulf_b & engulf_b2).astype(bool)

    # Engulfing bear — zelená pak větší červená
    prev_green = c.shift(1) > o.shift(1)
    curr_red   = o > c
    engulf_be  = o > c.shift(1)
    engulf_be2 = c < o.shift(1)
    df["signal_engulfing_bear"] = (prev_green & curr_red & engulf_be & engulf_be2).astype(bool)

    # Inside bar — celá svíčka uvnitř předchozí (komprese = breakout čeká)
    inside = (h < h.shift(1)) & (l > l.shift(1))
    # Breakout z inside baru
    df["signal_inside_bar_break_up"]   = (inside.shift(1) & (h > h.shift(2))).astype(bool)
    df["signal_inside_bar_break_down"] = (inside.shift(1) & (l < l.shift(2))).astype(bool)

    # Doji — velmi malé tělo
    df["doji"] = (body < 0.1 * candle_range).astype(bool)

    return df


# ─── KOMBINOVANÉ SIGNÁLY ───────────────────────────────────────

def add_composite_signals(df):
    """
    Kombinace více indikátorů = silnější signal.
    Marcos: jeden indikátor je šum, kombinace je edge.
    """
    # RSI oversold + Volume spike = silný reversal
    if "signal_rsi_oversold" in df.columns:
        df["signal_rsi_vol_bull"] = (
            df.get("signal_rsi_oversold", False) &
            df.get("signal_volume_spike_bull", False)
        ).astype(bool)

    # Stoch overbought + Pin bar bear = silný short signal
    if "signal_stoch_overbought_exit" in df.columns:
        df["signal_stoch_pin_bear"] = (
            df.get("signal_stoch_overbought_exit", False) &
            df.get("signal_pin_bar_bear", False)
        ).astype(bool)

    # MFI oversold + Engulfing bull
    if "mfi" in df.columns:
        df["signal_mfi_oversold"]   = (df["mfi"] < 20).astype(bool)
        df["signal_mfi_overbought"] = (df["mfi"] > 80).astype(bool)

        df["signal_mfi_engulf_bull"] = (
            df.get("signal_mfi_oversold", False) &
            df.get("signal_engulfing_bull", False)
        ).astype(bool)

    # Donchian breakout + high volume = momentum continuation
    if "signal_donchian_break_up" in df.columns:
        df["signal_donchian_vol_bull"] = (
            df.get("signal_donchian_break_up", False) &
            (df.get("volume_ratio", 0) > 1.5)
        ).astype(bool)

        df["signal_donchian_vol_bear"] = (
            df.get("signal_donchian_break_down", False) &
            (df.get("volume_ratio", 0) > 1.5)
        ).astype(bool)

    return df


# ─── HLAVNÍ FUNKCE ─────────────────────────────────────────────

def add_all_new_features(df):
    """Přidá všechny nové v2 featury do DataFrame."""
    df = add_obv(df)
    df = add_mfi(df)
    df = add_volume_features(df)
    df = add_stochastic(df)
    df = add_williams_r(df)
    df = add_cci(df)
    df = add_roc(df)
    df = add_keltner_channels(df)
    df = add_donchian_channels(df)
    df = add_atr_ratio(df)
    df = add_candlestick_patterns(df)
    df = add_composite_signals(df)
    return df


def count_new_signals(df):
    """Spočítej nové signal_ sloupce."""
    return [c for c in df.columns if c.startswith("signal_")]


def process_file(path, alt, category):
    """Načte Gold parquet, přidá nové featury + alternative data, uloží zpět."""
    df = pd.read_parquet(path)
    original_cols = len(df.columns)

    # Technické indikátory (existující)
    df = add_all_new_features(df)

    # Alternative data merge (nové)
    ticker = path.stem  # název souboru bez .parquet
    df = merge_alternative_data(df, alt, ticker, category)

    new_cols    = len(df.columns) - original_cols
    new_signals = count_new_signals(df)

    df.to_parquet(path, index=False)
    return new_cols, len(new_signals)



def main():
    print("╔══════════════════════════════════════════╗")
    print("║   MARKETPAL FEATURE ENGINEERING v2      ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  Přidávám: Volume, Momentum, Volatility, Patterns\n")
    print("  Načítám alternative data (FRED + COT)...")
    alt = load_alternative_data()
    print()
    total_files   = 0
    total_ok      = 0
    all_new_cols  = 0
    signal_count  = 0

    for tf in TIMEFRAMES:
        print(f"\n⏱️  Timeframe: {tf}")
        for category, tickers in CATEGORIES.items():
            for ticker in tickers:
                path = Path(INPUT_DIR) / tf / category / f"{ticker}.parquet"
                if not path.exists():
                    print(f"  ⚠️  {ticker}: soubor nenalezen ({path})")
                    continue

                total_files += 1
                try:
                    new_cols, n_signals = process_file(path, alt, category)
                    all_new_cols += new_cols
                    signal_count  = n_signals  # stejné pro všechny
                    total_ok     += 1
                    print(f"  ✅ {ticker:8} +{new_cols} featur, {n_signals} signálů celkem")
                except Exception as e:
                    print(f"  ❌ {ticker}: {e}")

    print(f"\n{'═'*45}")
    print(f"📋 SOUHRN")
    print(f"{'═'*45}")
    print(f"  Souborů zpracováno: {total_ok}/{total_files}")
    print(f"  Nových featur:      +{all_new_cols // max(total_ok, 1)} na soubor")
    print(f"  Signálů celkem:     {signal_count}")
    print(f"\n  💡 Další krok: spusť edge_matrix.py pro nové signály")


if __name__ == "__main__":
    main()