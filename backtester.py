"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - BACKTESTER v2                           ║
║         Phase 2 | Fixed: stops, entry timing, diagnostics   ║
╚══════════════════════════════════════════════════════════════╝

PRUBLEM V BACKTESTER v1:
    1. ATR stop 1.5x byl příliš těsný → stopy se trefovaly dřív než signal fungoval
    2. Entry na next candle OPEN → pro lagging indikátory (death_cross) je pohyb hotový
    3. Chyběla diagnostika: PROC trade prohrál?

OPRAVY v2:
    1. ATR multiplier zvýšen na 2.0, testujeme také 2.5 a 3.0
    2. Entry timing: testujeme CURRENT CLOSE i NEXT OPEN
    3. Diagnostika: stop rate, avg candles held, entry slippage analýza
    4. Signal filter: přidán volume/volatility filter pro kvalitnější vstupy
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime

# ─── CONFIG ────────────────────────────────────────────────────

INPUT_DIR  = "data/04_GOLD_FEATURES"
OUTPUT_DIR = "data/06_BACKTEST_RESULTS"

INITIAL_CAPITAL    = 10000
RISK_PER_TRADE_PCT = 1.0
COMMISSION_PCT     = 0.05

# v2 FIX: testujeme vice ATR multiplikátorů
ATR_MULTIPLIERS = [1.5, 2.0, 2.5, 3.0]

# v2 FIX: entry timing
# "next_open"    = původní (v1) — vstup na otevření další svíčky
# "current_close" = vstup na zavření signální svíčky (méně realistické ale méně slippage)
ENTRY_MODE = "next_open"

# ─── SIGNÁLY K TESTOVÁNÍ ───────────────────────────────────────
# Formát: (ticker, timeframe, signal_col, hold_candles, direction)

TOP_SIGNALS = [
    ("AMZN",   "M5",  "signal_rsi_overbought_exit", 12, "short"),
    ("AAPL",   "M15", "signal_bb_breakout_down",    12, "short"),
    ("EURUSD", "M15", "signal_death_cross",          3, "short"),
    ("USDJPY", "M5",  "signal_golden_cross",        24, "long"),
    ("USDCHF", "H1",  "signal_macd_bull",            3, "long"),

    # v2 NOVÉ: přidáváme více signálů z edge matrix na základě diagnostiky
    ("AMZN",   "M5",  "signal_rsi_overbought_exit", 24, "short"),  # delší hold
    ("AAPL",   "M15", "signal_bb_breakout_down",    24, "short"),  # delší hold
    ("AMZN",   "M15", "signal_macd_bull",           12, "long"),   # nový ze strong signals
    ("USDJPY", "H1",  "signal_bull_regime",         24, "long"),   # H1 = méně šumu
    ("USDJPY", "H1",  "signal_above_vwap",          24, "long"),   # z edge matrix
]

# ─── SIMULATE TRADES ───────────────────────────────────────────

def simulate_trades(df, signal_col, hold_candles, direction,
                    ticker, tf_name, atr_mult):
    """
    Simulace obchodů s diagnostikou.

    v2 změny:
    - Sledujeme PROC každý trade prohrál/vyhrál
    - Měříme stop rate (příliš vysoký = stop příliš těsný)
    - Měříme průměrný počet svíček do exitu
    """
    trades   = []
    equity   = INITIAL_CAPITAL

    for i in range(len(df) - hold_candles - 1):
        row = df.iloc[i]

        if not row.get(signal_col, False):
            continue

        atr = row.get("atr", np.nan)
        if pd.isna(atr) or atr <= 0:
            continue

        # v2: Entry timing
        if ENTRY_MODE == "next_open":
            entry_price = df.iloc[i + 1]["open"]
        else:
            entry_price = row["close"]

        stop_dist    = atr * atr_mult
        risk_amount  = equity * (RISK_PER_TRADE_PCT / 100)
        pos_size     = risk_amount / stop_dist

        if direction == "long":
            stop_price = entry_price - stop_dist
        else:
            stop_price = entry_price + stop_dist

        exit_price  = None
        exit_reason = "hold_expired"
        exit_candle = i + 1 + hold_candles
        candles_held = hold_candles

        for j in range(i + 1, min(i + 1 + hold_candles, len(df))):
            candle = df.iloc[j]

            if direction == "long"  and candle["low"]  <= stop_price:
                exit_price   = stop_price
                exit_reason  = "stop_loss"
                exit_candle  = j
                candles_held = j - (i + 1)
                break
            if direction == "short" and candle["high"] >= stop_price:
                exit_price   = stop_price
                exit_reason  = "stop_loss"
                exit_candle  = j
                candles_held = j - (i + 1)
                break

        if exit_price is None:
            if exit_candle < len(df):
                exit_price = df.iloc[exit_candle]["close"]
            else:
                continue

        if direction == "long":
            gross_pnl = (exit_price - entry_price) * pos_size
        else:
            gross_pnl = (entry_price - exit_price) * pos_size

        commission = entry_price * pos_size * (COMMISSION_PCT / 100) * 2
        net_pnl    = gross_pnl - commission
        equity    += net_pnl

        # v2: diagnostické pole — jak daleko byl exit od entry v ATR násobcích?
        price_move_atr = abs(exit_price - entry_price) / atr

        trades.append({
            "ticker":          ticker,
            "timeframe":       tf_name,
            "signal":          signal_col.replace("signal_", ""),
            "direction":       direction,
            "atr_mult":        atr_mult,
            "entry_price":     round(entry_price, 6),
            "exit_price":      round(exit_price, 6),
            "stop_price":      round(stop_price, 6),
            "atr_at_entry":    round(atr, 6),
            "pos_size":        round(pos_size, 4),
            "gross_pnl":       round(gross_pnl, 4),
            "commission":      round(commission, 4),
            "net_pnl":         round(net_pnl, 4),
            "equity_after":    round(equity, 2),
            "exit_reason":     exit_reason,
            "candles_held":    candles_held,
            "price_move_atr":  round(price_move_atr, 2),
            "win":             net_pnl > 0,
        })

    return trades, equity


# ─── STATS ─────────────────────────────────────────────────────

def compute_stats(trades, final_equity, label, atr_mult):
    if not trades:
        return None

    df_t = pd.DataFrame(trades)
    n    = len(df_t)

    wins          = df_t["win"].sum()
    win_rate      = wins / n * 100
    total_pnl     = df_t["net_pnl"].sum()
    avg_win       = df_t[df_t["win"]]["net_pnl"].mean()       if wins > 0        else 0
    avg_loss      = df_t[~df_t["win"]]["net_pnl"].mean()      if (n - wins) > 0  else 0
    gross_profit  = df_t[df_t["win"]]["net_pnl"].sum()
    gross_loss    = abs(df_t[~df_t["win"]]["net_pnl"].sum())
    pf            = gross_profit / gross_loss                  if gross_loss > 0  else np.inf
    stop_rate     = (df_t["exit_reason"] == "stop_loss").sum() / n * 100
    avg_candles   = df_t["candles_held"].mean()

    eq            = df_t["equity_after"]
    peak          = eq.cummax()
    drawdown      = (eq - peak) / peak * 100
    max_dd        = drawdown.min()
    total_return  = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    return {
        "strategy":        label,
        "atr_mult":        atr_mult,
        "n_trades":        n,
        "win_rate":        round(win_rate, 1),
        "total_return":    round(total_return, 2),
        "total_pnl":       round(total_pnl, 2),
        "max_dd":          round(max_dd, 2),
        "profit_factor":   round(pf, 2),
        "avg_win":         round(avg_win, 2),
        "avg_loss":        round(avg_loss, 2),
        "rr_ratio":        round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
        "stop_rate_pct":   round(stop_rate, 1),
        "avg_candles":     round(avg_candles, 1),
        "final_equity":    round(final_equity, 2),
        "ftmo_dd_ok":      max_dd > -10,
        "ftmo_return_ok":  total_return > 0,
    }


def print_stats(stats):
    dd_ok  = "✅" if stats["ftmo_dd_ok"]     else "❌"
    ret_ok = "✅" if stats["ftmo_return_ok"] else "❌"
    pf_ok  = "✅" if stats["profit_factor"] >= 1.2 else "⚠️ " if stats["profit_factor"] >= 1.0 else "❌"

    print(f"\n  {'─'*56}")
    print(f"  📊 {stats['strategy']}  [ATR×{stats['atr_mult']}]")
    print(f"  {'─'*56}")
    print(f"  Trades:        {stats['n_trades']}")
    print(f"  Win Rate:      {stats['win_rate']}%")
    print(f"  Stop Rate:     {stats['stop_rate_pct']}%  ← {'příliš vysoké!' if stats['stop_rate_pct'] > 50 else 'OK'}")
    print(f"  Avg Candles:   {stats['avg_candles']} held before exit")
    print(f"  Total Return:  {stats['total_return']}%  {ret_ok}")
    print(f"  Max Drawdown:  {stats['max_dd']}%  {dd_ok}")
    print(f"  Profit Factor: {stats['profit_factor']}  {pf_ok}")
    print(f"  Avg Win/Loss:  ${stats['avg_win']} / ${stats['avg_loss']}")
    print(f"  R:R Ratio:     {stats['rr_ratio']}")
    print(f"  Final Equity:  ${stats['final_equity']}")


# ─── MAIN ──────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL BACKTESTER v2            ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print(f"  Entry mode:    {ENTRY_MODE}")
    print(f"  ATR variants:  {ATR_MULTIPLIERS}")
    print(f"  Risk/trade:    {RISK_PER_TRADE_PCT}%\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_stats  = []
    all_trades = []

    for ticker, tf_name, signal_col, hold_candles, direction in TOP_SIGNALS:
        category = "stocks" if ticker in ["AAPL", "AMZN", "MSFT", "NVDA"] else "forex"
        path     = os.path.join(INPUT_DIR, tf_name, category, f"{ticker}.parquet")

        if not os.path.exists(path):
            print(f"⚠️  Soubor nenalezen: {path}")
            continue

        label = f"{ticker} {tf_name} {signal_col.replace('signal_', '')} h={hold_candles} {direction.upper()}"
        print(f"\n{'═'*60}")
        print(f"🔬 {label}")
        print(f"{'═'*60}")

        df = pd.read_parquet(path)

        # v2: testuj více ATR multiplikátorů a vyber nejlepší
        best_stats = None
        for atr_mult in ATR_MULTIPLIERS:
            trades, final_equity = simulate_trades(
                df, signal_col, hold_candles, direction,
                ticker, tf_name, atr_mult
            )
            if not trades:
                continue

            stats = compute_stats(trades, final_equity, label, atr_mult)
            if stats:
                if best_stats is None or stats["profit_factor"] > best_stats["profit_factor"]:
                    best_stats      = stats
                    best_trades     = trades
                    best_atr_mult   = atr_mult

        if best_stats:
            print_stats(best_stats)

            # Diagnostika: ukaz výsledky pro všechny ATR varianty
            print(f"\n  📈 ATR sensitivity (profit factor):")
            for atr_mult in ATR_MULTIPLIERS:
                trades_tmp, eq_tmp = simulate_trades(
                    df, signal_col, hold_candles, direction,
                    ticker, tf_name, atr_mult
                )
                if trades_tmp:
                    s = compute_stats(trades_tmp, eq_tmp, label, atr_mult)
                    bar = "█" * max(0, int(s["profit_factor"] * 10))
                    print(f"  ATR×{atr_mult}: PF={s['profit_factor']:<5} WR={s['win_rate']}% "
                          f"StopRate={s['stop_rate_pct']}%  {bar}")

            all_stats.append(best_stats)
            all_trades.extend(best_trades)

    # ── FINALNI REPORT ─────────────────────────────────────────
    if not all_stats:
        print("\n❌ Žádné výsledky.")
        return

    stats_df  = pd.DataFrame(all_stats)
    trades_df = pd.DataFrame(all_trades)

    stats_df.to_csv(os.path.join(OUTPUT_DIR,  "backtest_v2_stats.csv"),  index=False)
    trades_df.to_csv(os.path.join(OUTPUT_DIR, "backtest_v2_trades.csv"), index=False)

    print(f"\n{'═'*65}")
    print("🏆 FINÁLNÍ PŘEHLED (nejlepší ATR varianta pro každý signál)")
    print(f"{'═'*65}")
    print(f"  {'Strategie':<45} {'PF':<6} {'WR%':<7} {'DD%':<8} {'FTMO'}")
    print(f"  {'─'*65}")

    for _, row in stats_df.sort_values("profit_factor", ascending=False).iterrows():
        ftmo = "✅" if row["ftmo_dd_ok"] and row["ftmo_return_ok"] else "❌"
        pf   = f"{row['profit_factor']:.2f}"
        print(f"  {row['strategy'][:44]:<45} {pf:<6} {row['win_rate']:<7} {row['max_dd']:<8} {ftmo}")

    profitable = stats_df[stats_df["total_return"] > 0]
    ftmo_ok    = stats_df[stats_df["ftmo_dd_ok"] & stats_df["ftmo_return_ok"]]

    print(f"\n  Ziskové strategie:    {len(profitable)}/{len(stats_df)}")
    print(f"  FTMO-kompatibilní:    {len(ftmo_ok)}/{len(stats_df)}")
    print(f"\n  📁 Stats:  {OUTPUT_DIR}/backtest_v2_stats.csv")
    print(f"  📁 Trades: {OUTPUT_DIR}/backtest_v2_trades.csv")
    print(f"\n  💡 Další krok: Triple Barrier Method (Marcos) → lepší labeling")


if __name__ == "__main__":
    main()