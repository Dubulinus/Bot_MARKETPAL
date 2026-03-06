"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - BEST DAY RULE TRACKER                   ║
║         FTMO 2026 pravidlo — automatický monitoring         ║
╚══════════════════════════════════════════════════════════════╝

CO JE BEST DAY RULE (FTMO 2026):
    Žádný jeden obchodní den nesmí tvořit více než 30%
    celkového profitu z challenge.

    Příklad:
        Challenge profit target: +$1000 (10% z $10k)
        Max profit z jednoho dne: $1000 × 30% = $300

        Pokud v pondělí vyděláš $350 → PORUŠENÍ → challenge fail

    POZOR: Pravidlo platí pro PROFIT, ne pro loss.
           Ztráta v jeden den nemá limit (jen daily DD limit).

JAK SPUSTIT:
    Zobraz aktuální stav:
        python best_day_rule.py

    Zkontroluj konkrétní den:
        python best_day_rule.py --date 2026-03-06

    Automatická kontrola (volá mt5_executor před každým obchodem):
        from best_day_rule import can_trade_today
        allowed, reason = can_trade_today()
"""

import os
import json
import argparse
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()

import requests

# ─── CONFIG ────────────────────────────────────────────────────

TRADES_LOG   = "data/08_PAPER_TRADES/mt5_trades.json"
BDR_LOG      = "data/10_JOURNAL/best_day_rule.json"

ACCOUNT_SIZE    = 10000
PROFIT_TARGET   = 1000       # 10% z account — FTMO challenge target
MAX_DAY_PCT     = 30         # max % celkového profitu z jednoho dne
WARNING_PCT     = 20         # varování při 20%

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── TELEGRAM ──────────────────────────────────────────────────

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception:
        pass

# ─── DATA ──────────────────────────────────────────────────────

def load_trades():
    if not os.path.exists(TRADES_LOG):
        return []
    try:
        with open(TRADES_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_bdr_log():
    if not os.path.exists(BDR_LOG):
        return {}
    try:
        with open(BDR_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_bdr_log(data):
    os.makedirs(os.path.dirname(BDR_LOG), exist_ok=True)
    with open(BDR_LOG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ─── VÝPOČTY ───────────────────────────────────────────────────

def compute_daily_pnl(trades):
    """Seskupí P&L podle dne."""
    daily = defaultdict(float)
    for t in trades:
        if t.get("status") != "closed":
            continue
        exit_time = t.get("exit_time", "")
        if not exit_time:
            continue
        day = exit_time[:10]
        daily[day] += t.get("pnl", 0) or 0
    return dict(daily)


def compute_total_profit(trades):
    """Celkový profit (jen kladné dny počítají do BDR)."""
    closed = [t for t in trades if t.get("status") == "closed"]
    return sum(t.get("pnl", 0) or 0 for t in closed)


def get_best_day_limit(total_profit):
    """
    Vypočítá maximální povolený profit pro jeden den.
    Používá PROFIT_TARGET jako základ, ne aktuální profit.
    """
    return PROFIT_TARGET * MAX_DAY_PCT / 100


def check_best_day_rule(trades, target_date=None):
    """
    Zkontroluje Best Day Rule pro daný den.

    Returns:
        dict s výsledkem kontroly
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    daily_pnl    = compute_daily_pnl(trades)
    total_profit = compute_total_profit(trades)
    day_pnl      = daily_pnl.get(target_date, 0)
    max_day      = get_best_day_limit(total_profit)
    warning_day  = PROFIT_TARGET * WARNING_PCT / 100

    pct_of_target = day_pnl / PROFIT_TARGET * 100 if PROFIT_TARGET > 0 else 0

    if day_pnl >= max_day:
        status = "VIOLATION"
        icon   = "🚨"
    elif day_pnl >= warning_day:
        status = "WARNING"
        icon   = "⚠️"
    else:
        status = "OK"
        icon   = "✅"

    remaining = max(0, max_day - day_pnl)

    return {
        "date":           target_date,
        "day_pnl":        round(day_pnl, 2),
        "max_day_profit": round(max_day, 2),
        "remaining":      round(remaining, 2),
        "pct_of_target":  round(pct_of_target, 1),
        "status":         status,
        "icon":           icon,
        "total_profit":   round(total_profit, 2),
    }


def can_trade_today():
    """
    Hlavní funkce pro mt5_executor.py.
    Vrátí (allowed, reason).
    """
    trades = load_trades()
    result = check_best_day_rule(trades)

    if result["status"] == "VIOLATION":
        reason = (
            f"Best Day Rule: dnes ${result['day_pnl']:.2f} "
            f"(max ${result['max_day_profit']:.2f} = {MAX_DAY_PCT}% targetu)"
        )
        return False, reason

    if result["status"] == "WARNING":
        reason = (
            f"Best Day Rule WARNING: ${result['day_pnl']:.2f} / "
            f"${result['max_day_profit']:.2f} — zbývá ${result['remaining']:.2f}"
        )
        # Varování ale neblokujeme
        send_telegram(f"⚠️ <b>Best Day Rule Warning</b>\n{reason}")
        return True, reason

    return True, f"OK — dnes ${result['day_pnl']:+.2f} / max ${result['max_day_profit']:.2f}"

# ─── DISPLAY ───────────────────────────────────────────────────

def print_status(trades):
    daily_pnl    = compute_daily_pnl(trades)
    total_profit = compute_total_profit(trades)
    max_day      = get_best_day_limit(total_profit)
    today        = datetime.now().strftime("%Y-%m-%d")

    print(f"\n  {'='*55}")
    print(f"  BEST DAY RULE TRACKER")
    print(f"  {'='*55}")
    print(f"  Challenge target:    ${PROFIT_TARGET:,.2f} (10%)")
    print(f"  Max profit/den:      ${max_day:.2f} ({MAX_DAY_PCT}% targetu)")
    print(f"  Celkový profit:      ${total_profit:+.2f}")
    print(f"  {'─'*55}")

    # Progress bar k targetu
    progress = min(total_profit / PROFIT_TARGET * 100, 100) if PROFIT_TARGET > 0 else 0
    bar_len  = 30
    filled   = int(bar_len * progress / 100)
    bar      = "█" * filled + "░" * (bar_len - filled)
    print(f"  Challenge progress:  [{bar}] {progress:.1f}%")
    print(f"  {'─'*55}")

    # Posledních 10 dní
    if daily_pnl:
        print(f"\n  DENNÍ P&L (posledních 10 dní):")
        print(f"  {'Datum':<12} {'P&L':>10} {'% targetu':>10} {'Status'}")
        print(f"  {'─'*45}")

        for day in sorted(daily_pnl.keys())[-10:]:
            pnl     = daily_pnl[day]
            pct     = pnl / PROFIT_TARGET * 100
            result  = check_best_day_rule(trades, day)
            icon    = result["icon"]
            marker  = " ← DNES" if day == today else ""
            print(f"  {day:<12} ${pnl:>8.2f}  {pct:>8.1f}%   {icon}{marker}")

    # Dnešní status
    today_result = check_best_day_rule(trades, today)
    print(f"\n  DNES ({today}):")
    print(f"  Profit dnes:    ${today_result['day_pnl']:+.2f}")
    print(f"  Limit:          ${today_result['max_day_profit']:.2f}")
    print(f"  Zbývá:          ${today_result['remaining']:.2f}")
    print(f"  Status:         {today_result['icon']} {today_result['status']}")

    # Best day — nejrizikovější den
    if daily_pnl:
        best_day     = max(daily_pnl.items(), key=lambda x: x[1])
        best_day_pct = best_day[1] / PROFIT_TARGET * 100
        print(f"\n  Nejlepší den:   {best_day[0]} ${best_day[1]:+.2f} "
              f"({best_day_pct:.1f}% targetu)")
        if best_day_pct >= MAX_DAY_PCT:
            print(f"  ⚠️  POZOR: Tento den by porušil Best Day Rule!")

    print(f"\n  💡 Pravidlo: žádný den nesmí tvořit >{MAX_DAY_PCT}% celkového targetu")
    print(f"  💡 Při ${max_day:.2f}+ zisku v jednom dni → přestaň obchodovat")

# ─── MAIN ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Best Day Rule Tracker")
    parser.add_argument("--date", type=str, help="Zkontroluj konkrétní datum (YYYY-MM-DD)")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL BEST DAY RULE TRACKER    ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                  ║")
    print("╚══════════════════════════════════════════╝")

    os.makedirs(os.path.dirname(BDR_LOG), exist_ok=True)
    trades = load_trades()

    if args.date:
        result = check_best_day_rule(trades, args.date)
        print(f"\n  {result['icon']} {args.date}: ${result['day_pnl']:+.2f} "
              f"({result['pct_of_target']}% targetu) — {result['status']}")
        return

    print_status(trades)

    # Zkontroluj dnešní stav
    allowed, reason = can_trade_today()
    print(f"\n  {'✅ OBCHODOVAT POVOLEN' if allowed else '🚨 STOP — BEST DAY RULE'}")
    print(f"  {reason}")


if __name__ == "__main__":
    main()