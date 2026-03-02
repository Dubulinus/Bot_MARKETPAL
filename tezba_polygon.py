"""
╔══════════════════════════════════════════════════════════════╗
║          MARKETPAL - DATA MINING - POLYGON.IO               ║
║          Phase 2 | M5/M15/H1 | Forex + Nasdaq              ║
╚══════════════════════════════════════════════════════════════╝

PRIMARY SOURCE: Polygon.io (free tier)
FALLBACK SOURCE: Yahoo Finance (yfinance) - automatic if Polygon fails

Architecture note:
    We use a "waterfall" approach - try Polygon first, fall back to
    Yahoo Finance if Polygon returns no data or throws an error.
    This gives us redundancy without paying for a second API key.

Rate limits:
    Polygon free tier = 5 requests/minute → sleep 13s between calls
    Yahoo Finance = no official limit but be respectful
"""

import os
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional, Dict
from dotenv import load_dotenv
from polygon import RESTClient

# ─── CONFIG ────────────────────────────────────────────────────

load_dotenv()
API_KEY = os.getenv("POLYGON_API_KEY")

if not API_KEY:
    raise ValueError("❌ POLYGON_API_KEY not found in .env file!")

# Instruments to download
INSTRUMENTS = {
    "forex": [
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        "USD/CHF",
    ],
    "stocks": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
    ]
}

# Timeframes (Polygon format: multiplier + timespan)
TIMEFRAMES = {
    "M5":  {"multiplier": 5,  "timespan": "minute"},
    "M15": {"multiplier": 15, "timespan": "minute"},
    "H1":  {"multiplier": 1,  "timespan": "hour"},
}

# Yahoo Finance equivalent timeframe strings (for fallback)
YAHOO_INTERVALS = {
    "M5":  "5m",
    "M15": "15m",
    "H1":  "1h",
}

# How many days of history to download
DAYS_BACK = 30

# Output directory
OUTPUT_DIR = "data/02_POLYGON_RAW"

# ─── HELPERS ───────────────────────────────────────────────────

def create_folders():
    """Create output folder structure if it doesn't exist."""
    for tf in TIMEFRAMES.keys():
        for category in INSTRUMENTS.keys():
            path = os.path.join(OUTPUT_DIR, tf, category)
            os.makedirs(path, exist_ok=True)
    print(f"✅ Folder structure ready at: {OUTPUT_DIR}")


def date_to_str(d):
    return d.strftime("%Y-%m-%d")


def ticker_to_filename(ticker):
    """EUR/USD → EURUSD"""
    return ticker.replace("/", "").replace("-", "")


# ─── POLYGON FETCHERS ──────────────────────────────────────────

def fetch_forex_polygon(client, pair, tf_name, tf_config, date_from, date_to):
    """
    Fetch forex OHLCV data from Polygon.io.
    Polygon forex ticker format: C:EURUSD
    """
    ticker = "C:" + ticker_to_filename(pair)

    try:
        print(f"  📡 [POLYGON] Fetching {pair} ({tf_name}) from {date_from} to {date_to}...")

        aggs = []
        for bar in client.list_aggs(
            ticker=ticker,
            multiplier=tf_config["multiplier"],
            timespan=tf_config["timespan"],
            from_=date_from,
            to=date_to,
            adjusted=True,
            limit=50000
        ):
            aggs.append({
                "timestamp":    pd.to_datetime(bar.timestamp, unit="ms"),
                "open":         bar.open,
                "high":         bar.high,
                "low":          bar.low,
                "close":        bar.close,
                "volume":       bar.volume,
                "vwap":         bar.vwap,
                "transactions": bar.transactions,
                "source":       "polygon"
            })

        if not aggs:
            print(f"  ⚠️  [POLYGON] No data returned for {pair} ({tf_name})")
            return None

        df = pd.DataFrame(aggs)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        print(f"  ✅ [POLYGON] {pair} ({tf_name}): {len(df)} candles downloaded")
        return df

    except Exception as e:
        print(f"  ❌ [POLYGON] Error fetching {pair}: {e}")
        return None


def fetch_stocks_polygon(client, ticker, tf_name, tf_config, date_from, date_to):
    """
    Fetch stock OHLCV data from Polygon.io.
    """
    try:
        print(f"  📡 [POLYGON] Fetching {ticker} ({tf_name}) from {date_from} to {date_to}...")

        aggs = []
        for bar in client.list_aggs(
            ticker=ticker,
            multiplier=tf_config["multiplier"],
            timespan=tf_config["timespan"],
            from_=date_from,
            to=date_to,
            adjusted=True,
            limit=50000
        ):
            aggs.append({
                "timestamp":    pd.to_datetime(bar.timestamp, unit="ms"),
                "open":         bar.open,
                "high":         bar.high,
                "low":          bar.low,
                "close":        bar.close,
                "volume":       bar.volume,
                "vwap":         bar.vwap,
                "transactions": bar.transactions,
                "source":       "polygon"
            })

        if not aggs:
            print(f"  ⚠️  [POLYGON] No data returned for {ticker} ({tf_name})")
            return None

        df = pd.DataFrame(aggs)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        print(f"  ✅ [POLYGON] {ticker} ({tf_name}): {len(df)} candles downloaded")
        return df

    except Exception as e:
        print(f"  ❌ [POLYGON] Error fetching {ticker}: {e}")
        return None


# ─── YAHOO FINANCE FALLBACK ────────────────────────────────────

def fetch_yahoo_fallback(ticker, tf_name, days_back):
    """
    Fallback data source using Yahoo Finance (yfinance).

    Yahoo quirks:
    - Forex tickers: EURUSD=X, GBPUSD=X etc.
    - Intraday data (< 1h) only available for last 60 days
    - Free, no API key needed, but less reliable for forex

    We add a 'source' column so we always know where data came from.
    This is important for data quality tracking later.
    """
    yahoo_ticker = ticker
    if "/" in ticker:
        # EUR/USD → EURUSD=X
        yahoo_ticker = ticker.replace("/", "") + "=X"

    yahoo_interval = YAHOO_INTERVALS.get(tf_name, "15m")
    period = f"{min(days_back, 59)}d"

    try:
        print(f"  🔄 [YAHOO] Fallback for {ticker} ({tf_name})...")
        raw = yf.download(
            yahoo_ticker,
            period=period,
            interval=yahoo_interval,
            progress=False,
            auto_adjust=True
        )

        if raw.empty:
            print(f"  ❌ [YAHOO] No data for {ticker} either. Skipping.")
            return None

        # Standardize column names to lowercase
        raw.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                       for c in raw.columns]
        raw.index.name = "timestamp"
        raw["source"] = "yahoo_fallback"

        print(f"  ✅ [YAHOO] {ticker} ({tf_name}): {len(raw)} candles (fallback)")
        return raw

    except Exception as e:
        print(f"  ❌ [YAHOO] Fallback also failed for {ticker}: {e}")
        return None


# ─── SAVE DATA ─────────────────────────────────────────────────

def save_data(df, category, ticker, tf_name):
    """
    Save DataFrame as Parquet (main) + CSV (backup/debug).

    Why Parquet?
    - Compressed: ~5-10x smaller than CSV
    - Fast to read: columnar format, perfect for pandas
    - Preserves dtypes: no silent type conversion like CSV

    Why also CSV?
    - Human readable, easy to inspect in Excel
    - Good for debugging data quality issues
    """
    name = ticker_to_filename(ticker)
    base_path = os.path.join(OUTPUT_DIR, tf_name, category, name)

    df.to_parquet(f"{base_path}.parquet")
    df.to_csv(f"{base_path}.csv")

    source = df["source"].iloc[0] if "source" in df.columns else "unknown"
    print(f"  💾 Saved: {base_path}.parquet | {len(df)} rows | source: {source}")


# ─── MAIN ──────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║    MARKETPAL DATA MINING - PHASE 2      ║")
    print(f"║    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                   ║")
    print("╚══════════════════════════════════════════╝\n")

    create_folders()
    client = RESTClient(API_KEY)

    date_to   = datetime.now()
    date_from = date_to - timedelta(days=DAYS_BACK)
    from_str  = date_to_str(date_from)
    to_str    = date_to_str(date_to)

    print(f"📅 Date range: {from_str} → {to_str} ({DAYS_BACK} days)")
    print(f"⚠️  Rate limit: 13s sleep between calls (Polygon free tier = 5 req/min)\n")

    total_ok   = 0
    total_fail = 0
    used_yahoo = 0

    # ── FOREX ──────────────────────────────────────────────────
    print("═" * 55)
    print("💱 FOREX")
    print("═" * 55)

    for tf_name, tf_config in TIMEFRAMES.items():
        print(f"\n⏱️  Timeframe: {tf_name}")
        for pair in INSTRUMENTS["forex"]:

            df = fetch_forex_polygon(client, pair, tf_name, tf_config, from_str, to_str)

            if df is None:
                df = fetch_yahoo_fallback(pair, tf_name, DAYS_BACK)
                if df is not None:
                    used_yahoo += 1

            if df is not None:
                save_data(df, "forex", pair, tf_name)
                total_ok += 1
            else:
                total_fail += 1

            time.sleep(13)  # Respect Polygon free tier rate limit

    # ── STOCKS (NASDAQ) ────────────────────────────────────────
    print("\n" + "═" * 55)
    print("📈 STOCKS - NASDAQ")
    print("═" * 55)

    for tf_name, tf_config in TIMEFRAMES.items():
        print(f"\n⏱️  Timeframe: {tf_name}")
        for ticker in INSTRUMENTS["stocks"]:

            df = fetch_stocks_polygon(client, ticker, tf_name, tf_config, from_str, to_str)

            if df is None:
                df = fetch_yahoo_fallback(ticker, tf_name, DAYS_BACK)
                if df is not None:
                    used_yahoo += 1

            if df is not None:
                save_data(df, "stocks", ticker, tf_name)
                total_ok += 1
            else:
                total_fail += 1

            time.sleep(13)

    # ── SUMMARY ────────────────────────────────────────────────
    print("\n╔══════════════════════════════════════════╗")
    print("║              MINING SUMMARY             ║")
    print(f"║  ✅ Successfully saved:  {total_ok:<16}║")
    print(f"║  🔄 Yahoo fallback used: {used_yahoo:<16}║")
    print(f"║  ❌ Failed (both):       {total_fail:<16}║")
    print("╚══════════════════════════════════════════╝")

    if used_yahoo > 0:
        print(f"\n⚠️  Note: {used_yahoo} datasets came from Yahoo fallback.")
        print("   Check the 'source' column in your data files.")
        print("   Yahoo forex data can have gaps during weekends/holidays.")


if __name__ == "__main__":
    main()