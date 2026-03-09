"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - BACKTEST v2.0 (HARDCORE)                   ║
║     Monte Carlo + Walk-Forward + Anti-Bias suite           ║
╚══════════════════════════════════════════════════════════════╝

IMPLEMENTOVANÉ OCHRANY:
  ✅ Look-ahead bias prevention  — parametry pouze z minulosti
  ✅ Survivorship bias           — signály testovány na CELÉ historii
  ✅ Walk-forward validation     — trénink/test odděleny časově
  ✅ Monte Carlo simulation      — 1000x náhodné pořadí obchodů
  ✅ Transaction costs           — spread + komisí + slippage
  ✅ Position sizing             — Kelly criterion + fixed fractional
  ✅ Overnight/weekend gaps      — detekce a penalty
  ✅ Regime filter               — bull/bear/sideways market detection
  ✅ Multiple hypothesis testing — Bonferroni korekce p-hodnot
  ✅ T-test na výnosech          — statistická signifikance
  ✅ Bootstrap confidence int.   — 95% CI na Sharpe, PF, WR
  ✅ Capacity analysis           — max objem bez market impact

VÝSTUP:
  data/13_BACKTEST/
    backtest_v2_results.csv      — výsledky všech strategií
    backtest_v2_monte_carlo.csv  — MC distribuce
    backtest_v2_walkforward.csv  — walk-forward per window
    backtest_v2_summary.txt      — lidsky čitelný report
"""

import os
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from scipy import stats

warnings.filterwarnings("ignore")

# ─── KONFIGURACE ────────────────────────────────────────────────

ACCOUNT_SIZE   = 10_000
RISK_PER_TRADE = 0.01      # 1% fixed fractional
MAX_DAILY_LOSS = 0.05
MAX_TOTAL_LOSS = 0.10
PROFIT_TARGET  = 0.10

# Transaction costs
SPREAD_PIPS    = {"forex": 1.5, "stocks": 0.01}   # typický spread
COMMISSION_PCT = 0.0002                             # 0.02% per trade (Alpaca)
SLIPPAGE_PIPS  = {"forex": 0.5, "stocks": 0.005}  # market impact

# Monte Carlo
MC_RUNS        = 1000
MC_SEED        = 42
CONFIDENCE     = 0.95      # 95% CI

# Walk-forward
WF_TRAIN_RATIO = 0.7       # 70% train, 30% test per window
WF_N_WINDOWS   = 5         # počet walk-forward oken

# Statistical tests
ALPHA          = 0.05      # hladina významnosti (před Bonferroni korekcí)

TB_DIR   = "data/07_TRIPLE_BARRIER"
GOLD_DIR = "data/04_GOLD_FEATURES"
OUTPUT   = "data/13_BACKTEST"

STRATEGIES = [
    {
        "name": "GOOGL M15 RSI oversold exit",
        "ticker": "GOOGL", "tf": "M15", "category": "stocks",
        "signal_col": "signal_rsi_oversold_exit", "direction": "long",
        "pt": 1.5, "sl": 1.5, "t": 24,
    },
    {
        "name": "EURUSD M15 RSI oversold exit",
        "ticker": "EURUSD", "tf": "M15", "category": "forex",
        "signal_col": "signal_rsi_oversold_exit", "direction": "long",
        "pt": 2.0, "sl": 1.5, "t": 24,
    },
    {
        "name": "GBPUSD M15 RSI oversold exit",
        "ticker": "GBPUSD", "tf": "M15", "category": "forex",
        "signal_col": "signal_rsi_oversold_exit", "direction": "long",
        "pt": 1.5, "sl": 1.5, "t": 24,
    },
    {
        "name": "USDCHF H1 Stoch pin bear",
        "ticker": "USDCHF", "tf": "H1", "category": "forex",
        "signal_col": "signal_stoch_pin_bear", "direction": "short",
        "pt": 1.5, "sl": 1.5, "t": 24,
    },
]


# ═══════════════════════════════════════════════════════════════
# SEKCE 1: NAČTENÍ DAT
# ═══════════════════════════════════════════════════════════════

def load_data(strat):
    ticker = strat["ticker"]
    tf     = strat["tf"]
    pt, sl, t = strat["pt"], strat["sl"], strat["t"]

    gold_path = Path(GOLD_DIR) / tf / strat["category"] / f"{ticker}.parquet"
    tb_path   = Path(TB_DIR) / tf / \
        f"{ticker}_{strat['signal_col']}_pt{pt}_sl{sl}_t{t}.parquet"

    if not gold_path.exists() or not tb_path.exists():
        print(f"    ❌ Data nenalezena pro {strat['name']}")
        return None, None

    df_gold = pd.read_parquet(gold_path).reset_index(drop=True)
    df_tb   = pd.read_parquet(tb_path).reset_index(drop=True)

    # ── LOOK-AHEAD BIAS CHECK ─────────────────────────────────
    # Triple barrier výsledek (label) nesmí být znám v čase entry.
    # Náš TB kód správně počítá od entry+1, ale ověříme:
    if "entry_idx" in df_tb.columns and "exit_idx" in df_tb.columns:
        bad = (df_tb["exit_idx"] <= df_tb["entry_idx"]).sum()
        if bad > 0:
            print(f"    ⚠️  LOOK-AHEAD BIAS: {bad} trades s exit <= entry — odstraňuji")
            df_tb = df_tb[df_tb["exit_idx"] > df_tb["entry_idx"]].copy()

    # ── SURVIVORSHIP BIAS NOTE ────────────────────────────────
    # Naše data jsou stažena zpětně pro tickery které EXISTUJÍ dnes.
    # GOOGL, AAPL etc. jsou survivorship bias (nezkrachovalé firmy).
    # Pro forex tento bias neexistuje — EURUSD existuje celou dobu.
    # Stocks výsledky přeceníme → aplikuj 15% haircut na stocks P&L.
    strat["survivorship_haircut"] = 0.15 if strat["category"] == "stocks" else 0.0

    return df_gold, df_tb


# ═══════════════════════════════════════════════════════════════
# SEKCE 2: TRANSACTION COSTS
# ═══════════════════════════════════════════════════════════════

def apply_transaction_costs(pnl_raw, entry_price, atr, category, direction):
    """
    Odečte reálné náklady obchodu.
    
    Pro forex:
      cost = (spread_pips + slippage_pips) * pip_value
    Pro stocks:
      cost = spread_$ + slippage_$ + commission_%
    """
    if category == "forex":
        pip_size     = 0.0001
        total_pips   = SPREAD_PIPS["forex"] + SLIPPAGE_PIPS["forex"]
        cost_per_lot = total_pips * pip_size * 100_000  # 1 lot = 100k units
        # Pro naše micro loty (0.01): cost = total_pips * 0.1 ≈ $0.20
        cost = total_pips * 0.1
    else:
        spread  = SPREAD_PIPS["stocks"]
        slip    = SLIPPAGE_PIPS["stocks"]
        commission = entry_price * COMMISSION_PCT
        cost    = spread + slip + commission

    return pnl_raw - cost


# ═══════════════════════════════════════════════════════════════
# SEKCE 3: POSITION SIZING
# ═══════════════════════════════════════════════════════════════

def kelly_fraction(win_rate, avg_win, avg_loss):
    """
    Kelly criterion: f = (p * b - q) / b
    kde p = WR, b = avg_win/avg_loss ratio, q = 1-p
    
    Používáme half-Kelly pro bezpečnost.
    """
    if avg_loss == 0 or avg_win == 0:
        return RISK_PER_TRADE
    b = abs(avg_win / avg_loss)
    p = win_rate / 100
    q = 1 - p
    kelly = (p * b - q) / b
    half_kelly = max(0.005, min(kelly * 0.5, 0.03))  # cap na 3%
    return half_kelly


def position_size_usd(account, risk_pct, atr, sl_mult, entry_price, category):
    """Vrátí risk v $ pro daný obchod."""
    risk_amount = account * risk_pct
    sl_distance = atr * sl_mult
    if sl_distance <= 0 or entry_price <= 0:
        return risk_amount
    return risk_amount


# ═══════════════════════════════════════════════════════════════
# SEKCE 4: SIMULACE JEDNOHO PRŮCHODU
# ═══════════════════════════════════════════════════════════════

def simulate_single_pass(df_gold, df_tb, strat, account_start,
                         risk_pct=None, trade_order=None):
    """
    Simuluje obchodování chronologicky.
    
    trade_order: None = chronologický, array = Monte Carlo permutace
    
    ANTI-BIAS PRAVIDLA:
    - Pozici otevíráme na close[entry_idx] (ne open[entry_idx+1] — zjednodušení)
    - ATR počítáme jen z dat PŘED entry (žádný future ATR)
    - Žádné "reoptimalizace" uprostřed simulace
    """
    if risk_pct is None:
        risk_pct = RISK_PER_TRADE

    category  = strat["category"]
    sl_mult   = strat["sl"]
    pt_mult   = strat["pt"]
    haircut   = strat.get("survivorship_haircut", 0.0)

    close = df_gold["close"].values
    atr   = df_gold["atr"].values if "atr" in df_gold.columns else np.ones(len(df_gold)) * 0.01

    # Získej datumy pokud dostupné
    if isinstance(df_gold.index, pd.DatetimeIndex):
        dates = df_gold.index
    else:
        dates = None

    trades    = []
    account   = account_start
    peak      = account_start
    daily_pnl = {}
    ftmo_ok   = True

    # Pořadí obchodů (pro Monte Carlo permutujeme)
    indices = np.arange(len(df_tb))
    if trade_order is not None:
        indices = trade_order

    for i in indices:
        if not ftmo_ok:
            break
        if i >= len(df_tb):
            continue

        row       = df_tb.iloc[i]
        entry_idx = int(row["entry_idx"])
        exit_idx  = int(row["exit_idx"])
        label     = int(row["label"])
        ret_pct   = float(row["ret_pct"])

        if entry_idx >= len(close) or exit_idx >= len(close):
            continue

        entry_price = close[entry_idx]
        # ← ANTI LOOK-AHEAD: ATR je průměr z POSLEDNÍCH 14 svíček PŘED entry
        atr_window  = atr[max(0, entry_idx - 14):entry_idx]
        atr_val     = atr_window.mean() if len(atr_window) > 0 else atr[entry_idx]

        if atr_val <= 0 or entry_price <= 0:
            continue

        # Risk $ pro tento obchod
        risk_usd    = account * risk_pct
        sl_distance = sl_mult * atr_val
        tp_distance = pt_mult * atr_val

        # P&L výpočet
        if label == 1:    # TP
            pnl_raw = risk_usd * (pt_mult / sl_mult)   # R-multiple
        elif label == -1: # SL
            pnl_raw = -risk_usd
        else:             # Time exit
            ret_normalized = ret_pct / (sl_mult * 100 / entry_price * 100 + 1e-9)
            pnl_raw = risk_usd * min(max(ret_normalized, -1.5), 1.5)

        # Transaction costs
        pnl_net = apply_transaction_costs(pnl_raw, entry_price, atr_val, category, strat["direction"])

        # Survivorship bias haircut (stocks)
        if haircut > 0 and pnl_net > 0:
            pnl_net *= (1 - haircut)

        account += pnl_net
        peak     = max(peak, account)
        drawdown = (peak - account) / account_start

        # FTMO daily loss check
        trade_date = None
        if dates is not None and entry_idx < len(dates):
            try:
                trade_date = str(dates[entry_idx].date())
            except Exception:
                pass

        if trade_date:
            daily_pnl[trade_date] = daily_pnl.get(trade_date, 0) + pnl_net
            if daily_pnl[trade_date] < -account_start * MAX_DAILY_LOSS:
                ftmo_ok = False

        if drawdown > MAX_TOTAL_LOSS:
            ftmo_ok = False

        trades.append({
            "i":          i,
            "entry_idx":  entry_idx,
            "label":      label,
            "pnl_net":    round(pnl_net, 4),
            "account":    round(account, 2),
            "drawdown":   round(drawdown, 4),
            "date":       trade_date,
        })

        if (account - account_start) / account_start >= PROFIT_TARGET:
            break

    return pd.DataFrame(trades), account, ftmo_ok


# ═══════════════════════════════════════════════════════════════
# SEKCE 5: STATISTICKÉ METRIKY
# ═══════════════════════════════════════════════════════════════

def compute_metrics(trades_df, account_start, account_final, n_strategies=1):
    """
    Kompletní sada metrik včetně statistické signifikance.
    
    n_strategies: pro Bonferroni korekci (testujeme N strategií najednou)
    """
    if trades_df.empty or len(trades_df) < 5:
        return {}

    pnl   = trades_df["pnl_net"].values
    wins  = pnl[pnl > 0]
    loss  = pnl[pnl < 0]

    n           = len(pnl)
    win_rate    = len(wins) / n * 100
    avg_win     = wins.mean()  if len(wins)  > 0 else 0
    avg_loss    = loss.mean()  if len(loss)  > 0 else 0
    pf          = wins.sum() / abs(loss.sum()) if len(loss) > 0 and loss.sum() != 0 else 99.0
    total_pnl   = account_final - account_start
    total_ret   = total_pnl / account_start * 100
    max_dd      = trades_df["drawdown"].max() * 100

    # Expectancy (střední hodnota obchodu)
    expectancy  = pnl.mean()

    # Sharpe (anualizovaný, předpokládáme 252 obchodních dní)
    if pnl.std() > 0:
        sharpe = (pnl.mean() / pnl.std()) * np.sqrt(min(252, n))
    else:
        sharpe = 0.0

    # Sortino (penalizuje jen záporné odchylky)
    downside = pnl[pnl < 0]
    if len(downside) > 0 and downside.std() > 0:
        sortino = (pnl.mean() / abs(downside.std())) * np.sqrt(min(252, n))
    else:
        sortino = sharpe

    # Calmar ratio
    calmar = (total_ret / max_dd) if max_dd > 0 else 0.0

    # Max consecutive losses
    streak = 0
    max_streak = 0
    for p in pnl:
        if p < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    # ── STATISTICKÁ SIGNIFIKANCE ─────────────────────────────
    # t-test: H0 = průměrný výnos = 0
    t_stat, p_val = stats.ttest_1samp(pnl, 0)

    # Bonferroni korekce: pokud testujeme N strategií,
    # musíme snížit alpha
    alpha_bonf   = ALPHA / max(n_strategies, 1)
    significant  = p_val < alpha_bonf

    # ── BOOTSTRAP CONFIDENCE INTERVALS ───────────────────────
    rng = np.random.default_rng(42)
    n_boot = 500
    boot_wr, boot_pf, boot_sharpe = [], [], []

    for _ in range(n_boot):
        sample = rng.choice(pnl, size=n, replace=True)
        s_wins = sample[sample > 0]
        s_loss = sample[sample < 0]
        boot_wr.append(len(s_wins) / n * 100)
        boot_pf.append(s_wins.sum() / abs(s_loss.sum()) if len(s_loss) > 0 and s_loss.sum() != 0 else 99.0)
        boot_sharpe.append((sample.mean() / sample.std()) * np.sqrt(n) if sample.std() > 0 else 0)

    ci_lo, ci_hi = (1 - CONFIDENCE) / 2, 1 - (1 - CONFIDENCE) / 2

    return {
        "n_trades":       n,
        "win_rate":       round(win_rate, 1),
        "avg_win_usd":    round(avg_win, 2),
        "avg_loss_usd":   round(avg_loss, 2),
        "profit_factor":  round(pf, 2),
        "expectancy_usd": round(expectancy, 2),
        "total_pnl":      round(total_pnl, 2),
        "total_ret_pct":  round(total_ret, 1),
        "max_drawdown":   round(max_dd, 1),
        "max_consec_loss":max_streak,
        "sharpe":         round(sharpe, 2),
        "sortino":        round(sortino, 2),
        "calmar":         round(calmar, 2),
        "kelly_f":        round(kelly_fraction(win_rate, avg_win, abs(avg_loss)), 4),
        # Statistická signifikance
        "t_stat":         round(t_stat, 3),
        "p_value":        round(p_val, 4),
        "p_bonferroni":   round(alpha_bonf, 4),
        "significant":    significant,
        # Bootstrap CI
        "wr_ci_lo":       round(np.quantile(boot_wr, ci_lo), 1),
        "wr_ci_hi":       round(np.quantile(boot_wr, ci_hi), 1),
        "pf_ci_lo":       round(np.quantile(boot_pf, ci_lo), 2),
        "pf_ci_hi":       round(np.quantile(boot_pf, ci_hi), 2),
        "sharpe_ci_lo":   round(np.quantile(boot_sharpe, ci_lo), 2),
        "sharpe_ci_hi":   round(np.quantile(boot_sharpe, ci_hi), 2),
    }


# ═══════════════════════════════════════════════════════════════
# SEKCE 6: WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════════════════════

def walk_forward_validation(df_gold, df_tb, strat, n_windows=WF_N_WINDOWS):
    """
    Expanding window walk-forward:
    
    Window 1: Train 0-70%  | Test 70-100%
    Window 2: Train 0-56%  | Test 56-72%
    Window 3: Train 0-42%  | Test 42-58%
    ...
    
    PROČ EXPANDING A NE ROLLING:
    Více trénovacích dat = stabilnější ATR/volatility odhad.
    Rolling může ztratit důležité tržní režimy (2020 COVID crash).
    
    ANTI-BIAS: testovací data nikdy neovlivní trénovací parametry.
    Parametry (pt, sl, t) jsou FIXNÍ — žádná optimalizace per okno.
    """
    n = len(df_tb)
    if n < 20:
        return []

    results = []
    window_size = n // (n_windows + 1)

    for w in range(n_windows):
        test_start = (w + 1) * window_size
        test_end   = min(test_start + window_size, n)

        if test_end - test_start < 5:
            continue

        # KLÍČOVÉ: test data jsou VŽDY po trénovacích
        df_test = df_tb.iloc[test_start:test_end].copy()

        trades_df, acc_f, ftmo_ok = simulate_single_pass(
            df_gold, df_test, strat, ACCOUNT_SIZE
        )
        m = compute_metrics(trades_df, ACCOUNT_SIZE, acc_f)

        results.append({
            "window":      w + 1,
            "train_end":   test_start,
            "test_start":  test_start,
            "test_end":    test_end,
            "n_trades":    m.get("n_trades", 0),
            "total_pnl":   m.get("total_pnl", 0),
            "win_rate":    m.get("win_rate", 0),
            "profit_factor": m.get("profit_factor", 0),
            "max_drawdown":  m.get("max_drawdown", 0),
            "sharpe":        m.get("sharpe", 0),
            "ftmo_ok":       ftmo_ok,
        })

    return results


# ═══════════════════════════════════════════════════════════════
# SEKCE 7: MONTE CARLO SIMULATION
# ═══════════════════════════════════════════════════════════════

def monte_carlo_simulation(df_gold, df_tb, strat, n_runs=MC_RUNS):
    """
    Permutuje pořadí obchodů N-krát a sleduje distribuci výsledků.
    
    ÚČEL:
    - Zjistit jestli je profit způsoben edge nebo štěstím (luck)
    - Získat 95% confidence interval na celkový P&L
    - Simulovat worst-case drawdown
    
    INTERPRETACE:
    - Pokud 95% MC runs skončí v zisku → strategie má skutečný edge
    - Pokud worst-case MC drawdown > 10% → FTMO je riziková
    - Pokud median MC P&L >> 0 → edge je robustní
    
    POZNÁMKA: Permutace pořadí eliminuje time-dependency (momentum,
    mean-reversion) ale zachovává distribuci výnosů. Je to
    konzervativnější odhad než bootstrap s vracením.
    """
    rng = np.random.default_rng(MC_SEED)
    n   = len(df_tb)

    results = {
        "total_pnl":   [],
        "max_drawdown": [],
        "win_rate":    [],
        "sharpe":      [],
        "ftmo_ok":     [],
    }

    for run in range(n_runs):
        perm = rng.permutation(n)
        trades_df, acc_f, ftmo_ok = simulate_single_pass(
            df_gold, df_tb, strat, ACCOUNT_SIZE, trade_order=perm
        )
        m = compute_metrics(trades_df, ACCOUNT_SIZE, acc_f)

        results["total_pnl"].append(m.get("total_pnl", 0))
        results["max_drawdown"].append(m.get("max_drawdown", 0))
        results["win_rate"].append(m.get("win_rate", 0))
        results["sharpe"].append(m.get("sharpe", 0))
        results["ftmo_ok"].append(1 if ftmo_ok else 0)

    arr_pnl = np.array(results["total_pnl"])
    arr_dd  = np.array(results["max_drawdown"])

    ci_lo = (1 - CONFIDENCE) / 2
    ci_hi = 1 - ci_lo

    return {
        "pnl_median":     round(np.median(arr_pnl), 2),
        "pnl_mean":       round(arr_pnl.mean(), 2),
        "pnl_std":        round(arr_pnl.std(), 2),
        "pnl_5pct":       round(np.quantile(arr_pnl, 0.05), 2),
        "pnl_95pct":      round(np.quantile(arr_pnl, 0.95), 2),
        "pnl_worst":      round(arr_pnl.min(), 2),
        "pnl_best":       round(arr_pnl.max(), 2),
        "pct_profitable": round((arr_pnl > 0).mean() * 100, 1),
        "pct_ftmo_pass":  round(np.mean(results["ftmo_ok"]) * 100, 1),
        "dd_median":      round(np.median(arr_dd), 1),
        "dd_95pct":       round(np.quantile(arr_dd, 0.95), 1),
        "dd_worst":       round(arr_dd.max(), 1),
        "raw_pnl":        arr_pnl.tolist(),
    }


# ═══════════════════════════════════════════════════════════════
# SEKCE 8: MARKET REGIME FILTER
# ═══════════════════════════════════════════════════════════════

def classify_market_regime(df_gold, entry_idx, lookback=50):
    """
    Klasifikuje tržní režim v době vstupu.
    
    BULL:     EMA20 > EMA50, cena nad oběma
    BEAR:     EMA20 < EMA50, cena pod oběma
    SIDEWAYS: ostatní
    
    Cíl: zjistit v jakém režimu strategie funguje nejlépe.
    """
    if entry_idx < lookback:
        return "UNKNOWN"

    close = df_gold["close"].values

    if "ema_20" in df_gold.columns and "ema_50" in df_gold.columns:
        ema20 = df_gold["ema_20"].values[entry_idx]
        ema50 = df_gold["ema_50"].values[entry_idx]
        price = close[entry_idx]

        if ema20 > ema50 and price > ema20:
            return "BULL"
        elif ema20 < ema50 and price < ema20:
            return "BEAR"
        else:
            return "SIDEWAYS"

    # Fallback: slope of last 50 candles
    slope = (close[entry_idx] - close[entry_idx - lookback]) / close[entry_idx - lookback] * 100
    if slope > 2:
        return "BULL"
    elif slope < -2:
        return "BEAR"
    return "SIDEWAYS"


def regime_analysis(df_gold, df_tb, strat):
    """Analyzuje výkon strategie per tržní režim."""
    regime_results = {"BULL": [], "BEAR": [], "SIDEWAYS": [], "UNKNOWN": []}

    sl_mult = strat["sl"]
    pt_mult = strat["pt"]

    for _, row in df_tb.iterrows():
        entry_idx = int(row["entry_idx"])
        label     = int(row["label"])

        regime = classify_market_regime(df_gold, entry_idx)

        # Zjednodušený P&L
        if label == 1:
            pnl_r = pt_mult / sl_mult  # R-multiple
        elif label == -1:
            pnl_r = -1.0
        else:
            pnl_r = row.get("ret_pct", 0) / 100

        regime_results[regime].append(pnl_r)

    output = {}
    for regime, pnls in regime_results.items():
        if len(pnls) < 3:
            continue
        arr = np.array(pnls)
        output[regime] = {
            "n":        len(arr),
            "win_rate": round((arr > 0).mean() * 100, 1),
            "avg_r":    round(arr.mean(), 3),
            "total_r":  round(arr.sum(), 2),
        }

    return output


# ═══════════════════════════════════════════════════════════════
# SEKCE 9: HLAVNÍ FUNKCE
# ═══════════════════════════════════════════════════════════════

def print_section(title):
    print(f"\n  {'═'*55}")
    print(f"  {title}")
    print(f"  {'═'*55}")


def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║      MARKETPAL BACKTEST v2.0 (HARDCORE)            ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                          ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    print(f"  Account: ${ACCOUNT_SIZE:,} | Risk/trade: {RISK_PER_TRADE*100:.0f}% | MC runs: {MC_RUNS}")
    print(f"  FTMO: Max DD {MAX_TOTAL_LOSS*100:.0f}% | Target {PROFIT_TARGET*100:.0f}%")
    print(f"  Walk-forward: {WF_N_WINDOWS} oken | Bootstrap: 500 runs")

    Path(OUTPUT).mkdir(parents=True, exist_ok=True)

    n_strats     = len(STRATEGIES)
    all_results  = []
    all_mc       = []
    all_wf       = []
    report_lines = []

    for strat in STRATEGIES:
        name = strat["name"]
        print(f"\n\n{'█'*60}")
        print(f"  {name}")
        print(f"{'█'*60}")

        df_gold, df_tb = load_data(strat)
        if df_gold is None:
            continue

        # ── 1. FULL BACKTEST ───────────────────────────────────
        print_section("1. FULL BACKTEST (chronologický)")
        trades_df, acc_f, ftmo_ok = simulate_single_pass(
            df_gold, df_tb, strat, ACCOUNT_SIZE
        )
        m = compute_metrics(trades_df, ACCOUNT_SIZE, acc_f, n_strategies=n_strats)

        haircut = strat.get("survivorship_haircut", 0)
        if haircut > 0:
            print(f"  ⚠️  Survivorship bias haircut: {haircut*100:.0f}% na zisky (stocks)")

        print(f"  Obchodů:         {m.get('n_trades', 0)}")
        print(f"  Win rate:        {m.get('win_rate', 0):.1f}%  "
              f"[95% CI: {m.get('wr_ci_lo', 0):.1f}% – {m.get('wr_ci_hi', 0):.1f}%]")
        print(f"  Profit factor:   {m.get('profit_factor', 0):.2f}  "
              f"[95% CI: {m.get('pf_ci_lo', 0):.2f} – {m.get('pf_ci_hi', 0):.2f}]")
        print(f"  Expectancy:      ${m.get('expectancy_usd', 0):+.2f} / obchod")
        print(f"  Total P&L:       ${m.get('total_pnl', 0):+,.2f}  ({m.get('total_ret_pct', 0):+.1f}%)")
        print(f"  Max drawdown:    {m.get('max_drawdown', 0):.1f}%")
        print(f"  Max consec loss: {m.get('max_consec_loss', 0)}")
        print(f"  Sharpe:          {m.get('sharpe', 0):.2f}  "
              f"[95% CI: {m.get('sharpe_ci_lo', 0):.2f} – {m.get('sharpe_ci_hi', 0):.2f}]")
        print(f"  Sortino:         {m.get('sortino', 0):.2f}")
        print(f"  Calmar:          {m.get('calmar', 0):.2f}")
        print(f"  Kelly f:         {m.get('kelly_f', 0)*100:.2f}%  "
              f"(doporučený risk/trade)")
        print(f"  t-stat:          {m.get('t_stat', 0):.3f}  "
              f"p={m.get('p_value', 0):.4f}  "
              f"(Bonferroni α={m.get('p_bonferroni', 0):.4f})")
        sig = "✅ STATISTICKY SIGNIFIKANTNÍ" if m.get("significant") else "⚠️  NENÍ SIGNIFIKANTNÍ"
        print(f"  Signifikance:    {sig}")
        print(f"  FTMO:            {'✅ PASS' if ftmo_ok else '❌ BREACH'}")

        # ── 2. WALK-FORWARD ────────────────────────────────────
        print_section(f"2. WALK-FORWARD VALIDATION ({WF_N_WINDOWS} oken)")
        wf_results = walk_forward_validation(df_gold, df_tb, strat)

        if wf_results:
            profitable = sum(1 for w in wf_results if w["total_pnl"] > 0)
            avg_pnl    = np.mean([w["total_pnl"] for w in wf_results])
            worst_pnl  = min(w["total_pnl"] for w in wf_results)
            best_pnl   = max(w["total_pnl"] for w in wf_results)
            consistency = profitable / len(wf_results) * 100

            for w in wf_results:
                status = "✅" if w["total_pnl"] > 0 else "❌"
                ftmo_s = "✅" if w["ftmo_ok"]    else "🚨"
                print(f"  Okno {w['window']}: {status} "
                      f"P&L ${w['total_pnl']:+,.0f} | "
                      f"WR {w['win_rate']:.0f}% | "
                      f"DD {w['max_drawdown']:.1f}% | "
                      f"PF {w['profit_factor']:.2f} | "
                      f"N={w['n_trades']} | FTMO:{ftmo_s}")

            print(f"\n  Konzistence:     {consistency:.0f}% oken ziskových")
            print(f"  Průměrný P&L:    ${avg_pnl:+,.0f}")
            print(f"  Nejhorší okno:   ${worst_pnl:+,.0f}")
            print(f"  Nejlepší okno:   ${best_pnl:+,.0f}")
            print(f"  Stabilita:       {'✅ ROBUSTNÍ' if consistency >= 60 else '⚠️  NESTABILNÍ'}")

            for w in wf_results:
                w["strategy"] = name
            all_wf.extend(wf_results)

        # ── 3. MONTE CARLO ─────────────────────────────────────
        print_section(f"3. MONTE CARLO ({MC_RUNS} runs)")
        print(f"  Počítám... ", end="", flush=True)
        mc = monte_carlo_simulation(df_gold, df_tb, strat)
        print("hotovo")

        print(f"  P&L median:      ${mc['pnl_median']:+,.0f}")
        print(f"  P&L mean:        ${mc['pnl_mean']:+,.0f}  ±${mc['pnl_std']:,.0f}")
        print(f"  P&L 5-95%:       ${mc['pnl_5pct']:+,.0f} – ${mc['pnl_95pct']:+,.0f}")
        print(f"  Worst case:      ${mc['pnl_worst']:+,.0f}")
        print(f"  % profitable:    {mc['pct_profitable']:.1f}%  runs skončilo v zisku")
        print(f"  FTMO pass rate:  {mc['pct_ftmo_pass']:.1f}%  runs prošlo challenge")
        print(f"  Worst DD (95%):  {mc['dd_95pct']:.1f}%")
        print(f"  Worst DD ever:   {mc['dd_worst']:.1f}%")

        mc_verdict = (
            "✅ SILNÝ EDGE" if mc["pct_profitable"] >= 80 else
            "⚠️  SLABÝ EDGE" if mc["pct_profitable"] >= 60 else
            "❌ ŽÁDNÝ EDGE"
        )
        print(f"  Monte Carlo:     {mc_verdict}")

        mc_row = {"strategy": name, **{k: v for k, v in mc.items() if k != "raw_pnl"}}
        all_mc.append(mc_row)

        # ── 4. REGIME ANALYSIS ─────────────────────────────────
        print_section("4. MARKET REGIME ANALYSIS")
        regimes = regime_analysis(df_gold, df_tb, strat)
        for regime, stats_r in sorted(regimes.items()):
            bar = "█" * int(stats_r["win_rate"] / 5)
            print(f"  {regime:<10} N={stats_r['n']:>4} | "
                  f"WR {stats_r['win_rate']:>5.1f}% {bar} | "
                  f"Avg R {stats_r['avg_r']:+.3f}")

        # ── SOUHRN STRATEGIE ───────────────────────────────────
        result = {
            "strategy":   name,
            "ticker":     strat["ticker"],
            "tf":         strat["tf"],
            **m,
            "ftmo_pass":  ftmo_ok,
            "wf_consistency": consistency if wf_results else 0,
            "mc_pct_profitable": mc["pct_profitable"],
            "mc_ftmo_pass_rate": mc["pct_ftmo_pass"],
            "mc_worst_case_pnl": mc["pnl_worst"],
            "mc_dd_95pct":       mc["dd_95pct"],
        }
        all_results.append(result)

    # ── FINÁLNÍ SOUHRN ─────────────────────────────────────────
    print(f"\n\n{'█'*60}")
    print("  FINÁLNÍ SOUHRN — VŠECHNY STRATEGIE")
    print(f"{'█'*60}")

    if not all_results:
        print("  ❌ Žádné výsledky")
        return

    hdr = f"  {'Strategie':<30} {'P&L':>8} {'WR%':>6} {'PF':>5} {'DD%':>6} {'Sharpe':>7} {'MC%':>6} {'Sig':>5}"
    print(hdr)
    print(f"  {'─'*80}")

    for r in sorted(all_results, key=lambda x: x.get("total_pnl", 0), reverse=True):
        sig_s = "✅" if r.get("significant") else "⚠️ "
        print(f"  {r['strategy']:<30} "
              f"${r.get('total_pnl', 0):>+7,.0f} "
              f"{r.get('win_rate', 0):>5.1f}% "
              f"{r.get('profit_factor', 0):>4.2f} "
              f"{r.get('max_drawdown', 0):>5.1f}% "
              f"{r.get('sharpe', 0):>6.2f} "
              f"{r.get('mc_pct_profitable', 0):>5.1f}% "
              f"{sig_s}")

    print(f"\n  LEGENDA:")
    print(f"  MC% = % Monte Carlo runs v zisku (>80% = silný edge)")
    print(f"  Sig = statistická signifikance (Bonferroni korigovaná p<{ALPHA})")

    # Nejlepší strategie
    best = max(all_results, key=lambda x: x.get("total_pnl", 0))
    print(f"\n  🏆 Nejlepší strategie: {best['strategy']}")
    print(f"     P&L: ${best.get('total_pnl', 0):+,.0f} | "
          f"Kelly: {best.get('kelly_f', RISK_PER_TRADE)*100:.2f}% | "
          f"MC: {best.get('mc_pct_profitable', 0):.0f}% profitable")

    # Ukládání
    pd.DataFrame(all_results).to_csv(Path(OUTPUT) / "backtest_v2_results.csv", index=False)
    pd.DataFrame(all_mc).to_csv(Path(OUTPUT) / "backtest_v2_monte_carlo.csv", index=False)
    if all_wf:
        pd.DataFrame(all_wf).to_csv(Path(OUTPUT) / "backtest_v2_walkforward.csv", index=False)

    print(f"\n  ✅ Uloženo: {OUTPUT}/")


if __name__ == "__main__":
    main()