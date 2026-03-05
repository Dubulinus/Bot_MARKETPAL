"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - TRIPLE BARRIER METHOD                   ║
║         Marcos Lopez de Prado - AFML Chapter 3              ║
╚══════════════════════════════════════════════════════════════╝

PROČ TRIPLE BARRIER?

    Starý způsob (edge_matrix.py):
        "Jak dopadla cena za 12 svíček?"
        → Ignoruje stop lossy
        → Ignoruje kdy přesně trade skončil
        → Výsledky jsou příliš optimistické

    Triple Barrier (Marcos):
        Každý obchod má 3 bariéry:
        ┌─────────────────────────────┐
        │  ──── Horní (TP) ────────   │  ← +pt * ATR nad entry
        │                             │
        │  Entry ●                    │
        │                             │
        │  ──── Dolní (SL) ────────   │  ← -pt * ATR pod entry
        │  |← max t svíček →|         │  ← Vertikální bariéra
        └─────────────────────────────┘

        Label = která bariéra se dotkne PRVNÍ:
            +1  = TP hit → úspěšný obchod
            -1  = SL hit → ztratový obchod
             0  = čas vypršel → žádný výsledek

        Toto je realita. Ne "kde byla cena za N svíček."

META-LABELING (Marcos krok 2):
    Primární model  → říká SMĚR (long/short)
    Meta model      → říká JDU / NEJDU (filtruje špatné vstupy)
    Výsledek: méně obchodů, vyšší kvalita, lepší Sharpe

VÝSTUP:
    data/07_TRIPLE_BARRIER/
        {TICKER}_{TF}_labels.parquet   → labely pro každý signal
        {TICKER}_{TF}_stats.csv        → statistiky per signal
        triple_barrier_summary.csv     → přehled všech signálů
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# ─── CONFIG ────────────────────────────────────────────────────

INPUT_DIR  = "data/04_GOLD_FEATURES"
OUTPUT_DIR = "data/07_TRIPLE_BARRIER"

TIMEFRAMES = ["M5", "M15", "H1"]
CATEGORIES = {
    "forex":  ["EURUSD", "GBPUSD", "USDJPY", "USDCHF"],
    "stocks": ["AAPL", "MSFT", "NVDA", "AMZN"],
}

# Triple Barrier parametry
# pt = profit taking multiplikátor (horní bariéra = pt * ATR)
# sl = stop loss multiplikátor    (dolní bariéra = sl * ATR)
# t  = max svíček (vertikální bariéra)
BARRIER_CONFIGS = [
    {"pt": 1.5, "sl": 1.5, "t": 12},   # symetrický, krátký
    {"pt": 2.0, "sl": 1.0, "t": 12},   # asymetrický R:R 2:1, krátký
    {"pt": 1.5, "sl": 1.5, "t": 24},   # symetrický, dlouhý
    {"pt": 2.0, "sl": 1.0, "t": 24},   # asymetrický R:R 2:1, dlouhý
]

# Signály k testování (všechny signal_ sloupce z Gold dat)
MIN_SIGNALS = 10   # minimální počet výskytů signálu pro statistiku

# ─── TRIPLE BARRIER LABELING ───────────────────────────────────

def get_triple_barrier_label(df, entry_idx, direction, pt, sl, t):
    """
    Pro jeden vstupní bod vrátí label Triple Barrier Method.

    Args:
        df:         DataFrame se OHLCV daty
        entry_idx:  index řádku kde vstupujeme
        direction:  'long' nebo 'short'
        pt:         profit target v násobcích ATR
        sl:         stop loss v násobcích ATR
        t:          max počet svíček (vertikální bariéra)

    Returns:
        label:      +1 (TP hit), -1 (SL hit), 0 (čas vypršel)
        exit_idx:   index svíčky kde obchod skončil
        exit_reason: 'tp', 'sl', 'time'
        ret:        procentuální return obchodu
    """
    atr = df.iloc[entry_idx].get("atr", np.nan)
    if pd.isna(atr) or atr <= 0:
        return np.nan, entry_idx, "no_atr", 0.0

    # Entry na zavření signální svíčky
    # (realistické: mohli bychom vzít next_open, ale pro labeling je close OK)
    entry_price = df.iloc[entry_idx]["close"]
    if entry_price <= 0:
        return np.nan, entry_idx, "no_price", 0.0

    # Bariéry
    upper = entry_price + pt * atr   # TP pro long, SL pro short
    lower = entry_price - sl * atr   # SL pro long, TP pro short

    # Procházej svíčky dokud nenarazíš na bariéru nebo čas
    end_idx = min(entry_idx + t + 1, len(df))

    for i in range(entry_idx + 1, end_idx):
        high = df.iloc[i]["high"]
        low  = df.iloc[i]["low"]

        if direction == "long":
            if high >= upper:
                ret = (upper - entry_price) / entry_price * 100
                return +1, i, "tp", ret
            if low <= lower:
                ret = (lower - entry_price) / entry_price * 100
                return -1, i, "sl", ret
        else:  # short
            if low <= lower:   # lower = TP pro short
                ret = (entry_price - lower) / entry_price * 100
                return +1, i, "tp", ret
            if high >= upper:  # upper = SL pro short
                ret = (entry_price - upper) / entry_price * 100
                return -1, i, "sl", ret

    # Vertikální bariéra — čas vypršel
    if end_idx - 1 < len(df):
        exit_price = df.iloc[end_idx - 1]["close"]
        if direction == "long":
            ret = (exit_price - entry_price) / entry_price * 100
        else:
            ret = (entry_price - exit_price) / entry_price * 100
        label = +1 if ret > 0 else (-1 if ret < 0 else 0)
        return label, end_idx - 1, "time", ret

    return 0, entry_idx + t, "time", 0.0


def label_signal(df, signal_col, direction, pt, sl, t):
    """
    Prolabeluje všechny výskyty signálu Triple Barrier metodou.

    Vrátí DataFrame s labely a statistikami.
    """
    signal_rows = df[df[signal_col] == True].index.tolist()

    if len(signal_rows) < MIN_SIGNALS:
        return None

    labels  = []
    for idx in signal_rows:
        # Přeskoč pokud jsme příliš blízko konce dat
        if idx + t + 1 >= len(df):
            continue

        label, exit_idx, exit_reason, ret = get_triple_barrier_label(
            df, idx, direction, pt, sl, t
        )

        if pd.isna(label):
            continue

        labels.append({
            "entry_idx":   idx,
            "exit_idx":    exit_idx,
            "label":       label,
            "exit_reason": exit_reason,
            "ret_pct":     round(ret, 4),
            "entry_price": df.iloc[idx]["close"],
            "exit_price":  df.iloc[exit_idx]["close"] if exit_idx < len(df) else np.nan,
        })

    if not labels:
        return None

    return pd.DataFrame(labels)


def compute_barrier_stats(labels_df, signal_col, direction, pt, sl, t,
                          ticker, tf):
    """Spočítá statistiky pro jeden signál + barrier config."""
    if labels_df is None or len(labels_df) == 0:
        return None

    n         = len(labels_df)
    tp_hits   = (labels_df["exit_reason"] == "tp").sum()
    sl_hits   = (labels_df["exit_reason"] == "sl").sum()
    time_hits = (labels_df["exit_reason"] == "time").sum()

    # Win rate = TP hits / všechny labely
    win_rate  = tp_hits / n * 100

    # Průměrný return
    avg_ret   = labels_df["ret_pct"].mean()
    avg_win   = labels_df[labels_df["label"] == +1]["ret_pct"].mean()
    avg_loss  = labels_df[labels_df["label"] == -1]["ret_pct"].mean()

    # Profit factor
    gross_profit = labels_df[labels_df["ret_pct"] > 0]["ret_pct"].sum()
    gross_loss   = abs(labels_df[labels_df["ret_pct"] < 0]["ret_pct"].sum())
    pf           = gross_profit / gross_loss if gross_loss > 0 else np.inf

    # Edge ratio (Marcos) — průměrný win / průměrná ztráta abs
    edge_ratio = abs(avg_win / avg_loss) if avg_loss and avg_loss != 0 else 0

    # Rating
    if win_rate >= 55 and pf >= 1.5:
        rating = "🔥 STRONG"
    elif win_rate >= 50 and pf >= 1.2:
        rating = "✅ DECENT"
    else:
        rating = "❌ NO EDGE"

    return {
        "ticker":       ticker,
        "timeframe":    tf,
        "signal":       signal_col.replace("signal_", ""),
        "direction":    direction,
        "pt":           pt,
        "sl":           sl,
        "t":            t,
        "n_signals":    n,
        "tp_hits":      tp_hits,
        "sl_hits":      sl_hits,
        "time_hits":    time_hits,
        "win_rate":     round(win_rate, 1),
        "avg_ret":      round(avg_ret, 4),
        "avg_win":      round(avg_win, 4) if not pd.isna(avg_win) else 0,
        "avg_loss":     round(avg_loss, 4) if not pd.isna(avg_loss) else 0,
        "profit_factor": round(pf, 2) if pf != np.inf else 99.0,
        "edge_ratio":   round(edge_ratio, 2),
        "rating":       rating,
    }


# ─── DETEKCE SMĚRU ─────────────────────────────────────────────

def infer_direction(signal_name):
    """
    Odhadni směr obchodu ze jména signálu.
    Tohle lze přepsat manuálně pro přesnější kontrolu.
    """
    bearish_keywords = [
        "bear", "down", "death", "overbought", "short",
        "breakdown", "oversold_exit"
    ]
    bullish_keywords = [
        "bull", "up", "golden", "oversold", "long",
        "breakout_up", "above"
    ]

    name = signal_name.lower()

    # Speciální případy
    if "overbought_exit" in name:
        return "short"   # exit z overbought = short trade
    if "oversold_exit" in name:
        return "long"    # exit z oversold = long trade
    if "oversold" in name and "exit" not in name:
        return "long"    # oversold = čekáme long reversal

    for kw in bearish_keywords:
        if kw in name:
            return "short"
    for kw in bullish_keywords:
        if kw in name:
            return "long"

    return "long"  # default


# ─── HLAVNÍ FUNKCE ─────────────────────────────────────────────

def process_file(path, ticker, tf):
    """Zpracuje jeden Gold parquet soubor."""
    df = pd.read_parquet(path).reset_index(drop=True)

    signal_cols = [c for c in df.columns if c.startswith("signal_")]
    if not signal_cols:
        return []

    all_stats = []

    for signal_col in signal_cols:
        direction = infer_direction(signal_col)

        for config in BARRIER_CONFIGS:
            pt, sl, t = config["pt"], config["sl"], config["t"]

            labels_df = label_signal(df, signal_col, direction, pt, sl, t)
            stats     = compute_barrier_stats(
                labels_df, signal_col, direction, pt, sl, t, ticker, tf
            )

            if stats:
                all_stats.append(stats)

                # Ulož labels pro tento signál + config
                if labels_df is not None:
                    out_dir = Path(OUTPUT_DIR) / tf
                    out_dir.mkdir(parents=True, exist_ok=True)
                    label_path = out_dir / f"{ticker}_{signal_col}_pt{pt}_sl{sl}_t{t}.parquet"
                    labels_df.to_parquet(label_path, index=False)

    return all_stats


def main():
    print("╔══════════════════════════════════════════╗")
    print("║   MARKETPAL TRIPLE BARRIER METHOD       ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  Marcos Lopez de Prado — AFML Chapter 3")
    print("  Bariéry: TP / SL / Čas — která se dotkne první?\n")
    print(f"  Configs: {len(BARRIER_CONFIGS)} variant × signály × instrumenty\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_stats = []

    for tf in TIMEFRAMES:
        print(f"\n{'═'*55}")
        print(f"⏱️  Timeframe: {tf}")
        print(f"{'═'*55}")

        for category, tickers in CATEGORIES.items():
            for ticker in tickers:
                path = Path(INPUT_DIR) / tf / category / f"{ticker}.parquet"
                if not path.exists():
                    continue

                stats = process_file(path, ticker, tf)
                all_stats.extend(stats)

                strong = sum(1 for s in stats if "STRONG" in s.get("rating", ""))
                decent = sum(1 for s in stats if "DECENT" in s.get("rating", ""))
                print(f"  {ticker:8} → {len(stats):3} kombinací | "
                      f"🔥 {strong} strong | ✅ {decent} decent")

    if not all_stats:
        print("\n❌ Žádné výsledky.")
        return

    # ── SUMMARY ────────────────────────────────────────────────
    df_stats = pd.DataFrame(all_stats)

    # Nejlepší varianta pro každý signál+ticker+tf
    best = (df_stats
            .sort_values("profit_factor", ascending=False)
            .drop_duplicates(subset=["ticker", "timeframe", "signal"])
            .reset_index(drop=True))

    strong = best[best["rating"] == "🔥 STRONG"]
    decent = best[best["rating"] == "✅ DECENT"]

    # ── TISK TOP 20 ────────────────────────────────────────────
    print(f"\n{'═'*85}")
    print(f"🏆 TOP SIGNÁLY — Triple Barrier Method (nejlepší config per signál)")
    print(f"{'═'*85}")
    print(f"  {'Signal':<28} {'TF':<5} {'Tick':<8} {'Dir':<6} "
          f"{'WR%':<7} {'PF':<6} {'PT/SL/T':<12} {'Rating'}")
    print(f"  {'─'*85}")

    for _, row in strong.head(20).iterrows():
        config_str = f"{row['pt']}/{row['sl']}/{row['t']}"
        print(f"  {row['signal']:<28} {row['timeframe']:<5} {row['ticker']:<8} "
              f"{row['direction']:<6} {row['win_rate']:<7} {row['profit_factor']:<6} "
              f"{config_str:<12} {row['rating']}")

    # ── STATISTIKY ─────────────────────────────────────────────
    print(f"\n{'═'*55}")
    print(f"📊 CELKOVÝ PŘEHLED")
    print(f"{'═'*55}")
    print(f"  Celkem testováno:     {len(df_stats)} kombinací")
    print(f"  Unikátních signálů:   {df_stats['signal'].nunique()}")
    print(f"  🔥 Strong (best):     {len(strong)}")
    print(f"  ✅ Decent (best):     {len(decent)}")
    print(f"  ❌ No edge:           {len(best) - len(strong) - len(decent)}")

    # ── POROVNÁNÍ S EDGE MATRIX ────────────────────────────────
    print(f"\n  📌 KLÍČOVÝ ROZDÍL OD EDGE MATRIX:")
    print(f"     Edge matrix:   '{len(df_stats)} combinations, no stop logic'")
    print(f"     Triple Barrier: realistické TP/SL bariéry")
    print(f"     Signály které přežily obě metody = skutečný edge")

    # ── ULOŽENÍ ────────────────────────────────────────────────
    summary_path = os.path.join(OUTPUT_DIR, "triple_barrier_summary.csv")
    best_path    = os.path.join(OUTPUT_DIR, "triple_barrier_best.csv")

    df_stats.to_csv(summary_path, index=False)
    best.to_csv(best_path, index=False)

    print(f"\n  📁 Vše:     {summary_path}")
    print(f"  📁 Nejlepší: {best_path}")
    print(f"\n  💡 Další krok: Meta-Labeling — filtruj špatné vstupy ML modelem")


if __name__ == "__main__":
    main()