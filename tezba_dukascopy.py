"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - DUKASCOPY DOWNLOADER v1.0                  ║
║     10 let M1 OHLCV dat pro EURUSD/GBPUSD/USDJPY/USDCHF   ║
╚══════════════════════════════════════════════════════════════╝

Dukascopy poskytuje ZDARMA tick data od ~2003.
URL: https://datafeed.dukascopy.com/datafeed/{PAIR}/{YEAR}/{MONTH}/{DAY}/{HOUR}h_ticks.bi5

bi5 formát = LZMA + 5x uint32 per tick:
  time_ms, ask*10^5, bid*10^5, ask_vol, bid_vol

VÝSTUP:
  data/02_EXPANDED_RAW/M1/forex/{PAIR}.parquet
  (kompatibilní s pipeline: rafinerie → feature_eng → triple_barrier)

RESUME: přerušené stahování pokračuje automaticky z cache.
"""

import os
import struct
import lzma
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from time import sleep

OUTPUT_DIR = "data/02_EXPANDED_RAW/M1/forex"
CACHE_DIR  = "data/00_DUKASCOPY_CACHE"

PAIRS      = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"]
START_DATE = datetime(2016, 1, 1)
END_DATE   = datetime(2025, 12, 31)

BASE_URL = "https://datafeed.dukascopy.com/datafeed/{pair}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

MAX_RETRIES   = 3
RETRY_DELAY   = 2
REQUEST_DELAY = 0.05

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer":    "https://www.dukascopy.com/",
}


def download_bi5(pair, year, month, day, hour):
    # Dukascopy měsíce jsou 0-indexed (leden = 00)
    url = BASE_URL.format(
        pair=pair, year=year,
        month=month - 1,
        day=day, hour=hour
    )
    for _ in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200 and len(r.content) > 0:
                return r.content
            elif r.status_code == 404:
                return None
            sleep(RETRY_DELAY)
        except Exception:
            sleep(RETRY_DELAY)
    return None


def decode_bi5(data_bytes, pair, year, month, day, hour):
    try:
        raw = lzma.decompress(data_bytes)
    except Exception:
        return None

    if len(raw) % 20 != 0 or len(raw) == 0:
        return None

    n      = len(raw) // 20
    ticks  = struct.unpack(f">{n * 5}I", raw)
    pv     = 1000 if "JPY" in pair else 100_000
    base   = datetime(year, month, day, hour)
    rows   = []

    for i in range(n):
        t       = ticks[i * 5]
        ask     = ticks[i * 5 + 1] / pv
        bid     = ticks[i * 5 + 2] / pv
        av      = ticks[i * 5 + 3] / 1_000_000
        bv      = ticks[i * 5 + 4] / 1_000_000
        mid     = (ask + bid) / 2
        ts      = base + timedelta(milliseconds=t)
        rows.append((ts, mid, ask, bid, (av + bv) / 2))

    if not rows:
        return None
    return pd.DataFrame(rows, columns=["timestamp", "mid", "ask", "bid", "volume"])


def ticks_to_m1(df_ticks):
    df = df_ticks.set_index("timestamp")
    ohlcv          = df["mid"].resample("1min").ohlc()
    ohlcv["volume"]= df["volume"].resample("1min").sum()
    ohlcv          = ohlcv.dropna(subset=["open"])
    ohlcv.columns  = ["open", "high", "low", "close", "volume"]
    return ohlcv.reset_index()


def download_pair(pair, start_date, end_date):
    out_path  = Path(OUTPUT_DIR) / f"{pair}.parquet"
    cache_dir = Path(CACHE_DIR) / pair
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  📥 {pair}: {start_date.date()} → {end_date.date()}")

    existing = set(f.stem for f in cache_dir.glob("*.parquet"))
    all_m1   = []
    current  = start_date
    total    = (end_date - start_date).days
    n_dl = n_cache = n_empty = 0

    while current <= end_date:
        key = current.strftime("%Y%m%d")

        if current.weekday() >= 5:          # víkend
            current += timedelta(days=1)
            continue

        day_num = (current - start_date).days
        if day_num % 50 == 0:
            pct = day_num / total * 100
            print(f"    {pct:4.0f}% | {current.date()} | "
                  f"⬇{n_dl} 💾{n_cache} ∅{n_empty}    ", end="\r")

        cache_file = cache_dir / f"{key}.parquet"
        if key in existing and cache_file.exists():
            try:
                df_c = pd.read_parquet(cache_file)
                if not df_c.empty:
                    all_m1.append(df_c)
                n_cache += 1
            except Exception:
                pass
            current += timedelta(days=1)
            continue

        # Stáhni 24 hodin
        day_ticks = []
        for hour in range(24):
            raw = download_bi5(pair, current.year, current.month, current.day, hour)
            if raw:
                df_t = decode_bi5(raw, pair, current.year, current.month, current.day, hour)
                if df_t is not None and not df_t.empty:
                    day_ticks.append(df_t)
            sleep(REQUEST_DELAY)

        if day_ticks:
            df_m1 = ticks_to_m1(pd.concat(day_ticks, ignore_index=True))
            if not df_m1.empty:
                df_m1.to_parquet(cache_file, index=False)
                all_m1.append(df_m1)
                n_dl += 1
            else:
                n_empty += 1
                pd.DataFrame(columns=["timestamp","open","high","low","close","volume"]).to_parquet(cache_file, index=False)
        else:
            n_empty += 1
            pd.DataFrame(columns=["timestamp","open","high","low","close","volume"]).to_parquet(cache_file, index=False)

        current += timedelta(days=1)

    print(f"\n    ✅ Hotovo: ⬇{n_dl} dní | 💾{n_cache} cache | ∅{n_empty} prázdných")

    if not all_m1:
        print(f"    ❌ Žádná data")
        return None

    df = pd.concat(all_m1, ignore_index=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df["symbol"] = pair

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)

    print(f"    💾 {out_path}")
    print(f"    📊 {len(df):,} M1 svíček | {df['timestamp'].min().date()} → {df['timestamp'].max().date()}")
    return df


def quick_test():
    print("  🧪 Test spojení (EURUSD 2024-01-02 10h)...")
    raw = download_bi5("EURUSD", 2024, 1, 2, 10)
    if not raw:
        print("  ❌ Nelze se připojit k Dukascopy")
        return False
    df = decode_bi5(raw, "EURUSD", 2024, 1, 2, 10)
    if df is None or df.empty:
        print("  ❌ Dekódování selhalo")
        return False
    m1 = ticks_to_m1(df)
    print(f"  ✅ OK — {len(df)} ticků → {len(m1)} M1 svíček")
    return True


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║      MARKETPAL DUKASCOPY DOWNLOADER v1.0           ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                          ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    print(f"  Páry:    {', '.join(PAIRS)}")
    print(f"  Období:  {START_DATE.date()} → {END_DATE.date()}")
    print(f"  Output:  {OUTPUT_DIR}/")
    print(f"  Cache:   {CACHE_DIR}/  (resume-friendly)\n")
    print(f"  ⏱  Odhadovaný čas: 30-90 min / pár\n")

    if not quick_test():
        print("\n  Zkontroluj připojení nebo zkus za chvíli.")
        return

    print()
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    results = {}
    for pair in PAIRS:
        df = download_pair(pair, START_DATE, END_DATE)
        if df is not None:
            results[pair] = len(df)

    print(f"\n{'═'*50}")
    print("  SOUHRN")
    print(f"{'═'*50}")
    for pair, n in results.items():
        print(f"  {pair:<8} {n:>10,} M1 svíček")

    print(f"\n  💡 Další krok (spusť v pořadí):")
    print(f"     python rafinerie_polygon.py")
    print(f"     python feature_engineering.py")
    print(f"     python feature_engineering_v2.py")
    print(f"     python triple_barrier.py")
    print(f"     python backtest_v3.py")


if __name__ == "__main__":
    main()