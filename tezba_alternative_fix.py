"""
OPRAVY:
  COT fix:      Přesné názvy CFTC market names + sloupců
  Congress fix: capitoltrades.com místo mrtvých S3 bucketů
"""

import time
import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = "data/12_ALTERNATIVE"
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

# Přesné názvy jak jsou v CFTC legacy COT datech
COT_FOREX_MAP = {
    "EURUSD": "EURO FX",
    "GBPUSD": "BRITISH POUND STERLING",
    "USDJPY": "JAPANESE YEN",
    "USDCHF": "SWISS FRANC",
}

# Přesné názvy sloupců v legacy COT
NC_LONG_COL  = "Noncommercial Positions-Long (All)"
NC_SHORT_COL = "Noncommercial Positions-Short (All)"
NAME_COL     = "Market and Exchange Names"
DATE_COL     = "As of Date in Form YYYY-MM-DD"

CONGRESS_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "TSLA",
    "META", "GOOGL", "AMD",
]


# ─── COT FIX ────────────────────────────────────────────────────

def fix_cot():
    try:
        import cot_reports as cot
    except ImportError:
        print("❌ pip install cot_reports")
        return None

    print("╔══════════════════════════════════════════╗")
    print("║   COT FIX + CONGRESS FIX                ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")

    print("═" * 55)
    print("  📈 COT DATA — FIX")
    print("═" * 55)
    print("  Načítám COT data z disku (již staženo)...")

    try:
        # Data jsou už stažená — cot_all načte z cache
        df = cot.cot_all(cot_report_type="legacy_fut")
    except Exception as e:
        print(f"  ❌ {e}")
        return None

    print(f"  Dostupné market names (ukázka forex):")
    # Ukaž co skutečně máme
    mask_fx = df[NAME_COL].str.contains(
        "EURO|POUND|YEN|FRANC|DOLLAR|PESO|REAL|PESO",
        case=False, na=False
    )
    fx_names = df[mask_fx][NAME_COL].unique()
    for n in sorted(fx_names)[:15]:
        print(f"    {n}")

    # Parsuj datum
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], errors="coerce")
    df = df.dropna(subset=[DATE_COL])
    df = df.set_index(DATE_COL).sort_index()
    df = df[df.index >= "2020-01-01"]

    # Ověř existence sloupců
    print(f"\n  NC Long col:  '{NC_LONG_COL}' → {'✅' if NC_LONG_COL in df.columns else '❌ CHYBÍ'}")
    print(f"  NC Short col: '{NC_SHORT_COL}' → {'✅' if NC_SHORT_COL in df.columns else '❌ CHYBÍ'}")

    if NC_LONG_COL not in df.columns:
        # Najdi správné názvy
        nc_cols = [c for c in df.columns if "noncommercial" in c.lower() or "non-commercial" in c.lower()]
        print(f"\n  Dostupné NC sloupce:")
        for c in nc_cols[:10]:
            print(f"    '{c}'")
        return None

    all_pairs = {}

    for local, cot_name in COT_FOREX_MAP.items():
        mask = df[NAME_COL].str.upper().str.contains(
            cot_name.split()[0], na=False  # "EURO", "BRITISH", "JAPANESE", "SWISS"
        )
        # Pokud "BRITISH" nenajde nic, zkus první slovo tickeru
        df_pair = df[mask].copy()

        if len(df_pair) == 0:
            # Zkus alternativní substring
            alt = {"GBPUSD": "POUND", "USDJPY": "YEN", "USDCHF": "FRANC", "EURUSD": "EURO"}
            mask2 = df[NAME_COL].str.upper().str.contains(alt[local], na=False)
            df_pair = df[mask2].copy()

        if len(df_pair) == 0:
            print(f"  ❌ {local}: stále nenalezen — zkontroluj výpis market names výše")
            continue

        # Vezmi první match (může být více burz)
        first_market = df_pair[NAME_COL].iloc[0]
        df_pair = df_pair[df_pair[NAME_COL] == first_market]

        nc_long  = pd.to_numeric(df_pair[NC_LONG_COL],  errors="coerce")
        nc_short = pd.to_numeric(df_pair[NC_SHORT_COL], errors="coerce")
        nc_net   = nc_long - nc_short

        result = pd.DataFrame({
            f"{local}_nc_long":  nc_long,
            f"{local}_nc_short": nc_short,
            f"{local}_nc_net":   nc_net,
        })

        # COT percentil (kde jsme historicky)
        result[f"{local}_cot_pct"]   = nc_net.rank(pct=True) * 100
        result[f"{local}_cot_long"]  = (result[f"{local}_cot_pct"] > 80).astype(int)
        result[f"{local}_cot_short"] = (result[f"{local}_cot_pct"] < 20).astype(int)

        all_pairs[local] = result
        print(f"  ✅ {local}: {len(result)} týdnů | "
              f"net: {nc_net.min():.0f} → {nc_net.max():.0f} | "
              f"market: {first_market[:40]}")

    if not all_pairs:
        return None

    df_cot = pd.concat(all_pairs.values(), axis=1)

    # Resample na denní (forward-fill)
    dr = pd.date_range("2020-01-01", datetime.now(), freq="D")
    df_daily = df_cot.reindex(dr).ffill()

    path = Path(OUTPUT_DIR) / "cot_forex.parquet"
    df_daily.to_parquet(path)
    print(f"\n  ✅ COT uloženo: {path} | {len(df_daily)} řádků | {len(df_daily.columns)} features")
    return df_daily


# ─── CONGRESSIONAL TRADING FIX ──────────────────────────────────

def fix_congress():
    print(f"\n{'═'*55}")
    print("  🏛️  CONGRESSIONAL TRADING — FIX (capitoltrades.com)")
    print(f"{'═'*55}\n")

    # capitoltrades.com má veřejné JSON API
    # Stránkování: page=1,2,3... po 100 záznámech
    all_trades = []
    headers = {
        "User-Agent": "Mozilla/5.0 (research project, non-commercial)",
        "Accept": "application/json",
    }

    # Zkus přímé CSV z quiverquant (mají congressional trading zdarma)
    sources = [
        # Capitol Trades API
        "https://www.capitoltrades.com/api/trades?page=1&pageSize=100&sortBy=-publishedAt",
        # Alternativa: Quiverquant congressional
        "https://api.quiverquant.com/beta/live/congresstrading",
    ]

    df = None

    # Metoda 1: Capitol Trades stránkování
    print("  Metoda 1: capitoltrades.com API...")
    base_url = "https://www.capitoltrades.com/api/trades"

    for page in range(1, 21):  # Max 20 stran = 2000 transakcí
        try:
            resp = requests.get(
                base_url,
                params={"page": page, "pageSize": 100, "sortBy": "-publishedAt"},
                headers=headers,
                timeout=15
            )

            if resp.status_code == 200:
                data = resp.json()
                trades = data.get("data", data.get("trades", data if isinstance(data, list) else []))
                if not trades:
                    break
                all_trades.extend(trades if isinstance(trades, list) else [])
                print(f"    strana {page}: {len(trades)} transakcí")
                time.sleep(0.5)
            elif resp.status_code == 403:
                print(f"    ⚠️  403 na straně {page} — zkouším alternativu")
                break
            else:
                break

        except Exception as e:
            print(f"    ❌ strana {page}: {e}")
            break

    if all_trades:
        df = pd.DataFrame(all_trades)
        print(f"  ✅ Capitol Trades: {len(df)} transakcí")
    else:
        # Metoda 2: GitHub archiv housestockwatcher (nová URL)
        print("\n  Metoda 2: GitHub archiv...")
        github_urls = [
            "https://raw.githubusercontent.com/jbesomi/congress-stock-scraper/master/data/all_transactions.csv",
            "https://raw.githubusercontent.com/jldbc/coffee-and-coding/master/data/congress_trading/house_trades.csv",
        ]

        for url in github_urls:
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    from io import StringIO
                    df = pd.read_csv(StringIO(resp.text))
                    print(f"  ✅ GitHub: {len(df)} transakcí z {url.split('/')[-1]}")
                    break
            except Exception as e:
                print(f"  ❌ {e}")

    if df is None or len(df) == 0:
        # Metoda 3: Vytvoř placeholder s instrukcemi
        print("\n  ⚠️  Všechny zdroje nedostupné.")
        print("  Manuální download:")
        print("  1. Jdi na https://efts.sec.gov/LATEST/search-index?q=%22Form+4%22&dateRange=custom")
        print("  2. NEBO: https://www.capitoltrades.com → Export CSV")
        print("  3. Ulož jako data/12_ALTERNATIVE/congress_manual.csv")

        # Vytvoř prázdný placeholder
        dr = pd.date_range("2020-01-01", datetime.now(), freq="D")
        df_features = pd.DataFrame(index=dr)
        for ticker in CONGRESS_TICKERS:
            df_features[f"{ticker}_congress_signal"] = 0
        path = Path(OUTPUT_DIR) / "congress_trades.parquet"
        df_features.to_parquet(path)
        print(f"  📝 Placeholder uložen: {path} (všechny hodnoty = 0)")
        return df_features

    # Zpracuj data
    print(f"\n  Sloupce: {list(df.columns[:8])}")

    # Standardizuj
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if any(x in cl for x in ["ticker", "symbol", "asset"]):
            col_map[c] = "ticker"
        elif any(x in cl for x in ["transaction", "type", "trade_type"]):
            col_map[c] = "transaction_type"
        elif any(x in cl for x in ["date", "filed", "traded", "disclosed"]):
            col_map[c] = "date"
    df = df.rename(columns=col_map)

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df = df[df["date"] >= "2020-01-01"]

    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()

    if "transaction_type" in df.columns:
        df["is_buy"]  = df["transaction_type"].str.lower().str.contains("purchase|buy", na=False).astype(int)
        df["is_sell"] = df["transaction_type"].str.lower().str.contains("sale|sell",    na=False).astype(int)
    else:
        df["is_buy"] = 0; df["is_sell"] = 0

    # Features per ticker
    dr = pd.date_range("2020-01-01", datetime.now(), freq="D")
    df_features = pd.DataFrame(index=dr)

    df_our = df[df["ticker"].isin(CONGRESS_TICKERS)] if "ticker" in df.columns else df

    for ticker in CONGRESS_TICKERS:
        df_t = df_our[df_our["ticker"] == ticker] if "ticker" in df_our.columns else pd.DataFrame()
        if len(df_t) == 0:
            df_features[f"{ticker}_congress_signal"] = 0
            continue

        df_t = df_t.set_index("date").sort_index()
        buys  = df_t["is_buy"].resample("D").sum().reindex(dr).fillna(0)
        sells = df_t["is_sell"].resample("D").sum().reindex(dr).fillna(0)

        df_features[f"{ticker}_buy_30d"]  = buys.rolling(30).sum()
        df_features[f"{ticker}_sell_30d"] = sells.rolling(30).sum()
        df_features[f"{ticker}_net_30d"]  = df_features[f"{ticker}_buy_30d"] - df_features[f"{ticker}_sell_30d"]
        net = df_features[f"{ticker}_net_30d"]
        df_features[f"{ticker}_congress_signal"] = np.where(net > 0, 1, np.where(net < 0, -1, 0))

        print(f"  ✅ {ticker}: {len(df_t)} obchodů")

    path = Path(OUTPUT_DIR) / "congress_trades.parquet"
    df_features.to_parquet(path)
    print(f"\n  ✅ Congress uloženo: {path} | {len(df_features.columns)} features")
    return df_features


if __name__ == "__main__":
    fix_cot()
    fix_congress()
    print("\n✅ Hotovo. Spusť python feature_engineering.py")