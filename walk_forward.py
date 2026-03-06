"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - WALK-FORWARD OPTIMALIZACE               ║
║         Marcos: backtest bez WFO je zbytečný                ║
╚══════════════════════════════════════════════════════════════╝

CO JE WALK-FORWARD OPTIMALIZACE:

    Klasický backtest:
        [──────── celá historie ────────] → jeden výsledek
        Problém: možná jsme jen náhodou trefili dobré parametry

    Walk-Forward:
        [─ trénink ─][─ test ─]
                [─ trénink ─][─ test ─]
                        [─ trénink ─][─ test ─]
        → 5-10 nezávislých výsledků
        → pokud konzistentně pozitivní = skutečný edge

    Klíčová otázka: "Funguje strategie na datech která NIKDY neviděla?"

VÝSLEDEK:
    WFO Efficiency Ratio = průměrný OOS Sharpe / IS Sharpe
    > 0.7 = robustní strategie
    < 0.5 = overfitting, zahazujeme

JAK SPUSTIT:
    python walk_forward.py
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from itertools import product

# ─── CONFIG ────────────────────────────────────────────────────

GOLD_DIR   = "data/04_GOLD_FEATURES"
OUTPUT_DIR = "data/09_WALK_FORWARD"

# Walk-forward parametry
N_SPLITS      = 5      # počet oken
TRAIN_RATIO   = 0.7    # 70% trénink, 30% test v každém okně
MIN_TRADES    = 10     # minimální počet obchodů pro statistiku

# Strategie k testování — top kandidáti z backtesteru
STRATEGIES = [
    {
        "name":      "AMZN RSI OB Exit M5",
        "ticker":    "AMZN",
        "tf":        "M5",
        "category":  "stocks",
        "signal":    "signal_rsi_overbought_exit",
        "direction": "short",
    },
    {
        "name":      "AAPL BB Breakdown M15",
        "ticker":    "AAPL",
        "tf":        "M15",
        "category":  "stocks",
        "signal":    "signal_bb_breakout_down",
        "direction": "short",
    },
    {
        "name":      "EURUSD Death Cross M15",
        "ticker":    "EURUSD",
        "tf":        "M15",
        "category":  "forex",
        "signal":    "signal_death_cross",
        "direction": "short",
    },
    {
        "name":      "NVDA RSI OB Exit M15",
        "ticker":    "NVDA",
        "tf":        "M15",
        "category":  "stocks",
        "signal":    "signal_rsi_overbought_exit",
        "direction": "short",
    },
    {
        "name":      "USDCHF Stoch Pin Bear M5",
        "ticker":    "USDCHF",
        "tf":        "M5",
        "category":  "forex",
        "signal":    "signal_stoch_pin_bear",
        "direction": "short",
    },
]

# Parametry k optimalizaci (ATR multiplikátory)
PARAM_GRID = {
    "pt_atr": [1.5, 2.0, 2.5, 3.0],
    "sl_atr": [1.0, 1.5, 2.0, 3.0],
    "hold":   [6, 12, 24],
}

# ─── BACKTEST JÁDRO ────────────────────────────────────────────

def backtest_window(df, signal_col, direction, pt_atr, sl_atr, hold,
                    risk_pct=1.0, initial_equity=10000):
    """
    Rychlý backtest na jednom datovém okně.
    Vrátí slovník se statistikami.
    """
    close  = df["close"].values
    high   = df["high"].values
    low    = df["low"].values
    atr    = df["atr"].values if "atr" in df.columns else np.full(len(df), np.nan)
    signal = df[signal_col].values.astype(bool)

    entry_indices = np.where(signal)[0]
    entry_indices = entry_indices[entry_indices + hold + 1 < len(df)]

    if len(entry_indices) < MIN_TRADES:
        return None

    equity   = initial_equity
    returns  = []
    wins     = 0

    for idx in entry_indices:
        atr_val = atr[idx]
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        entry_price = close[idx]
        upper = entry_price + pt_atr * atr_val
        lower = entry_price - sl_atr * atr_val

        end   = min(idx + hold + 1, len(df))
        highs = high[idx+1:end]
        lows  = low[idx+1:end]

        if direction == "long":
            tp_hits = highs >= upper
            sl_hits = lows  <= lower
        else:
            tp_hits = lows  <= lower
            sl_hits = highs >= upper

        tp_idx = np.argmax(tp_hits) if tp_hits.any() else len(tp_hits)
        sl_idx = np.argmax(sl_hits) if sl_hits.any() else len(sl_hits)

        risk_amount = equity * risk_pct / 100
        stop_dist   = sl_atr * atr_val
        pos_size    = risk_amount / stop_dist if stop_dist > 0 else 0

        if not tp_hits.any() and not sl_hits.any():
            ep  = close[end - 1]
            ret = (ep - entry_price) if direction == "long" else (entry_price - ep)
        elif tp_hits.any() and (not sl_hits.any() or tp_idx <= sl_idx):
            ret = pt_atr * atr_val
            wins += 1
        else:
            ret = -sl_atr * atr_val

        pnl     = ret * pos_size
        equity += pnl
        returns.append(pnl / (equity - pnl) * 100 if equity != pnl else 0)

    if not returns or len(returns) < MIN_TRADES:
        return None

    returns_arr  = np.array(returns)
    total_return = (equity - initial_equity) / initial_equity * 100
    win_rate     = wins / len(returns) * 100
    avg_ret      = returns_arr.mean()
    std_ret      = returns_arr.std()
    sharpe       = avg_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0

    # Max drawdown
    eq_curve = initial_equity * np.cumprod(1 + returns_arr / 100)
    peak     = np.maximum.accumulate(eq_curve)
    dd       = (eq_curve - peak) / peak * 100
    max_dd   = dd.min()

    # Profit factor
    gross_profit = returns_arr[returns_arr > 0].sum()
    gross_loss   = abs(returns_arr[returns_arr < 0].sum())
    pf           = gross_profit / gross_loss if gross_loss > 0 else 99.0

    return {
        "n_trades":     len(returns),
        "win_rate":     round(win_rate, 1),
        "total_return": round(total_return, 2),
        "sharpe":       round(sharpe, 3),
        "max_dd":       round(max_dd, 2),
        "profit_factor": round(pf, 2),
    }


# ─── OPTIMALIZACE NA IS OKNĚ ───────────────────────────────────

def optimize_on_window(df, signal_col, direction):
    """
    Najdi nejlepší parametry na in-sample okně.
    Kritérium: maximální Sharpe ratio.
    """
    best_sharpe = -np.inf
    best_params = None

    for pt, sl, hold in product(
        PARAM_GRID["pt_atr"],
        PARAM_GRID["sl_atr"],
        PARAM_GRID["hold"]
    ):
        result = backtest_window(df, signal_col, direction, pt, sl, hold)
        if result and result["sharpe"] > best_sharpe:
            best_sharpe = result["sharpe"]
            best_params = {"pt_atr": pt, "sl_atr": sl, "hold": hold}

    return best_params, best_sharpe


# ─── WALK-FORWARD ──────────────────────────────────────────────

def walk_forward(df, signal_col, direction, strategy_name):
    """
    Provede walk-forward analýzu na datech.

    Vrátí seznam výsledků pro každé okno + celkové statistiky.
    """
    n    = len(df)
    step = n // (N_SPLITS + 1)

    window_results = []

    print(f"\n  {'─'*55}")
    print(f"  {strategy_name}")
    print(f"  Data: {n} svíček | {N_SPLITS} oken | step: {step}")
    print(f"  {'─'*55}")
    print(f"  {'Okno':<6} {'IS Sharpe':<12} {'OOS Sharpe':<12} "
          f"{'OOS WR%':<10} {'OOS PF':<8} {'Params'}")
    print(f"  {'─'*55}")

    for i in range(N_SPLITS):
        # Definuj okno
        is_start  = i * step
        is_end    = is_start + int(step * (1 / (1 - TRAIN_RATIO) * TRAIN_RATIO))
        oos_start = is_end
        oos_end   = min(oos_start + step, n)

        if oos_end - oos_start < 50:
            continue

        df_is  = df.iloc[is_start:is_end].reset_index(drop=True)
        df_oos = df.iloc[oos_start:oos_end].reset_index(drop=True)

        # Optimalizuj na IS
        best_params, is_sharpe = optimize_on_window(df_is, signal_col, direction)

        if best_params is None:
            print(f"  {i+1:<6} IS: nedostatek dat")
            continue

        # Testuj na OOS s IS parametry
        oos_result = backtest_window(
            df_oos, signal_col, direction,
            best_params["pt_atr"], best_params["sl_atr"], best_params["hold"]
        )

        if oos_result is None:
            print(f"  {i+1:<6} {is_sharpe:<12.3f} OOS: nedostatek dat")
            continue

        oos_sharpe = oos_result["sharpe"]
        params_str = f"PT{best_params['pt_atr']}/SL{best_params['sl_atr']}/H{best_params['hold']}"

        print(f"  {i+1:<6} {is_sharpe:<12.3f} {oos_sharpe:<12.3f} "
              f"{oos_result['win_rate']:<10} {oos_result['profit_factor']:<8} "
              f"{params_str}")

        window_results.append({
            "window":      i + 1,
            "is_sharpe":   is_sharpe,
            "oos_sharpe":  oos_sharpe,
            "oos_wr":      oos_result["win_rate"],
            "oos_pf":      oos_result["profit_factor"],
            "oos_dd":      oos_result["max_dd"],
            "oos_return":  oos_result["total_return"],
            "best_params": best_params,
        })

    if not window_results:
        return None

    # WFO Efficiency Ratio
    avg_is_sharpe  = np.mean([r["is_sharpe"]  for r in window_results])
    avg_oos_sharpe = np.mean([r["oos_sharpe"] for r in window_results])
    wfo_ratio      = avg_oos_sharpe / avg_is_sharpe if avg_is_sharpe > 0 else 0

    # Konzistence — kolik oken bylo pozitivních OOS?
    positive_windows = sum(1 for r in window_results if r["oos_sharpe"] > 0)
    consistency      = positive_windows / len(window_results) * 100

    # Nejčastější parametry přes okna (stabilita)
    all_params = [r["best_params"] for r in window_results]
    pt_vals    = [p["pt_atr"] for p in all_params]
    sl_vals    = [p["sl_atr"] for p in all_params]
    hold_vals  = [p["hold"]   for p in all_params]

    # Doporučené parametry = nejčastější přes všechna okna
    from collections import Counter
    rec_pt   = Counter(pt_vals).most_common(1)[0][0]
    rec_sl   = Counter(sl_vals).most_common(1)[0][0]
    rec_hold = Counter(hold_vals).most_common(1)[0][0]

    # Verdict
    if wfo_ratio >= 0.7 and consistency >= 60:
        verdict = "✅ ROBUSTNÍ — nasadit do live"
    elif wfo_ratio >= 0.5 and consistency >= 40:
        verdict = "⚠️  MARGINAL — opatrně, sledovat"
    else:
        verdict = "❌ OVERFITTING — nezasazovat"

    print(f"\n  WFO Efficiency Ratio: {wfo_ratio:.3f}  "
          f"({'dobrý' if wfo_ratio >= 0.7 else 'slabý'})")
    print(f"  Konzistence:          {consistency:.0f}%  "
          f"({positive_windows}/{len(window_results)} oken pozitivních)")
    print(f"  Avg IS Sharpe:        {avg_is_sharpe:.3f}")
    print(f"  Avg OOS Sharpe:       {avg_oos_sharpe:.3f}")
    print(f"  Doporučené params:    PT{rec_pt}/SL{rec_sl}/H{rec_hold}")
    print(f"  Verdict:              {verdict}")

    return {
        "strategy":        strategy_name,
        "n_windows":       len(window_results),
        "avg_is_sharpe":   round(avg_is_sharpe, 3),
        "avg_oos_sharpe":  round(avg_oos_sharpe, 3),
        "wfo_ratio":       round(wfo_ratio, 3),
        "consistency_pct": round(consistency, 1),
        "rec_pt":          rec_pt,
        "rec_sl":          rec_sl,
        "rec_hold":        rec_hold,
        "verdict":         verdict,
        "windows":         window_results,
    }


# ─── MAIN ──────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║   MARKETPAL WALK-FORWARD OPTIMALIZACE   ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print(f"  Okna: {N_SPLITS} | Train/Test: {int(TRAIN_RATIO*100)}/{int((1-TRAIN_RATIO)*100)}%")
    print(f"  Parametry: {len(PARAM_GRID['pt_atr'])}×{len(PARAM_GRID['sl_atr'])}×"
          f"{len(PARAM_GRID['hold'])} = "
          f"{len(PARAM_GRID['pt_atr'])*len(PARAM_GRID['sl_atr'])*len(PARAM_GRID['hold'])} "
          f"kombinací per okno\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_results = []

    for strat in STRATEGIES:
        path = Path(GOLD_DIR) / strat["tf"] / strat["category"] / f"{strat['ticker']}.parquet"
        if not path.exists():
            print(f"⚠️  {strat['ticker']}: soubor nenalezen")
            continue

        df     = pd.read_parquet(path).reset_index(drop=True)
        result = walk_forward(df, strat["signal"], strat["direction"], strat["name"])

        if result:
            all_results.append(result)

    if not all_results:
        print("\n❌ Žádné výsledky.")
        return

    # ── FINÁLNÍ PŘEHLED ────────────────────────────────────────
    print(f"\n{'═'*65}")
    print("🏆 FINÁLNÍ PŘEHLED — Walk-Forward Optimalizace")
    print(f"{'═'*65}")
    print(f"  {'Strategie':<30} {'WFO':<7} {'Konz%':<8} {'OOS SR':<9} Verdict")
    print(f"  {'─'*65}")

    robust  = 0
    for r in sorted(all_results, key=lambda x: x["wfo_ratio"], reverse=True):
        print(f"  {r['strategy']:<30} {r['wfo_ratio']:<7} "
              f"{r['consistency_pct']:<8} {r['avg_oos_sharpe']:<9} "
              f"{r['verdict']}")
        if "ROBUSTNÍ" in r["verdict"]:
            robust += 1

    print(f"\n  Robustních strategií: {robust}/{len(all_results)}")
    print(f"\n  💡 Tyto strategie použij v mt5_executor.py:")
    for r in all_results:
        if "ROBUSTNÍ" in r["verdict"]:
            print(f"     {r['strategy']}: PT{r['rec_pt']}/SL{r['rec_sl']}/H{r['rec_hold']}")

    # Ulož výsledky
    summary = [{k: v for k, v in r.items() if k != "windows"} for r in all_results]
    pd.DataFrame(summary).to_csv(
        os.path.join(OUTPUT_DIR, "wfo_summary.csv"), index=False
    )
    print(f"\n  📁 {OUTPUT_DIR}/wfo_summary.csv")


if __name__ == "__main__":
    main()