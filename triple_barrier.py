"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - TRIPLE BARRIER METHOD v2.2                 ║
║     Fix: BARRIER_CONFIGS rozšířen o chybějící kombinace    ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

INPUT_DIR  = "data/04_GOLD_FEATURES"
OUTPUT_DIR = "data/07_TRIPLE_BARRIER"

TIMEFRAMES = ["M5", "M15", "H1"]
CATEGORIES = {
    "forex":  ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"],
    "stocks": ["AAPL", "MSFT", "NVDA", "AMZN"],
}

# ── FIX: původní kód měl jen 4 konfigurace, chyběly:
#    pt=1.5/sl=1.0/t=6  → AMZN M5
#    pt=3.0/sl=1.0/t=24 → USDCHF M15
# Nyní generujeme všechny rozumné kombinace.
BARRIER_CONFIGS = [
    # Krátký horizont (M5 scalping)
    {"pt": 1.5, "sl": 1.0, "t":  6},   # ← byl chybějící
    {"pt": 1.5, "sl": 1.5, "t":  6},
    {"pt": 2.0, "sl": 1.0, "t":  6},
    {"pt": 2.0, "sl": 1.5, "t":  6},
    # Střední horizont (M15)
    {"pt": 1.5, "sl": 1.0, "t": 12},
    {"pt": 1.5, "sl": 1.5, "t": 12},
    {"pt": 2.0, "sl": 1.0, "t": 12},
    {"pt": 2.0, "sl": 1.5, "t": 12},
    # Delší horizont (H1)
    {"pt": 1.5, "sl": 1.5, "t": 24},
    {"pt": 2.0, "sl": 1.0, "t": 24},
    {"pt": 2.0, "sl": 1.5, "t": 24},
    {"pt": 3.0, "sl": 1.0, "t": 24},   # ← byl chybějící
    {"pt": 3.0, "sl": 1.5, "t": 24},
]

MIN_SIGNALS = 10


def triple_barrier_vectorized(df, signal_col, direction, pt, sl, t):
    close  = df["close"].values
    high   = df["high"].values
    low    = df["low"].values
    atr    = df["atr"].values if "atr" in df.columns else np.full(len(df), np.nan)
    signal = df[signal_col].values.astype(bool)

    entry_indices = np.where(signal)[0]
    entry_indices = entry_indices[entry_indices + t + 1 < len(df)]

    if len(entry_indices) < MIN_SIGNALS:
        return None

    labels, exit_idxs, exit_reasons, returns, valid_entries = [], [], [], [], []

    for idx in entry_indices:
        atr_val = atr[idx]
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        entry_price = close[idx]
        if entry_price <= 0:
            continue

        # Správné bariéry pro LONG i SHORT (fix z v2.1)
        if direction == "long":
            tp_level = entry_price + pt * atr_val
            sl_level = entry_price - sl * atr_val
        else:  # short
            tp_level = entry_price - pt * atr_val
            sl_level = entry_price + sl * atr_val

        end   = min(idx + t + 1, len(df))
        highs = high[idx+1:end]
        lows  = low[idx+1:end]

        if direction == "long":
            tp_hits = highs >= tp_level
            sl_hits = lows  <= sl_level
        else:
            tp_hits = lows  <= tp_level
            sl_hits = highs >= sl_level

        tp_idx = np.argmax(tp_hits) if tp_hits.any() else len(tp_hits)
        sl_idx = np.argmax(sl_hits) if sl_hits.any() else len(sl_hits)

        if not tp_hits.any() and not sl_hits.any():
            exit_i = end - 1
            ep     = close[exit_i]
            ret    = (ep - entry_price) / entry_price * 100 if direction == "long" \
                     else (entry_price - ep) / entry_price * 100
            label  = +1 if ret > 0 else (-1 if ret < 0 else 0)
            reason = "time"
        elif tp_hits.any() and (not sl_hits.any() or tp_idx <= sl_idx):
            exit_i = idx + 1 + tp_idx
            ret    = (tp_level - entry_price) / entry_price * 100 if direction == "long" \
                     else (entry_price - tp_level) / entry_price * 100
            label  = +1
            reason = "tp"
        else:
            exit_i = idx + 1 + sl_idx
            ret    = (sl_level - entry_price) / entry_price * 100 if direction == "long" \
                     else (entry_price - sl_level) / entry_price * 100
            label  = -1
            reason = "sl"

        valid_entries.append(idx)
        labels.append(label)
        exit_idxs.append(exit_i)
        exit_reasons.append(reason)
        returns.append(round(ret, 4))

    if not labels:
        return None

    return pd.DataFrame({
        "entry_idx":   valid_entries,
        "exit_idx":    exit_idxs,
        "label":       labels,
        "exit_reason": exit_reasons,
        "ret_pct":     returns,
    })


def compute_stats(ldf, signal_col, direction, pt, sl, t, ticker, tf):
    if ldf is None or len(ldf) == 0:
        return None

    n        = len(ldf)
    tp_hits  = (ldf["exit_reason"] == "tp").sum()
    sl_hits  = (ldf["exit_reason"] == "sl").sum()
    win_rate = tp_hits / n * 100

    wins = ldf[ldf["label"] == +1]["ret_pct"]
    losses = ldf[ldf["label"] == -1]["ret_pct"]
    gp   = wins.sum()
    gl   = abs(losses.sum())
    pf   = round(gp / gl, 2) if gl > 0 else 99.0

    if win_rate >= 55 and pf >= 1.5:
        rating = "STRONG"
    elif win_rate >= 50 and pf >= 1.2:
        rating = "DECENT"
    else:
        rating = "NO EDGE"

    return {
        "ticker": ticker, "timeframe": tf,
        "signal": signal_col.replace("signal_", ""),
        "signal_col": signal_col,
        "direction": direction,
        "pt": pt, "sl": sl, "t": t,
        "n_signals": n, "tp_hits": int(tp_hits), "sl_hits": int(sl_hits),
        "win_rate": round(win_rate, 1),
        "avg_ret":  round(ldf["ret_pct"].mean(), 4),
        "profit_factor": pf,
        "rating": rating,
    }


def infer_direction(name):
    name = name.lower()
    if "overbought_exit" in name: return "short"
    if "oversold_exit"   in name: return "long"
    if "oversold"        in name: return "long"
    for kw in ["bear", "down", "death", "short"]:
        if kw in name: return "short"
    return "long"


def main():
    print("╔══════════════════════════════════════════╗")
    print("║   TRIPLE BARRIER METHOD v2.2            ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  13 barrier configs | všechny kombinace\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_stats = []
    t_start   = datetime.now()

    for tf in TIMEFRAMES:
        print(f"\n=== {tf} ===")
        for category, tickers in CATEGORIES.items():
            for ticker in tickers:
                path = Path(INPUT_DIR) / tf / category / f"{ticker}.parquet"
                if not path.exists():
                    continue

                df = pd.read_parquet(path).reset_index(drop=True)
                signal_cols = [c for c in df.columns if c.startswith("signal_")]
                ticker_stats = []

                for sc in signal_cols:
                    direction = infer_direction(sc)
                    for cfg in BARRIER_CONFIGS:
                        ldf = triple_barrier_vectorized(
                            df, sc, direction, cfg["pt"], cfg["sl"], cfg["t"]
                        )
                        stats = compute_stats(
                            ldf, sc, direction,
                            cfg["pt"], cfg["sl"], cfg["t"], ticker, tf
                        )
                        if stats:
                            if ldf is not None:
                                out_dir = Path(OUTPUT_DIR) / tf
                                out_dir.mkdir(parents=True, exist_ok=True)
                                fname = (f"{ticker}_{sc}"
                                         f"_pt{cfg['pt']}_sl{cfg['sl']}_t{cfg['t']}.parquet")
                                ldf.to_parquet(out_dir / fname, index=False)
                            ticker_stats.append(stats)

                all_stats.extend(ticker_stats)
                strong  = sum(1 for s in ticker_stats if s["rating"] == "STRONG")
                decent  = sum(1 for s in ticker_stats if s["rating"] == "DECENT")
                elapsed = (datetime.now() - t_start).total_seconds()
                print(f"  {ticker:8} {len(ticker_stats):3} kombinaci | "
                      f"STRONG:{strong:2} DECENT:{decent:2} | {elapsed:.0f}s")

    if not all_stats:
        print("\n❌ Žádné výsledky — zkontroluj cestu data/04_GOLD_FEATURES/")
        return

    df_all  = pd.DataFrame(all_stats)
    df_best = (df_all
               .sort_values("profit_factor", ascending=False)
               .drop_duplicates(subset=["ticker", "timeframe", "signal"])
               .reset_index(drop=True))

    strong = df_best[df_best["rating"] == "STRONG"]
    decent = df_best[df_best["rating"] == "DECENT"]

    print(f"\n{'='*75}")
    print("STRONG signály — kandidáti pro meta-labeling")
    print(f"{'='*75}")
    print(f"  {'Signal':<28} {'TF':<5} {'Tick':<7} {'Dir':<6}"
          f"{'WR%':<7} {'PF':<6} {'N':<5} {'PT/SL/T'}")
    print(f"  {'-'*75}")
    for _, r in strong.head(20).iterrows():
        cfg = f"{r['pt']}/{r['sl']}/{r['t']}"
        print(f"  {r['signal']:<28} {r['timeframe']:<5} {r['ticker']:<7}"
              f"{r['direction']:<6} {r['win_rate']:<7} {r['profit_factor']:<6}"
              f"{r['n_signals']:<5} {cfg}")

    elapsed = (datetime.now() - t_start).total_seconds()
    print(f"\n  Celkem kombinaci:  {len(df_all)}")
    print(f"  STRONG:            {len(strong)}")
    print(f"  DECENT:            {len(decent)}")
    print(f"  NO EDGE:           {len(df_best)-len(strong)-len(decent)}")
    print(f"  Čas:               {elapsed:.1f}s")

    df_all.to_csv(os.path.join(OUTPUT_DIR, "triple_barrier_full.csv"),  index=False)
    df_best.to_csv(os.path.join(OUTPUT_DIR, "triple_barrier_best.csv"), index=False)
    print(f"\n  ✅ Uloženo: {OUTPUT_DIR}/triple_barrier_best.csv")
    print(f"  💡 Zkopíruj STRONG signály do meta_labeling.py → STRATEGIES")


if __name__ == "__main__":
    main()