"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - CENTRÁLNÍ LOGGER v1.0                      ║
║     Jeden import → strukturované logy napříč celým systémem ║
╚══════════════════════════════════════════════════════════════╝

POUŽITÍ (v každém skriptu):
    from logger import get_logger
    log = get_logger("feature_engineering")

    log.info("Zpracovávám EURUSD...")
    log.warning("Chybí FRED data")
    log.error("Pipeline selhala")
    log.debug("ATR = 0.00123")  # jen při DEBUG módu

VÝSTUP:
    Console:           barevný, čitelný
    data/logs/*.log:   soubor per skript, rotace 10 MB, 5 záložních

LOGUJE SE:
    - Každý spuštěný skript (čas, verze)
    - Každý zpracovaný soubor
    - Každý order (entry, exit, P&L)
    - Každá chyba s full traceback
    - Performance metriky (čas zpracování)
"""

import os
import sys
import logging
import traceback
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

# Logdir
LOG_DIR = Path("data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Barevné výstupy pro konzoli (Windows i Linux)
COLORS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # zelená
    "WARNING":  "\033[33m",   # žlutá
    "ERROR":    "\033[31m",   # červená
    "CRITICAL": "\033[35m",   # fialová
    "RESET":    "\033[0m",
}

# Vypni barvy pokud terminál nepodporuje (např. Windows CMD bez WT)
USE_COLORS = sys.stdout.isatty() or os.environ.get("TERM") is not None


class ColorFormatter(logging.Formatter):
    """Formátuje logy s barvami pro konzoli."""

    FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
    DATE   = "%H:%M:%S"

    def format(self, record):
        msg = super().format(record)
        if USE_COLORS:
            color = COLORS.get(record.levelname, "")
            reset = COLORS["RESET"]
            return f"{color}{msg}{reset}"
        return msg


class FileFormatter(logging.Formatter):
    """Formátuje logy pro soubor (bez barev, s plným datumem)."""
    FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
    DATE   = "%Y-%m-%d %H:%M:%S"

    def __init__(self):
        super().__init__(fmt=self.FORMAT, datefmt=self.DATE)


# Cache loggerů — každý název = jeden logger
_loggers: dict = {}


def get_logger(name: str, level: str = None) -> logging.Logger:
    """
    Vrátí (nebo vytvoří) logger pro daný modul.

    Args:
        name:  název modulu, např. "feature_engineering", "mt5_executor"
        level: "DEBUG" / "INFO" / "WARNING" — default z env MARKETPAL_LOG_LEVEL

    Použití:
        log = get_logger("triple_barrier")
        log.info("Zpracovávám 31 obchodů...")
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(f"MARKETPAL.{name}")

    # Level z env nebo default INFO
    env_level = os.environ.get("MARKETPAL_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, level or env_level, logging.INFO)
    logger.setLevel(log_level)

    # Zabrání duplikaci handlerů při re-importu
    if logger.handlers:
        return logger

    # ── Console handler ──────────────────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(log_level)
    ch.setFormatter(ColorFormatter(
        fmt=ColorFormatter.FORMAT,
        datefmt=ColorFormatter.DATE
    ))
    logger.addHandler(ch)

    # ── File handler (rotující) ───────────────────────────────
    log_file = LOG_DIR / f"{name}.log"
    fh = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=10,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)      # do souboru vše, i DEBUG
    fh.setFormatter(FileFormatter())
    logger.addHandler(fh)

    # ── Centrální all.log (vše dohromady) ─────────────────────
    all_log = LOG_DIR / "all.log"
    ah = RotatingFileHandler(
        all_log,
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=3,
        encoding="utf-8",
    )
    ah.setLevel(logging.INFO)
    ah.setFormatter(FileFormatter())
    logger.addHandler(ah)

    # Nezasílej do root loggeru (zabrání duplikaci)
    logger.propagate = False

    _loggers[name] = logger
    return logger


# ─── KONTEXTOVÝ MANAGER pro měření času ──────────────────────

class Timer:
    """
    Měří čas bloku kódu a zaloguje výsledek.

    Použití:
        log = get_logger("pipeline")
        with Timer(log, "Feature engineering EURUSD"):
            df = add_all_features(df)
        # → log: "Feature engineering EURUSD dokončen za 4.32s"
    """
    def __init__(self, logger: logging.Logger, label: str):
        self.log   = logger
        self.label = label

    def __enter__(self):
        self.start = datetime.utcnow()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = (datetime.utcnow() - self.start).total_seconds()
        if exc_type:
            self.log.error(f"{self.label} SELHALO za {elapsed:.2f}s — {exc_val}")
        else:
            self.log.info(f"{self.label} dokončeno za {elapsed:.2f}s")
        return False  # neskrývej výjimky


# ─── GLOBAL EXCEPTION HANDLER ────────────────────────────────

def setup_global_exception_handler(script_name: str):
    """
    Zachytí všechny neošetřené výjimky a zaloguje je.
    Volej na začátku každého skriptu v main():

        setup_global_exception_handler("triple_barrier")
    """
    log = get_logger(script_name)

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        log.critical(
            f"NEOŠETŘENÁ VÝJIMKA:\n"
            f"{''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))}"
        )

    sys.excepthook = handle_exception


# ─── PIPELINE AUDIT LOG ──────────────────────────────────────

class PipelineAudit:
    """
    Zaznamenává výsledky každého pipeline běhu do audit.log.
    Slouží jako history: kdy co běželo, kolik souborů zpracoval, jestli prošlo.

    Použití:
        audit = PipelineAudit("feature_engineering_v2")
        audit.start()
        # ... zpracování ...
        audit.finish(files_ok=12, files_failed=0, notes="146 featur")
    """
    AUDIT_FILE = LOG_DIR / "pipeline_audit.jsonl"

    def __init__(self, script_name: str):
        self.name  = script_name
        self.start_time = None
        self.log   = get_logger("audit")

    def start(self):
        self.start_time = datetime.utcnow()
        self.log.info(f"[START] {self.name}")

    def finish(self, files_ok: int = 0, files_failed: int = 0,
               notes: str = ""):
        elapsed = (datetime.utcnow() - self.start_time).total_seconds()
        status  = "OK" if files_failed == 0 else "PARTIAL" if files_ok > 0 else "FAIL"

        record = {
            "timestamp":    str(datetime.utcnow()),
            "script":       self.name,
            "status":       status,
            "files_ok":     files_ok,
            "files_failed": files_failed,
            "elapsed_s":    round(elapsed, 2),
            "notes":        notes,
        }

        # Append do JSONL souboru
        with open(self.AUDIT_FILE, "a", encoding="utf-8") as f:
            import json
            f.write(json.dumps(record) + "\n")

        emoji = "✅" if status == "OK" else "⚠️" if status == "PARTIAL" else "❌"
        self.log.info(
            f"[FINISH] {emoji} {self.name} | "
            f"{files_ok} OK / {files_failed} FAIL | "
            f"{elapsed:.1f}s | {notes}"
        )