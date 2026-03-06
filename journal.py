"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - OBCHODNÍ DENÍK                          ║
║         Automatický záznam + týdenní Telegram report        ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    1. Načte všechny uzavřené obchody z mt5_trades.json
    2. Přidá poznámky, analýzu, statistiky
    3. Každý pátek v 18:00 pošle týdenní report do Telegramu
    4. Uloží HTML report pro dashboard

MARCOS: "Without a trading journal you are flying blind.
         The journal is your feedback loop."

JAK SPUSTIT:
    Zobraz deník:
        python journal.py

    Týdenní report hned (test):
        python journal.py --report

    Přidej manuální poznámku k obchodu:
        python journal.py --note "AMZN short fungoval, trh byl v bear režimu"
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import requests
from dotenv import load_dotenv
load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────

TRADES_LOG   = "data/08_PAPER_TRADES/mt5_trades.json"
JOURNAL_FILE = "data/10_JOURNAL/journal.json"
REPORT_DIR   = "data/10_JOURNAL"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ACCOUNT_SIZE = 10000

# ─── TELEGRAM ──────────────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️  Telegram: token nenastaven")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
        print("  ✅ Telegram report odeslán")
    except Exception as e:
        print(f"  ⚠️  Telegram chyba: {e}")

# ─── DATA ──────────────────────────────────────────────────────

def load_trades():
    if not os.path.exists(TRADES_LOG):
        return []
    try:
        with open(TRADES_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_journal():
    if not os.path.exists(JOURNAL_FILE):
        return {"notes": [], "weekly_reports": []}
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"notes": [], "weekly_reports": []}


def save_journal(journal):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(journal, f, indent=2, ensure_ascii=False)

# ─── STATISTIKY ────────────────────────────────────────────────

def compute_stats(trades):
    """Kompletní statistiky pro seznam obchodů."""
    closed = [t for t in trades if t.get("status") == "closed"]
    if not closed:
        return None

    n      = len(closed)
    wins   = [t for t in closed if t.get("exit_reason") == "tp"]
    losses = [t for t in closed if t.get("exit_reason") == "sl"]
    pnls   = [t.get("pnl", 0) or 0 for t in closed]

    win_rate     = len(wins) / n * 100
    total_pnl    = sum(pnls)
    avg_win      = sum(t.get("pnl", 0) for t in wins)   / len(wins)   if wins   else 0
    avg_loss     = sum(t.get("pnl", 0) for t in losses) / len(losses) if losses else 0
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss   = abs(sum(p for p in pnls if p < 0))
    pf           = gross_profit / gross_loss if gross_loss > 0 else 99.0

    # Equity curve + max drawdown
    equity = ACCOUNT_SIZE
    peak   = ACCOUNT_SIZE
    max_dd = 0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = (equity - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd

    # Streak
    best_streak  = 0
    worst_streak = 0
    cur_win      = 0
    cur_loss     = 0
    for t in closed:
        if t.get("exit_reason") == "tp":
            cur_win  += 1
            cur_loss  = 0
        else:
            cur_loss += 1
            cur_win   = 0
        best_streak  = max(best_streak,  cur_win)
        worst_streak = max(worst_streak, cur_loss)

    # Per strategie
    by_strategy = defaultdict(list)
    for t in closed:
        by_strategy[t.get("name", "Unknown")].append(t)

    strategy_stats = {}
    for name, strats in by_strategy.items():
        s_pnls = [t.get("pnl", 0) or 0 for t in strats]
        s_wins = sum(1 for t in strats if t.get("exit_reason") == "tp")
        strategy_stats[name] = {
            "n":        len(strats),
            "win_rate": round(s_wins / len(strats) * 100, 1),
            "total_pnl": round(sum(s_pnls), 2),
            "avg_pnl":  round(sum(s_pnls) / len(strats), 2),
        }

    return {
        "n_trades":      n,
        "n_wins":        len(wins),
        "n_losses":      len(losses),
        "win_rate":      round(win_rate, 1),
        "total_pnl":     round(total_pnl, 2),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "profit_factor": round(pf, 2),
        "max_dd":        round(max_dd, 2),
        "final_equity":  round(ACCOUNT_SIZE + total_pnl, 2),
        "best_streak":   best_streak,
        "worst_streak":  worst_streak,
        "by_strategy":   strategy_stats,
    }


def compute_weekly_stats(trades, week_start, week_end):
    """Statistiky pro konkrétní týden."""
    weekly = [
        t for t in trades
        if t.get("status") == "closed"
        and t.get("exit_time", "")
        and week_start <= t["exit_time"][:10] <= week_end
    ]
    return compute_stats(weekly), weekly

# ─── DISPLAY ───────────────────────────────────────────────────

def print_stats(stats, title="STATISTIKY"):
    if not stats:
        print("  Žádné uzavřené obchody.")
        return

    print(f"\n  {'='*50}")
    print(f"  {title}")
    print(f"  {'='*50}")
    print(f"  Obchodů:        {stats['n_trades']} "
          f"(TP:{stats['n_wins']} SL:{stats['n_losses']})")
    print(f"  Win Rate:       {stats['win_rate']}%")
    print(f"  Total P&L:      ${stats['total_pnl']:+.2f}")
    print(f"  Avg Win/Loss:   ${stats['avg_win']:+.2f} / ${stats['avg_loss']:+.2f}")
    print(f"  Profit Factor:  {stats['profit_factor']}")
    print(f"  Max Drawdown:   {stats['max_dd']}%")
    print(f"  Final Equity:   ${stats['final_equity']:,.2f}")
    print(f"  Best Streak:    {stats['best_streak']} wins za sebou")
    print(f"  Worst Streak:   {stats['worst_streak']} losses za sebou")

    if stats["by_strategy"]:
        print(f"\n  PER STRATEGIE:")
        for name, s in sorted(stats["by_strategy"].items(),
                               key=lambda x: x[1]["total_pnl"], reverse=True):
            print(f"  {name[:35]:<35} "
                  f"n={s['n']:<4} WR={s['win_rate']}% "
                  f"P&L=${s['total_pnl']:+.2f}")

# ─── TÝDENNÍ REPORT ────────────────────────────────────────────

def generate_weekly_report(trades, journal):
    """
    Vygeneruje týdenní report a pošle na Telegram.
    Spouští se každý pátek automaticky ze scheduleru.
    """
    now        = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    week_end   = now.strftime("%Y-%m-%d")

    stats, weekly_trades = compute_weekly_stats(trades, week_start, week_end)
    all_stats            = compute_stats(trades)

    print(f"\n  TÝDENNÍ REPORT: {week_start} → {week_end}")

    if not stats:
        msg = (
            f"📊 <b>MarketPal — Týdenní report</b>\n"
            f"📅 {week_start} → {week_end}\n\n"
            f"Tento týden žádné uzavřené obchody.\n"
            f"Bot běží, čeká na signály."
        )
        send_telegram(msg)
        return

    print_stats(stats, f"TENTO TÝDEN ({week_start} → {week_end})")

    # Nejlepší a nejhorší obchod týdne
    if weekly_trades:
        best  = max(weekly_trades, key=lambda x: x.get("pnl", 0) or 0)
        worst = min(weekly_trades, key=lambda x: x.get("pnl", 0) or 0)
    else:
        best = worst = None

    # Telegram zpráva
    strategy_lines = ""
    if stats["by_strategy"]:
        for name, s in sorted(stats["by_strategy"].items(),
                               key=lambda x: x[1]["total_pnl"], reverse=True):
            icon = "✅" if s["total_pnl"] >= 0 else "❌"
            strategy_lines += f"{icon} {name[:25]}: ${s['total_pnl']:+.2f} ({s['win_rate']}% WR)\n"

    ftmo_progress = ""
    if all_stats:
        monthly_return = (all_stats["final_equity"] - ACCOUNT_SIZE) / ACCOUNT_SIZE * 100
        ftmo_progress  = f"\n📈 Celkový return: {monthly_return:+.2f}%"
        if monthly_return >= 8:
            ftmo_progress += " 🎯 FTMO target dosažen!"
        elif monthly_return >= 5:
            ftmo_progress += " 🔥 Na dobré cestě"

    best_line  = f"\n🏆 Nejlepší: {best['name'][:20]} ${best.get('pnl',0):+.2f}" if best else ""
    worst_line = f"\n💀 Nejhorší: {worst['name'][:20]} ${worst.get('pnl',0):+.2f}" if worst else ""

    msg = (
        f"📊 <b>MarketPal — Týdenní report</b>\n"
        f"📅 {week_start} → {week_end}\n\n"
        f"<b>Tento týden:</b>\n"
        f"Obchodů: {stats['n_trades']} | WR: {stats['win_rate']}%\n"
        f"P&L: <b>${stats['total_pnl']:+.2f}</b> | PF: {stats['profit_factor']}\n"
        f"Max DD: {stats['max_dd']}%\n\n"
        f"<b>Per strategie:</b>\n{strategy_lines}"
        f"{best_line}{worst_line}"
        f"{ftmo_progress}\n\n"
        f"🛡️ FTMO DD limit zbývá: ${450 + min(0, all_stats['total_pnl'] if all_stats else 0):.2f}"
    )

    send_telegram(msg)

    # Ulož report do journal
    report_entry = {
        "timestamp":  now.isoformat(),
        "week_start": week_start,
        "week_end":   week_end,
        "stats":      stats,
    }
    journal["weekly_reports"].append(report_entry)
    journal["weekly_reports"] = journal["weekly_reports"][-52:]  # 1 rok
    save_journal(journal)

    print(f"\n  Report uložen do {JOURNAL_FILE}")

# ─── PŘIDAT POZNÁMKU ───────────────────────────────────────────

def add_note(note_text, trades):
    """Přidá manuální poznámku do deníku s časovým razítkem."""
    journal = load_journal()

    # Najdi poslední uzavřený obchod
    closed = [t for t in trades if t.get("status") == "closed"]
    last_trade = closed[-1] if closed else None

    entry = {
        "timestamp":   datetime.now().isoformat(),
        "note":        note_text,
        "last_trade":  last_trade.get("name", "") if last_trade else "",
        "trade_pnl":   last_trade.get("pnl", 0)  if last_trade else 0,
    }

    journal["notes"].append(entry)
    save_journal(journal)
    print(f"  ✅ Poznámka přidána: {note_text[:60]}")

# ─── MAIN ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MarketPal Obchodní Deník")
    parser.add_argument("--report", action="store_true", help="Pošli týdenní report")
    parser.add_argument("--note",   type=str,            help="Přidej poznámku")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL OBCHODNÍ DENÍK           ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝")

    os.makedirs(REPORT_DIR, exist_ok=True)
    trades  = load_trades()
    journal = load_journal()

    if args.note:
        add_note(args.note, trades)
        return

    if args.report:
        generate_weekly_report(trades, journal)
        return

    # Default: zobraz celkové statistiky
    all_stats = compute_stats(trades)
    print_stats(all_stats, "CELKOVÉ STATISTIKY")

    # Zobraz posledních 10 poznámek
    notes = journal.get("notes", [])
    if notes:
        print(f"\n  POZNÁMKY ({len(notes)} celkem):")
        for n in notes[-5:]:
            ts    = n["timestamp"][:16].replace("T", " ")
            trade = f" [{n['last_trade'][:20]}]" if n["last_trade"] else ""
            print(f"  {ts}{trade}: {n['note'][:60]}")

    print(f"\n  💡 Přidat poznámku: python journal.py --note \"tvoje poznamka\"")
    print(f"  💡 Týdenní report:  python journal.py --report")


if __name__ == "__main__":
    main()