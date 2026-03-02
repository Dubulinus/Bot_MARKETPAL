"""
╔══════════════════════════════════════════════════════════════╗
║          MARKETPAL - TĚŽBA DAT - POLYGON.IO                 ║
║          Fáze 2 | M5/M15 | Forex + Nasdaq                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import time
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from polygon import RESTClient

# ─── KONFIGURACE ───────────────────────────────────────────────
load_dotenv()
API_KEY = os.getenv("POLYGON_API_KEY")

if not API_KEY:
    raise ValueError("❌ POLYGON_API_KEY nenalezen v .env souboru!")

# Instrumenty které chceme těžit
INSTRUMENTY = {
    "forex": [
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        "USD/CHF",
    ],
    "akcie": [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
    ]
}

# Timeframy (Polygon formát: multiplier + timespan)
TIMEFRAMY = {
    "M5":  {"multiplier": 5,  "timespan": "minute"},
    "M15": {"multiplier": 15, "timespan": "minute"},
    "H1":  {"multiplier": 1,  "timespan": "hour"},
}

# Kolik dní history chceme stáhnout
DNY_ZPET = 30

# Kam ukládat data
OUTPUT_DIR = "data/02_POLYGON_RAW"

# ─── POMOCNÉ FUNKCE ────────────────────────────────────────────

def vytvor_slozky():
    """Vytvoří potřebné složky pokud neexistují."""
    for tf in TIMEFRAMY.keys():
        for kategorie in INSTRUMENTY.keys():
            cesta = os.path.join(OUTPUT_DIR, tf, kategorie)
            os.makedirs(cesta, exist_ok=True)
    print(f"✅ Složky připraveny v: {OUTPUT_DIR}")


def datum_na_str(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def ticker_na_nazev_souboru(ticker: str) -> str:
    """EUR/USD → EURUSD"""
    return ticker.replace("/", "").replace("-", "")


def stahni_forex(client: RESTClient, par: str, tf_nazev: str, tf_config: dict, od: str, do: str) -> pd.DataFrame | None:
    """Stáhne forex data z Polygon.io Forex API."""
    # Polygon forex ticker formát: C:EURUSD
    ticker = "C:" + ticker_na_nazev_souboru(par)
    
    try:
        print(f"  📡 Stahuji {par} ({tf_nazev}) od {od} do {do}...")
        
        aggs = []
        for bar in client.list_aggs(
            ticker=ticker,
            multiplier=tf_config["multiplier"],
            timespan=tf_config["timespan"],
            from_=od,
            to=do,
            adjusted=True,
            limit=50000
        ):
            aggs.append({
                "timestamp": pd.to_datetime(bar.timestamp, unit="ms"),
                "open":   bar.open,
                "high":   bar.high,
                "low":    bar.low,
                "close":  bar.close,
                "volume": bar.volume,
                "vwap":   bar.vwap,
                "transactions": bar.transactions
            })
        
        if not aggs:
            print(f"  ⚠️  Žádná data pro {par} ({tf_nazev})")
            return None
        
        df = pd.DataFrame(aggs)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        print(f"  ✅ {par} ({tf_nazev}): {len(df)} svíček staženo")
        return df

    except Exception as e:
        print(f"  ❌ Chyba při stahování {par}: {e}")
        return None


def stahni_akcii(client: RESTClient, ticker: str, tf_nazev: str, tf_config: dict, od: str, do: str) -> pd.DataFrame | None:
    """Stáhne akciová data z Polygon.io."""
    try:
        print(f"  📡 Stahuji {ticker} ({tf_nazev}) od {od} do {do}...")
        
        aggs = []
        for bar in client.list_aggs(
            ticker=ticker,
            multiplier=tf_config["multiplier"],
            timespan=tf_config["timespan"],
            from_=od,
            to=do,
            adjusted=True,
            limit=50000
        ):
            aggs.append({
                "timestamp": pd.to_datetime(bar.timestamp, unit="ms"),
                "open":   bar.open,
                "high":   bar.high,
                "low":    bar.low,
                "close":  bar.close,
                "volume": bar.volume,
                "vwap":   bar.vwap,
                "transactions": bar.transactions
            })
        
        if not aggs:
            print(f"  ⚠️  Žádná data pro {ticker} ({tf_nazev})")
            return None
        
        df = pd.DataFrame(aggs)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        print(f"  ✅ {ticker} ({tf_nazev}): {len(df)} svíček staženo")
        return df

    except Exception as e:
        print(f"  ❌ Chyba při stahování {ticker}: {e}")
        return None


def uloz_data(df: pd.DataFrame, kategorie: str, ticker: str, tf_nazev: str):
    """Uloží DataFrame jako Parquet + CSV."""
    nazev = ticker_na_nazev_souboru(ticker)
    cesta_base = os.path.join(OUTPUT_DIR, tf_nazev, kategorie, nazev)
    
    # Parquet (hlavní formát — rychlý, komprimovaný)
    df.to_parquet(f"{cesta_base}.parquet")
    
    # CSV záloha (pro debug a Excel)
    df.to_csv(f"{cesta_base}.csv")
    
    print(f"  💾 Uloženo: {cesta_base}.parquet ({len(df)} řádků)")


# ─── HLAVNÍ PROGRAM ────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║   MARKETPAL TĚŽBA - POLYGON.IO START    ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                    ║")
    print("╚══════════════════════════════════════════╝\n")

    # Inicializace
    vytvor_slozky()
    client = RESTClient(API_KEY)

    # Datové rozsahy
    do_datum = datetime.now()
    od_datum = do_datum - timedelta(days=DNY_ZPET)
    od_str = datum_na_str(od_datum)
    do_str = datum_na_str(do_datum)

    print(f"📅 Rozsah: {od_str} → {do_str} ({DNY_ZPET} dní)\n")

    celkem_stazeno = 0
    celkem_chyb = 0

    # ── FOREX ──────────────────────────────────────────────────
    print("═" * 50)
    print("💱 FOREX")
    print("═" * 50)

    for tf_nazev, tf_config in TIMEFRAMY.items():
        print(f"\n⏱️  Timeframe: {tf_nazev}")
        for par in INSTRUMENTY["forex"]:
            df = stahni_forex(client, par, tf_nazev, tf_config, od_str, do_str)
            if df is not None:
                uloz_data(df, "forex", par, tf_nazev)
                celkem_stazeno += 1
            else:
                celkem_chyb += 1
            
            # Respektuj rate limit (free tier = 5 req/min)
            time.sleep(13)

    # ── AKCIE (NASDAQ) ─────────────────────────────────────────
    print("\n" + "═" * 50)
    print("📈 AKCIE - NASDAQ")
    print("═" * 50)

    for tf_nazev, tf_config in TIMEFRAMY.items():
        print(f"\n⏱️  Timeframe: {tf_nazev}")
        for ticker in INSTRUMENTY["akcie"]:
            df = stahni_akcii(client, ticker, tf_nazev, tf_config, od_str, do_str)
            if df is not None:
                uloz_data(df, "akcie", ticker, tf_nazev)
                celkem_stazeno += 1
            else:
                celkem_chyb += 1
            
            time.sleep(13)

    # ── SOUHRN ─────────────────────────────────────────────────
    print("\n╔══════════════════════════════════════════╗")
    print("║              SOUHRN TĚŽBY               ║")
    print(f"║  ✅ Úspěšně staženo: {celkem_stazeno:<20}║")
    print(f"║  ❌ Chyby:           {celkem_chyb:<20}║")
    print("╚══════════════════════════════════════════╝")


if __name__ == "__main__":
    main()