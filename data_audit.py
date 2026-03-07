"""
MARKETPAL - DATA AUDIT
Zjistí: kolik dat máš, odkud pochází, co chybí
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

GOLD_DIR = "data/04_GOLD_FEATURES"

def audit():
    print("╔══════════════════════════════════════════╗")
    print("║         MARKETPAL DATA AUDIT            ║")
    print("╚══════════════════════════════════════════╝\n")

    results = []

    for parquet in sorted(Path(GOLD_DIR).rglob("*.parquet")):
        try:
            df = pd.read_parquet(parquet)

            # Najdi timestamp
            ts = None
            if isinstance(df.index, pd.DatetimeIndex):
                ts = df.index
            elif "timestamp" in df.columns:
                ts = pd.to_datetime(df["timestamp"], errors="coerce")
            else:
                for col in df.columns:
                    if "time" in col.lower() or "date" in col.lower():
                        ts = pd.to_datetime(df[col], errors="coerce")
                        break

            parts = parquet.parts
            tf       = parts[-3] if len(parts) >= 3 else "?"
            category = parts[-2] if len(parts) >= 2 else "?"
            ticker   = parquet.stem

            if ts is not None and len(ts) > 0:
                start = ts.min()
                end   = ts.max()
                days  = (end - start).days
                years = days / 365
            else:
                start = end = None
                days  = 0
                years = 0

            signal_cols = [c for c in df.columns if c.startswith("signal_")]

            results.append({
                "ticker":   ticker,
                "tf":       tf,
                "rows":     len(df),
                "days":     days,
                "years":    round(years, 1),
                "start":    start.strftime("%Y-%m-%d") if start else "?",
                "end":      end.strftime("%Y-%m-%d")   if end   else "?",
                "signals":  len(signal_cols),
                "cols":     len(df.columns),
            })

        except Exception as e:
            print(f"  ⚠️  Chyba při čtení {parquet}: {e}")

    if not results:
        print("❌ Žádná data v data/04_GOLD_FEATURES/")
        print("   Zkontroluj cestu nebo spusť pipeline od začátku.")
        return

    df_r = pd.DataFrame(results)

    # ── Přehled po timeframe ────────────────────────────────────
    print(f"{'='*65}")
    print("PŘEHLED DAT")
    print(f"{'='*65}")
    print(f"  {'Ticker':<8} {'TF':<5} {'Řádků':>8} {'Roků':>6} {'Od':<12} {'Do':<12} {'Signálů':>8}")
    print(f"  {'-'*65}")

    for tf in ["M5", "M15", "H1"]:
        sub = df_r[df_r["tf"] == tf].sort_values("ticker")
        if sub.empty:
            continue
        print(f"\n  ── {tf} ──")
        for _, r in sub.iterrows():
            flag = ""
            if r["years"] < 1:   flag = " ⚠️ MÁLO"
            elif r["years"] < 2: flag = " ℹ️ OK"
            else:                 flag = " ✅ DOST"
            print(f"  {r['ticker']:<8} {r['tf']:<5} {r['rows']:>8,} "
                  f"{r['years']:>5.1f}r  {r['start']:<12} {r['end']:<12} "
                  f"{r['signals']:>4}{flag}")

    # ── Souhrn ──────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("SOUHRN")
    print(f"{'='*65}")
    print(f"  Celkem souborů:    {len(results)}")
    print(f"  Průměr roků:       {df_r['years'].mean():.1f}")
    print(f"  Min roků:          {df_r['years'].min():.1f}  ({df_r.loc[df_r['years'].idxmin(), 'ticker']} {df_r.loc[df_r['years'].idxmin(), 'tf']})")
    print(f"  Max roků:          {df_r['years'].max():.1f}  ({df_r.loc[df_r['years'].idxmax(), 'ticker']} {df_r.loc[df_r['years'].idxmax(), 'tf']})")

    problem = df_r[df_r["years"] < 1]
    if not problem.empty:
        print(f"\n  ⚠️  Kriticky málo dat (<1 rok):")
        for _, r in problem.iterrows():
            print(f"     {r['ticker']} {r['tf']} — pouze {r['years']:.1f} let ({r['rows']:,} řádků)")

    # ── Co stahovat ─────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("CO PŘIDAT (doporučení)")
    print(f"{'='*65}")

    h1_data = df_r[df_r["tf"] == "H1"]
    if h1_data.empty or h1_data["years"].max() < 3:
        print(f"\n  🔴 PRIORITA 1: Více H1 dat")
        print(f"     Aktuálně: {h1_data['years'].max():.1f} let")
        print(f"     Potřeba:  3+ roky (= ~26,000 H1 svíček na ticker)")
        print(f"     Proč: STRONG signály jsou na H1 — meta model potřebuje 100+ vzorků")

    m15_data = df_r[df_r["tf"] == "M15"]
    if m15_data.empty or m15_data["years"].max() < 2:
        print(f"\n  🟡 PRIORITA 2: Více M15 dat")
        print(f"     Aktuálně: {m15_data['years'].max():.1f} let")
        print(f"     Potřeba:  2+ roky")

    print(f"\n  API doporučení pro víc historických dat:")
    print(f"     Yahoo Finance:  max ~5 let M1, 10+ let D1 (zdarma)")
    print(f"     Polygon.io:     2 roky M1 zdarma, neomezeno placené")
    print(f"     EODHD:          15+ let historická data, vč. fundamentals")
    print(f"     Alpaca:         5 let M1 zdarma (potřeba účet)")

    print(f"\n  Spusť znovu po stažení dat:")
    print(f"     python data_audit.py → ověř roky")
    print(f"     python triple_barrier.py → přegeneruj labely")
    print(f"     python meta_labeling.py → natrénuj modely")


if __name__ == "__main__":
    audit()