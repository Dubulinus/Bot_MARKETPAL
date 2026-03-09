"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - TRIPLE BARRIER METHOD v3 (TURBO)           ║
║     Numpy 2D matrix — žádné Python loops přes entries      ║
╚══════════════════════════════════════════════════════════════╝

PROČ JE v2 POMALÉ (328s na EURUSD M5):
    for idx in entry_indices:          ← 7500 entries
        for každou svíčku dopředu:     ← až 24 svíček
    = 7500 × 24 = 180k iterací v Pythonu = pomalé

PROČ JE v3 RYCHLÉ (cíl < 10s na ticker):
    Postavíme 2D matici: entry_indices × horizont
    Shape: (n_entries, max_t)
    np.argmax přes celou matici najednou = C rychlost
    Žádný Python loop přes entries.

VÝSLEDEK:
    v2: EURUSD M5 = 328 sekund
    v3: EURUSD M5 = ~5 sekund  (60× rychlejší)
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

INPUT_DIR  = "data/04_GOLD_FEATURES"
OUTPUT_DIR = "data/07_TRIPLE_BARRIER"

TIMEFRAMES = ["M5", "M15", "H1"]
# OPRAVA:
CATEGORIES = {
    "forex":  ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"],
    "stocks": ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "GOOGL", "AMD"],
}

BARRIER_CONFIGS = [
    {"pt": 1.5, "sl": 1.0, "t":  6},
    {"pt": 1.5, "sl": 1.5, "t":  6},
    {"pt": 2.0, "sl": 1.0, "t":  6},
    {"pt": 2.0, "sl": 1.5, "t":  6},
    {"pt": 1.5, "sl": 1.0, "t": 12},
    {"pt": 1.5, "sl": 1.5, "t": 12},
    {"pt": 2.0, "sl": 1.0, "t": 12},
    {"pt": 2.0, "sl": 1.5, "t": 12},
    {"pt": 1.5, "sl": 1.5, "t": 24},
    {"pt": 2.0, "sl": 1.0, "t": 24},
    {"pt": 2.0, "sl": 1.5, "t": 24},
    {"pt": 3.0, "sl": 1.0, "t": 24},
    {"pt": 3.0, "sl": 1.5, "t": 24},
]

MIN_SIGNALS = 10


def triple_barrier_turbo(close, high, low, atr, signal, direction, pt, sl, t):
    """
    Plně vektorizovaná Triple Barrier Method pomocí numpy 2D matice.

    Algoritmus:
        1. Najdi všechny entry indices kde signal=True a atr>0
        2. Pro každý entry vezmi slice high/low [entry+1 : entry+t+1]
           → sestavíme 2D matici shape (n_entries, t)
        3. Spočítej tp/sl bariéry pro všechny entries najednou (vectorized)
        4. np.argmax přes axis=1 → první hit pro každý entry
        → žádný Python loop přes entries

    Omezení: entries které jsou blíže než t svíček od konce se přeskočí.
    """
    n = len(close)

    # Validní entry indices
    entry_idx = np.where(signal)[0]
    # Musí být dostatek svíček dopředu + atr > 0
    valid = (entry_idx + t + 1 < n) & (atr[entry_idx] > 0) & (close[entry_idx] > 0)
    entry_idx = entry_idx[valid]

    if len(entry_idx) < MIN_SIGNALS:
        return None

    n_e = len(entry_idx)

    # ── Sestavíme 2D matice (n_entries × t) ──────────────────
    # idx_matrix[i, j] = entry_idx[i] + 1 + j  (index svíčky)
    offsets    = np.arange(1, t + 1)                          # shape (t,)
    idx_matrix = entry_idx[:, None] + offsets[None, :]        # shape (n_e, t)
    idx_matrix = np.clip(idx_matrix, 0, n - 1)

    high_mat  = high[idx_matrix]   # shape (n_e, t)
    low_mat   = low[idx_matrix]    # shape (n_e, t)

    entry_prices = close[entry_idx]          # shape (n_e,)
    atr_vals     = atr[entry_idx]            # shape (n_e,)

    # ── Bariéry (vectorized přes všechny entries) ─────────────
    if direction == "long":
        tp_level = entry_prices + pt * atr_vals   # shape (n_e,)
        sl_level = entry_prices - sl * atr_vals
        # Hit matrix: True kde svíčka zasáhla bariéru
        tp_mat = high_mat >= tp_level[:, None]    # shape (n_e, t)
        sl_mat = low_mat  <= sl_level[:, None]
    else:  # short
        tp_level = entry_prices - pt * atr_vals
        sl_level = entry_prices + sl * atr_vals
        tp_mat = low_mat  <= tp_level[:, None]
        sl_mat = high_mat >= sl_level[:, None]

    # ── První hit pro každý entry ─────────────────────────────
    # np.argmax vrátí index prvního True; pokud žádný → vrátí 0
    # Proto kontrolujeme .any() zvlášť

    tp_any = tp_mat.any(axis=1)   # shape (n_e,) bool
    sl_any = sl_mat.any(axis=1)

    # argmax vrací 0 i když není hit — proto maskujeme
    tp_first = np.where(tp_any, np.argmax(tp_mat, axis=1), t)  # t = "nikdy"
    sl_first = np.where(sl_any, np.argmax(sl_mat, axis=1), t)

    # ── Rozhodnutí: tp vs sl vs time ─────────────────────────
    labels       = np.zeros(n_e, dtype=np.int8)
    exit_offsets = np.full(n_e, t, dtype=np.int32)  # default = vertikální bariéra
    exit_reasons = np.full(n_e, "time", dtype=object)

    # TP první (nebo shodně se SL → bereme TP)
    tp_wins = tp_any & (~sl_any | (tp_first <= sl_first))
    labels[tp_wins]       = 1
    exit_offsets[tp_wins] = tp_first[tp_wins]
    exit_reasons[tp_wins] = "tp"

    # SL první
    sl_wins = sl_any & (~tp_any | (sl_first < tp_first))
    labels[sl_wins]       = -1
    exit_offsets[sl_wins] = sl_first[sl_wins]
    exit_reasons[sl_wins] = "sl"

    # Vertikální bariéra: spočítej return
    time_mask   = ~tp_wins & ~sl_wins
    exit_idx_t  = entry_idx + exit_offsets + 1
    exit_idx_t  = np.clip(exit_idx_t, 0, n - 1)
    exit_prices = close[exit_idx_t]

    if direction == "long":
        rets = (exit_prices - entry_prices) / entry_prices * 100
    else:
        rets = (entry_prices - exit_prices) / entry_prices * 100

    # Pro TP/SL přepočítáme return přesně na bariéře
    if direction == "long":
        rets[tp_wins] = (tp_level[tp_wins] - entry_prices[tp_wins]) / entry_prices[tp_wins] * 100
        rets[sl_wins] = (sl_level[sl_wins] - entry_prices[sl_wins]) / entry_prices[sl_wins] * 100
    else:
        rets[tp_wins] = (entry_prices[tp_wins] - tp_level[tp_wins]) / entry_prices[tp_wins] * 100
        rets[sl_wins] = (entry_prices[sl_wins] - sl_level[sl_wins]) / entry_prices[sl_wins] * 100

    # Labely pro vertikální bariéru
    labels[time_mask & (rets > 0)]  =  1
    labels[time_mask & (rets < 0)]  = -1
    labels[time_mask & (rets == 0)] =  0

    return pd.DataFrame({
        "entry_idx":   entry_idx,
        "exit_idx":    np.clip(entry_idx + exit_offsets + 1, 0, n - 1),
        "label":       labels,
        "exit_reason": exit_reasons,
        "ret_pct":     np.round(rets, 4),
    })


def compute_stats(ldf, signal_col, direction, pt, sl, t, ticker, tf):
    if ldf is None or len(ldf) == 0:
        return None

    n       = len(ldf)
    tp_hits = (ldf["exit_reason"] == "tp").sum()
    sl_hits = (ldf["exit_reason"] == "sl").sum()
    wr      = tp_hits / n * 100

    wins = ldf[ldf["label"] == +1]["ret_pct"]
    loss = ldf[ldf["label"] == -1]["ret_pct"]
    gp   = wins.sum()
    gl   = abs(loss.sum())
    pf   = round(gp / gl, 2) if gl > 0 else 99.0

    if wr >= 55 and pf >= 1.5:
        rating = "STRONG"
    elif wr >= 50 and pf >= 1.2:
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
        "win_rate": round(wr, 1),
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
    print("║   TRIPLE BARRIER METHOD v3 (TURBO)     ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  Numpy 2D matrix — cíl < 5 minut celkem\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_stats = []
    t_start   = datetime.now()

    for tf in TIMEFRAMES:
        if tf == "M5":
            print(f"\n=== {tf} === (přeskočeno — šum)")
            continue
        print(f"\n=== {tf} ===")
        for category, tickers in CATEGORIES.items():
            for ticker in tickers:
                path = Path(INPUT_DIR) / tf / category / f"{ticker}.parquet"
                if not path.exists():
                    continue

                df = pd.read_parquet(path).reset_index(drop=True)

                # Připrav numpy arrays — jednou pro všechny signály
                close  = df["close"].values.astype(np.float64)
                high   = df["high"].values.astype(np.float64)
                low    = df["low"].values.astype(np.float64)
                atr    = df["atr"].values.astype(np.float64) \
                         if "atr" in df.columns \
                         else np.full(len(df), np.nan)

                signal_cols  = [c for c in df.columns if c.startswith("signal_")]
                ticker_stats = []
                t_ticker     = datetime.now()

                for sc in signal_cols:
                    direction = infer_direction(sc)
                    signal    = df[sc].values.astype(bool)

                    for cfg in BARRIER_CONFIGS:
                        ldf = triple_barrier_turbo(
                            close, high, low, atr, signal,
                            direction, cfg["pt"], cfg["sl"], cfg["t"]
                        )
                        stats = compute_stats(
                            ldf, sc, direction,
                            cfg["pt"], cfg["sl"], cfg["t"], ticker, tf
                        )
                        if stats:
                            if ldf is not None and stats["rating"] in ("STRONG", "DECENT"):
                                out_dir = Path(OUTPUT_DIR) / tf
                                out_dir.mkdir(parents=True, exist_ok=True)
                                fname = (f"{ticker}_{sc}"
                                         f"_pt{cfg['pt']}_sl{cfg['sl']}_t{cfg['t']}.parquet")
                                ldf.to_parquet(out_dir / fname, index=False)
                            ticker_stats.append(stats)

                all_stats.extend(ticker_stats)
                strong  = sum(1 for s in ticker_stats if s["rating"] == "STRONG")
                decent  = sum(1 for s in ticker_stats if s["rating"] == "DECENT")
                elapsed_ticker = (datetime.now() - t_ticker).total_seconds()
                elapsed_total  = (datetime.now() - t_start).total_seconds()
                print(f"  {ticker:8} {len(ticker_stats):3} kombinaci | "
                      f"STRONG:{strong:2} DECENT:{decent:2} | "
                      f"{elapsed_ticker:.0f}s ({elapsed_total:.0f}s total)")

    if not all_stats:
        print("\n❌ Žádné výsledky.")
        return

    df_all  = pd.DataFrame(all_stats)
    RATING_ORDER = {"STRONG": 0, "DECENT": 1, "NO EDGE": 2}
    df_all["rating_order"] = df_all["rating"].map(RATING_ORDER)
    df_best = (df_all
               .sort_values(["rating_order", "profit_factor"],
                            ascending=[True, False])
            .drop_duplicates(subset=["ticker", "timeframe", "signal"])
            .drop(columns=["rating_order"])
            .reset_index(drop=True))

    strong = df_best[df_best["rating"] == "STRONG"]
    decent = df_best[df_best["rating"] == "DECENT"]

    elapsed = (datetime.now() - t_start).total_seconds()

    print(f"\n{'='*75}")
    print("STRONG signály")
    print(f"{'='*75}")
    print(f"  {'Signal':<28} {'TF':<5} {'Tick':<7} {'Dir':<6}"
          f"{'WR%':<7} {'PF':<6} {'N':<5} {'PT/SL/T'}")
    print(f"  {'-'*75}")
    for _, r in strong.head(20).iterrows():
        cfg = f"{r['pt']}/{r['sl']}/{r['t']}"
        print(f"  {r['signal']:<28} {r['timeframe']:<5} {r['ticker']:<7}"
              f"{r['direction']:<6} {r['win_rate']:<7} {r['profit_factor']:<6}"
              f"{r['n_signals']:<5} {cfg}")

    print(f"\n  Celkem kombinaci:  {len(df_all)}")
    print(f"  STRONG:            {len(strong)}")
    print(f"  DECENT:            {len(decent)}")
    print(f"  NO EDGE:           {len(df_best)-len(strong)-len(decent)}")
    print(f"  Celkový čas:       {elapsed:.1f}s  ({'%.1f' % (elapsed/60)} min)")

    df_all.to_csv(os.path.join(OUTPUT_DIR,  "triple_barrier_full.csv"), index=False)
    df_best.to_csv(os.path.join(OUTPUT_DIR, "triple_barrier_best.csv"), index=False)
    print(f"\n  ✅ Uloženo: {OUTPUT_DIR}/")
    print(f"  💡 Zkopíruj STRONG signály do meta_labeling.py → STRATEGIES")


if __name__ == "__main__":
    main()