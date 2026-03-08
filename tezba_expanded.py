"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - EXPANDED DATA MINING                        ║
║     Alpaca (stocks 5+ let) + Yahoo Finance (forex H1)       ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    Polygon free má limity — 2 roky M5, slabá H1 forex data.
    Tento skript to doplní ze dvou dalších zdrojů:

    ALPACA  → US stocks (AAPL, MSFT, NVDA, AMZN + nové tickery)
               M5: 5+ let | M15: 5+ let | H1: 5+ let
               Zdarma, paper trading účet

    YAHOO   → Forex H1 (EURUSD, GBPUSD, USDCHF, USDJPY)
               H1: 2 roky zdarma, bez API klíče
               Opravuje chybějící H1 forex z Polygon

VÝSTUP:
    data/02_EXPANDED_RAW/{TF}/{category}/{ticker}.parquet
    Stejný formát jako Polygon raw → rafinerie_polygon.py to přijme bez změn

SETUP:
    pip install alpaca-trade-api yfinance
    V .env souboru:
        ALPACA_API_KEY=tvuj_klic
        ALPACA_SECRET_KEY=tvuj_secret
"""

import os
import gc
import time
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────

OUTPUT_DIR = "data/02_EXPANDED_RAW"

# Alpaca stocks — přidej kolik chceš tickerů
ALPACA_STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "GOOGL", "AMD"]

# Yahoo forex — opravuje H1 kde Polygon selhal
YAHOO_FOREX = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDCHF": "USDCHF=X",
    "USDJPY": "USDJPY=X",
}

TIMEFRAMES_ALPACA = {
    "M5":  {"alpaca_tf": "5Min",  "days": 365 * 4},   # 4 roky M5
    "M15": {"alpaca_tf": "15Min", "days": 365 * 4},   # 4 roky M15
    "H1":  {"alpaca_tf": "1Hour", "days": 365 * 5},   # 5 let H1
}

TIMEFRAMES_YAHOO_H1 = {
    "H1": {"interval": "1h", "period": "730d"},        # 2 roky H1
}

# Alpaca max 1000 bars per request — stahujeme po blocích
ALPACA_BLOCK_DAYS = 30   # M5/M15: 30 dní na blok (jinak příliš mnoho bars)
ALPACA_BLOCK_DAYS_H1 = 365  # H1: 1 rok na blok

# Rate limiting
ALPACA_SLEEP = 0.2    # 0.2s mezi requesty (Alpaca: 200 req/min free)
YAHOO_SLEEP  = 1.0    # 1s mezi tickery


# ─── HELPERS ────────────────────────────────────────────────────

def ensure_dirs():
    for tf in ["M5", "M15", "H1"]:
        Path(OUTPUT_DIR, tf, "stocks").mkdir(parents=True, exist_ok=True)
        Path(OUTPUT_DIR, tf, "forex").mkdir(parents=True, exist_ok=True)
    print(f"✅ Složky připraveny: {OUTPUT_DIR}")


def should_skip(path, min_rows=100):
    """Přeskoč soubor pokud existuje, je čerstvý a má dost dat."""
    if not path.exists():
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > 12:
        return False
    try:
        df = pd.read_parquet(path)
        if len(df) < min_rows:
            return False
        print(f"    ✓ existuje ({len(df):,} svíček, {age_hours:.0f}h staré) — přeskočeno")
        return True
    except Exception:
        return False


def save_parquet(df, path):
    """Ulož DataFrame jako parquet se standardizovanými sloupci."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # Standardizuj názvy sloupců
    rename_map = {
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # Zajisti povinné sloupce
    required = ["open", "high", "low", "close", "volume"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        print(f"    ⚠️  Chybí sloupce: {missing}")
        return False

    df = df[required].copy()
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["close"] > 0]
    df = df.sort_index()

    if len(df) == 0:
        print(f"    ⚠️  Prázdný DataFrame po čištění")
        return False

    df.to_parquet(path, compression="snappy")
    return True


# ─── ALPACA ────────────────────────────────────────────────────

def get_alpaca_client():
    """Inicializuj Alpaca klienta z .env."""
    try:
        import alpaca_trade_api as tradeapi
    except ImportError:
        print("❌ Chybí alpaca-trade-api: pip install alpaca-trade-api")
        return None

    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        print("❌ Chybí ALPACA_API_KEY nebo ALPACA_SECRET_KEY v .env souboru")
        print("   Zaregistruj se na https://app.alpaca.markets/signup")
        print("   Paper Trading → API Keys → zkopíruj do .env")
        return None

    try:
        api = tradeapi.REST(
            api_key, secret_key,
            base_url="https://paper-api.alpaca.markets",
            api_version="v2"
        )
        # Test připojení
        account = api.get_account()
        print(f"  ✅ Alpaca připojeno (paper account)")
        return api
    except Exception as e:
        print(f"  ❌ Alpaca chyba: {e}")
        return None


def fetch_alpaca_blocks(api, ticker, tf_name, alpaca_tf, days_back, block_days):
    """
    Stáhni historická data z Alpaca po blocích (RAM-safe).
    Vrátí spojený DataFrame nebo None.
    """
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days_back)

    all_blocks = []
    current    = start_dt

    print(f"    Stahuji {days_back} dní po blocích {block_days} dní...")

    block_num = 0
    while current < end_dt:
        block_end = min(current + timedelta(days=block_days), end_dt)
        block_num += 1

        start_str = current.strftime("%Y-%m-%d")
        end_str   = block_end.strftime("%Y-%m-%d")

        try:
            bars = api.get_bars(
                ticker,
                alpaca_tf,
                start=start_str,
                end=end_str,
                limit=10000,
                adjustment="raw",
            ).df

            if bars is not None and len(bars) > 0:
                all_blocks.append(bars)
                print(f"    blok {block_num:02d} ({start_str} → {end_str}) = {len(bars):,} svíček")
            else:
                print(f"    blok {block_num:02d} ({start_str} → {end_str}) = prázdný")

        except Exception as e:
            err = str(e)
            if "subscription" in err.lower() or "forbidden" in err.lower():
                print(f"    ⚠️  Blok {start_str}: nedostatečné předplatné Alpaca")
            elif "rate" in err.lower() or "429" in err.lower():
                print(f"    ⚠️  Rate limit, čekám 10s...")
                time.sleep(10)
                # Zkus znovu
                try:
                    bars = api.get_bars(ticker, alpaca_tf, start=start_str, end=end_str, limit=10000).df
                    if bars is not None and len(bars) > 0:
                        all_blocks.append(bars)
                except Exception:
                    pass
            else:
                print(f"    ⚠️  Blok {start_str}: {err[:80]}")

        current = block_end + timedelta(days=1)
        time.sleep(ALPACA_SLEEP)

    if not all_blocks:
        return None

    print(f"    Spojuji {len(all_blocks)} bloků...")
    df = pd.concat(all_blocks)
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    return df


def download_alpaca_stocks(api):
    """Stáhni stocks data z Alpaca pro všechny TIMEFRAMES."""
    print(f"\n{'═'*55}")
    print("  📈 ALPACA STOCKS")
    print(f"{'═'*55}")

    total_ok = 0

    for tf_name, tf_cfg in TIMEFRAMES_ALPACA.items():
        print(f"\n  ── {tf_name} ({tf_cfg['days']//365} let) ──")
        block_days = ALPACA_BLOCK_DAYS_H1 if tf_name == "H1" else ALPACA_BLOCK_DAYS

        for ticker in ALPACA_STOCKS:
            out_path = Path(OUTPUT_DIR) / tf_name / "stocks" / f"{ticker}.parquet"
            print(f"\n  ► {ticker}")

            if should_skip(out_path, min_rows=500):
                total_ok += 1
                continue

            df = fetch_alpaca_blocks(
                api, ticker, tf_name,
                tf_cfg["alpaca_tf"], tf_cfg["days"], block_days
            )

            if df is None or len(df) < 10:
                print(f"    ❌ Žádná data")
                continue

            if save_parquet(df, out_path):
                years = (df.index[-1] - df.index[0]).days / 365
                print(f"    ✅ {len(df):,} svíček | {years:.1f} let "
                      f"({df.index[0].date()} → {df.index[-1].date()})")
                total_ok += 1
            else:
                print(f"    ❌ Uložení selhalo")

            del df
            gc.collect()

    return total_ok


# ─── YAHOO FINANCE ──────────────────────────────────────────────

def fetch_yahoo_forex_h1():
    """
    Stáhni H1 forex data z Yahoo Finance.
    Yahoo dává 2 roky H1 zdarma, bez API klíče.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("❌ Chybí yfinance: pip install yfinance")
        return 0

    print(f"\n{'═'*55}")
    print("  💱 YAHOO FINANCE — FOREX H1")
    print(f"{'═'*55}\n")

    total_ok = 0

    for local_name, yahoo_ticker in YAHOO_FOREX.items():
        out_path = Path(OUTPUT_DIR) / "H1" / "forex" / f"{local_name}.parquet"
        print(f"  ► {local_name} ({yahoo_ticker})")

        if should_skip(out_path, min_rows=100):
            total_ok += 1
            continue

        try:
            df = yf.download(
                yahoo_ticker,
                period="730d",
                interval="1h",
                auto_adjust=True,
                progress=False,
                timeout=30,
            )

            if df is None or len(df) == 0:
                print(f"    ❌ Žádná data z Yahoo")
                continue

            # Flatten multi-index pokud existuje
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)

            if save_parquet(df, out_path):
                years = (df.index[-1] - df.index[0]).days / 365
                print(f"    ✅ {len(df):,} svíček | {years:.1f} let "
                      f"({df.index[0].date()} → {df.index[-1].date()})")
                total_ok += 1
            else:
                print(f"    ❌ Uložení selhalo")

        except Exception as e:
            print(f"    ❌ Yahoo chyba: {e}")

        time.sleep(YAHOO_SLEEP)
        gc.collect()

    return total_ok


# ─── MAIN ───────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║   MARKETPAL EXPANDED DATA MINING        ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  Alpaca (stocks) + Yahoo Finance (forex H1)\n")

    ensure_dirs()
    t_start  = datetime.now()
    total_ok = 0

    # ── Yahoo Forex H1 (nevyžaduje API klíč) ──
    total_ok += fetch_yahoo_forex_h1()

    # ── Alpaca Stocks ──
    print(f"\n  Inicializuji Alpaca...")
    api = get_alpaca_client()

    if api:
        total_ok += download_alpaca_stocks(api)
    else:
        print("\n  ⚠️  Alpaca přeskočeno — chybí API klíče nebo modul")
        print("  Forex H1 data z Yahoo Finance jsou ale připravena.")

    elapsed = (datetime.now() - t_start).total_seconds()

    print(f"\n{'═'*55}")
    print(f"  SOUHRN")
    print(f"{'═'*55}")
    print(f"  ✅ Souborů připraveno: {total_ok}")
    print(f"  ⏱️  Čas:               {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  📁 Output:            {OUTPUT_DIR}")
    print(f"""
  DALŠÍ KROKY:
    Zkopíruj data do hlavní pipeline:
      xcopy data\\02_EXPANDED_RAW data\\02_POLYGON_RAW /E /Y /I

    Nebo uprav rafinerie_polygon.py aby četla z obou složek.

    Pak spusť:
      python rafinerie_polygon.py
      python feature_engineering.py
      python triple_barrier.py
      python meta_labeling.py
""")


if __name__ == "__main__":
    main()