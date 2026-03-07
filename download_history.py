"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - DOWNLOADER (RAM-SAFE)                      ║
║     Ghetto-server edition — starý HDD, málo RAM            ║
╚══════════════════════════════════════════════════════════════╝

ZMĚNY OPROTI STANDARDNÍ VERZI:
    • Data jdou na D:\  (volných 270 GB)
    • Každý blok se okamžitě zapíše na disk a uvolní z RAM
    • Nikdy nedržíme v RAM více než 1 blok najednou (~2-5 MB)
    • Po každém tickeru garbage collector manuálně vyčistí RAM
    • Komprese parquet = snads 70% méně místa na disku

ODHADOVANÁ VELIKOST NA DISKU (po kompresi):
    M5  × 8 tickerů × 2 roky  ≈ 150 MB celkem
    M15 × 8 tickerů × 2 roky  ≈  50 MB celkem
    H1  × 8 tickerů × 5 let   ≈  20 MB celkem
    CELKEM: ~220 MB  (bezpečně vejde na C: i D:)
"""

import os
import gc
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    print("❌ Nainstaluj: pip install yfinance pyarrow")
    exit(1)

# ── Výstupní adresář: D:\ má 270 GB volných ───────────────────
OUTPUT_DIR = r"D:\MARKETPAL_DATA\01_RAW"

# Cesta k projektu na C: (kam pipeline čte Gold Features)
# Downloader uloží na D:, pipeline musí vědět kde číst
# → buď uprav INPUT_DIR v pipeline, nebo použij symlink
# → nejjednodušší: nastav OUTPUT_DIR stejně jako INPUT_DIR v pipeline
# Pokud chceš rovnou do projektu na C::
# OUTPUT_DIR = r"C:\Bot_MARKETPAL\data\01_RAW"

TICKERS = {
    "stocks": {
        "AAPL":   "AAPL",
        "MSFT":   "MSFT",
        "NVDA":   "NVDA",
        "AMZN":   "AMZN",
    },
    "forex": {
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
        "USDJPY": "USDJPY=X",
        "USDCHF": "USDCHF=X",
    }
}

TIMEFRAMES = {
    "M5": {
        "yf_interval": "5m",
        "years_back":  2,
        "block_days":  59,      # Yahoo limit pro intraday = 60 dní
    },
    "M15": {
        "yf_interval": "15m",
        "years_back":  2,
        "block_days":  59,
    },
    "H1": {
        "yf_interval": "1h",
        "years_back":  5,
        "block_days":  729,     # Yahoo limit pro H1 = 730 dní
    },
}

SLEEP_SEC = 1.5   # pauza mezi requesty — nezabij server ani Yahoo


def download_and_save(local_name, yf_symbol, tf_name, tf_cfg):
    """
    Stáhne data PO JEDNOM BLOKU, okamžitě zapíše na disk.
    V RAM je vždy jen 1 blok (~pár MB).
    Na konci spojí bloky na disku (ne v RAM).
    """
    interval   = tf_cfg["yf_interval"]
    years_back = tf_cfg["years_back"]
    block_days = tf_cfg["block_days"]

    end_date   = datetime.now()
    start_date = end_date - timedelta(days=int(years_back * 365))

    # Dočasná složka pro bloky
    out_dir  = Path(OUTPUT_DIR) / tf_name / ("stocks" if local_name in ["AAPL","MSFT","NVDA","AMZN"] else "forex")
    tmp_dir  = out_dir / f"_tmp_{local_name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{local_name}.parquet"

    # Pokud finální soubor existuje a je čerstvý, přeskoč
    if out_path.exists():
        age_h = (time.time() - out_path.stat().st_mtime) / 3600
        if age_h < 24:
            df_check = pd.read_parquet(out_path, columns=["close"])
            rows = len(df_check)
            del df_check
            gc.collect()
            print(f"    ✓ existuje ({rows:,} řádků, {age_h:.0f}h staré) — přeskočeno")
            return True

    # Stáhni blok po bloku, každý okamžitě zapiš
    block_start = start_date
    block_num   = 0
    total_rows  = 0

    while block_start < end_date:
        block_end = min(block_start + timedelta(days=block_days), end_date)

        try:
            ticker_obj = yf.Ticker(yf_symbol)
            df_block = ticker_obj.history(
                start       = block_start.strftime("%Y-%m-%d"),
                end         = block_end.strftime("%Y-%m-%d"),
                interval    = interval,
                auto_adjust = True,
                prepost     = False,
            )

            if df_block is not None and len(df_block) > 0:
                # Normalizace přímo na bloku — žádné hromadění v RAM
                df_block = df_block.rename(columns={
                    "Open": "open", "High": "high",
                    "Low":  "low",  "Close": "close", "Volume": "volume"
                })
                df_block.index = pd.to_datetime(df_block.index, utc=True)
                df_block.index = df_block.index.tz_convert("Europe/Prague")
                df_block["timestamp"] = df_block.index

                keep = ["timestamp", "open", "high", "low", "close", "volume"]
                df_block = df_block[[c for c in keep if c in df_block.columns]]
                df_block = df_block.dropna(subset=["open", "high", "low", "close"])
                df_block = df_block[df_block["close"] > 0]
                df_block = df_block.reset_index(drop=True)

                if len(df_block) > 0:
                    # OKAMŽITĚ zapiš blok na disk
                    block_path = tmp_dir / f"block_{block_num:04d}.parquet"
                    df_block.to_parquet(
                        block_path,
                        index=False,
                        compression="snappy"   # rychlá komprese, šetří disk
                    )
                    total_rows += len(df_block)
                    block_num  += 1

                    print(f"    blok {block_num:02d} "
                          f"({block_start.strftime('%Y-%m')} → {block_end.strftime('%Y-%m')}) "
                          f"= {len(df_block):,} řádků", end="\r")

                # Uvolni RAM ihned
                del df_block
                gc.collect()

        except Exception as e:
            print(f"\n    ⚠️  Blok {block_start.date()}: {e}")

        block_start = block_end + timedelta(days=1)
        time.sleep(SLEEP_SEC)

    if block_num == 0:
        print(f"    ❌ žádná data stažena")
        return False

    # Spoj bloky na disku — čteme po jednom, append do výsledku
    print(f"\n    Spojuji {block_num} bloků → {out_path.name} ...", end="", flush=True)

    chunk_list = []
    for block_path in sorted(tmp_dir.glob("block_*.parquet")):
        chunk = pd.read_parquet(block_path)
        chunk_list.append(chunk)
        # Spoj jakmile máme víc chunků aby RAM nepřetekla
        if len(chunk_list) >= 10:
            merged = pd.concat(chunk_list, ignore_index=True)
            chunk_list = [merged]
            del merged
            gc.collect()

    if chunk_list:
        final_df = pd.concat(chunk_list, ignore_index=True)
        final_df = final_df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        final_df = final_df.reset_index(drop=True)

        # Ulož finální soubor s kompresí
        final_df.to_parquet(out_path, index=False, compression="snappy")

        ts    = pd.to_datetime(final_df["timestamp"])
        years = (ts.max() - ts.min()).days / 365

        del final_df, chunk_list
        gc.collect()

        print(f" ✅ {total_rows:,} řádků | {years:.1f} let")

    # Ukliď dočasné bloky
    for f in tmp_dir.glob("*.parquet"):
        f.unlink()
    try:
        tmp_dir.rmdir()
    except Exception:
        pass

    return True


def check_disk_space():
    """Zkontroluje volné místo na D: a C:"""
    try:
        import shutil
        d_free = shutil.disk_usage("D:\\").free / (1024**3)
        c_free = shutil.disk_usage("C:\\").free / (1024**3)
        print(f"  Disk D: {d_free:.1f} GB volných")
        print(f"  Disk C: {c_free:.1f} GB volných")
        if d_free < 1:
            print("  ❌ D: má méně než 1 GB! Změň OUTPUT_DIR.")
            return False
        return True
    except Exception as e:
        print(f"  ⚠️  Nelze zkontrolovat disk: {e}")
        return True


def main():
    print("╔══════════════════════════════════════════╗")
    print("║   DOWNLOADER — RAM-SAFE EDITION         ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")

    print(f"  Výstup: {OUTPUT_DIR}\n")

    if not check_disk_space():
        return

    t_start = datetime.now()

    for tf_name, tf_cfg in TIMEFRAMES.items():
        print(f"\n{'='*55}")
        print(f"  {tf_name}  —  {tf_cfg['years_back']} let  —  blok po {tf_cfg['block_days']} dnech")
        print(f"{'='*55}")

        for category, tickers in TICKERS.items():
            for local_name, yf_symbol in tickers.items():
                print(f"\n  ► {local_name} ({yf_symbol})")
                download_and_save(local_name, yf_symbol, tf_name, tf_cfg)

                # Po každém tickeru uvolni RAM
                gc.collect()

    elapsed = (datetime.now() - t_start).total_seconds() / 60
    print(f"\n{'='*55}")
    print(f"  ✅ Hotovo za {elapsed:.1f} minut")
    print(f"  Data uložena: {OUTPUT_DIR}")
    print(f"""
  DALŠÍ KROKY:
  1. Zkontroluj data:
       python data_audit.py
       → uprav cestu v data_audit.py: GOLD_DIR = r"D:\\MARKETPAL_DATA\\01_RAW"

  2. Spusť feature engineering pipeline
       → uprav INPUT_DIR v pipeline na: D:\\MARKETPAL_DATA\\01_RAW
       → nebo zkopíruj data zpátky do: C:\\Bot_MARKETPAL\\data\\01_RAW

  3. python triple_barrier.py
  4. python meta_labeling.py
  """)


if __name__ == "__main__":
    main()