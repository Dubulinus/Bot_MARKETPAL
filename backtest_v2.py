"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - BACKTEST v1.0                               ║
║     Walk-forward validace + FTMO pravidla                   ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    Simuluje reálné obchodování na historických datech.
    Každý STRONG signál dostane přiřazenu velikost pozice
    a sledujeme P&L, drawdown, Sharpe ratio.

FTMO PRAVIDLA (challenge $10,000):
    Max daily loss:    $500  (5%)
    Max total loss:    $1000 (10%)
    Profit target:     $1000 (10%)
    Min trading days:  4

VÝSTUP:
    data/13_BACKTEST/backtest_results.csv
    data/13_BACKTEST/equity_curve.csv
    data/13_BACKTEST/summary.txt
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

TB_DIR     = "data/07_TRIPLE_BARRIER"
GOLD_DIR   = "data/04_GOLD_FEATURES"
OUTPUT_DIR = "data/13_BACKTEST"

# ── FTMO CONFIG ───────────────────────────────────────────────
ACCOUNT_SIZE     = 10_000   # $10,000 FTMO challenge
RISK_PER_TRADE   = 0.01     # 1% risk per trade = $100
MAX_DAILY_LOSS   = 0.05     # 5% = $500
MAX_TOTAL_LOSS   = 0.10     # 10% = $1000
PROFIT_TARGET    = 0.10     # 10% = $1000

# ── STRATEGIE ─────────────────────────────────────────────────
STRATEGIES = [
    {
        "name":       "GOOGL M15 RSI oversold exit",
        "ticker":     "GOOGL",
        "tf":         "M15",
        "category":   "stocks",
        "signal_col": "signal_rsi_oversold_exit",
        "direction":  "long",
        "pt": 1.5, "sl": 1.5, "t": 24,
        "pip_value":  1.0,    # $ per 1 share move
        "lot_size":   1,      # shares (dynamicky přepočítáme)
    },
    {
        "name":       "EURUSD M15 RSI oversold exit",
        "ticker":     "EURUSD",
        "tf":         "M15",
        "category":   "forex",
        "signal_col": "signal_rsi_oversold_exit",
        "direction":  "long",
        "pt": 2.0, "sl": 1.5, "t": 24,
        "pip_value":  10.0,   # $ per pip (0.0001) na 1 lot
        "lot_size":   0.01,   # micro lot
    },
    {
        "name":       "GBPUSD M15 RSI oversold exit",
        "ticker":     "GBPUSD",
        "tf":         "M15",
        "category":   "forex",
        "signal_col": "signal_rsi_oversold_exit",
        "direction":  "long",
        "pt": 1.5, "sl": 1.5, "t": 24,
        "pip_value":  10.0,
        "lot_size":   0.01,
    },
    {
        "name":       "USDCHF H1 Stoch pin bear",
        "ticker":     "USDCHF",
        "tf":         "H1",
        "category":   "forex",
        "signal_col": "signal_stoch_pin_bear",
        "direction":  "short",
        "pt": 1.5, "sl": 1.5, "t": 24,
        "pip_value":  10.0,
        "lot_size":   0.01,
    },
]


def load_strategy_data(strat):
    """Načte Gold data + TB labely pro strategii."""
    ticker = strat["ticker"]
    tf     = strat["tf"]
    pt, sl, t = strat["pt"], strat["sl"], strat["t"]

    gold_path = Path(GOLD_DIR) / tf / strat["category"] / f"{ticker}.parquet"
    tb_path   = Path(TB_DIR) / tf / \
        f"{ticker}_{strat['signal_col']}_pt{pt}_sl{sl}_t{t}.parquet"

    if not gold_path.exists():
        print(f"    ❌ Gold data nenalezena: {gold_path}")
        return None, None

    if not tb_path.exists():
        print(f"    ❌ TB labely nenalezeny: {tb_path}")
        return None, None

    df_gold = pd.read_parquet(gold_path).reset_index(drop=True)
    df_tb   = pd.read_parquet(tb_path)

    return df_gold, df_tb


def calc_position_size(account, risk_pct, entry_price, sl_atr, atr_mult, category):
    """
    Vypočítá velikost pozice tak aby risk = risk_pct * account.

    Stocks: risk = shares * sl_distance_$
    Forex:  risk = lots * pip_value * sl_pips
    """
    risk_amount = account * risk_pct  # např. $100

    if category == "stocks":
        sl_distance = sl_atr * atr_mult  # $ pohyb
        if sl_distance <= 0:
            return 1
        shares = risk_amount / sl_distance
        return max(1, round(shares))

    else:  # forex
        # sl_distance v cenových jednotkách
        sl_distance = sl_atr * atr_mult
        # pip = 0.0001 pro EURUSD/GBPUSD, 0.01 pro USDJPY
        pip_size    = 0.01 if "JPY" in category else 0.0001
        sl_pips     = sl_distance / pip_size
        # 1 lot = $10/pip, 0.01 lot = $0.10/pip
        lot_value   = 10.0  # $ per pip per lot
        if sl_pips <= 0 or lot_value <= 0:
            return 0.01
        lots = risk_amount / (sl_pips * lot_value)
        # Zaokrouhli na 0.01 (micro lot)
        return max(0.01, round(lots, 2))


def simulate_trades(df_gold, df_tb, strat, account_start):
    """
    Simuluje všechny obchody chronologicky.
    Sleduje equity, drawdown, FTMO pravidla.
    """
    ticker    = strat["ticker"]
    category  = strat["category"]
    direction = strat["direction"]
    pt, sl, t = strat["pt"], strat["sl"], strat["t"]

    close  = df_gold["close"].values
    atr    = df_gold["atr"].values if "atr" in df_gold.columns else np.ones(len(df_gold))

    trades   = []
    account  = account_start
    peak     = account_start
    daily_pnl = {}
    ftmo_breached = False
    ftmo_reason   = None

    for _, row in df_tb.iterrows():
        if ftmo_breached:
            break

        entry_idx = int(row["entry_idx"])
        exit_idx  = int(row["exit_idx"])
        label     = int(row["label"])
        ret_pct   = float(row["ret_pct"])

        if entry_idx >= len(close) or exit_idx >= len(close):
            continue

        entry_price = close[entry_idx]
        atr_val     = atr[entry_idx] if entry_idx < len(atr) else 0.001

        # Získej datum z indexu (pokud DatetimeIndex)
        if hasattr(df_gold.index, 'to_series'):
            try:
                entry_date = df_gold.index[entry_idx]
                if hasattr(entry_date, 'date'):
                    trade_date = entry_date.date()
                else:
                    trade_date = None
            except Exception:
                trade_date = None
        else:
            trade_date = None

        # Vypočítej P&L v $
        if category == "stocks":
            pos_size    = calc_position_size(account, RISK_PER_TRADE, entry_price, atr_val, sl, category)
            sl_distance = sl * atr_val
            tp_distance = pt * atr_val

            if label == 1:   # TP hit
                pnl = pos_size * tp_distance if direction == "long" else pos_size * tp_distance
            elif label == -1:  # SL hit
                pnl = -pos_size * sl_distance
            else:  # time exit
                pnl = pos_size * entry_price * ret_pct / 100

        else:  # forex
            pip_size = 0.0001  # default
            lots     = calc_position_size(account, RISK_PER_TRADE, entry_price, atr_val, sl, category)
            pip_val  = lots * 10  # $ per pip

            sl_pips = (sl * atr_val) / pip_size
            tp_pips = (pt * atr_val) / pip_size

            if label == 1:
                pnl = pip_val * tp_pips
            elif label == -1:
                pnl = -pip_val * sl_pips
            else:
                pnl = pip_val * (ret_pct / 100 * entry_price / pip_size)

        pnl = round(pnl, 2)
        account += pnl
        peak = max(peak, account)

        # FTMO check — daily loss
        if trade_date:
            daily_pnl[trade_date] = daily_pnl.get(trade_date, 0) + pnl
            if daily_pnl[trade_date] < -account_start * MAX_DAILY_LOSS:
                ftmo_breached = True
                ftmo_reason   = f"Daily loss limit: {daily_pnl[trade_date]:.0f}$"

        # FTMO check — total loss
        drawdown = (peak - account) / account_start
        if drawdown > MAX_TOTAL_LOSS:
            ftmo_breached = True
            ftmo_reason   = f"Max drawdown: {drawdown*100:.1f}%"

        trades.append({
            "trade_date":  str(trade_date) if trade_date else str(entry_idx),
            "entry_idx":   entry_idx,
            "exit_idx":    exit_idx,
            "label":       label,
            "ret_pct":     ret_pct,
            "pnl_usd":     pnl,
            "account":     round(account, 2),
            "drawdown_pct": round(drawdown * 100, 2),
        })

        # FTMO check — profit target hit
        if (account - account_start) / account_start >= PROFIT_TARGET:
            break

    return pd.DataFrame(trades), account, ftmo_breached, ftmo_reason


def calc_metrics(trades_df, account_start, account_final):
    """Spočítej výkonnostní metriky."""
    if trades_df.empty:
        return {}

    pnl = trades_df["pnl_usd"]
    wins  = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    total_return = (account_final - account_start) / account_start * 100
    win_rate     = len(wins) / len(pnl) * 100
    avg_win      = wins.mean() if len(wins) > 0 else 0
    avg_loss     = losses.mean() if len(losses) > 0 else 0
    profit_factor = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99
    max_dd       = trades_df["drawdown_pct"].max()

    # Sharpe (zjednodušený — denní PnL)
    daily = trades_df.groupby("trade_date")["pnl_usd"].sum()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0

    return {
        "n_trades":      len(pnl),
        "win_rate":      round(win_rate, 1),
        "total_return":  round(total_return, 1),
        "total_pnl":     round(account_final - account_start, 2),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown":  round(max_dd, 1),
        "sharpe":        round(sharpe, 2),
    }


def walk_forward_split(df_tb, n_splits=4):
    """
    Rozdělí data na n_splits časových bloků.
    Každý blok = ~1 rok (4 roky celkem).
    """
    n = len(df_tb)
    block = n // n_splits
    splits = []
    for i in range(n_splits):
        start = i * block
        end   = (i + 1) * block if i < n_splits - 1 else n
        splits.append(df_tb.iloc[start:end].copy())
    return splits


def main():
    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL BACKTEST v1.0            ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print(f"  Account: ${ACCOUNT_SIZE:,} | Risk/trade: {RISK_PER_TRADE*100:.0f}%")
    print(f"  FTMO: Max DD {MAX_TOTAL_LOSS*100:.0f}% | Target {PROFIT_TARGET*100:.0f}%\n")

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    all_results = []
    all_trades  = []

    for strat in STRATEGIES:
        print(f"\n{'═'*55}")
        print(f"  {strat['name']}")
        print(f"{'═'*55}")

        df_gold, df_tb = load_strategy_data(strat)
        if df_gold is None or df_tb is None:
            continue

        print(f"  Signálů celkem: {len(df_tb)}")
        print(f"  Období: {df_tb['entry_idx'].min()} → {df_tb['entry_idx'].max()} (index)\n")

        # ── FULL BACKTEST ──────────────────────────────────────
        trades_df, account_final, breached, breach_reason = simulate_trades(
            df_gold, df_tb, strat, ACCOUNT_SIZE
        )

        metrics = calc_metrics(trades_df, ACCOUNT_SIZE, account_final)

        ftmo_status = "❌ BREACH" if breached else (
            "✅ TARGET HIT" if metrics.get("total_return", 0) >= PROFIT_TARGET * 100
            else "📊 IN PROGRESS"
        )

        print(f"  {'─'*50}")
        print(f"  FULL BACKTEST (4 roky):")
        print(f"  {'─'*50}")
        print(f"  Obchodů:        {metrics.get('n_trades', 0)}")
        print(f"  Win rate:       {metrics.get('win_rate', 0):.1f}%")
        print(f"  Total P&L:      ${metrics.get('total_pnl', 0):+,.2f}")
        print(f"  Total return:   {metrics.get('total_return', 0):+.1f}%")
        print(f"  Profit factor:  {metrics.get('profit_factor', 0):.2f}")
        print(f"  Max drawdown:   {metrics.get('max_drawdown', 0):.1f}%")
        print(f"  Sharpe ratio:   {metrics.get('sharpe', 0):.2f}")
        print(f"  FTMO status:    {ftmo_status}")
        if breached:
            print(f"  Breach reason:  {breach_reason}")

        # ── WALK-FORWARD (4 × 1 rok) ──────────────────────────
        print(f"\n  Walk-forward (4 roky × 1 rok bloky):")
        wf_splits = walk_forward_split(df_tb, n_splits=4)
        wf_results = []

        for i, split in enumerate(wf_splits):
            if len(split) < 5:
                continue
            t_df, acc_f, br, _ = simulate_trades(df_gold, split, strat, ACCOUNT_SIZE)
            m = calc_metrics(t_df, ACCOUNT_SIZE, acc_f)
            wf_results.append(m)
            status = "✅" if m.get("total_pnl", 0) > 0 else "❌"
            print(f"    Rok {i+1}: {status} "
                  f"P&L ${m.get('total_pnl', 0):+,.0f} | "
                  f"WR {m.get('win_rate', 0):.0f}% | "
                  f"DD {m.get('max_drawdown', 0):.1f}% | "
                  f"N={m.get('n_trades', 0)}")

        if wf_results:
            pnls = [r.get("total_pnl", 0) for r in wf_results]
            profitable_years = sum(1 for p in pnls if p > 0)
            print(f"\n    Ziskových let: {profitable_years}/{len(wf_results)}")
            print(f"    Průměrný roční P&L: ${np.mean(pnls):+,.0f}")
            print(f"    Nejhorší rok:       ${min(pnls):+,.0f}")

        result = {
            "strategy": strat["name"],
            "ticker":   strat["ticker"],
            "tf":       strat["tf"],
            **metrics,
            "ftmo_breached": breached,
            "ftmo_status":   ftmo_status,
        }
        all_results.append(result)

        trades_df["strategy"] = strat["name"]
        all_trades.append(trades_df)

    # ── SOUHRN ────────────────────────────────────────────────
    print(f"\n\n{'═'*65}")
    print("  SOUHRN BACKTESTŮ — FTMO CHALLENGE $10,000")
    print(f"{'═'*65}")
    print(f"  {'Strategie':<30} {'P&L':>8} {'WR%':>6} {'DD%':>6} {'PF':>5} {'FTMO'}")
    print(f"  {'─'*65}")

    for r in sorted(all_results, key=lambda x: x.get("total_pnl", 0), reverse=True):
        print(f"  {r['strategy']:<30} "
              f"${r.get('total_pnl', 0):>+7,.0f} "
              f"{r.get('win_rate', 0):>5.1f}% "
              f"{r.get('max_drawdown', 0):>5.1f}% "
              f"{r.get('profit_factor', 0):>4.2f} "
              f"{r.get('ftmo_status', '?')}")

    total_pnl = sum(r.get("total_pnl", 0) for r in all_results)
    print(f"\n  Portfolio P&L (všechny strategie): ${total_pnl:+,.2f}")
    print(f"  Portfolio return: {total_pnl/ACCOUNT_SIZE*100:+.1f}%")

    # FTMO verdict
    print(f"\n  {'─'*55}")
    print(f"  FTMO CHALLENGE VERDIKT:")
    any_breach = any(r.get("ftmo_breached") for r in all_results)
    best_pnl   = max((r.get("total_pnl", 0) for r in all_results), default=0)

    if any_breach:
        print(f"  ❌ FAIL — některá strategie překročila drawdown limit")
    elif best_pnl >= ACCOUNT_SIZE * PROFIT_TARGET:
        print(f"  ✅ PASS — nejlepší strategie dosáhla profit target!")
    elif best_pnl > 0:
        print(f"  ⚠️  PARTIAL — strategie jsou ziskové ale nedosáhly 10% target")
        print(f"     Potřeba: ${ACCOUNT_SIZE * PROFIT_TARGET:,.0f} | Nejlepší: ${best_pnl:,.0f}")
    else:
        print(f"  ❌ FAIL — žádná strategie není celkově zisková")

    # Ulož výsledky
    if all_results:
        pd.DataFrame(all_results).to_csv(
            Path(OUTPUT_DIR) / "backtest_results.csv", index=False
        )

    if all_trades:
        pd.concat(all_trades).to_csv(
            Path(OUTPUT_DIR) / "all_trades.csv", index=False
        )

    print(f"\n  ✅ Uloženo: {OUTPUT_DIR}/")
    print(f"  💡 Equity curve: data/13_BACKTEST/all_trades.csv")


if __name__ == "__main__":
    main()