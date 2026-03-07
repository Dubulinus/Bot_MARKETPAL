"""
╔══════════════════════════════════════════════════════════════╗
║          MARKETPAL - DATA MINING - POLYGON.IO               ║
║          v2 | RAM-safe | 2-5 let historie                   ║
╚══════════════════════════════════════════════════════════════╝

ZMĚNY v2:
    BUG FIX:  DAYS_BACK byl 30 → teď 730 (M5/M15) a 1825 (H1)
    RAM-SAFE: každý blok se okamžitě zapíše na disk a uvolní z RAM
              v RAM je vždy jen 1 blok (~2-5 MB) — ghetto-server safe
    BLOK:     Polygon vrací max 50000 svíček na request
              → M5 blok = 180 dní, H1 blok = 365 dní

PIPELINE (nezměněno):
    tezba_polygon.py  → data/02_POLYGON_RAW
    rafinerie_polygon.py → data/03_SILVER_CLEAN
    feature_engineering.py → data/04_GOLD_FEATURES
"""

import os
import gc
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from dotenv import load_dotenv
from polygon import RESTClient

# ─── CONFIG ────────────────────────────────────────────────────

load_dotenv()
API_KEY = os.getenv("POLYGON_API_KEY")

if not API_KEY:
    raise ValueError("❌ POLYGON_API_KEY not found in .env file!")

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

TIMEFRAMES = {
    # DAYS_BACK = kolik let historie
    # BLOCK_DAYS = max dní na 1 Polygon request (50k svíček limit)
    #   M5:  180 dní × 5min × 8h = ~43k svíček → bezpečně pod 50k
    #   M15: 365 dní × 15min × 8h = ~35k svíček → ok
    #   H1:  365 dní × 24h = ~8k svíček → ok
    "M5":  {"multiplier": 5,  "timespan": "minute", "days_back": 730,  "block_days": 180},
    "M15": {"multiplier": 15, "timespan": "minute", "days_back": 730,  "block_days": 365},
    "H1":  {"multiplier": 1,  "timespan": "hour",   "days_back": 1825, "block_days": 365},
}

YAHOO_INTERVALS = {
    "M5":  "5m",
    "M15": "15m",
    "H1":  "1h",
}

OUTPUT_DIR = "data/02_POLYGON_RAW"
SLEEP_SEC  = 13   # Polygon free tier = 5 req/min → 12s + buffer


# ─── HELPERS ───────────────────────────────────────────────────

def create_folders():
    for tf in TIMEFRAMES:
        for cat in INSTRUMENTS:
            os.makedirs(os.path.join(OUTPUT_DIR, tf, cat), exist_ok=True)
    print(f"✅ Složky připraveny: {OUTPUT_DIR}")


def ticker_to_filename(ticker):
    return ticker.replace("/", "").replace("-", "")


def date_to_str(d):
    return d.strftime("%Y-%m-%d")


# ─── POLYGON BLOCK FETCHER ─────────────────────────────────────

def fetch_polygon_blocks(client, polygon_ticker, tf_name, tf_cfg,
                         category, local_name, out_path):
    """
    Stahuje data po blocích — každý blok okamžitě zapíše na disk.
    V RAM je vždy jen 1 blok. Na konci spojí bloky.
    Vrátí True pokud OK, False pokud selhalo.
    """
    multiplier = tf_cfg["multiplier"]
    timespan   = tf_cfg["timespan"]
    block_days = tf_cfg["block_days"]
    days_back  = tf_cfg["days_back"]

    end_date   = datetime.now()
    start_date = end_date - timedelta(days=days_back)

    # Dočasná složka pro bloky
    base_dir = os.path.dirname(out_path)
    tmp_dir  = os.path.join(base_dir, f"_tmp_{local_name}")
    os.makedirs(tmp_dir, exist_ok=True)

    block_start = start_date
    block_num   = 0
    total_rows  = 0

    print(f"    Stahuji {days_back} dní po blocích {block_days} dní...")

    while block_start < end_date:
        block_end = min(block_start + timedelta(days=block_days), end_date)

        try:
            aggs = []
            for bar in client.list_aggs(
                ticker     = polygon_ticker,
                multiplier = multiplier,
                timespan   = timespan,
                from_      = date_to_str(block_start),
                to         = date_to_str(block_end),
                adjusted   = True,
                limit      = 50000,
            ):
                aggs.append({
                    "timestamp":    pd.to_datetime(bar.timestamp, unit="ms"),
                    "open":         bar.open,
                    "high":         bar.high,
                    "low":          bar.low,
                    "close":        bar.close,
                    "volume":       getattr(bar, "volume",       0),
                    "vwap":         getattr(bar, "vwap",         None),
                    "transactions": getattr(bar, "transactions", None),
                    "source":       "polygon",
                })

            if aggs:
                df_block = pd.DataFrame(aggs)
                df_block = df_block.dropna(subset=["open", "high", "low", "close"])
                df_block = df_block[df_block["close"] > 0]

                if len(df_block) > 0:
                    block_path = os.path.join(tmp_dir, f"block_{block_num:04d}.parquet")
                    df_block.to_parquet(block_path, index=False, compression="snappy")
                    total_rows += len(df_block)
                    block_num  += 1
                    print(f"    blok {block_num:02d} "
                          f"({date_to_str(block_start)} → {date_to_str(block_end)}) "
                          f"= {len(df_block):,} svíček", end="\r")

                del df_block, aggs
                gc.collect()

        except Exception as e:
            print(f"\n    ⚠️  Blok {date_to_str(block_start)}: {e}")

        block_start = block_end + timedelta(days=1)
        time.sleep(SLEEP_SEC)

    if block_num == 0:
        # Ukliď prázdný tmp
        try:
            os.rmdir(tmp_dir)
        except Exception:
            pass
        return False

    # Spoj bloky na disku — max 10 najednou v RAM
    print(f"\n    Spojuji {block_num} bloků → {os.path.basename(out_path)} ...",
          end="", flush=True)

    chunk_list = []
    for fname in sorted(os.listdir(tmp_dir)):
        if not fname.endswith(".parquet"):
            continue
        chunk = pd.read_parquet(os.path.join(tmp_dir, fname))
        chunk_list.append(chunk)
        if len(chunk_list) >= 10:
            merged = pd.concat(chunk_list, ignore_index=True)
            chunk_list = [merged]
            del merged
            gc.collect()

    if not chunk_list:
        return False

    final_df = pd.concat(chunk_list, ignore_index=True)
    final_df = final_df.drop_duplicates(subset=["timestamp"])
    final_df = final_df.sort_values("timestamp").reset_index(drop=True)

    # Nastav index jako timestamp (zachová kompatibilitu s rafinerie_polygon.py)
    final_df = final_df.set_index("timestamp")
    final_df.to_parquet(out_path, compression="snappy")
    final_df.to_csv(out_path.replace(".parquet", ".csv"))

    ts    = final_df.index
    years = (ts.max() - ts.min()).days / 365
    print(f" ✅ {len(final_df):,} svíček | {years:.1f} let "
          f"({str(ts.min())[:10]} → {str(ts.max())[:10]})")

    del final_df, chunk_list
    gc.collect()

    # Ukliď tmp bloky
    for fname in os.listdir(tmp_dir):
        os.remove(os.path.join(tmp_dir, fname))
    os.rmdir(tmp_dir)

    return True


# ─── YAHOO FALLBACK (nezměněno) ────────────────────────────────

def fetch_yahoo_fallback(ticker, tf_name, days_back):
    yahoo_ticker = ticker
    if "/" in ticker:
        yahoo_ticker = ticker.replace("/", "") + "=X"

    yahoo_interval = YAHOO_INTERVALS.get(tf_name, "15m")
    period = f"{min(days_back, 59)}d"

    try:
        print(f"    🔄 [YAHOO fallback] {ticker} ({tf_name})...")
        raw = yf.download(
            yahoo_ticker,
            period     = period,
            interval   = yahoo_interval,
            progress   = False,
            auto_adjust= True,
        )

        if raw.empty:
            print(f"    ❌ [YAHOO] Žádná data.")
            return None

        raw.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                       for c in raw.columns]
        raw.index.name = "timestamp"
        raw["source"] = "yahoo_fallback"
        print(f"    ✅ [YAHOO] {len(raw)} svíček (fallback, pouze {period})")
        return raw

    except Exception as e:
        print(f"    ❌ [YAHOO] Selhalo: {e}")
        return None


# ─── MAIN ──────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║    MARKETPAL DATA MINING v2             ║")
    print(f"║    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  RAM-safe: 1 blok v RAM najednou (~2-5 MB)\n")

    create_folders()
    client = RESTClient(API_KEY)

    total_ok   = 0
    total_fail = 0
    used_yahoo = 0

    for category, instruments in INSTRUMENTS.items():
        print(f"\n{'═'*55}")
        print(f"  {'💱 FOREX' if category == 'forex' else '📈 STOCKS'}")
        print(f"{'═'*55}")

        for tf_name, tf_cfg in TIMEFRAMES.items():
            print(f"\n  ── {tf_name} ({tf_cfg['days_back']} dní = "
                  f"{tf_cfg['days_back']//365:.1f} let) ──")

            for instrument in instruments:
                local_name = ticker_to_filename(instrument)
                print(f"\n  ► {instrument}")

                out_dir  = os.path.join(OUTPUT_DIR, tf_name, category)
                out_path = os.path.join(out_dir, f"{local_name}.parquet")

                # Pokud existuje a je čerstvé (< 24h), přeskoč
                if os.path.exists(out_path):
                    age_h = (time.time() - os.path.getmtime(out_path)) / 3600
                    if age_h < 24:
                        try:
                            n = len(pd.read_parquet(out_path, columns=["open"]))
                            print(f"    ✓ existuje ({n:,} svíček, {age_h:.0f}h staré) — přeskočeno")
                            total_ok += 1
                            continue
                        except Exception:
                            pass

                # Polygon ticker formát
                if category == "forex":
                    polygon_ticker = "C:" + local_name
                else:
                    polygon_ticker = instrument

                ok = fetch_polygon_blocks(
                    client, polygon_ticker, tf_name, tf_cfg,
                    category, local_name, out_path
                )

                if not ok:
                    print(f"    ❌ Polygon selhal → zkouším Yahoo fallback...")
                    df_yahoo = fetch_yahoo_fallback(instrument, tf_name, 59)
                    if df_yahoo is not None:
                        df_yahoo.to_parquet(out_path, compression="snappy")
                        df_yahoo.to_csv(out_path.replace(".parquet", ".csv"))
                        del df_yahoo
                        gc.collect()
                        used_yahoo += 1
                        total_ok   += 1
                    else:
                        total_fail += 1
                else:
                    total_ok += 1

                gc.collect()

    print(f"\n{'═'*55}")
    print(f"  SOUHRN")
    print(f"{'═'*55}")
    print(f"  ✅ Staženo:          {total_ok}")
    print(f"  🔄 Yahoo fallback:   {used_yahoo}")
    print(f"  ❌ Selhalo:          {total_fail}")
    print(f"""
  DALŠÍ KROKY:
    python rafinerie_polygon.py   → vyčisti data
    python feature_engineering.py → vypočti indikátory
    python data_audit.py          → ověř kolik let dat máš
    python triple_barrier.py      → přegeneruj labely
    python meta_labeling.py       → natrénuj modely
  """)


if __name__ == "__main__":
    main()