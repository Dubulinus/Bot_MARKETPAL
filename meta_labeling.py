"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - META-LABELING v1.3                     ║
║         STRATEGIES = STRONG signály z triple_barrier v3    ║
╚══════════════════════════════════════════════════════════════╝

STRONG signály (triple_barrier.py výsledky 2026-03-07):
    macd_bull      H1  AAPL    long   WR:61.5% PF:3.43  N:13
    rsi_oversold   H1  USDJPY  long   WR:80.0% PF:3.31  N:15  ← nejlepší!
    macd_bear      H1  GBPUSD  short  WR:55.0% PF:2.45  N:20
    macd_bear      M15 NVDA    short  WR:58.5% PF:2.12  N:53  ← nejvíce vzorků

POZNÁMKA:
    H1 signály mají malé N (13-20) → modely budou orientační.
    NVDA M15 má N=53 → nejspolehlivější model.
    Po doplnění H1 dat (EURUSD/GBPUSD/USDCHF/AAPL/AMZN) spusť znovu.
"""

import os
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score, TimeSeriesSplit
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import precision_score
    import pickle
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    print("⚠️  Spusť: pip install scikit-learn")

GOLD_DIR   = "data/04_GOLD_FEATURES"
TB_DIR     = "data/07_TRIPLE_BARRIER"
OUTPUT_DIR = "data/11_META_LABELS"

MIN_SAMPLES = 10

# ── STRONG signály z triple_barrier.py ────────────────────────
STRATEGIES = [
    {
        "name":      "NVDA MACD Bear M15",
        "ticker":    "NVDA",
        "tf":        "M15",
        "category":  "stocks",
        "signal":    "signal_macd_bear",
        "direction": "short",
        "pt":        1.5,
        "sl":        1.5,
        "t":         12,
        # WR: 58.5% | PF: 2.12 | N: 53 ← nejvíce vzorků, nejspolehlivější
    },
    {
        "name":      "USDJPY RSI Oversold H1",
        "ticker":    "USDJPY",
        "tf":        "H1",
        "category":  "forex",
        "signal":    "signal_rsi_oversold_exit",
        "direction": "long",
        "pt":        1.5,
        "sl":        1.5,
        "t":         24,
        # WR: 80.0% | PF: 3.31 | N: 15 ← nejlepší WR, ale malé N
    },
    {
        "name":      "GBPUSD MACD Bear H1",
        "ticker":    "GBPUSD",
        "tf":        "H1",
        "category":  "forex",
        "signal":    "signal_macd_bear",
        "direction": "short",
        "pt":        1.5,
        "sl":        1.0,
        "t":         12,
        # WR: 55.0% | PF: 2.45 | N: 20
    },
    {
        "name":      "AAPL MACD Bull H1",
        "ticker":    "AAPL",
        "tf":        "H1",
        "category":  "stocks",
        "signal":    "signal_macd_bull",
        "direction": "long",
        "pt":        3.0,
        "sl":        1.0,
        "t":         24,
        # WR: 61.5% | PF: 3.43 | N: 13
    },
]


def get_timestamp_series(df):
    if "timestamp" in df.columns:
        return pd.to_datetime(df["timestamp"], errors="coerce")
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index.to_series().reset_index(drop=True)
    for col in df.columns:
        if "time" in col.lower() or "date" in col.lower():
            try:
                return pd.to_datetime(df[col], errors="coerce")
            except Exception:
                pass
    return None


def build_meta_features(df, signal_col):
    timestamps = get_timestamp_series(df)
    features   = []
    signal_indices = np.where(df[signal_col].values.astype(bool))[0]

    for idx in signal_indices:
        row  = df.iloc[idx]
        feat = {}

        if "ema_20" in df.columns and "ema_50" in df.columns:
            ema20 = row.get("ema_20", np.nan)
            ema50 = row.get("ema_50", np.nan)
            close = row.get("close",  np.nan)
            if not any(pd.isna([ema20, ema50, close])):
                feat["ema_trend"]      = (ema20 - ema50) / ema50 * 100
                feat["price_vs_ema20"] = (close - ema20) / ema20 * 100
                feat["price_vs_ema50"] = (close - ema50) / ema50 * 100

        if "vwap" in df.columns:
            vwap  = row.get("vwap",  np.nan)
            close = row.get("close", np.nan)
            if not any(pd.isna([vwap, close])) and vwap > 0:
                feat["price_vs_vwap"] = (close - vwap) / vwap * 100

        if "atr" in df.columns:
            atr = row.get("atr", np.nan)
            if not pd.isna(atr) and idx >= 50:
                atr_mean = df["atr"].iloc[max(0, idx-50):idx].mean()
                feat["atr_ratio"] = atr / atr_mean if atr_mean > 0 else 1.0

        if "bb_upper" in df.columns and "bb_lower" in df.columns:
            bbu   = row.get("bb_upper", np.nan)
            bbl   = row.get("bb_lower", np.nan)
            close = row.get("close",    np.nan)
            if not any(pd.isna([bbu, bbl, close])) and (bbu - bbl) > 0:
                feat["bb_width"]    = (bbu - bbl) / close * 100
                feat["bb_position"] = (close - bbl) / (bbu - bbl)

        if "rsi" in df.columns:
            feat["rsi"] = row.get("rsi", 50)

        if "macd" in df.columns and "macd_signal" in df.columns:
            macd = row.get("macd",        np.nan)
            msig = row.get("macd_signal", np.nan)
            if not any(pd.isna([macd, msig])):
                feat["macd_hist"] = macd - msig

        if "volume" in df.columns and idx >= 20:
            vol      = row.get("volume", np.nan)
            vol_mean = df["volume"].iloc[max(0, idx-20):idx].mean()
            if not pd.isna(vol) and vol_mean > 0:
                feat["volume_ratio"] = vol / vol_mean

        if timestamps is not None:
            ts = timestamps.iloc[idx] if idx < len(timestamps) else None
            if ts is not None and not pd.isna(ts):
                feat["hour"]        = ts.hour
                feat["day_of_week"] = ts.dayofweek

        if idx >= 20:
            closes = df["close"].iloc[max(0, idx-20):idx+1].values
            if len(closes) > 1:
                feat["trend_20"] = (closes[-1] - closes[0]) / closes[0] * 100
        if idx >= 5:
            closes = df["close"].iloc[max(0, idx-5):idx+1].values
            if len(closes) > 1:
                feat["trend_5"] = (closes[-1] - closes[0]) / closes[0] * 100

        feat["entry_idx"] = idx
        features.append(feat)

    return pd.DataFrame(features)


def train_meta_model(df_features, labels_df, strategy_name, n_raw):
    EMPTY = (None, None, None)
    if not SKLEARN_OK:
        return EMPTY

    if n_raw < 20:
        print(f"    ⚠️  Malé N={n_raw} — přidej více H1 dat pro spolehlivý model")

    df = df_features.copy()
    df = df.merge(labels_df[["entry_idx", "label"]], on="entry_idx", how="inner")
    df = df[df["label"] != 0]

    if len(df) < MIN_SAMPLES:
        print(f"    Nedostatek vzorků: {len(df)} < {MIN_SAMPLES}")
        return EMPTY

    df["target"] = (df["label"] == 1).astype(int)
    feature_cols = [c for c in df.columns
                    if c not in ["entry_idx", "label", "target"]
                    and not df[c].isna().all()]

    df_clean = df[feature_cols + ["target"]].dropna()
    if len(df_clean) < MIN_SAMPLES:
        print(f"    Nedostatek čistých vzorků: {len(df_clean)}")
        return EMPTY

    X = df_clean[feature_cols].values
    y = df_clean["target"].values
    baseline_wr = y.mean() * 100

    n_splits = min(5, len(df_clean) // 4)
    if n_splits < 2:
        print(f"    Příliš málo dat pro CV ({len(df_clean)} vzorků)")
        return EMPTY

    tscv     = TimeSeriesSplit(n_splits=n_splits)
    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = RandomForestClassifier(
        n_estimators  = 200,
        max_depth     = 4,
        min_samples_leaf = 3,
        class_weight  = "balanced",
        random_state  = 42,
    )

    cv_scores    = cross_val_score(model, X_scaled, y, cv=tscv, scoring="precision")
    cv_precision = cv_scores.mean() * 100
    cv_std       = cv_scores.std()   * 100

    model.fit(X_scaled, y)
    train_precision = precision_score(y, model.predict(X_scaled), zero_division=0) * 100

    importances  = pd.Series(model.feature_importances_, index=feature_cols)
    top_features = importances.nlargest(5)

    improvement = cv_precision - baseline_wr

    print(f"    Vzorků:          {len(df_clean)} ({int(y.sum())} wins / {int((1-y).sum())} losses)")
    print(f"    Baseline WR:     {baseline_wr:.1f}%")
    print(f"    CV Precision:    {cv_precision:.1f}% ± {cv_std:.1f}%  (splits={n_splits})")
    print(f"    Train Precision: {train_precision:.1f}%")
    print(f"    Zlepšení:        {improvement:+.1f}%")
    print(f"    Top features:")
    for feat, imp in top_features.items():
        print(f"      {feat:<20} {imp:.3f}")

    if improvement >= 5:
        verdict = "✅ UŽITEČNÝ"
    elif improvement >= 2:
        verdict = "⚠️  MARGINÁLNÍ"
    elif n_raw < 20:
        verdict = "⏳ NEDOSTATEK DAT"
    else:
        verdict = "❌ NEPOMÁHÁ"

    print(f"    Verdict:         {verdict}")

    return model, {
        "strategy":     strategy_name,
        "n_samples":    len(df_clean),
        "baseline_wr":  round(baseline_wr, 1),
        "cv_precision": round(cv_precision, 1),
        "cv_std":       round(cv_std, 1),
        "improvement":  round(improvement, 1),
        "top_features": top_features.to_dict(),
        "verdict":      verdict,
        "feature_cols": feature_cols,
    }, scaler


def predict_meta(model, scaler, features_row, feature_cols):
    if model is None:
        return True, 1.0
    try:
        row = {f: features_row.get(f, 0) for f in feature_cols}
        X   = np.array([[row[f] for f in feature_cols]])
        X_s = scaler.transform(X)
        proba        = model.predict_proba(X_s)[0]
        confidence   = proba[1]
        should_trade = confidence >= 0.60
        return should_trade, round(confidence, 3)
    except Exception:
        return True, 1.0


def main():
    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL META-LABELING v1.3       ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")
    print("  STRATEGIES: 4 STRONG signály z triple_barrier\n")

    if not SKLEARN_OK:
        print("  pip install scikit-learn")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_stats = []

    for strat in STRATEGIES:
        ticker    = strat["ticker"]
        tf        = strat["tf"]
        pt, sl, t = strat["pt"], strat["sl"], strat["t"]

        print(f"\n  {'─'*50}")
        print(f"  {strat['name']}")
        print(f"  {'─'*50}")

        gold_path = Path(GOLD_DIR) / tf / strat["category"] / f"{ticker}.parquet"
        if not gold_path.exists():
            print(f"    ❌ Gold data nenalezena: {gold_path}")
            continue

        df = pd.read_parquet(gold_path).reset_index(drop=True)

        if strat["signal"] not in df.columns:
            avail = [c for c in df.columns if c.startswith("signal_")]
            print(f"    ❌ Signál '{strat['signal']}' nenalezen.")
            print(f"       Dostupné: {avail}")
            continue

        tb_path = Path(TB_DIR) / tf / \
            f"{ticker}_{strat['signal']}_pt{pt}_sl{sl}_t{t}.parquet"

        if not tb_path.exists():
            print(f"    ❌ TB labely nenalezeny: {tb_path}")
            print(f"       Spusť: python triple_barrier.py")
            continue

        labels_df   = pd.read_parquet(tb_path)
        n_raw       = len(labels_df)
        df_features = build_meta_features(df, strat["signal"])

        if df_features.empty:
            print(f"    Žádné features")
            continue

        model, stats, scaler = train_meta_model(
            df_features, labels_df, strat["name"], n_raw
        )

        if model is None:
            continue

        all_stats.append(stats)
        model_path = os.path.join(OUTPUT_DIR, f"{ticker}_{tf}_meta_model.pkl")
        with open(model_path, "wb") as f:
            pickle.dump({
                "model":        model,
                "scaler":       scaler,
                "feature_cols": stats["feature_cols"],
            }, f)
        print(f"    💾 Model uložen: {model_path}")

    if all_stats:
        print(f"\n{'='*60}")
        print("SOUHRN META-LABELING")
        print(f"{'='*60}")
        print(f"  {'Strategie':<28} {'Baseline':>9} {'CV Prec':>9} {'Zlepšení':>9}")
        print(f"  {'─'*60}")
        for s in sorted(all_stats, key=lambda x: x["improvement"], reverse=True):
            print(f"  {s['strategy']:<28} {s['baseline_wr']:>8.1f}% "
                  f"{s['cv_precision']:>8.1f}% {s['improvement']:>+8.1f}%  {s['verdict']}")

        pd.DataFrame(all_stats).to_csv(
            os.path.join(OUTPUT_DIR, "meta_stats.csv"), index=False
        )

        useful = [s for s in all_stats if "UŽITEČNÝ" in s["verdict"]]
        print(f"\n  ✅ Modely uloženy: {OUTPUT_DIR}/")

        if useful:
            print(f"\n  🎯 POUŽITELNÉ modely ({len(useful)}):")
            for s in useful:
                print(f"     • {s['strategy']}")
                print(f"       CV {s['cv_precision']:.1f}% vs baseline {s['baseline_wr']:.1f}%"
                      f" → filtruje {s['improvement']:.1f}% špatných vstupů")
            print(f"\n  💡 Další krok: python mt5_executor.py (integruj predict_meta)")
        else:
            print(f"\n  ⚠️  Žádný model není zatím UŽITEČNÝ.")
            print(f"     Nejlepší kandidát: NVDA MACD Bear M15 (N=53, nejvíce dat)")
            print(f"     Po doplnění H1 dat spusť znovu.")
    else:
        print("\n  ❌ Žádné modely. Zkontroluj triple_barrier.py výsledky.")


if __name__ == "__main__":
    main()