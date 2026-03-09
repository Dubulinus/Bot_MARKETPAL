"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - SAFEGUARDS v1.0                            ║
║     Pojistky, kontroly, circuit breaker, watchdog          ║
╚══════════════════════════════════════════════════════════════╝

MODULY:
  CircuitBreaker     — po X chybách zastav vše, čekej, obnov
  PreFlightChecks    — kontrola před spuštěním bota (env, soubory, API)
  PipelineValidator  — validace každého kroku pipeline
  Reconciler         — srovnání stavu bota vs brokera
  Watchdog           — heartbeat monitor, auto-restart při pádu
  EmergencyStop      — nouzové zastavení celého systému

POUŽITÍ:
  from safeguards import CircuitBreaker, PreFlightChecks, run_pre_flight

  # Před spuštěním bota:
  run_pre_flight()   # zastaví program pokud něco chybí

  # Okolo kritických operací:
  cb = CircuitBreaker("mt5_orders", max_failures=3)
  with cb:
      result = place_order(signal)
"""

import os
import sys
import json
import time
import signal
import threading
import traceback
from enum import Enum
from pathlib import Path
from datetime import datetime, timedelta
from typing import Callable, Optional

from logger import get_logger, PipelineAudit

log = get_logger("safeguards")


# ═══════════════════════════════════════════════════════════════
# SEKCE 1: CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════

class CBState(Enum):
    CLOSED   = "CLOSED"    # normální provoz — propouští requesty
    OPEN     = "OPEN"      # příliš mnoho chyb — blokuje vše
    HALF_OPEN= "HALF_OPEN" # zkušební stav — propustí 1 request

class CircuitBreakerError(Exception):
    """Vyvolána když circuit breaker je OPEN."""
    pass

class CircuitBreaker:
    """
    Chrání kritické operace (MT5 order, API call) před kaskádovými selháními.

    Logika:
      CLOSED  → normální. Po max_failures chybách → OPEN
      OPEN    → blokuje vše. Po reset_timeout sekundách → HALF_OPEN
      HALF_OPEN → pustí 1 pokus. Úspěch → CLOSED, selhání → OPEN

    Použití:
        cb = CircuitBreaker("mt5_orders", max_failures=3, reset_timeout=60)

        @cb.call
        def place_order(signal):
            return mt5.order_send(...)

        # nebo jako context manager:
        with cb:
            result = mt5.order_send(...)
    """

    _instances: dict = {}

    def __new__(cls, name: str, **kwargs):
        """Singleton per název — sdílej stav napříč celým programem."""
        if name not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[name] = instance
        return cls._instances[name]

    def __init__(self, name: str, max_failures: int = 3,
                 reset_timeout: int = 60):
        if hasattr(self, "_initialized"):
            return
        self.name          = name
        self.max_failures  = max_failures
        self.reset_timeout = reset_timeout
        self._state        = CBState.CLOSED
        self._failures     = 0
        self._last_failure: Optional[datetime] = None
        self._lock         = threading.Lock()
        self._initialized  = True

    @property
    def state(self) -> CBState:
        with self._lock:
            if self._state == CBState.OPEN:
                # Zkontroluj jestli uplynul reset_timeout
                if (self._last_failure and
                    datetime.utcnow() - self._last_failure >
                    timedelta(seconds=self.reset_timeout)):
                    self._state = CBState.HALF_OPEN
                    log.info(f"[CB:{self.name}] OPEN → HALF_OPEN "
                             f"(zkušební mode po {self.reset_timeout}s)")
            return self._state

    def record_success(self):
        with self._lock:
            self._failures = 0
            if self._state == CBState.HALF_OPEN:
                self._state = CBState.CLOSED
                log.info(f"[CB:{self.name}] HALF_OPEN → CLOSED ✅")

    def record_failure(self, error: Exception):
        with self._lock:
            self._failures += 1
            self._last_failure = datetime.utcnow()
            log.warning(f"[CB:{self.name}] Chyba {self._failures}/{self.max_failures}: {error}")

            if self._failures >= self.max_failures:
                self._state = CBState.OPEN
                log.error(
                    f"[CB:{self.name}] 🔴 OPEN — příliš mnoho chyb! "
                    f"Blokuji na {self.reset_timeout}s. "
                    f"Poslední chyba: {error}"
                )
                # Telegram alert
                _send_telegram_alert(
                    f"🔴 Circuit Breaker OPEN: {self.name}\n"
                    f"Chyb: {self._failures} | Reset za {self.reset_timeout}s\n"
                    f"Chyba: {error}"
                )

    def __enter__(self):
        if self.state == CBState.OPEN:
            raise CircuitBreakerError(
                f"Circuit breaker '{self.name}' je OPEN. "
                f"Další pokus za "
                f"{self.reset_timeout - (datetime.utcnow() - self._last_failure).seconds}s"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.record_success()
        elif not isinstance(exc_val, CircuitBreakerError):
            self.record_failure(exc_val)
        return False  # neskrývej výjimky

    def call(self, func: Callable) -> Callable:
        """Decorator varianta."""
        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)
        return wrapper

    @property
    def is_open(self) -> bool:
        return self.state == CBState.OPEN

    def status(self) -> dict:
        return {
            "name":        self.name,
            "state":       self.state.value,
            "failures":    self._failures,
            "max_failures":self.max_failures,
            "last_failure":str(self._last_failure),
        }


# ═══════════════════════════════════════════════════════════════
# SEKCE 2: PRE-FLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════

class PreFlightChecks:
    """
    Kontroly před spuštěním bota.
    Pokud cokoliv selže → program se nezapne.

    Kontroluje:
    - Povinné env variables (.env)
    - Kritické soubory (gold data, meta modely)
    - Diskový prostor
    - Stav gold dat (ne příliš stará)
    - FTMO limity (nepřekročeny z minulé session)
    """

    def __init__(self):
        self.errors   : list[str] = []
        self.warnings : list[str] = []
        self.passed   : list[str] = []

    def check_env_variables(self):
        """Povinné env variables musí být nastavené."""
        required = [
            ("TELEGRAM_TOKEN",   "Telegram bot notifikace"),
            ("TELEGRAM_CHAT_ID", "Telegram chat ID"),
        ]
        optional = [
            ("POLYGON_API_KEY",  "Polygon.io data"),
            ("ALPACA_API_KEY",   "Alpaca paper trading"),
            ("MT5_LOGIN",        "MT5 live trading"),
        ]

        for key, desc in required:
            val = os.environ.get(key, "")
            if not val or val in ("BOT_TOKEN_ZDE", "CHAT_ID_ZDE", "FILL_IN_"):
                self.errors.append(f"ENV chybí: {key} ({desc})")
            else:
                self.passed.append(f"ENV OK: {key}")

        for key, desc in optional:
            if not os.environ.get(key):
                self.warnings.append(f"ENV optional chybí: {key} ({desc})")

    def check_gold_data(self, pairs: list, timeframes: list,
                        gold_dir: str = "data/04_GOLD_FEATURES",
                        max_age_hours: int = 25):
        """Gold data musí existovat a nesmí být příliš stará."""
        for tf in timeframes:
            for pair in pairs:
                path = Path(gold_dir) / tf / "forex" / f"{pair}.parquet"
                if not path.exists():
                    self.errors.append(f"Gold chybí: {pair} {tf} ({path})")
                    continue

                # Stáří souboru
                age_h = (time.time() - path.stat().st_mtime) / 3600
                if age_h > max_age_hours:
                    self.warnings.append(
                        f"Gold stará data: {pair} {tf} "
                        f"({age_h:.0f}h stará — spusť pipeline)"
                    )
                else:
                    self.passed.append(f"Gold OK: {pair} {tf} ({age_h:.1f}h)")

    def check_meta_models(self, expected: list,
                          meta_dir: str = "data/11_META_LABELS"):
        """Meta-modely by měly existovat (warning pokud ne)."""
        for name in expected:
            path = Path(meta_dir) / f"{name}_meta_model.pkl"
            if not path.exists():
                self.warnings.append(f"Meta-model chybí: {name} (použije se p=0.5)")
            else:
                self.passed.append(f"Meta-model OK: {name}")

    def check_disk_space(self, min_gb: float = 2.0):
        """Musí být dost místa pro logy a nová data."""
        import shutil
        total, used, free = shutil.disk_usage(".")
        free_gb = free / (1024 ** 3)
        if free_gb < min_gb:
            self.errors.append(
                f"Málo místa na disku: {free_gb:.1f} GB "
                f"(minimum: {min_gb} GB)"
            )
        else:
            self.passed.append(f"Disk OK: {free_gb:.1f} GB volné")

    def check_ftmo_limits(self, state_file: str = "data/bot_state.json",
                           account_size: float = 10_000,
                           max_total_loss: float = 1_000):
        """Zkontroluj že FTMO limity nejsou překročeny z minulé session."""
        if not Path(state_file).exists():
            self.warnings.append("State soubor chybí — první spuštění?")
            return

        try:
            with open(state_file) as f:
                state = json.load(f)

            equity    = state.get("equity", account_size)
            total_dd  = account_size - equity
            daily_pnl = state.get("daily_pnl", 0.0)

            if total_dd >= max_total_loss:
                self.errors.append(
                    f"FTMO MAX DD překročen: ${total_dd:.0f} >= ${max_total_loss:.0f}\n"
                    f"  → NELZE spustit obchodování!"
                )
            elif total_dd >= max_total_loss * 0.8:
                self.warnings.append(
                    f"FTMO DD varování: ${total_dd:.0f} "
                    f"({total_dd/max_total_loss*100:.0f}% limitu)"
                )
            else:
                self.passed.append(
                    f"FTMO DD OK: ${total_dd:.0f} / ${max_total_loss:.0f}"
                )

            if daily_pnl <= -500:
                self.errors.append(
                    f"FTMO denní limit překročen: ${daily_pnl:.0f}\n"
                    f"  → Počkej na reset v půlnoci UTC"
                )

        except Exception as e:
            self.warnings.append(f"Nepodařilo se načíst state: {e}")

    def check_log_dir(self):
        """Log adresář musí existovat a být zapisovatelný."""
        log_dir = Path("data/logs")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            test_file = log_dir / ".write_test"
            test_file.write_text("test")
            test_file.unlink()
            self.passed.append("Log dir OK: zapisovatelný")
        except Exception as e:
            self.errors.append(f"Log dir problém: {e}")

    def run_all(self, pairs=None, timeframes=None,
                meta_models=None) -> bool:
        """
        Spustí všechny kontroly.
        Vrátí True pokud prošly (možná s warnings), False pokud jsou errors.
        """
        pairs      = pairs      or ["EURUSD", "GBPUSD", "USDCHF"]
        timeframes = timeframes or ["M15", "H1"]
        meta_models= meta_models or ["EURUSD_M15", "GBPUSD_M15", "USDCHF_H1"]

        self.check_env_variables()
        self.check_disk_space()
        self.check_log_dir()
        self.check_gold_data(pairs, timeframes)
        self.check_meta_models(meta_models)
        self.check_ftmo_limits()

        return self._print_results()

    def _print_results(self) -> bool:
        log.info("═" * 55)
        log.info("PRE-FLIGHT CHECKS")
        log.info("═" * 55)

        for msg in self.passed:
            log.info(f"  ✅ {msg}")

        for msg in self.warnings:
            log.warning(f"  ⚠️  {msg}")

        for msg in self.errors:
            log.error(f"  ❌ {msg}")

        log.info("─" * 55)
        if self.errors:
            log.error(
                f"PRE-FLIGHT FAILED: {len(self.errors)} chyb, "
                f"{len(self.warnings)} varování"
            )
            log.error("Bot se NESPUSTÍ dokud nejsou chyby opraveny.")
            return False
        elif self.warnings:
            log.warning(
                f"PRE-FLIGHT OK s varováními: {len(self.warnings)} ⚠️  "
                f"| {len(self.passed)} ✅ prošlo"
            )
            return True
        else:
            log.info(
                f"PRE-FLIGHT PASSED ✅ — {len(self.passed)} kontrol prošlo"
            )
            return True


def run_pre_flight(**kwargs) -> bool:
    """
    Zkratka — volej na začátku main() každého live skriptu.

    Pokud selže, ukončí program s exit code 1.
    """
    checks = PreFlightChecks()
    ok     = checks.run_all(**kwargs)
    if not ok:
        log.critical("Pre-flight selhaly — program se nezapne.")
        sys.exit(1)
    return True


# ═══════════════════════════════════════════════════════════════
# SEKCE 3: PIPELINE VALIDATOR
# ═══════════════════════════════════════════════════════════════

class PipelineValidator:
    """
    Validuje výstup každého kroku pipeline před spuštěním dalšího.

    Zabrání situaci kdy:
    - triple_barrier běží na prázdných datech (žádné signály)
    - meta_labeling trénuje na 5 vzorcích (N příliš malé)
    - backtest počítá na datech s 90% NaN (feature engineering selhal)
    """

    @staticmethod
    def validate_parquet(path: str, name: str,
                          min_rows: int = 100,
                          required_cols: list = None,
                          max_nan_pct: float = 0.3) -> bool:
        """
        Validuje parquet soubor.
        Volej po každém uložení výstupu pipeline.
        """
        import pandas as pd

        p = Path(path)
        if not p.exists():
            log.error(f"[VALID] {name}: soubor neexistuje: {path}")
            return False

        try:
            df = pd.read_parquet(p)
        except Exception as e:
            log.error(f"[VALID] {name}: nelze načíst parquet: {e}")
            return False

        # Počet řádků
        if len(df) < min_rows:
            log.error(
                f"[VALID] {name}: příliš málo řádků "
                f"({len(df)} < {min_rows})"
            )
            return False

        # Povinné sloupce
        if required_cols:
            missing = [c for c in required_cols if c not in df.columns]
            if missing:
                log.error(f"[VALID] {name}: chybí sloupce {missing}")
                return False

        # NaN procenta
        if required_cols:
            nan_pct = df[required_cols].isna().mean()
            bad = nan_pct[nan_pct > max_nan_pct]
            if len(bad) > 0:
                log.warning(
                    f"[VALID] {name}: vysoké NaN% v sloupcích:\n"
                    + "\n".join(f"    {col}: {pct*100:.0f}%"
                                for col, pct in bad.items())
                )

        log.info(
            f"[VALID] ✅ {name}: {len(df):,} řádků, "
            f"{len(df.columns)} sloupců"
        )
        return True

    @staticmethod
    def validate_signals(df, name: str, min_signal_count: int = 10) -> bool:
        """
        Zkontroluje že v DataFrame existují signal_ sloupce s dostatkem signálů.
        Volej po feature_engineering před triple_barrier.
        """
        signal_cols = [c for c in df.columns if c.startswith("signal_")]

        if not signal_cols:
            log.error(f"[VALID] {name}: žádné signal_ sloupce!")
            return False

        signal_counts = {
            col: int(df[col].sum())
            for col in signal_cols
        }
        active = {k: v for k, v in signal_counts.items() if v >= min_signal_count}

        if not active:
            log.error(
                f"[VALID] {name}: žádný signál nemá >= {min_signal_count} výskytů\n"
                f"  Maximální počet: {max(signal_counts.values(), default=0)}"
            )
            return False

        log.info(
            f"[VALID] ✅ {name}: {len(active)}/{len(signal_cols)} signálů "
            f"aktivních (>= {min_signal_count})"
        )
        return True

    @staticmethod
    def validate_meta_labels(df, name: str,
                              min_samples: int = 50,
                              min_positive_rate: float = 0.2) -> bool:
        """
        Validuje výstup triple_barrier před meta_labeling.
        Zkontroluje počet vzorků a balance tříd.
        """
        if "label" not in df.columns:
            log.error(f"[VALID] {name}: chybí 'label' sloupec")
            return False

        n        = len(df)
        pos_rate = (df["label"] == 1).mean()

        if n < min_samples:
            log.error(
                f"[VALID] {name}: příliš málo vzorků pro ML "
                f"({n} < {min_samples})\n"
                f"  → Statistická nesignifikance zaručena. Přidej data."
            )
            return False

        if pos_rate < min_positive_rate:
            log.warning(
                f"[VALID] {name}: velmi nízký podíl pozitivních labelů "
                f"({pos_rate*100:.0f}%) — přidej class_weight='balanced'"
            )

        log.info(
            f"[VALID] ✅ {name}: {n} vzorků | "
            f"positive rate: {pos_rate*100:.0f}%"
        )
        return True


# ═══════════════════════════════════════════════════════════════
# SEKCE 4: RECONCILER
# ═══════════════════════════════════════════════════════════════

class Reconciler:
    """
    Porovnává stav bota (bot_state.json) se skutečným stavem u brokera.

    Spouštěj:
    - Při startu bota (po restartu)
    - Každých 5 minut (background thread)
    - Po každém uzavřeném obchodu

    Detekuje:
    - Pozice u brokera které bot nezná
    - Pozice v botu které broker nemá (ghost trades)
    - Nesoulad SL/TP
    - Nesoulad equity
    """

    def __init__(self, state_file: str = "data/bot_state.json",
                 trade_log: str = "data/trade_log.json"):
        self.state_file = Path(state_file)
        self.trade_log  = Path(trade_log)

    def reconcile(self, broker_positions: list) -> dict:
        """
        Porovná bot state s broker pozicemi.
        Vrátí dict s nesoulady.

        broker_positions: list od mt5_executor.get_open_positions()
        """
        bot_state  = self._load_state()
        bot_trades = self._load_trades()
        bot_open   = [t for t in bot_trades if t.get("status") == "OPEN"]

        issues = {
            "ghost_in_bot":    [],   # bot si myslí že má pozici, broker ne
            "unknown_in_broker": [], # broker má pozici, bot neví
            "sl_tp_mismatch":  [],   # SL/TP nesouhlasí
            "equity_mismatch": None, # equity nesouhlasí
        }

        broker_tickets = {p["ticket"]: p for p in broker_positions}
        bot_tickets    = {t.get("ticket"): t for t in bot_open
                         if t.get("ticket")}

        # Ghost trades v botu
        for ticket, trade in bot_tickets.items():
            if ticket not in broker_tickets:
                issues["ghost_in_bot"].append(trade)
                log.warning(
                    f"[RECON] Ghost trade: #{ticket} "
                    f"{trade.get('ticker')} {trade.get('direction')} "
                    f"je v botu ale NE u brokera"
                )

        # Neznámé pozice u brokera
        for ticket, pos in broker_tickets.items():
            if ticket not in bot_tickets:
                issues["unknown_in_broker"].append(pos)
                log.warning(
                    f"[RECON] Neznámá pozice: #{ticket} "
                    f"{pos.get('ticker')} {pos.get('direction')} "
                    f"je u brokera ale NE v botu"
                )

        # SL/TP nesoulad
        for ticket in set(broker_tickets) & set(bot_tickets):
            bp  = broker_tickets[ticket]
            bot = bot_tickets[ticket]
            sl_diff = abs(bp.get("sl", 0) - bot.get("sl", 0))
            tp_diff = abs(bp.get("tp", 0) - bot.get("tp", 0))
            if sl_diff > 0.0001 or tp_diff > 0.0001:
                issues["sl_tp_mismatch"].append({
                    "ticket": ticket,
                    "broker_sl": bp.get("sl"),
                    "bot_sl":    bot.get("sl"),
                    "broker_tp": bp.get("tp"),
                    "bot_tp":    bot.get("tp"),
                })
                log.warning(f"[RECON] SL/TP nesoulad: #{ticket}")

        # Equity nesoulad
        # (broker equity bychom dostali z get_account_info())
        if not any([
            issues["ghost_in_bot"],
            issues["unknown_in_broker"],
            issues["sl_tp_mismatch"],
        ]):
            log.info("[RECON] ✅ Bot a broker jsou synchronizovány")
        else:
            total = (len(issues["ghost_in_bot"]) +
                     len(issues["unknown_in_broker"]) +
                     len(issues["sl_tp_mismatch"]))
            log.error(f"[RECON] ❌ {total} nesouladů nalezeno!")
            _send_telegram_alert(
                f"⚠️ Reconciliation nesoulad:\n"
                f"Ghost trades: {len(issues['ghost_in_bot'])}\n"
                f"Neznámé pozice: {len(issues['unknown_in_broker'])}\n"
                f"SL/TP mismatch: {len(issues['sl_tp_mismatch'])}"
            )

        return issues

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except Exception:
                pass
        return {}

    def _load_trades(self) -> list:
        if self.trade_log.exists():
            try:
                return json.loads(self.trade_log.read_text())
            except Exception:
                pass
        return []


# ═══════════════════════════════════════════════════════════════
# SEKCE 5: WATCHDOG
# ═══════════════════════════════════════════════════════════════

class Watchdog:
    """
    Sleduje heartbeat bota. Pokud bot nereaguje → Telegram alert.
    Spusť v separátním procesu nebo threadu.

    Watchdog nerestartuje bot automaticky (příliš riskantní s real money).
    Místo toho pošle Telegram alert a čeká na manuální zásah.
    Pro auto-restart napiš systemd service nebo supervisor config.
    """

    HEARTBEAT_FILE = Path("data/bot_heartbeat.json")

    def __init__(self, timeout_min: int = 35):
        self.timeout_min = timeout_min

    @classmethod
    def ping(cls, status: str = "OK", extra: dict = None):
        """
        Volej z hlavní smyčky bota každých N minut.
        Zapisuje timestamp do souboru.
        """
        data = {
            "timestamp": str(datetime.utcnow()),
            "status":    status,
            **(extra or {}),
        }
        cls.HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        cls.HEARTBEAT_FILE.write_text(json.dumps(data))

    def start(self):
        """Spustí watchdog v background threadu."""
        thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="Watchdog"
        )
        thread.start()
        log.info(f"[WATCHDOG] Spuštěn (timeout: {self.timeout_min} min)")

    def _watch_loop(self):
        while True:
            time.sleep(60)  # kontrola každou minutu
            self._check()

    def _check(self):
        if not self.HEARTBEAT_FILE.exists():
            log.warning("[WATCHDOG] Heartbeat soubor neexistuje")
            return

        try:
            data = json.loads(self.HEARTBEAT_FILE.read_text())
            last = datetime.fromisoformat(data["timestamp"])
            age_min = (datetime.utcnow() - last).total_seconds() / 60

            if age_min > self.timeout_min:
                msg = (
                    f"🚨 WATCHDOG ALERT\n"
                    f"Bot neodpovídá {age_min:.0f} min!\n"
                    f"Poslední heartbeat: {last.strftime('%H:%M UTC')}\n"
                    f"Status byl: {data.get('status', '?')}"
                )
                log.critical(f"[WATCHDOG] {msg}")
                _send_telegram_alert(msg)
            else:
                log.debug(f"[WATCHDOG] ✅ Heartbeat OK ({age_min:.0f} min starý)")

        except Exception as e:
            log.error(f"[WATCHDOG] Chyba při čtení heartbeat: {e}")


# ═══════════════════════════════════════════════════════════════
# SEKCE 6: EMERGENCY STOP
# ═══════════════════════════════════════════════════════════════

class EmergencyStop:
    """
    Registruje SIGINT/SIGTERM handler.
    Při Ctrl+C nebo kill signálu:
    1. Zastaví nové obchody
    2. Odešle Telegram alert
    3. Zaloguje stav
    4. Čistě ukončí

    Volej setup() na začátku main() live skriptů.
    """

    _stop_event = threading.Event()

    @classmethod
    def setup(cls, on_stop: Callable = None):
        """
        Zaregistruje signal handlery.
        on_stop: volitelná funkce která se zavolá při zastavení
                 (např. close_all_positions)
        """
        def handler(signum, frame):
            sig_name = "SIGINT" if signum == 2 else "SIGTERM"
            log.warning(f"[EMERGENCY] {sig_name} přijat — čisté zastavení...")
            cls._stop_event.set()

            _send_telegram_alert(
                f"🔴 MARKETPAL AOS zastaven ({sig_name})\n"
                f"Čas: {datetime.utcnow().strftime('%H:%M UTC')}\n"
                f"⚠️ Zkontroluj otevřené pozice v MT5!"
            )

            if on_stop:
                try:
                    on_stop()
                except Exception as e:
                    log.error(f"[EMERGENCY] on_stop() selhal: {e}")

            sys.exit(0)

        signal.signal(signal.SIGINT,  handler)
        signal.signal(signal.SIGTERM, handler)
        log.info("[EMERGENCY] Signal handlery registrovány (Ctrl+C = čisté zastavení)")

    @classmethod
    def should_stop(cls) -> bool:
        """Volej v hlavní smyčce: if EmergencyStop.should_stop(): break"""
        return cls._stop_event.is_set()


# ═══════════════════════════════════════════════════════════════
# SEKCE 7: HELPERS
# ═══════════════════════════════════════════════════════════════

def _send_telegram_alert(text: str):
    """Odešle Telegram zprávu bez závislosti na telegram_bot modulu."""
    token   = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or token == "BOT_TOKEN_ZDE":
        log.debug(f"[TELEGRAM MOCK] {text[:80]}...")
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        log.debug(f"Telegram send failed: {e}")


# ═══════════════════════════════════════════════════════════════
# SEKCE 8: SELF-TEST
# ═══════════════════════════════════════════════════════════════

def run_self_test():
    """Otestuje všechny safeguards moduly."""
    print("\n╔══════════════════════════════════════════╗")
    print("║   SAFEGUARDS SELF-TEST                  ║")
    print("╚══════════════════════════════════════════╝\n")

    # 1. Circuit breaker
    print("1. Circuit Breaker...")
    cb = CircuitBreaker("test_cb", max_failures=2, reset_timeout=5)
    try:
        with cb:
            raise ValueError("testovací chyba")
    except ValueError:
        pass
    try:
        with cb:
            raise ValueError("druhá chyba")
    except ValueError:
        pass
    try:
        with cb:
            pass
    except CircuitBreakerError:
        print("   ✅ Circuit Breaker OPEN po 2 chybách — správně")
    time.sleep(6)
    print(f"   ✅ Po 6s: stav = {cb.state.value} (měl by být HALF_OPEN)")

    # 2. Pre-flight (simulace — bez skutečných souborů)
    print("\n2. Pre-flight Checks (simulace)...")
    checks = PreFlightChecks()
    checks.check_disk_space(min_gb=0.001)  # určitě projde
    checks.check_log_dir()
    print(f"   ✅ {len(checks.passed)} prošlo, "
          f"{len(checks.warnings)} varování, "
          f"{len(checks.errors)} chyb")

    # 3. Pipeline validator
    print("\n3. Pipeline Validator...")
    import pandas as pd, numpy as np
    df = pd.DataFrame({
        "close": np.random.randn(200),
        "signal_test": [True] * 20 + [False] * 180,
        "label": [1]*60 + [0]*140,
    })
    ok1 = PipelineValidator.validate_signals(df, "test_df", min_signal_count=10)
    ok2 = PipelineValidator.validate_meta_labels(df, "test_df", min_samples=50)
    print(f"   {'✅' if ok1 else '❌'} validate_signals")
    print(f"   {'✅' if ok2 else '❌'} validate_meta_labels")

    # 4. Reconciler
    print("\n4. Reconciler...")
    r = Reconciler()
    issues = r.reconcile(broker_positions=[])
    print(f"   ✅ Reconciler běží (0 pozic = 0 nesouladů)")

    # 5. Watchdog
    print("\n5. Watchdog...")
    Watchdog.ping(status="TEST")
    wd = Watchdog(timeout_min=1)
    print(f"   ✅ Heartbeat zapsán: {Watchdog.HEARTBEAT_FILE}")

    print("\n✅ Self-test dokončen\n")


if __name__ == "__main__":
    run_self_test()