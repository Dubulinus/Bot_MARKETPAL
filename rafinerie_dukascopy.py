"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - RAFINERIE DUKASCOPY v1.0                   ║
║     M1 tick data → Silver clean layer                      ║
╚══════════════════════════════════════════════════════════════╝

CO DĚLÁ:
  Čte:  data/02_EXPANDED_RAW/M1/forex/{PAIR}.parquet  (Dukascopy M1)
  Píše: data/03_SILVER_CLEAN/M1/forex/{PAIR}.parquet  (Silver layer)

ROZDÍLY vs Alpaca data (proto potřebujeme vlastní rafinerii):
  ❌ Dukascopy nemá: vwap, trade_count, exchange
  ✅ Dukascopy má:   timestamp, open, high, low, close, volume, symbol
  ✅ Mid price z bid/ask (uloženo jako close)
  ✅ ~1.8M svíček na pár (vs ~500k z Alpaca)

CO PŘIDÁVÁME V SILVER VRSTVĚ:
  - Validace a čištění (outliers, gaps, zero volume)
  - VWAP syntetický (z OHLCV, ne z tick dat)
  - ATR, základní EMA pro regime detection
  - Session labels (Asian/London/NY)
  - Gap detekce (pátek close → pondělí open)
  - Normalizace sloupců na stejný formát jako Alpaca silver
"""

import os
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")

# ─── CONFIG ────────────────────────────────────────────────────
INPUT_DIR  = "data/02_EXPANDED_RAW/M1/forex"
OUTPUT_DIR = "data/03_SILVER_CLEAN"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"]

# Také přidáme M15 a H1 resample (feature_engineering je potřebuje)
RESAMPLE_TFS = {
    "M5":  "5min",
    "M15": "15min",
    "H1":  "1h",
    "H4":  "4h",
}

# Cleaning parametry
MAX_BODY_MULT   = 10.0   # svíčka jejíž tělo > 10× ATR = outlier
MIN_PRICE       = 0.0001 # nulové ceny = chyba
MAX_SPREAD_MULT = 5.0    # high-low > 5× ATR = spike/error
MIN_VOLUME      = 0.0    # objem může být nula (forex = virtuální)

# Session times (UTC)
SESSIONS = {
    "asian":  (0,  8),    # 00:00 - 08:00 UTC
    "london": (7,  16),   # 07:00 - 16:00 UTC
    "ny":     (13, 22),   # 13:00 - 22:00 UTC
    "overlap":(13, 16),   # London + NY overlap (nejlikvidnější)
}


# ═══════════════════════════════════════════════════════════════
# SEKCE 1: ČIŠTĚNÍ DAT
# ═══════════════════════════════════════════════════════════════

def clean_ohlcv(df: pd.DataFrame, pair: str) -> pd.DataFrame:
    """
    Vyčistí surová M1 data od chyb a anomálií.

    Kontroly:
    1. OHLC konzistence (high >= low, close/open uvnitř high-low)
    2. Cenové outliers (spike detekce přes ATR)
    3. Duplicitní timestampy
    4. Zero/negative prices
    5. Víkendové svíčky (forex obchoduje Sun 22:00 - Fri 22:00 UTC)
    """
    original_len = len(df)

    # Zajisti datetime index
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    df.index = pd.to_datetime(df.index)

    # 1. Odstraň duplikáty
    df = df[~df.index.duplicated(keep="first")]

    # 2. Odstraň nulové / záporné ceny
    price_cols = ["open", "high", "low", "close"]
    mask_valid = (df[price_cols] > MIN_PRICE).all(axis=1)
    df = df[mask_valid]

    # 3. OHLC konzistence
    mask_ohlc = (
        (df["high"] >= df["low"]) &
        (df["high"] >= df["open"]) &
        (df["high"] >= df["close"]) &
        (df["low"]  <= df["open"]) &
        (df["low"]  <= df["close"])
    )
    df = df[mask_ohlc]

    # 4. Spike detekce — high-low > MAX_SPREAD_MULT × rolling ATR
    hl_range = df["high"] - df["low"]
    atr_roll = hl_range.rolling(50, min_periods=10).mean()
    mask_spike = hl_range <= (MAX_SPREAD_MULT * atr_roll)
    df = df[mask_spike | atr_roll.isna()]

    # 5. Odstraň víkendové svíčky (Saturday = 5, Sunday = 6)
    # Forex začíná neděle 22:00 UTC — ponech Sunday >= 22:00
    weekday = df.index.dayofweek
    hour    = df.index.hour
    mask_weekend = ~(
        (weekday == 5) |  # celou sobotu pryč
        ((weekday == 6) & (hour < 22))  # neděle před 22:00 pryč
    )
    df = df[mask_weekend]

    removed = original_len - len(df)
    if removed > 0:
        pct = removed / original_len * 100
        print(f"    🧹 Odstraněno {removed} svíček ({pct:.1f}%) — outliers/errors")

    return df


# ═══════════════════════════════════════════════════════════════
# SEKCE 2: SYNTETICKÝ VWAP
# ═══════════════════════════════════════════════════════════════

def add_synthetic_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Syntetický VWAP z OHLCV dat (bez raw tick dat).

    Typický price = (H + L + C) / 3
    VWAP = cumsum(TP × Volume) / cumsum(Volume)
    Resetuje se každý den (pro stocks) nebo každých N hodin (forex).

    Pro forex resetujeme každý den v 00:00 UTC.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3

    # Denní skupiny pro reset
    date_group = df.index.date

    vwap_values = np.zeros(len(df))
    unique_dates = np.unique(date_group)

    for d in unique_dates:
        mask = date_group == d
        tp_d  = tp[mask].values
        vol_d = df["volume"][mask].values

        cum_tpv = np.cumsum(tp_d * vol_d)
        cum_vol = np.cumsum(vol_d)

        # Zabrání dělení nulou (forex může mít nulový objem)
        with np.errstate(divide="ignore", invalid="ignore"):
            vwap_d = np.where(cum_vol > 0, cum_tpv / cum_vol, tp_d)

        vwap_values[mask] = vwap_d

    df["vwap"] = vwap_values
    return df


# ═══════════════════════════════════════════════════════════════
# SEKCE 3: ZÁKLADNÍ INDIKÁTORY (pro regime detection)
# ═══════════════════════════════════════════════════════════════

def add_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Přidá EMA20, EMA50, ATR — potřebné pro regime_analysis v backtest_v3.
    feature_engineering.py přidá zbytek.
    """
    # ATR (True Range)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()

    # EMA pro regime detection
    df["ema_20"] = df["close"].ewm(span=20,  adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema_200"]= df["close"].ewm(span=200, adjust=False).mean()

    return df


# ═══════════════════════════════════════════════════════════════
# SEKCE 4: SESSION LABELS
# ═══════════════════════════════════════════════════════════════

def add_session_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Přidá label tržní seance — důležité pro:
    - Filtrování signálů (EURUSD nejlepší v London+NY overlap)
    - Feature engineering (hour, session jako features)
    - News spike filter (vyhni se prvním 30 min London/NY open)
    """
    hour = df.index.hour

    df["session_asian"]   = ((hour >= 0)  & (hour < 8)).astype(bool)
    df["session_london"]  = ((hour >= 7)  & (hour < 16)).astype(bool)
    df["session_ny"]      = ((hour >= 13) & (hour < 22)).astype(bool)
    df["session_overlap"] = ((hour >= 13) & (hour < 16)).astype(bool)  # nejlepší

    # London open (7:00-7:30) — high volatility, skip signály
    df["london_open"]     = ((hour == 7)).astype(bool)
    # NY open (13:00-13:30)
    df["ny_open"]         = ((hour == 13)).astype(bool)

    return df


# ═══════════════════════════════════════════════════════════════
# SEKCE 5: GAP DETEKCE
# ═══════════════════════════════════════════════════════════════

def add_gap_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detekuje gappy — rozdíl mezi close předchozí svíčky a open aktuální.
    Důležité pro:
    - Risk management (pozice přes noc/víkend)
    - Feature pro ML model (gap = informace)
    """
    prev_close = df["close"].shift(1)
    gap        = (df["open"] - prev_close) / prev_close * 100

    df["gap_pct"]    = gap
    df["gap_up"]     = (gap >  0.05).astype(bool)  # > 0.05%
    df["gap_down"]   = (gap < -0.05).astype(bool)
    df["weekend_gap"]= (
        (df.index.dayofweek == 0) &  # pondělí
        (gap.abs() > 0.1)            # gap > 0.1%
    ).astype(bool)

    return df


# ═══════════════════════════════════════════════════════════════
# SEKCE 6: RESAMPLE NA VYŠŠÍ TIMEFRAMY
# ═══════════════════════════════════════════════════════════════

def resample_to_tf(df_m1: pd.DataFrame, tf_str: str) -> pd.DataFrame:
    """
    Resampleuje M1 data na vyšší timeframe.
    Používá správnou OHLCV agregaci.
    """
    agg = {
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }

    # Resample
    df_tf = df_m1[["open","high","low","close","volume"]].resample(tf_str).agg(agg)
    df_tf = df_tf.dropna(subset=["open", "close"])

    # Přidej indikátory znovu (ATR, EMA jsou závislé na timeframu)
    df_tf = add_base_indicators(df_tf)
    df_tf = add_synthetic_vwap(df_tf)
    df_tf = add_session_labels(df_tf)
    df_tf = add_gap_features(df_tf)

    # Přidej symbol zpět
    if "symbol" in df_m1.columns:
        df_tf["symbol"] = df_m1["symbol"].iloc[0]

    return df_tf


# ═══════════════════════════════════════════════════════════════
# SEKCE 7: VALIDACE VÝSTUPU
# ═══════════════════════════════════════════════════════════════

def validate_silver(df: pd.DataFrame, name: str) -> bool:
    """
    Zkontroluje že silver data mají správný formát.
    assert = zastaví pipeline při nevalidních datech.
    """
    required = ["open", "high", "low", "close", "volume", "vwap", "atr", "ema_20", "ema_50"]
    missing  = [c for c in required if c not in df.columns]

    if missing:
        print(f"    ❌ {name}: chybí sloupce {missing}")
        return False

    if len(df) < 100:
        print(f"    ❌ {name}: příliš málo řádků ({len(df)})")
        return False

    nan_pct = df[required].isna().mean()
    bad     = nan_pct[nan_pct > 0.3]
    if len(bad) > 0:
        print(f"    ⚠️  {name}: vysoké NaN%\n{bad.to_string()}")

    return True


# ═══════════════════════════════════════════════════════════════
# SEKCE 8: HLAVNÍ FUNKCE
# ═══════════════════════════════════════════════════════════════

def process_pair(pair: str) -> dict:
    """Zpracuje jeden forex pár: načti → vyčisti → resampleuj → ulož."""
    input_path = Path(INPUT_DIR) / f"{pair}.parquet"

    if not input_path.exists():
        print(f"  ❌ {pair}: soubor nenalezen ({input_path})")
        print(f"     Spusť nejdřív: python tezba_dukascopy.py")
        return {}

    print(f"\n  📂 {pair}: načítám...")
    df_raw = pd.read_parquet(input_path)

    # Nastav index
    if "timestamp" in df_raw.columns:
        df_raw["timestamp"] = pd.to_datetime(df_raw["timestamp"])
        df_raw = df_raw.set_index("timestamp")
    df_raw.index = pd.to_datetime(df_raw.index)
    df_raw = df_raw.sort_index()

    print(f"    Raw: {len(df_raw):,} M1 svíček | "
          f"{df_raw.index.min().date()} → {df_raw.index.max().date()}")

    # Čištění
    df_m1 = clean_ohlcv(df_raw, pair)

    # Přidej features na M1
    df_m1 = add_base_indicators(df_m1)
    df_m1 = add_synthetic_vwap(df_m1)
    df_m1 = add_session_labels(df_m1)
    df_m1 = add_gap_features(df_m1)
    df_m1["symbol"] = pair

    results = {}

    # Ulož M1
    out_m1 = Path(OUTPUT_DIR) / "M1" / "forex"
    out_m1.mkdir(parents=True, exist_ok=True)
    if validate_silver(df_m1, f"{pair} M1"):
        path = out_m1 / f"{pair}.parquet"
        df_m1.reset_index().to_parquet(path, index=False)
        results["M1"] = len(df_m1)
        print(f"    ✅ M1: {len(df_m1):,} svíček → {path}")

    # Resampleuj na vyšší timeframy
    for tf_name, tf_str in RESAMPLE_TFS.items():
        try:
            df_tf = resample_to_tf(df_m1, tf_str)

            out_tf = Path(OUTPUT_DIR) / tf_name / "forex"
            out_tf.mkdir(parents=True, exist_ok=True)

            if validate_silver(df_tf, f"{pair} {tf_name}"):
                path = out_tf / f"{pair}.parquet"
                df_tf.reset_index().to_parquet(path, index=False)
                results[tf_name] = len(df_tf)
                print(f"    ✅ {tf_name}: {len(df_tf):,} svíček → {path}")

        except Exception as e:
            print(f"    ❌ {pair} {tf_name}: {e}")

    return results


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║      MARKETPAL RAFINERIE DUKASCOPY v1.0            ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                          ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    print(f"  Input:  {INPUT_DIR}/")
    print(f"  Output: {OUTPUT_DIR}/\n")

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    all_results = {}
    for pair in PAIRS:
        results = process_pair(pair)
        all_results[pair] = results

    # Souhrn
    print(f"\n{'═'*55}")
    print("  SOUHRN")
    print(f"{'═'*55}")
    print(f"  {'Pár':<10}", end="")
    for tf in ["M1"] + list(RESAMPLE_TFS.keys()):
        print(f"  {tf:>8}", end="")
    print()
    print(f"  {'─'*55}")

    for pair, results in all_results.items():
        print(f"  {pair:<10}", end="")
        for tf in ["M1"] + list(RESAMPLE_TFS.keys()):
            n = results.get(tf, 0)
            print(f"  {n:>8,}" if n else f"  {'—':>8}", end="")
        print()

    print(f"\n  💡 Další krok:")
    print(f"     python feature_engineering.py")
    print(f"     python feature_engineering_v2.py")
    print(f"     python triple_barrier.py")


if __name__ == "__main__":
    main()