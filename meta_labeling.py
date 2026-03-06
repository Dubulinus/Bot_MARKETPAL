"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - META-LABELING                           ║
║         Marcos Lopez de Prado — AFML Chapter 3              ║
╚══════════════════════════════════════════════════════════════╝

CO JE META-LABELING:

    Bez meta-labeling:
        Signal → Trade
        Win rate: 65%

    S meta-labeling:
        Signal → Meta Model → Trade (jen když ML říká ANO)
        Win rate: 75%+ (méně obchodů, vyšší kvalita)

    Meta model NEOPRAVUJE primární signál.
    Meta model FILTRUJE kdy primárnímu signálu věřit.

    Features pro meta model:
        - Tržní podmínky (trend, volatilita, volume)
        - Technické indikátory v době signálu
        - Session, hodina dne
        - Vzdálenost od supportů/resistancí
        - ATR ratio (aktuální vs průměrná volatilita)

VÝSTUP:
    data/11_META_LABELS/
        {TICKER}_{TF}_meta_model.pkl    → trénovaný model
        {TICKER}_{TF}_meta_stats.csv    → výsledky

JAK SPUSTIT:
    python meta_labeling.py
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
    from sklearn.metrics import classification_report, precision_score
    import pickle
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    print("⚠️  scikit-learn není nainstalován. Spusť: pip install scikit-learn")

# ─── CONFIG ────────────────────────────────────────────────────

GOLD_DIR   = "data/04_GOLD_FEATURES"
TB_DIR     = "data/07_TRIPLE_BARRIER"
OUTPUT_DIR = "data/11_META_LABELS"

# Strategie s existujícími Triple Barrier labely
STRATEGIES = [
    {
        "name":      "AMZN RSI OB Exit M5",
        "ticker":    "AMZN",
        "tf":        "M5",
        "category":  "stocks",
        "signal":    "signal_rsi_overbought_exit",
        "direction": "short",
        "pt":        1.5,
        "sl":        1.0,
        "t":         6,
    },
    {
        "name":      "AAPL BB Breakdown M15",
        "ticker":    "AAPL",
        "tf":        "M15",
        "category":  "stocks",
        "signal":    "signal_bb_breakout_down",
        "direction": "short",
        "pt":        2.0,
        "sl":        1.0,
        "t":         24,
    },
    {
        "name":      "USDCHF BB Breakdown M15",
        "ticker":    "USDCHF",
        "tf":        "M15",
        "category":  "forex",
        "signal":    "signal_bb_breakout_down",
        "direction": "short",
        "pt":        3.0,
        "sl":        1.0,
        "t":         24,
    },
]

MIN_SAMPLES = 30  # minimum vzorků pro trénink

# ─── FEATURE ENGINEERING PRO META MODEL ────────────────────────

def build_meta_features(df, signal_col):
    """
    Vytvoří features pro meta model na základě tržních podmínek
    v době každého signálu.

    Features:
        Trend:       ema_20 vs ema_50, price vs vwap
        Volatilita:  atr_ratio (aktuální/průměrná), bb_width
        Momentum:    rsi, macd
        Volume:      volume_ratio
        Čas:         hodina, den týdne
        Kontext:     vzdálenost od BB, price vs sma
    """
    features = []
    signal_indices = np.where(df[signal_col].values.astype(bool))[0]

    for idx in signal_indices:
        row  = df.iloc[idx]
        feat = {}

        # ── Trend features ──
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

        # ── Volatilita ──
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

        # ── Momentum ──
        if "rsi" in df.columns:
            feat["rsi"] = row.get("rsi", 50)

        if "macd" in df.columns and "macd_signal" in df.columns:
            macd   = row.get("macd",        np.nan)
            msig   = row.get("macd_signal", np.nan)
            if not any(pd.isna([macd, msig])):
                feat["macd_hist"] = macd - msig

        # ── Volume ──
        if "volume" in df.columns and idx >= 20:
            vol      = row.get("volume", np.nan)
            vol_mean = df["volume"].iloc[max(0, idx-20):idx].mean()
            if not pd.isna(vol) and vol_mean > 0:
                feat["volume_ratio"] = vol / vol_mean

        # ── Čas ──
        if "timestamp" in df.columns:
            ts = pd.to_datetime(row.get("timestamp", None), errors="coerce")
            if ts is not None and not pd.isna(ts):
                feat["hour"]       = ts.hour
                feat["day_of_week"] = ts.dayofweek

        # ── Trend síla ──
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


# ─── META MODEL TRÉNINK ────────────────────────────────────────

def train_meta_model(df_features, labels_df, strategy_name):
    """
    Trénuje Random Forest meta model.

    Input:
        df_features: features v době každého signálu
        labels_df:   Triple Barrier labely (+1/-1/0)
    """
    if not SKLEARN_OK:
        return None, None

    # Spoj features s labely podle entry_idx
    df = df_features.copy()
    df = df.merge(
        labels_df[["entry_idx", "label"]],
        on="entry_idx",
        how="inner"
    )

    # Filtruj label=0 (čas vypršel, nejednoznačné)
    df = df[df["label"] != 0]

    if len(df) < MIN_SAMPLES:
        print(f"    Nedostatek vzorků: {len(df)} < {MIN_SAMPLES}")
        return None, None

    # Binární label: 1 = win (TP hit), 0 = loss (SL hit)
    df["target"] = (df["label"] == 1).astype(int)

    # Feature sloupce (bez idx a labelu)
    feature_cols = [c for c in df.columns
                    if c not in ["entry_idx", "label", "target"]
                    and not df[c].isna().all()]

    df_clean = df[feature_cols + ["target"]].dropna()

    if len(df_clean) < MIN_SAMPLES:
        print(f"    Nedostatek čistých vzorků: {len(df_clean)}")
        return None, None

    X = df_clean[feature_cols].values
    y = df_clean["target"].values

    baseline_wr = y.mean() * 100

    # TimeSeriesSplit — správná cross-validace pro časové řady
    # (žádný data leakage — budoucnost nezná minulost)
    tscv   = TimeSeriesSplit(n_splits=5)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Random Forest — robustní, nepotřebuje tuning
    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=4,          # mělký strom = méně overfittingu
        min_samples_leaf=5,   # minimálně 5 vzorků v listu
        class_weight="balanced",
        random_state=42,
    )

    # Cross-validace na časových řezech
    cv_scores    = cross_val_score(model, X_scaled, y, cv=tscv, scoring="precision")
    cv_precision = cv_scores.mean() * 100
    cv_std       = cv_scores.std()   * 100

    # Trénuj finální model na celých datech
    model.fit(X_scaled, y)
    train_precision = precision_score(y, model.predict(X_scaled)) * 100

    # Feature importance
    importances = pd.Series(model.feature_importances_, index=feature_cols)
    top_features = importances.nlargest(5)

    print(f"    Vzorků:          {len(df_clean)} ({int(y.sum())} wins, {int((1-y).sum())} losses)")
    print(f"    Baseline WR:     {baseline_wr:.1f}%")
    print(f"    CV Precision:    {cv_precision:.1f}% ± {cv_std:.1f}%")
    print(f"    Train Precision: {train_precision:.1f}%")
    print(f"    Zlepšení:        +{cv_precision - baseline_wr:.1f}%")
    print(f"    Top features:")
    for feat, imp in top_features.items():
        print(f"      {feat:<20} {imp:.3f}")

    improvement = cv_precision - baseline_wr

    if improvement >= 5:
        verdict = "✅ UŽITEČNÝ — filtruje špatné vstupy"
    elif improvement >= 2:
        verdict = "⚠️  MARGINÁLNÍ — malé zlepšení"
    else:
        verdict = "❌ NEPOMÁHÁ — nepoužívat"

    print(f"    Verdict:         {verdict}")

    return model, {
        "strategy":        strategy_name,
        "n_samples":       len(df_clean),
        "baseline_wr":     round(baseline_wr, 1),
        "cv_precision":    round(cv_precision, 1),
        "cv_std":          round(cv_std, 1),
        "improvement":     round(improvement, 1),
        "top_features":    top_features.to_dict(),
        "verdict":         verdict,
        "feature_cols":    feature_cols,
    }, scaler


def predict_meta(model, scaler, features_row, feature_cols):
    """
    Predikuj pro jeden obchod — zavolej z mt5_executor.
    Vrátí (should_trade, confidence).
    """
    if model is None:
        return True, 1.0

    try:
        row = {f: features_row.get(f, 0) for f in feature_cols}
        X   = np.array([[row[f] for f in feature_cols]])
        X_s = scaler.transform(X)

        proba      = model.predict_proba(X_s)[0]
        confidence = proba[1]  # pravděpodobnost výhry

        # Obchoduj jen pokud confidence > 60%
        should_trade = confidence >= 0.60

        return should_trade, round(confidence, 3)
    except Exception:
        return True, 1.0  # fallback — obchoduj

# ─── MAIN ──────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL META-LABELING            ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝\n")

    if not SKLEARN_OK:
        print("  Nainstaluj: pip install scikit-learn")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_stats = []

    for strat in STRATEGIES:
        ticker = strat["ticker"]
        tf     = strat["tf"]
        pt, sl, t = strat["pt"], strat["sl"], strat["t"]

        print(f"\n  {'─'*50}")
        print(f"  {strat['name']}")
        print(f"  {'─'*50}")

        # Načti Gold data
        gold_path = Path(GOLD_DIR) / tf / strat["category"] / f"{ticker}.parquet"
        if not gold_path.exists():
            print(f"    Gold data nenalezena: {gold_path}")
            continue

        df = pd.read_parquet(gold_path).reset_index(drop=True)

        if strat["signal"] not in df.columns:
            print(f"    Signal {strat['signal']} nenalezen v datech")
            continue

        # Načti Triple Barrier labely
        tb_path = Path(TB_DIR) / tf / \
            f"{ticker}_{strat['signal']}_pt{pt}_sl{sl}_t{t}.parquet"

        if not tb_path.exists():
            print(f"    Triple Barrier labely nenalezeny: {tb_path}")
            print(f"    Spusť nejdřív: python triple_barrier.py")
            continue

        labels_df = pd.read_parquet(tb_path)

        # Build features
        df_features = build_meta_features(df, strat["signal"])
        if df_features.empty:
            print(f"    Žádné features")
            continue

        # Trénuj model
        result = train_meta_model(df_features, labels_df, strat["name"])
        if result[0] is None:
            continue

        model, stats, scaler = result
        all_stats.append(stats)

        # Ulož model
        model_path = os.path.join(OUTPUT_DIR, f"{ticker}_{tf}_meta_model.pkl")
        with open(model_path, "wb") as f:
            pickle.dump({"model": model, "scaler": scaler,
                         "feature_cols": stats["feature_cols"]}, f)

    # Souhrn
    if all_stats:
        print(f"\n{'='*55}")
        print("SOUHRN META-LABELING")
        print(f"{'='*55}")
        print(f"  {'Strategie':<30} {'Baseline':>9} {'CV Prec':>9} {'Zlepšení':>9}")
        print(f"  {'─'*55}")
        for s in sorted(all_stats, key=lambda x: x["improvement"], reverse=True):
            print(f"  {s['strategy']:<30} {s['baseline_wr']:>8.1f}% "
                  f"{s['cv_precision']:>8.1f}% {s['improvement']:>+8.1f}%")

        pd.DataFrame(all_stats).to_csv(
            os.path.join(OUTPUT_DIR, "meta_stats.csv"), index=False
        )
        print(f"\n  Modely uloženy: {OUTPUT_DIR}/")
        print(f"  💡 Další krok: integruj do mt5_executor.py")


if __name__ == "__main__":
    main()