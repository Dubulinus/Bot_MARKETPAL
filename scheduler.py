"""
╔══════════════════════════════════════════════════════════════╗
║         MARKETPAL - DAILY SCHEDULER                         ║
║         Phase 2 | Automatická pipeline každý den v 6:00    ║
╚══════════════════════════════════════════════════════════════╝

CO TENTO SKRIPT DĚLÁ:
    Každý den v 6:00 ráno automaticky spustí celou pipeline:
        1. tezba_polygon.py      → stáhni nová data (Bronze)
        2. rafinerie_polygon.py  → vyčisti data (Silver)
        3. feature_engineering.py → vypočti features (Gold)
        4. edge_matrix.py        → ověř že signály stále mají edge
        5. Telegram report       → pošli denní souhrn do telefonu

    Bot pak obchoduje z čerstvých Gold dat.
    Ty se probudíš s reportem v telefonu.

JAK SPUSTIT:
    Jednorázový test:
        python scheduler.py --now

    Spustit plánovač (běží dokud nezavřeš terminál):
        python scheduler.py

    Na Windows jako služba (spustí se po restartu automaticky):
        viz instrukce dole — Windows Task Scheduler

WINDOWS TASK SCHEDULER (nastav jednou, pak běží samo):
    1. Otevři Task Scheduler (hledej ve Start menu)
    2. Create Basic Task → název: MarketPal Daily Pipeline
    3. Trigger: Daily, 6:00 AM
    4. Action: Start a program
       Program: C:\\Bot_MARKETPAL\\.venv\\Scripts\\python.exe
       Arguments: C:\\Bot_MARKETPAL\\scheduler.py --now
    5. Finish
    → Každý den v 6:00 se pipeline spustí sama, i bez tebe
"""

import os
import sys
import time
import subprocess
import json
from datetime import datetime, time as dtime
import requests  # pip install requests

# ─── CONFIG ────────────────────────────────────────────────────

# Čas spuštění každý den (24h formát)
RUN_HOUR   = 6
RUN_MINUTE = 0

# Telegram notifikace
# Získej token: @BotFather na Telegramu → /newbot
# Získej chat_id: pošli botu zprávu, pak:
# https://api.telegram.org/bot<TOKEN>/getUpdates
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Skripty ke spuštění v pořadí
PIPELINE_SCRIPTS = [
    ("tezba_polygon.py",       "📥 Bronze — stahování dat"),
    ("rafinerie_polygon.py",   "🔬 Silver — čištění dat"),
    ("feature_engineering.py", "⚙️  Gold  — výpočet featur"),
    ("edge_matrix.py",         "🎯 Edge  — validace signálů"),
    ("mt5_executor.py",         "🚀 MT5   — první kontrola signálů"),
]

# Log soubor
LOG_FILE = "data/scheduler_log.json"

# ─── TELEGRAM ──────────────────────────────────────────────────

def send_telegram(message):
    """Pošli zprávu na Telegram. Tiše selže pokud není token."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ℹ️  Telegram: token nenastaven, přeskakuji")
        return

    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            print("  ✅ Telegram: zpráva odeslána")
        else:
            print(f"  ⚠️  Telegram error: {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️  Telegram selhání: {e}")


# ─── PIPELINE RUNNER ───────────────────────────────────────────

def run_script(script_name, description):
    """
    Spustí jeden skript a vrátí (success, duration, output_tail).
    Zachytí stdout pro log — neblokuje terminál.
    """
    print(f"\n  🔄 {description}")
    print(f"     Spouštím: {script_name}")

    start = datetime.now()

    # Timeout závisí na skriptu — Bronze stahuje 24 souborů s 13s pauzou = 300s+ minimum
    TIMEOUTS = {
        "tezba_polygon.py":       700,   # 24 souboru x 13s pauza = 312s + processing
        "rafinerie_polygon.py":    60,
        "feature_engineering.py":  60,
        "edge_matrix.py":         120,
        "mt5_executor.py":          30,
    }
    timeout = TIMEOUTS.get(script_name, 300)

    try:
        # FIX: nastav UTF-8 pro child proces — Windows cp1250 neumi emoji
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        # mt5_executor potřebuje --once argument
        cmd = [sys.executable, script_name]
        if script_name == "mt5_executor.py":
            cmd.append("--once")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            env=env
        )
        duration = (datetime.now() - start).total_seconds()
        success  = result.returncode == 0

        # Poslední 3 řádky výstupu (summary)
        output_lines = (result.stdout or "").strip().split("\n")
        tail = "\n".join(output_lines[-3:]) if output_lines else ""

        if success:
            print(f"  ✅ Hotovo za {duration:.1f}s")
        else:
            print(f"  ❌ Chyba! (kód {result.returncode})")
            if result.stderr:
                print(f"     {result.stderr[:200]}")

        return success, duration, tail

    except subprocess.TimeoutExpired:
        print(f"  ⏰ Timeout! Skript běžel déle než {timeout}s.")
        return False, timeout, "TIMEOUT"
    except FileNotFoundError:
        print(f"  ❌ Soubor nenalezen: {script_name}")
        return False, 0, "FILE_NOT_FOUND"


def run_full_pipeline():
    """
    Spustí celou pipeline a vrátí souhrn výsledků.
    Ukládá log do JSON pro historii.
    """
    print(f"\n{'═'*55}")
    print(f"🚀 SPOUŠTÍM DENNÍ PIPELINE")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═'*55}")

    pipeline_start = datetime.now()
    results        = []
    all_ok         = True

    for script, description in PIPELINE_SCRIPTS:
        success, duration, tail = run_script(script, description)
        results.append({
            "script":      script,
            "description": description,
            "success":     success,
            "duration_s":  round(duration, 1),
            "tail":        tail,
        })
        if not success:
            all_ok = False
            print(f"\n  ⚠️  Pipeline zastavena kvůli chybě v {script}")
            print(f"     Zkontroluj log: {LOG_FILE}")
            break

    total_duration = (datetime.now() - pipeline_start).total_seconds()

    # ── SOUHRN ─────────────────────────────────────────────────
    print(f"\n{'═'*55}")
    print(f"📋 VÝSLEDKY PIPELINE")
    print(f"{'═'*55}")
    for r in results:
        icon = "✅" if r["success"] else "❌"
        print(f"  {icon} {r['description']:<35} {r['duration_s']}s")
    print(f"\n  Celkový čas: {total_duration:.0f}s")
    print(f"  Status:      {'✅ VŠE OK' if all_ok else '❌ CHYBA'}")

    # ── LOG ────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    # Načti existující log
    log_history = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                log_history = json.load(f)
        except Exception:
            log_history = []

    # Přidej dnešní záznam
    log_entry = {
        "timestamp":       datetime.now().isoformat(),
        "success":         all_ok,
        "total_duration_s": round(total_duration, 1),
        "steps":           results,
    }
    log_history.append(log_entry)

    # Uchovej posledních 90 záznamů (3 měsíce)
    log_history = log_history[-90:]

    with open(LOG_FILE, "w", encoding='utf-8') as f:
        json.dump(log_history, f, indent=2, ensure_ascii=False)

    # ── TELEGRAM REPORT ────────────────────────────────────────
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if all_ok:
        steps_text = "\n".join(
            f"✅ {r['description']} ({r['duration_s']}s)"
            for r in results
        )
        message = (
            f"🤖 <b>MarketPal — Denní pipeline</b>\n"
            f"📅 {now_str}\n\n"
            f"{steps_text}\n\n"
            f"⏱ Celkem: {total_duration:.0f}s\n"
            f"✅ Data jsou čerstvá — bot může obchodovat"
        )
    else:
        failed = next((r for r in results if not r["success"]), None)
        message = (
            f"🚨 <b>MarketPal — Pipeline SELHALA</b>\n"
            f"📅 {now_str}\n\n"
            f"❌ Chyba v: {failed['script'] if failed else 'neznámý krok'}\n\n"
            f"🔍 Zkontroluj log: {LOG_FILE}"
        )

    send_telegram(message)

    # Po úspěšné pipeline spusť executor loop v background threadu
    if all_ok and "--now" not in sys.argv:
        import threading
        t = threading.Thread(target=run_executor_loop, daemon=True)
        t.start()
        print("\n  🚀 MT5 Executor loop spuštěn v pozadí")

    return all_ok




# ─── MT5 EXECUTOR LOOP ─────────────────────────────────────────

def run_executor_loop():
    """
    Spustí mt5_executor.py každých EXECUTOR_LOOP_MINUTES minut.
    Volá se po úspěšné ranní pipeline a běží celý den.
    """
    print(f"\n  🚀 MT5 Executor loop spuštěn ({EXECUTOR_LOOP_MINUTES} min interval)")

    while True:
        now = datetime.now()

        # Zastav v noci (22:00 - 6:00) — trh spí
        if now.hour >= 22 or now.hour < 6:
            print(f"  😴 {now.strftime('%H:%M')} — noční pauza (22:00-6:00)")
            time.sleep(600)  # 10 minut
            continue

        # Spusť executor
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            subprocess.run(
                [sys.executable, "mt5_executor.py", "--once"],
                timeout=60,
                env=env
            )
        except subprocess.TimeoutExpired:
            print(f"  ⚠️  Executor timeout")
        except Exception as e:
            print(f"  ⚠️  Executor chyba: {e}")

        time.sleep(EXECUTOR_LOOP_MINUTES * 60)

# ─── SCHEDULER SMYČKA ──────────────────────────────────────────

def should_run_today(log_history):
    """
    Zkontroluj jestli jsme dnes pipeline už spustili.
    Zabrání dvojitému spuštění pokud scheduler restartuje.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    for entry in reversed(log_history):
        if entry["timestamp"].startswith(today):
            return False
    return True


def load_log_history():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def run_scheduler():
    """
    Hlavní plánovač — každou minutu zkontroluje čas.
    Když je 6:00, spustí pipeline (pokud dnes ještě neběžela).
    """
    print("╔══════════════════════════════════════════╗")
    print("║      MARKETPAL DAILY SCHEDULER          ║")
    print(f"║      Spuštěn: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}         ║")
    print("╚══════════════════════════════════════════╝\n")
    print(f"  Pipeline se spustí každý den v {RUN_HOUR:02d}:{RUN_MINUTE:02d}")
    print(f"  Zastav: Ctrl+C\n")
    print(f"  💡 Pro okamžité spuštění: python scheduler.py --now\n")

    send_telegram(
        f"🤖 <b>MarketPal Scheduler spuštěn</b>\n"
        f"Pipeline bude běžet každý den v {RUN_HOUR:02d}:{RUN_MINUTE:02d}"
    )

    while True:
        now = datetime.now()

        # Zkontroluj čas spuštění
        if now.hour == RUN_HOUR and now.minute == RUN_MINUTE:
            log_history = load_log_history()
            if should_run_today(log_history):
                run_full_pipeline()
            else:
                print(f"  ℹ️  {now.strftime('%H:%M')} — pipeline dnes již proběhla, přeskakuji")

        # Čekej 60 sekund a zkontroluj znovu
        # (granularita 1 minuta — přesnost ±1 minuta)
        next_check = 60 - now.second
        time.sleep(next_check)


# ─── ENTRY POINT ───────────────────────────────────────────────

if __name__ == "__main__":
    if "--now" in sys.argv:
        # Okamžité spuštění (pro Task Scheduler nebo manuální test)
        print("╔══════════════════════════════════════════╗")
        print("║      MARKETPAL DAILY SCHEDULER          ║")
        print("║      Režim: OKAMŽITÉ SPUŠTĚNÍ           ║")
        print("╚══════════════════════════════════════════╝")
        success = run_full_pipeline()
        sys.exit(0 if success else 1)
    else:
        # Spustit plánovač (nekonečná smyčka)
        try:
            run_scheduler()
        except KeyboardInterrupt:
            print("\n\n  ⏹️  Scheduler zastaven (Ctrl+C)")
            send_telegram("⏹️ <b>MarketPal Scheduler zastaven</b>")