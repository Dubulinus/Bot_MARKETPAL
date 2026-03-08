"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - ALTERNATIVE DATA MINING                     ║
║     FRED (makro) + COT (hedge funds) + Congress (insider)   ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    Stahuje 3 alternativní datové zdroje které retail tradeři
    nepoužívají — a ukládá je jako týdenní/denní features
    pro feature_engineering.py

    FRED API  → makroekonomické indikátory (Fed, CPI, VIX, yield)
               Klíč zdarma: https://fred.stlouisfed.org/docs/api/api_key.html
               Registrace 30 sekund, okamžitě

    COT Data  → pozice hedge fondů na forex párech každý týden
               CFTC publikuje každý pátek, zdarma bez registrace
               pip install cot_reports

    Congress  → obchody senátorů a kongresmaniů (Form 4 equivalent)
               housestockwatcher.com + senatorstockwatcher.com
               Zdarma JSON API, bez registrace

VÝSTUP:
    data/12_ALTERNATIVE/fred_macro.parquet      ← denní makro features
    data/12_ALTERNATIVE/cot_forex.parquet       ← týdenní COT pozice
    data/12_ALTERNATIVE/congress_trades.parquet ← insider Congress trades

POUŽITÍ:
    Tyto data pak přidáš do feature_engineering.py jako nové sloupce
    → merge na datum → triple_barrier dostane víc signálů
"""

import os
import time
import warnings
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

OUTPUT_DIR = "data/12_ALTERNATIVE"
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

# ─── FRED CONFIG ────────────────────────────────────────────────
# Klíč zdarma: https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

FRED_SERIES = {
    # Úrokové sazby
    "fed_funds_rate":     "FEDFUNDS",       # Fed Funds Rate (měsíční)
    "us_10y_yield":       "DGS10",          # 10Y Treasury yield (denní)
    "us_2y_yield":        "DGS2",           # 2Y Treasury yield (denní)
    "yield_curve_spread": None,             # Počítáme sami: 10Y - 2Y

    # Inflace & ekonomika
    "cpi_yoy":            "CPIAUCSL",       # CPI YoY (měsíční)
    "pce_inflation":      "PCEPI",          # PCE Inflation (měsíční)
    "unemployment":       "UNRATE",         # Míra nezaměstnanosti (měsíční)
    "jolts_openings":     "JTSJOL",         # Job openings (měsíční)

    # Volatilita & sentiment
    "vix":                "VIXCLS",         # VIX (denní)
    "financial_stress":   "STLFSI4",        # St. Louis Financial Stress Index

    # Měnové agregáty
    "m2_money_supply":    "M2SL",           # M2 (měsíční)
    "dollar_index":       "DTWEXBGS",       # Dollar index (denní)

    # Úvěry & podmínky
    "credit_spread":      "BAMLH0A0HYM2",  # High Yield spread (denní)
    "ted_spread":         "TEDRATE",        # TED spread (denní, proxy credit stress)
}

# COT páry — forex futures na CME
COT_FOREX_PAIRS = {
    "EURUSD": "EURO FX",
    "GBPUSD": "BRITISH POUND",
    "USDJPY": "JAPANESE YEN",
    "USDCHF": "SWISS FRANC",
}

# Tickery pro Congressional trading filter
CONGRESS_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "TSLA",
    "META", "GOOGL", "AMD",
    "EURUSD", "GBPUSD",  # Congress neobchoduje forex přímo ale ponecháme pro kompletnost
]


# ─── FRED API ───────────────────────────────────────────────────

def fetch_fred_series(series_id, start_date="2020-01-01"):
    """Stáhni jednu sérii z FRED API."""
    if not FRED_API_KEY:
        raise ValueError("Chybí FRED_API_KEY v .env souboru")

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id":        series_id,
        "api_key":          FRED_API_KEY,
        "file_type":        "json",
        "observation_start": start_date,
        "observation_end":  datetime.now().strftime("%Y-%m-%d"),
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "observations" not in data:
        raise ValueError(f"FRED: žádná data pro {series_id}")

    df = pd.DataFrame(data["observations"])
    df["date"]  = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df[["date", "value"]].dropna().set_index("date")
    df.columns = [series_id.lower()]

    return df


def download_fred_macro():
    """Stáhni všechny FRED série a spoj do jednoho DataFrame."""
    if not FRED_API_KEY:
        print("\n  ⚠️  FRED_API_KEY chybí v .env souboru!")
        print("  Zaregistruj se ZDARMA: https://fred.stlouisfed.org/docs/api/api_key.html")
        print("  Přidej do .env: FRED_API_KEY=tvuj_klic")
        return None

    print(f"\n{'═'*55}")
    print("  📊 FRED API — MAKROEKONOMICKÁ DATA")
    print(f"{'═'*55}\n")

    all_series = {}
    start = "2020-01-01"

    for local_name, fred_id in FRED_SERIES.items():
        if fred_id is None:
            continue  # yield_curve_spread počítáme sami
        try:
            df = fetch_fred_series(fred_id, start)
            df.columns = [local_name]
            all_series[local_name] = df
            print(f"  ✅ {local_name:<25} {len(df):>5} obs "
                  f"({df.index[0].date()} → {df.index[-1].date()})")
            time.sleep(0.3)  # FRED rate limit: 120 req/min
        except Exception as e:
            print(f"  ❌ {local_name:<25} chyba: {str(e)[:60]}")

    if not all_series:
        return None

    # Spoj na denní index (forward-fill pro měsíční data)
    date_range = pd.date_range(start=start, end=datetime.now(), freq="D")
    df_all = pd.DataFrame(index=date_range)

    for name, s in all_series.items():
        df_all = df_all.join(s, how="left")

    # Forward-fill (měsíční data platí do příštího release)
    df_all = df_all.ffill()

    # Yield curve spread = 10Y - 2Y (inverze = recese signal)
    if "us_10y_yield" in df_all.columns and "us_2y_yield" in df_all.columns:
        df_all["yield_curve_spread"] = df_all["us_10y_yield"] - df_all["us_2y_yield"]
        print(f"\n  ✅ yield_curve_spread     computed (10Y - 2Y)")

    # Derived features
    if "vix" in df_all.columns:
        df_all["vix_high_regime"] = (df_all["vix"] > 25).astype(int)
        df_all["vix_spike"]       = (df_all["vix"] > df_all["vix"].rolling(20).mean() * 1.5).astype(int)

    if "yield_curve_spread" in df_all.columns:
        df_all["yield_inverted"] = (df_all["yield_curve_spread"] < 0).astype(int)

    if "credit_spread" in df_all.columns:
        df_all["credit_stress"] = (df_all["credit_spread"] > df_all["credit_spread"].rolling(60).quantile(0.8)).astype(int)

    # Ulož
    out_path = Path(OUTPUT_DIR) / "fred_macro.parquet"
    df_all.to_parquet(out_path, compression="snappy")

    print(f"\n  ✅ FRED uloženo: {out_path}")
    print(f"  📊 {len(df_all)} denních řádků | {len(df_all.columns)} features")

    return df_all


# ─── COT DATA ───────────────────────────────────────────────────

def download_cot_forex():
    """
    Stáhni COT (Commitments of Traders) data pro forex futures.

    COT data ukazují pozice 3 skupin:
    - Commercial (hedgers, banky) — smart money
    - Non-commercial (hedge fondy, spekulanti) — trend followers
    - Non-reportable (retail) — contrarian indicator

    Klíčový signal: Net position non-commercial (hedge fondy)
    Pokud hedge fondy masivně long EURUSD → bullish
    """
    try:
        import cot_reports as cot
    except ImportError:
        print("\n  ❌ Chybí cot_reports: pip install cot_reports")
        return None

    print(f"\n{'═'*55}")
    print("  📈 COT DATA — HEDGE FUND POZICE (CFTC)")
    print(f"{'═'*55}\n")

    try:
        # Stáhni legacy futures-only report (nejkompletnější)
        print("  Stahuji COT data (může trvat 30-60s)...")
        df_cot = cot.cot_all(cot_report_type="legacy_fut")

        if df_cot is None or len(df_cot) == 0:
            print("  ❌ COT data prázdná")
            return None

        print(f"  ✅ Staženo {len(df_cot)} řádků, {len(df_cot.columns)} sloupců")
        print(f"  Dostupné sloupce (ukázka): {list(df_cot.columns[:8])}")

    except Exception as e:
        print(f"  ❌ COT stahování selhalo: {e}")
        return None

    # Najdi date sloupec
    date_col = None
    for c in df_cot.columns:
        if "date" in c.lower() or "report" in c.lower():
            date_col = c
            break

    if date_col is None:
        date_col = df_cot.columns[0]

    df_cot[date_col] = pd.to_datetime(df_cot[date_col], errors="coerce")
    df_cot = df_cot.dropna(subset=[date_col])
    df_cot = df_cot.set_index(date_col).sort_index()

    # Filtruj jen od 2020
    df_cot = df_cot[df_cot.index >= "2020-01-01"]

    # Najdi name sloupec
    name_col = None
    for c in df_cot.columns:
        if "market" in c.lower() or "name" in c.lower() or "commodity" in c.lower():
            name_col = c
            break

    all_pairs = {}

    for local_name, cot_name in COT_FOREX_PAIRS.items():
        try:
            # Filtruj na daný forex pár
            if name_col:
                mask = df_cot[name_col].str.upper().str.contains(
                    cot_name.split()[0], na=False
                )
                df_pair = df_cot[mask].copy()
            else:
                df_pair = df_cot.copy()

            if len(df_pair) == 0:
                print(f"  ⚠️  {local_name}: nenalezen v COT datech")
                continue

            # Extrahuj klíčové sloupce
            # COT sloupce mají různá jména — hledáme non-commercial long/short
            nc_long  = None
            nc_short = None

            for c in df_pair.columns:
                cl = c.lower()
                if "noncommercial" in cl.replace(" ", "").replace("-", "") or \
                   "non_commercial" in cl or "non-commercial" in cl:
                    if "long" in cl and nc_long is None:
                        nc_long = c
                    elif "short" in cl and nc_short is None:
                        nc_short = c

            if nc_long is None or nc_short is None:
                # Fallback: vezmi sloupce 2 a 3 (typicky NC long, NC short)
                numeric_cols = df_pair.select_dtypes(include=[np.number]).columns
                if len(numeric_cols) >= 2:
                    nc_long  = numeric_cols[0]
                    nc_short = numeric_cols[1]

            if nc_long and nc_short:
                result = pd.DataFrame(index=df_pair.index)
                result[f"{local_name}_nc_long"]  = pd.to_numeric(df_pair[nc_long],  errors="coerce")
                result[f"{local_name}_nc_short"] = pd.to_numeric(df_pair[nc_short], errors="coerce")
                result[f"{local_name}_nc_net"]   = result[f"{local_name}_nc_long"] - result[f"{local_name}_nc_short"]

                # Normalizuj net position na percentil (0-100)
                net = result[f"{local_name}_nc_net"]
                result[f"{local_name}_cot_percentile"] = net.rank(pct=True) * 100

                # Signal: extrémní pozice
                result[f"{local_name}_cot_extreme_long"]  = (result[f"{local_name}_cot_percentile"] > 80).astype(int)
                result[f"{local_name}_cot_extreme_short"] = (result[f"{local_name}_cot_percentile"] < 20).astype(int)

                all_pairs[local_name] = result
                print(f"  ✅ {local_name}: {len(result)} týdenních záznamů "
                      f"| net range: {net.min():.0f} → {net.max():.0f}")
            else:
                print(f"  ⚠️  {local_name}: nelze najít NC long/short sloupce")

        except Exception as e:
            print(f"  ❌ {local_name}: {e}")

    if not all_pairs:
        print("  ❌ Žádná COT data nezpracována")
        return None

    # Spoj všechny páry
    df_all = pd.concat(all_pairs.values(), axis=1)

    # Resample na denní (forward-fill z týdenních)
    date_range = pd.date_range(start="2020-01-01", end=datetime.now(), freq="D")
    df_daily = df_all.reindex(date_range).ffill()

    out_path = Path(OUTPUT_DIR) / "cot_forex.parquet"
    df_daily.to_parquet(out_path, compression="snappy")

    print(f"\n  ✅ COT uloženo: {out_path}")
    print(f"  📊 {len(df_daily)} denních řádků | {len(df_daily.columns)} features")

    return df_daily


# ─── CONGRESSIONAL TRADING ──────────────────────────────────────

def download_congress_trades():
    """
    Stáhni Congressional trading data z housestockwatcher.com
    a senatestockwatcher.com — zdarma JSON API, bez klíče.

    Logika:
    - Nancy Pelosi koupí NVDA → AI regulace nebude → bullish
    - Senator prodá META → negativní legislativa přichází → bearish
    - Insider knowledge je legální pro Congress (STOCK Act má díry)

    Features:
    - {TICKER}_congress_buy_30d  ← počet nákupů za posledních 30 dní
    - {TICKER}_congress_sell_30d ← počet prodejů za posledních 30 dní
    - {TICKER}_congress_net_30d  ← net sentiment (buy - sell)
    - {TICKER}_congress_signal   ← 1=bullish, -1=bearish, 0=neutral
    """
    print(f"\n{'═'*55}")
    print("  🏛️  CONGRESSIONAL TRADING — INSIDER SIGNAL")
    print(f"{'═'*55}\n")

    all_trades = []

    # House of Representatives
    sources = [
        {
            "name": "House",
            "url": "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
        },
        {
            "name": "Senate",
            "url": "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
        },
    ]

    for src in sources:
        try:
            print(f"  Stahuji {src['name']} data...")
            resp = requests.get(src["url"], timeout=60)
            resp.raise_for_status()
            data = resp.json()

            df = pd.DataFrame(data)
            print(f"  ✅ {src['name']}: {len(df)} transakcí")
            all_trades.append(df)

        except Exception as e:
            print(f"  ❌ {src['name']}: {e}")

    if not all_trades:
        print("  ❌ Žádná Congressional data")
        return None

    # Spoj House + Senate
    df = pd.concat(all_trades, ignore_index=True)

    # Standardizuj sloupce
    rename_map = {}
    for c in df.columns:
        cl = c.lower()
        if "ticker" in cl or "symbol" in cl:
            rename_map[c] = "ticker"
        elif "transaction" in cl and "type" in cl:
            rename_map[c] = "transaction_type"
        elif "disclosure" in cl or "date" in cl:
            rename_map[c] = "date"
        elif "amount" in cl:
            rename_map[c] = "amount"
        elif "representative" in cl or "senator" in cl or "name" in cl:
            rename_map[c] = "politician"

    df = df.rename(columns=rename_map)

    # Parsuj datum
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])

    # Filtruj od 2020
    df = df[df["date"] >= "2020-01-01"]
    df["ticker"] = df["ticker"].str.upper().str.strip()

    # Filtruj na naše tickery
    df_filtered = df[df["ticker"].isin(CONGRESS_TICKERS)].copy()

    print(f"\n  📊 Celkem transakcí: {len(df)}")
    print(f"  📊 Pro naše tickery: {len(df_filtered)}")

    if len(df_filtered) == 0:
        print("  ⚠️  Žádné transakce pro naše tickery — ukládám všechna data")
        df_filtered = df

    # Kategorizuj purchase vs sale
    if "transaction_type" in df_filtered.columns:
        df_filtered["is_buy"]  = df_filtered["transaction_type"].str.lower().str.contains(
            "purchase|buy", na=False
        ).astype(int)
        df_filtered["is_sell"] = df_filtered["transaction_type"].str.lower().str.contains(
            "sale|sell", na=False
        ).astype(int)
    else:
        df_filtered["is_buy"]  = 0
        df_filtered["is_sell"] = 0

    # Uložme raw data
    out_raw = Path(OUTPUT_DIR) / "congress_trades_raw.parquet"
    df_filtered.to_parquet(out_raw, compression="snappy", index=False)

    # Vytvoř denní features per ticker
    date_range = pd.date_range(start="2020-01-01", end=datetime.now(), freq="D")
    df_features = pd.DataFrame(index=date_range)

    for ticker in CONGRESS_TICKERS:
        df_t = df_filtered[df_filtered["ticker"] == ticker].copy()
        if len(df_t) == 0:
            continue

        df_t = df_t.set_index("date").sort_index()

        # Rolling 30-denní počty
        daily_buys  = df_t["is_buy"].resample("D").sum()
        daily_sells = df_t["is_sell"].resample("D").sum()

        df_features[f"{ticker}_congress_buy_30d"]  = daily_buys.reindex(date_range).fillna(0).rolling(30).sum()
        df_features[f"{ticker}_congress_sell_30d"] = daily_sells.reindex(date_range).fillna(0).rolling(30).sum()
        df_features[f"{ticker}_congress_net_30d"]  = (
            df_features[f"{ticker}_congress_buy_30d"] -
            df_features[f"{ticker}_congress_sell_30d"]
        )

        # Signal: +1 pokud více nákupů, -1 pokud více prodejů
        net = df_features[f"{ticker}_congress_net_30d"]
        df_features[f"{ticker}_congress_signal"] = np.where(
            net > 0, 1, np.where(net < 0, -1, 0)
        )

        n_trades = len(df_t)
        n_buys   = df_t["is_buy"].sum()
        n_sells  = df_t["is_sell"].sum()
        print(f"  ✅ {ticker:<6} {n_trades:>4} obchodů | "
              f"nákupy: {n_buys} | prodeje: {n_sells}")

    out_path = Path(OUTPUT_DIR) / "congress_trades.parquet"
    df_features.to_parquet(out_path, compression="snappy")

    print(f"\n  ✅ Congress uloženo: {out_path}")
    print(f"  📊 {len(df_features)} denních řádků | {len(df_features.columns)} features")

    return df_features


# ─── MAIN ───────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║   MARKETPAL ALTERNATIVE DATA MINING    ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  FRED (makro) + COT (hedge funds) + Congress (insider)\n")

    t_start = datetime.now()
    results = {}

    # 1. FRED
    df_fred = download_fred_macro()
    results["FRED"] = df_fred is not None

    # 2. COT
    df_cot = download_cot_forex()
    results["COT"] = df_cot is not None

    # 3. Congressional
    df_congress = download_congress_trades()
    results["Congress"] = df_congress is not None

    elapsed = (datetime.now() - t_start).total_seconds()

    print(f"\n{'═'*55}")
    print(f"  SOUHRN")
    print(f"{'═'*55}")
    for name, ok in results.items():
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")

    print(f"\n  ⏱️  Čas: {elapsed:.0f}s")
    print(f"  📁 Output: {OUTPUT_DIR}")
    print(f"""
  DALŠÍ KROKY:
    1. Zkopíruj data do feature_engineering.py jako merge:
       df = df.merge(fred_macro, left_index=True, right_index=True, how='left')
       df = df.merge(cot_forex,  left_index=True, right_index=True, how='left')

    2. Spusť znovu feature_engineering.py
    3. Spusť triple_barrier.py — nové features = nové signály
""")


if __name__ == "__main__":
    main()