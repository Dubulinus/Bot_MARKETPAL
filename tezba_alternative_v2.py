"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - ALTERNATIVE DATA v2.0                      ║
║     SEC EDGAR + Congressional Trading + Options Flow       ║
╚══════════════════════════════════════════════════════════════╝

NOVÉ DATA CHANNELS:
  1. SEC EDGAR Form 4     — insider trading (buy/sell od insiderů)
  2. Congressional trades  — kongresmani kupují/prodávají akcie
  3. CBOE Options flow     — put/call ratio jako sentiment indikátor

VÝSTUP:
  data/12_ALTERNATIVE/sec_insider.parquet
  data/12_ALTERNATIVE/congressional.parquet
  data/12_ALTERNATIVE/options_flow.parquet
  data/12_ALTERNATIVE/alternative_features.parquet  ← merged

SPUŠTĚNÍ:
  python tezba_alternative_v2.py           # vše
  python tezba_alternative_v2.py sec       # jen SEC
  python tezba_alternative_v2.py congress  # jen Congress
  python tezba_alternative_v2.py options   # jen Options
"""

import sys
import time
import json
import requests
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

warnings.filterwarnings("ignore")

try:
    from config import C
    from logger import get_logger, setup_global_exception_handler
    log = get_logger("tezba_alternative_v2")
    setup_global_exception_handler("tezba_alternative_v2")
    ALT_DIR = C.PATHS.ALT_DIR
except ImportError:
    import logging
    log = logging.getLogger("tezba_alternative_v2")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    ALT_DIR = Path("data/12_ALTERNATIVE")

ALT_DIR.mkdir(parents=True, exist_ok=True)

# Akcie které sledujeme (z MARKETPAL portfolia)
TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "GOOGL", "AMD"]

# Headers pro SEC (vyžadují User-Agent)
SEC_HEADERS = {
    "User-Agent": "MARKETPAL Research bot@marketpal.local",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov"
}


# ═══════════════════════════════════════════════════════════════
# SEKCE 1: SEC EDGAR — INSIDER TRADING (Form 4)
# ═══════════════════════════════════════════════════════════════

# Cache pro CIK mapping
_CIK_CACHE = {}

# Hardcoded CIK map pro sledované tickery (stabilnější než live API)
# CIK = SEC identifikátor firmy
_CIK_MAP = {
    "AAPL":  "0000320193",
    "MSFT":  "0000789019",
    "NVDA":  "0001045810",
    "AMZN":  "0001018724",
    "TSLA":  "0001318605",
    "META":  "0001326801",
    "GOOGL": "0001652044",
    "AMD":   "0000002488",
    "NFLX":  "0001065280",
    "INTC":  "0000050863",
}

def _load_cik_cache():
    """Načte CIK z hardcoded map + zkusí live update ze SEC."""
    global _CIK_CACHE
    if _CIK_CACHE:
        return
    # Začni s hardcoded
    _CIK_CACHE.update(_CIK_MAP)
    log.info(f"CIK cache: {len(_CIK_CACHE)} tickerů (hardcoded)")
    # Zkus live update
    try:
        mapping_url = "https://www.sec.gov/files/company_tickers.json"
        resp = requests.get(mapping_url, headers=SEC_HEADERS, timeout=10)
        time.sleep(0.15)
        if resp.status_code == 200:
            data = resp.json()
            for key, val in data.items():
                t = val.get("ticker", "").upper()
                if t:
                    _CIK_CACHE[t] = str(val["cik_str"]).zfill(10)
            log.info(f"CIK cache live update: {len(_CIK_CACHE)} tickerů")
    except Exception:
        log.info("CIK cache: live update přeskočen, používám hardcoded")


def get_cik_for_ticker(ticker: str) -> Optional[str]:
    """Převede ticker na CIK číslo pro SEC EDGAR."""
    _load_cik_cache()
    return _CIK_CACHE.get(ticker.upper())


def fetch_insider_trades(ticker: str, days_back: int = 365) -> pd.DataFrame:
    """
    Stáhne Form 4 (insider trading) pro daný ticker z SEC EDGAR.
    Form 4 = povinné hlášení nákupů/prodejů od insiderů do 2 dnů.
    """
    log.info(f"SEC EDGAR Form 4: {ticker}")

    cik = get_cik_for_ticker(ticker)
    if not cik:
        log.warning(f"CIK nenalezeno pro {ticker}")
        return pd.DataFrame()

    # Stáhni submissions (seznam filings)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=15)
        time.sleep(0.1)  # SEC rate limit: max 10 req/sec

        if resp.status_code != 200:
            log.warning(f"SEC EDGAR error {resp.status_code} pro {ticker}")
            return pd.DataFrame()

        data = resp.json()

        # Filtruj Form 4 filings
        filings = data.get("filings", {}).get("recent", {})
        if not filings:
            return pd.DataFrame()

        forms       = filings.get("form", [])
        dates       = filings.get("filingDate", [])
        accessions  = filings.get("accessionNumber", [])
        descriptions= filings.get("primaryDocument", [])

        rows = []
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        for form, date, acc, desc in zip(forms, dates, accessions, descriptions):
            if form != "4":
                continue
            try:
                filing_date = datetime.strptime(date, "%Y-%m-%d")
                if filing_date < cutoff:
                    continue
                rows.append({
                    "ticker":       ticker,
                    "date":         date,
                    "form":         form,
                    "accession":    acc,
                    "document":     desc,
                    "source":       "SEC_EDGAR",
                })
            except Exception:
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        log.info(f"  {ticker}: {len(df)} Form 4 filings nalezeno")
        return df

    except Exception as e:
        log.error(f"SEC EDGAR error pro {ticker}: {e}")
        return pd.DataFrame()


def fetch_insider_sentiment(ticker: str, df_filings: pd.DataFrame) -> pd.DataFrame:
    """
    Z počtu Form 4 filings vytvoří denní sentiment signal.
    Logika: více filings = více insider aktivity = potenciální signal.
    """
    if df_filings.empty:
        return pd.DataFrame()

    # Agreguj per den
    daily = df_filings.groupby(df_filings["date"].dt.date).agg(
        insider_filings=("accession", "count"),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["ticker"] = ticker

    # Rolling 30-denní průměr
    daily = daily.sort_values("date")
    daily["insider_filing_ma30"] = daily["insider_filings"].rolling(30, min_periods=1).mean()
    daily["insider_activity_zscore"] = (
        (daily["insider_filings"] - daily["insider_filing_ma30"]) /
        daily["insider_filings"].rolling(30, min_periods=1).std().clip(lower=0.01)
    )

    # Signal: z-score > 1.5 = neobvyklá aktivita
    daily["signal_insider_unusual"] = (daily["insider_activity_zscore"] > 1.5).astype(int)

    return daily


def run_sec_edgar(tickers: list = TICKERS) -> pd.DataFrame:
    """Stáhne insider trading data pro všechny tickery."""
    log.info("═" * 50)
    log.info("SEC EDGAR — Form 4 Insider Trading")
    log.info("═" * 50)

    all_filings   = []
    all_sentiment = []

    for ticker in tickers:
        filings   = fetch_insider_trades(ticker)
        if not filings.empty:
            sentiment = fetch_insider_sentiment(ticker, filings)
            all_filings.append(filings)
            if not sentiment.empty:
                all_sentiment.append(sentiment)
        time.sleep(0.15)  # SEC rate limit

    if all_filings:
        df_filings = pd.concat(all_filings, ignore_index=True)
        df_filings.to_parquet(ALT_DIR / "sec_insider_filings.parquet")
        log.info(f"Uloženo: sec_insider_filings.parquet ({len(df_filings)} řádků)")

    if all_sentiment:
        df_sentiment = pd.concat(all_sentiment, ignore_index=True)
        df_sentiment.to_parquet(ALT_DIR / "sec_insider.parquet")
        log.info(f"Uloženo: sec_insider.parquet ({len(df_sentiment)} řádků)")
        return df_sentiment

    log.warning("SEC EDGAR: žádná data")
    return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════
# SEKCE 2: CONGRESSIONAL TRADING
# ═══════════════════════════════════════════════════════════════

def fetch_congressional_trades_unusualwhales() -> pd.DataFrame:
    """
    Stáhne obchody kongresmánů z Unusual Whales API (free tier).
    STOCK Act vyžaduje hlášení do 45 dnů.
    """
    log.info("Congressional trading — Unusual Whales")

    url = "https://api.unusualwhales.com/api/congress/trades"
    headers = {"Accept": "application/json"}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            trades = data.get("data", [])
            if trades:
                df = pd.DataFrame(trades)
                log.info(f"  Unusual Whales: {len(df)} obchodů")
                return df
        else:
            log.warning(f"  Unusual Whales: {resp.status_code}")
    except Exception as e:
        log.warning(f"  Unusual Whales error: {e}")

    return pd.DataFrame()


def fetch_congressional_trades_quiverquant() -> pd.DataFrame:
    """
    Fallback: QuiverQuant congressional trading (free tier).
    """
    log.info("Congressional trading — QuiverQuant fallback")

    url = "https://api.quiverquant.com/beta/live/congresstrading"
    headers = {"Accept": "application/json"}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                df = pd.DataFrame(data)
                log.info(f"  QuiverQuant: {len(df)} obchodů")
                return df
    except Exception as e:
        log.warning(f"  QuiverQuant error: {e}")

    return pd.DataFrame()


def process_congressional_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Zpracuje surová data na obchodovatelné signály.
    Logika: Kongresmáni mají statisticky lepší výsledky než trh
    → jejich nákupy jsou bullish signal pro daný ticker.
    """
    if df.empty:
        return pd.DataFrame()

    # Normalize column names (různé API mají různá jména)
    col_map = {
        "ticker": ["ticker", "symbol", "stock"],
        "date":   ["transaction_date", "date", "filed_date", "reportDate"],
        "type":   ["transaction_type", "type", "trade_type", "transaction"],
        "amount": ["amount", "range", "value", "trade_size"],
        "name":   ["name", "politician", "representative", "senator"],
    }

    normalized = {}
    for target, candidates in col_map.items():
        for c in candidates:
            if c in df.columns:
                normalized[target] = df[c]
                break
        if target not in normalized:
            normalized[target] = pd.Series(["UNKNOWN"] * len(df))

    result = pd.DataFrame(normalized)

    # Normalize ticker (může být "AAPL US", "AAPL.US" atd.)
    result["ticker"] = result["ticker"].str.upper().str.split().str[0].str.replace(r'[^A-Z]', '', regex=True)

    # Debug: ukáž top tickery v datech
    top_tickers = result["ticker"].value_counts().head(10).index.tolist()
    log.info(f"  QuiverQuant top tickery: {top_tickers}")

    # Filtruj na naše tickery (nebo všechny pokud žádný nesedí)
    filtered = result[result["ticker"].isin(TICKERS)]
    if filtered.empty:
        log.info("  Žádné naše tickery → ukládám všechna data jako fallback")
        return result  # vrať vše, zpracuj v merge fázi
    result = filtered

    if result.empty:
        log.warning("Congressional: žádné obchody pro sledované tickery")
        return pd.DataFrame()

    # Parse datum
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result = result.dropna(subset=["date"])

    # Směr obchodu
    result["is_buy"] = result["type"].str.lower().str.contains(
        "purchase|buy|bought", na=False
    ).astype(int)
    result["is_sell"] = result["type"].str.lower().str.contains(
        "sale|sell|sold", na=False
    ).astype(int)

    # Agreguj per ticker per den
    daily = result.groupby(["ticker", result["date"].dt.date]).agg(
        congress_buys=("is_buy", "sum"),
        congress_sells=("is_sell", "sum"),
        congress_trades=("type", "count"),
        congress_politicians=("name", "nunique"),
    ).reset_index()

    daily.columns = ["ticker", "date", "congress_buys",
                     "congress_sells", "congress_trades", "congress_politicians"]
    daily["date"] = pd.to_datetime(daily["date"])

    # Net signal: buys - sells
    daily["congress_net"] = daily["congress_buys"] - daily["congress_sells"]
    daily["signal_congress_bull"] = (daily["congress_net"] > 0).astype(int)
    daily["signal_congress_bear"] = (daily["congress_net"] < 0).astype(int)

    log.info(f"Congressional: {len(daily)} řádků zpracováno")
    return daily


def run_congressional(tickers: list = TICKERS) -> pd.DataFrame:
    """Stáhne a zpracuje congressional trading data."""
    log.info("═" * 50)
    log.info("Congressional Trading Data")
    log.info("═" * 50)

    # Zkus Unusual Whales nejdřív, pak fallback
    df = fetch_congressional_trades_unusualwhales()
    if df.empty:
        df = fetch_congressional_trades_quiverquant()

    if df.empty:
        log.warning("Congressional: API nedostupné — ukládám prázdný placeholder")
        # Ulož prázdný soubor se správnou strukturou
        empty = pd.DataFrame(columns=[
            "ticker", "date", "congress_buys", "congress_sells",
            "congress_trades", "congress_politicians",
            "congress_net", "signal_congress_bull", "signal_congress_bear"
        ])
        empty.to_parquet(ALT_DIR / "congressional.parquet")
        return empty

    result = process_congressional_data(df)
    if not result.empty:
        result.to_parquet(ALT_DIR / "congressional.parquet")
        log.info(f"Uloženo: congressional.parquet ({len(result)} řádků)")

    return result


# ═══════════════════════════════════════════════════════════════
# SEKCE 3: OPTIONS FLOW — PUT/CALL RATIO
# ═══════════════════════════════════════════════════════════════

def fetch_cboe_pcr() -> pd.DataFrame:
    """
    Stáhne historický Put/Call Ratio z CBOE.
    PCR > 1.0 = více puts = bearish sentiment
    PCR < 0.7 = více calls = bullish sentiment
    PCR je jeden z nejlepších contrarian indikátorů.
    """
    log.info("CBOE Options — Put/Call Ratio")

    # CBOE poskytuje daily data zdarma
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

    # Stáhni VIX (proxy pro options sentiment)
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            df.columns = [c.strip().upper() for c in df.columns]

            if "DATE" in df.columns:
                df["date"] = pd.to_datetime(df["DATE"], errors="coerce")
                df = df.dropna(subset=["date"])

                # VIX jako proxy pro options sentiment
                if "CLOSE" in df.columns:
                    df = df.rename(columns={"CLOSE": "vix_close",
                                            "OPEN": "vix_open",
                                            "HIGH": "vix_high",
                                            "LOW": "vix_low"})
                    df["vix_close"] = pd.to_numeric(df["vix_close"], errors="coerce")
                    df = df.dropna(subset=["vix_close"])

                    log.info(f"  VIX data: {len(df)} řádků")
                    return df[["date", "vix_close", "vix_open", "vix_high", "vix_low"]]

    except Exception as e:
        log.warning(f"CBOE VIX error: {e}")

    return pd.DataFrame()


def fetch_equity_pcr() -> pd.DataFrame:
    """
    Equity Put/Call Ratio z CBOE.
    Toto je specifičtější než total PCR — jen equity options.
    """
    log.info("CBOE — Equity Put/Call Ratio")

    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/PC_History.csv"

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            df.columns = [c.strip().upper() for c in df.columns]

            if "DATE" in df.columns:
                df["date"] = pd.to_datetime(df["DATE"], errors="coerce")
                df = df.dropna(subset=["date"])

                # PC = put/call ratio
                if "PC" in df.columns or "TOTAL" in df.columns:
                    col = "PC" if "PC" in df.columns else "TOTAL"
                    df = df.rename(columns={col: "equity_pcr"})
                    df["equity_pcr"] = pd.to_numeric(df["equity_pcr"], errors="coerce")
                    df = df.dropna(subset=["equity_pcr"])

                    log.info(f"  Equity PCR: {len(df)} řádků")
                    return df[["date", "equity_pcr"]]

    except Exception as e:
        log.warning(f"CBOE PCR error: {e}")

    return pd.DataFrame()


def process_options_flow(df_vix: pd.DataFrame,
                          df_pcr: pd.DataFrame) -> pd.DataFrame:
    """
    Vytvoří obchodovatelné signály z VIX a PCR dat.

    Strategie:
    - VIX spike (>30) = fear = contrarian buy signal
    - VIX low (<15) = complacency = caution
    - PCR >1.2 = extreme fear = contrarian bull
    - PCR <0.6 = extreme greed = contrarian bear
    """
    if df_vix.empty and df_pcr.empty:
        return pd.DataFrame()

    # Merge VIX a PCR
    if not df_vix.empty and not df_pcr.empty:
        df = df_vix.merge(df_pcr, on="date", how="outer")
    elif not df_vix.empty:
        df = df_vix.copy()
        df["equity_pcr"] = np.nan
    else:
        df = df_pcr.copy()
        df["vix_close"] = np.nan

    df = df.sort_values("date")

    # VIX features
    if "vix_close" in df.columns:
        df["vix_ma20"] = df["vix_close"].rolling(20, min_periods=1).mean()
        df["vix_zscore"] = (
            (df["vix_close"] - df["vix_ma20"]) /
            df["vix_close"].rolling(20, min_periods=1).std().clip(lower=0.1)
        )
        df["vix_spike"] = (df["vix_close"] > 30).astype(int)
        df["vix_low"]   = (df["vix_close"] < 15).astype(int)

        # Contrarian signály (VIX spike = buy)
        df["signal_vix_fear_buy"]  = (df["vix_zscore"] > 2.0).astype(int)
        df["signal_vix_greed_sell"]= (df["vix_zscore"] < -1.5).astype(int)

    # PCR features
    if "equity_pcr" in df.columns:
        df["pcr_ma10"] = df["equity_pcr"].rolling(10, min_periods=1).mean()
        df["pcr_extreme_fear"]  = (df["equity_pcr"] > 1.2).astype(int)
        df["pcr_extreme_greed"] = (df["equity_pcr"] < 0.6).astype(int)

        # Contrarian
        df["signal_pcr_bull"] = df["pcr_extreme_fear"]   # fear → buy
        df["signal_pcr_bear"] = df["pcr_extreme_greed"]  # greed → sell

    # Kombinovaný sentiment score (-1 bearish ... +1 bullish)
    bull_signals = []
    bear_signals = []

    if "signal_vix_fear_buy" in df.columns:
        bull_signals.append(df["signal_vix_fear_buy"])
    if "signal_pcr_bull" in df.columns:
        bull_signals.append(df["signal_pcr_bull"])
    if "signal_vix_greed_sell" in df.columns:
        bear_signals.append(df["signal_vix_greed_sell"])
    if "signal_pcr_bear" in df.columns:
        bear_signals.append(df["signal_pcr_bear"])

    if bull_signals:
        df["options_bull_score"] = sum(bull_signals) / len(bull_signals)
    if bear_signals:
        df["options_bear_score"] = sum(bear_signals) / len(bear_signals)

    log.info(f"Options flow: {len(df)} řádků zpracováno")
    return df


def run_options_flow() -> pd.DataFrame:
    """Stáhne a zpracuje options flow data."""
    log.info("═" * 50)
    log.info("Options Flow — VIX + Put/Call Ratio")
    log.info("═" * 50)

    df_vix = fetch_cboe_pcr()
    df_pcr = fetch_equity_pcr()

    result = process_options_flow(df_vix, df_pcr)

    if not result.empty:
        result.to_parquet(ALT_DIR / "options_flow.parquet")
        log.info(f"Uloženo: options_flow.parquet ({len(result)} řádků)")

    return result


# ═══════════════════════════════════════════════════════════════
# SEKCE 4: MERGE — ALTERNATIVE FEATURES
# ═══════════════════════════════════════════════════════════════

def merge_alternative_features(df_insider: pd.DataFrame,
                                 df_congress: pd.DataFrame,
                                 df_options: pd.DataFrame) -> pd.DataFrame:
    """
    Sloučí všechny alternativní data do jednoho feature souboru.
    Tento soubor se pak merge-uje s gold features pro trénink modelu.
    """
    log.info("Mergování alternative features...")

    # Options jsou globální (ne per ticker)
    # Insider a Congressional jsou per ticker

    frames = []

    for ticker in TICKERS:
        # Base frame — denní index od 2015
        dates = pd.date_range("2015-01-01", datetime.utcnow().date(), freq="B")
        df = pd.DataFrame({"date": dates, "ticker": ticker})

        # Přidej insider data
        if not df_insider.empty and "ticker" in df_insider.columns:
            ins = df_insider[df_insider["ticker"] == ticker][
                ["date", "insider_filings", "insider_activity_zscore",
                 "signal_insider_unusual"]
            ].copy()
            df = df.merge(ins, on="date", how="left")

        # Přidej congressional data
        congress_cols = ["congress_buys", "congress_sells",
                         "congress_net", "signal_congress_bull", "signal_congress_bear"]
        if not df_congress.empty and "ticker" in df_congress.columns:
            available = [c for c in congress_cols if c in df_congress.columns]
            if available:
                con = df_congress[df_congress["ticker"] == ticker][
                    ["date"] + available
                ].copy()
                df = df.merge(con, on="date", how="left")

        # Přidej options data (globální)
        if not df_options.empty:
            opt_cols = ["date"] + [c for c in df_options.columns
                                   if c not in ["date"]]
            df = df.merge(df_options[opt_cols], on="date", how="left")

        frames.append(df)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)

    # Fill NaN signálů s 0
    signal_cols = [c for c in result.columns if c.startswith("signal_")]
    result[signal_cols] = result[signal_cols].fillna(0).astype(int)

    # Fill NaN numerických s forward fill
    num_cols = result.select_dtypes(include=[np.number]).columns
    result[num_cols] = result.groupby("ticker")[num_cols].transform(
        lambda x: x.ffill().fillna(0)
    )

    result.to_parquet(ALT_DIR / "alternative_features.parquet")
    log.info(f"Uloženo: alternative_features.parquet ({len(result)} řádků)")
    log.info(f"Sloupce: {list(result.columns)}")

    return result


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main(mode: str = "all"):
    log.info("╔══════════════════════════════════════════════════╗")
    log.info("║   ALTERNATIVE DATA v2.0                        ║")
    log.info("║   SEC EDGAR + Congressional + Options Flow     ║")
    log.info("╚══════════════════════════════════════════════════╝")

    df_insider  = pd.DataFrame()
    df_congress = pd.DataFrame()
    df_options  = pd.DataFrame()

    if mode in ("all", "sec"):
        df_insider = run_sec_edgar()

    if mode in ("all", "congress"):
        df_congress = run_congressional()

    if mode in ("all", "options"):
        df_options = run_options_flow()

    if mode == "all":
        merged = merge_alternative_features(df_insider, df_congress, df_options)

        log.info("═" * 50)
        log.info("SOUHRN:")
        log.info(f"  SEC insider:    {len(df_insider)} řádků")
        log.info(f"  Congressional:  {len(df_congress)} řádků")
        log.info(f"  Options flow:   {len(df_options)} řádků")
        log.info(f"  Merged:         {len(merged)} řádků")
        log.info("═" * 50)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    main(mode)