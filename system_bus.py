"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - SYSTEM BUS v1.0                            ║
║     Sdílená komunikační vrstva mezi všemi skripty          ║
╚══════════════════════════════════════════════════════════════╝

IDEA:
  Každý skript v ekosystému může:
    - PUBLIKOVAT: "mám nový signál", "pipeline dokončena", "chyba"
    - ČÍST:       "co se stalo od posledního checkupu?"
    - REAGOVAT:   na eventy ostatních skriptů

  Vše jde přes jeden soubor: data/system_bus.json

ARCHITEKTURA:

  ┌─────────────────────────────────────────────────────┐
  │                  SYSTEM BUS                         │
  │              data/system_bus.json                   │
  │                                                     │
  │  pipeline_status  │  signals  │  trades  │  alerts  │
  └──────┬────────────┴─────┬─────┴────┬─────┴────┬─────┘
         │                  │          │           │
   feature_eng.py    signal_gen.py  mt5_exec.py  telegram
   triple_barrier.py              backtest.py    safeguards
   meta_labeling.py

PŘÍKLADY KOMUNIKACE:

  1. feature_engineering dokončí → zapíše event
     signal_generator to přečte → spustí signal check

  2. signal_generator najde signál → zapíše do bus
     mt5_executor to přečte → otevře pozici
     telegram_bot to přečte → pošle alert

  3. mt5_executor zavře obchod → zapíše výsledek
     backtest modul to přečte → aktualizuje live statistiky
     telegram_bot to přečte → pošle exit alert

  4. safeguards detekuje DD breach → zapíše EMERGENCY
     všechny skripty to přečtou → zastaví nové obchody

POUŽITÍ:
  from system_bus import Bus, Event, EventType

  bus = Bus()

  # Publikuj event
  bus.publish(Event(
      type    = EventType.SIGNAL_FOUND,
      source  = "live_signal_generator",
      payload = {"ticker": "EURUSD", "direction": "long", ...}
  ))

  # Čti nové eventy (od posledního čtení)
  for event in bus.read_new("mt5_executor"):
      if event.type == EventType.SIGNAL_FOUND:
          place_order(event.payload)
"""

import json
import time
import threading
import fcntl  # Linux file locking (Windows: použijeme msvcrt)
import sys
from enum import Enum
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Optional
from dataclasses import dataclass, asdict

from logger import get_logger

log = get_logger("system_bus")

BUS_FILE      = Path("data/system_bus.json")
CURSOR_FILE   = Path("data/bus_cursors.json")  # každý skript si pamatuje kde skončil
MAX_EVENTS    = 1000   # maximální počet eventů v historii (starší se mažou)


# ═══════════════════════════════════════════════════════════════
# SEKCE 1: EVENT TYPY
# ═══════════════════════════════════════════════════════════════

class EventType(str, Enum):
    # Pipeline eventy
    PIPELINE_STARTED    = "pipeline.started"
    PIPELINE_FINISHED   = "pipeline.finished"
    PIPELINE_FAILED     = "pipeline.failed"
    DATA_UPDATED        = "data.updated"        # nová gold data dostupná

    # Signal eventy
    SIGNAL_FOUND        = "signal.found"        # signal generator našel signál
    SIGNAL_SKIPPED      = "signal.skipped"      # signál přeskočen (filtr)
    SIGNAL_CHECK_DONE   = "signal.check_done"   # pravidelný check dokončen

    # Trading eventy
    ORDER_PLACED        = "order.placed"        # MT5 order odeslán
    ORDER_FILLED        = "order.filled"        # order vyplněn
    ORDER_REJECTED      = "order.rejected"      # broker odmítl
    POSITION_OPENED     = "position.opened"
    POSITION_CLOSED     = "position.closed"     # + P&L výsledek
    BREAKEVEN_SET       = "position.breakeven"  # SL přesunut na entry

    # Risk eventy
    DRAWDOWN_WARNING    = "risk.dd_warning"     # DD > 70% limitu
    DAILY_LIMIT_HIT     = "risk.daily_limit"    # denní ztráta u limitu
    MAX_DD_BREACH       = "risk.max_dd_breach"  # FTMO breach → STOP VŠE
    CIRCUIT_BREAKER     = "risk.circuit_breaker"# CB přešel do OPEN

    # System eventy
    BOT_STARTED         = "system.started"
    BOT_STOPPED         = "system.stopped"
    HEARTBEAT           = "system.heartbeat"
    ERROR               = "system.error"
    RECONCILIATION      = "system.reconciliation"

    # Makro eventy (z alternative data pipeline)
    MACRO_UPDATE        = "macro.update"        # nová FRED/COT data
    HIGH_IMPACT_NEWS    = "macro.high_impact"   # blíží se NFP/FOMC → skip signály


@dataclass
class Event:
    """Jeden event na sběrnici."""
    type:      EventType
    source:    str                    # kdo publishoval ("signal_generator")
    payload:   dict                   # libovolná data
    timestamp: str = None
    id:        int  = None            # auto-increment, přiřadí Bus

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        d = d.copy()
        d["type"] = EventType(d["type"])
        return cls(**d)


# ═══════════════════════════════════════════════════════════════
# SEKCE 2: BUS — jádro systému
# ═══════════════════════════════════════════════════════════════

class Bus:
    """
    Sdílená sběrnice pro všechny MARKETPAL skripty.

    Thread-safe pomocí file lockingu.
    Každý skript si pamatuje cursor (kde naposledy četl).
    """

    def __init__(self):
        BUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── PUBLISH ──────────────────────────────────────────────

    def publish(self, event: Event) -> int:
        """
        Publikuje event na sběrnici.
        Vrátí ID eventu.

        Příklad:
            bus.publish(Event(
                type    = EventType.SIGNAL_FOUND,
                source  = "live_signal_generator",
                payload = {"ticker": "EURUSD", "direction": "long"}
            ))
        """
        with self._lock:
            state = self._load()
            events = state.get("events", [])

            # Přiřaď ID
            next_id = state.get("next_id", 1)
            event.id = next_id
            state["next_id"] = next_id + 1

            events.append(event.to_dict())

            # Ořež na MAX_EVENTS (starší mazat)
            if len(events) > MAX_EVENTS:
                events = events[-MAX_EVENTS:]

            state["events"]   = events
            state["last_event"] = event.to_dict()
            state["updated_at"] = datetime.utcnow().isoformat()

            self._save(state)

        log.debug(
            f"[BUS] 📤 {event.source} → {event.type.value} "
            f"| payload: {list(event.payload.keys())}"
        )
        return event.id

    # ── READ NEW ─────────────────────────────────────────────

    def read_new(self, reader: str,
                 event_types: list = None) -> list[Event]:
        """
        Čte nové eventy od posledního čtení tohoto readera.

        reader:      název skriptu který čte ("mt5_executor")
        event_types: filtr — None = vše, jinak jen tyto typy

        Příklad:
            for event in bus.read_new("mt5_executor",
                                      [EventType.SIGNAL_FOUND]):
                place_order(event.payload)
        """
        with self._lock:
            state   = self._load()
            cursors = self._load_cursors()
            cursor  = cursors.get(reader, 0)

            events  = state.get("events", [])
            new     = [
                Event.from_dict(e)
                for e in events
                if e.get("id", 0) > cursor
            ]

            # Filtr na typy
            if event_types:
                type_vals = [t.value for t in event_types]
                new = [e for e in new if e.type.value in type_vals]

            # Aktualizuj cursor
            if events:
                cursors[reader] = state.get("next_id", 1) - 1
                self._save_cursors(cursors)

        if new:
            log.debug(f"[BUS] 📥 {reader} přečetl {len(new)} nových eventů")

        return new

    # ── PEEK ─────────────────────────────────────────────────

    def peek_last(self, event_type: EventType = None) -> Optional[Event]:
        """Vrátí poslední event (bez pohybu cursoru)."""
        state  = self._load()
        events = state.get("events", [])
        if not events:
            return None
        if event_type:
            filtered = [e for e in reversed(events)
                        if e.get("type") == event_type.value]
            return Event.from_dict(filtered[0]) if filtered else None
        return Event.from_dict(events[-1])

    def get_status(self) -> dict:
        """Vrátí aktuální stav sběrnice — pro dashboard."""
        state = self._load()
        events = state.get("events", [])

        # Počty per typ za posledních 24h
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        recent = [e for e in events if e.get("timestamp", "") > cutoff]

        counts = {}
        for e in recent:
            t = e.get("type", "unknown")
            counts[t] = counts.get(t, 0) + 1

        return {
            "total_events":  len(events),
            "events_24h":    len(recent),
            "counts_24h":    counts,
            "last_event":    state.get("last_event"),
            "updated_at":    state.get("updated_at"),
        }

    # ── HELPERS ──────────────────────────────────────────────

    def _load(self) -> dict:
        if not BUS_FILE.exists():
            return {"events": [], "next_id": 1}
        try:
            return json.loads(BUS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"events": [], "next_id": 1}

    def _save(self, state: dict):
        BUS_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def _load_cursors(self) -> dict:
        if not CURSOR_FILE.exists():
            return {}
        try:
            return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cursors(self, cursors: dict):
        CURSOR_FILE.write_text(
            json.dumps(cursors, indent=2),
            encoding="utf-8"
        )


# ═══════════════════════════════════════════════════════════════
# SEKCE 3: SUBSCRIBER — reaktivní eventy
# ═══════════════════════════════════════════════════════════════

class Subscriber:
    """
    Reaktivní listener — volá callback když přijde konkrétní event.
    Běží v background threadu.

    Příklad:
        sub = Subscriber("mt5_executor")
        sub.on(EventType.SIGNAL_FOUND,   place_order_callback)
        sub.on(EventType.MAX_DD_BREACH,  emergency_stop_callback)
        sub.start(poll_interval=5)
    """

    def __init__(self, name: str):
        self.name      = name
        self.bus       = Bus()
        self._handlers: dict[EventType, list[Callable]] = {}
        self._running  = False

    def on(self, event_type: EventType, callback: Callable):
        """Zaregistruje callback pro daný typ eventu."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(callback)
        log.debug(f"[BUS] {self.name} registrován na {event_type.value}")
        return self  # chaining

    def start(self, poll_interval: float = 5.0):
        """Spustí polling v background threadu."""
        self._running = True
        thread = threading.Thread(
            target=self._poll_loop,
            args=(poll_interval,),
            daemon=True,
            name=f"Bus-{self.name}"
        )
        thread.start()
        log.info(f"[BUS] Subscriber '{self.name}' spuštěn "
                 f"(poll každých {poll_interval}s)")

    def stop(self):
        self._running = False

    def _poll_loop(self, interval: float):
        while self._running:
            try:
                subscribed_types = list(self._handlers.keys())
                new_events = self.bus.read_new(self.name, subscribed_types)

                for event in new_events:
                    handlers = self._handlers.get(event.type, [])
                    for handler in handlers:
                        try:
                            handler(event)
                        except Exception as e:
                            log.error(
                                f"[BUS] Handler chyba: {self.name} "
                                f"→ {event.type.value}: {e}"
                            )
            except Exception as e:
                log.error(f"[BUS] Poll chyba: {e}")

            time.sleep(interval)


# ═══════════════════════════════════════════════════════════════
# SEKCE 4: MASTER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class Orchestrator:
    """
    Volitelný master skript který propojuje vše dohromady.

    Spusť místo samostatných skriptů:
        python system_bus.py orchestrate

    Spustí všechny komponenty v jednom procesu:
        - Live signal generator (každých 15 min)
        - MT5 executor (subscriber na SIGNAL_FOUND)
        - Telegram bot (subscriber na vše důležité)
        - Watchdog (heartbeat monitor)
        - Reconciler (každých 5 min)

    VÝHODA:  jeden příkaz = celý systém
    NEVÝHODA: jeden pád = vše padne
               → proto každý modul má vlastní error handling
    """

    def __init__(self):
        self.bus  = Bus()
        self.subs = []

    def setup_signal_generator(self):
        """Spustí signal check každých 15 minut."""
        def signal_loop():
            from live_signal_generator import check_signals

            while True:
                try:
                    signals = check_signals(verbose=True)
                    for sig in signals:
                        self.bus.publish(Event(
                            type    = EventType.SIGNAL_FOUND,
                            source  = "signal_generator",
                            payload = sig,
                        ))
                    self.bus.publish(Event(
                        type    = EventType.SIGNAL_CHECK_DONE,
                        source  = "signal_generator",
                        payload = {"signals_found": len(signals)},
                    ))
                except Exception as e:
                    log.error(f"[ORCH] Signal generator chyba: {e}")
                    self.bus.publish(Event(
                        type    = EventType.ERROR,
                        source  = "signal_generator",
                        payload = {"error": str(e)},
                    ))

                time.sleep(15 * 60)  # 15 minut

        thread = threading.Thread(
            target=signal_loop, daemon=True, name="SignalGenerator"
        )
        thread.start()
        log.info("[ORCH] Signal generator spuštěn (15 min interval)")

    def setup_mt5_executor(self):
        """Reaguje na SIGNAL_FOUND → place_order."""
        from mt5_executor import place_order, MT5Connection, get_open_positions
        from safeguards  import Reconciler

        MT5Connection.connect()

        # Reconciliation při startu
        positions = get_open_positions()
        Reconciler().reconcile(positions)

        sub = Subscriber("mt5_executor")

        def on_signal(event: Event):
            sig = event.payload
            log.info(f"[ORCH] MT5: zpracovávám signál {sig.get('name')}")
            result = place_order(sig)

            if result.get("success"):
                self.bus.publish(Event(
                    type    = EventType.POSITION_OPENED,
                    source  = "mt5_executor",
                    payload = result,
                ))
            else:
                self.bus.publish(Event(
                    type    = EventType.ORDER_REJECTED,
                    source  = "mt5_executor",
                    payload = result,
                ))

        def on_max_dd(event: Event):
            log.critical("[ORCH] MAX DD BREACH — zavírám vše!")
            from mt5_executor import close_all_positions
            close_all_positions("max_dd_breach")

        sub.on(EventType.SIGNAL_FOUND,   on_signal)
        sub.on(EventType.MAX_DD_BREACH,  on_max_dd)
        sub.start(poll_interval=2.0)  # rychlá reakce na signály

        self.subs.append(sub)
        log.info("[ORCH] MT5 executor spuštěn")

    def setup_telegram_notifier(self):
        """Reaguje na důležité eventy → Telegram alert."""
        try:
            from telegram_bot import (
                notify_trade_entry, notify_trade_exit,
                notify_drawdown_alert, notify_ftmo_breach
            )
        except ImportError:
            log.warning("[ORCH] telegram_bot.py nedostupný")
            return

        sub = Subscriber("telegram_notifier")

        def on_position_opened(event: Event):
            p = event.payload
            notify_trade_entry(
                p.get("ticker","?"), p.get("direction","?").upper(),
                p.get("entry",0), p.get("sl",0), p.get("tp",0), p.get("volume",0)
            )

        def on_position_closed(event: Event):
            p = event.payload
            notify_trade_exit(
                p.get("ticker","?"), p.get("direction","?"),
                p.get("entry",0), p.get("close_price",0),
                p.get("pnl",0), p.get("reason","?")
            )

        def on_dd_warning(event: Event):
            p = event.payload
            notify_drawdown_alert(p.get("dd",0), p.get("daily_dd",0))

        def on_max_dd(event: Event):
            notify_ftmo_breach()

        def on_error(event: Event):
            from telegram_bot import send_message
            src = event.payload.get("source", "unknown")
            err = event.payload.get("error", "?")
            send_message(f"⚠️ Systémová chyba v <b>{src}</b>:\n<code>{err[:200]}</code>")

        (sub
            .on(EventType.POSITION_OPENED,  on_position_opened)
            .on(EventType.POSITION_CLOSED,  on_position_closed)
            .on(EventType.DRAWDOWN_WARNING, on_dd_warning)
            .on(EventType.MAX_DD_BREACH,    on_max_dd)
            .on(EventType.ERROR,            on_error)
        )
        sub.start(poll_interval=3.0)
        self.subs.append(sub)
        log.info("[ORCH] Telegram notifier spuštěn")

    def setup_watchdog(self):
        """Heartbeat každých 5 minut, timeout 10 minut."""
        from safeguards import Watchdog

        def heartbeat_loop():
            while True:
                state = {}
                try:
                    import json
                    p = Path("data/bot_state.json")
                    if p.exists():
                        state = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass

                Watchdog.ping(status="OK", extra={
                    "equity":    state.get("equity", 0),
                    "daily_pnl": state.get("daily_pnl", 0),
                })
                self.bus.publish(Event(
                    type    = EventType.HEARTBEAT,
                    source  = "orchestrator",
                    payload = {"equity": state.get("equity", 0)},
                ))
                time.sleep(5 * 60)  # ping každých 5 minut

        Watchdog(timeout_min=10).start()  # ← ZMĚNĚNO: 35 → 10 minut

        thread = threading.Thread(
            target=heartbeat_loop, daemon=True, name="Heartbeat"
        )
        thread.start()
        log.info("[ORCH] Watchdog spuštěn (timeout: 10 min)")

    def run(self):
        """Spustí celý ekosystém."""
        log.info("╔══════════════════════════════════════════╗")
        log.info("║  MARKETPAL ORCHESTRATOR                 ║")
        log.info("╚══════════════════════════════════════════╝")

        from safeguards import run_pre_flight, EmergencyStop

        # Pre-flight
        run_pre_flight()

        # Spusť všechny komponenty
        self.setup_watchdog()
        self.setup_telegram_notifier()
        self.setup_mt5_executor()
        self.setup_signal_generator()

        # Publikuj start event
        self.bus.publish(Event(
            type    = EventType.BOT_STARTED,
            source  = "orchestrator",
            payload = {"timestamp": datetime.utcnow().isoformat()},
        ))

        # Emergency stop handler
        def on_stop():
            self.bus.publish(Event(
                type    = EventType.BOT_STOPPED,
                source  = "orchestrator",
                payload = {"reason": "manual_stop"},
            ))
            for sub in self.subs:
                sub.stop()

        EmergencyStop.setup(on_stop=on_stop)

        log.info("✅ Všechny komponenty spuštěny — systém běží")
        log.info("   Ctrl+C = čisté zastavení")

        # Drž hlavní thread naživu
        try:
            while not EmergencyStop.should_stop():
                time.sleep(1)
        except KeyboardInterrupt:
            pass


# ═══════════════════════════════════════════════════════════════
# SEKCE 5: BUS MONITOR (CLI)
# ═══════════════════════════════════════════════════════════════

def print_bus_status():
    """Zobrazí aktuální stav sběrnice v terminálu."""
    bus    = Bus()
    status = bus.get_status()

    print("\n╔══════════════════════════════════════════╗")
    print("║  SYSTEM BUS STATUS                      ║")
    print("╚══════════════════════════════════════════╝")
    print(f"  Celkem eventů:  {status['total_events']}")
    print(f"  Posledních 24h: {status['events_24h']}")
    print(f"  Aktualizováno:  {status.get('updated_at', 'nikdy')}")

    if status["counts_24h"]:
        print("\n  Eventy za 24h:")
        for event_type, count in sorted(
            status["counts_24h"].items(), key=lambda x: -x[1]
        ):
            print(f"    {count:>4}×  {event_type}")

    last = status.get("last_event")
    if last:
        print(f"\n  Poslední event:")
        print(f"    Type:   {last.get('type')}")
        print(f"    Source: {last.get('source')}")
        print(f"    Time:   {last.get('timestamp', '')[:19]}")
        payload = last.get("payload", {})
        if payload:
            for k, v in list(payload.items())[:3]:
                print(f"    {k}: {v}")
    print()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "orchestrate":
        Orchestrator().run()

    elif cmd == "status":
        print_bus_status()

    elif cmd == "test":
        print("Testuji System Bus...\n")
        bus = Bus()

        # Publikuj testovací eventy
        bus.publish(Event(
            type    = EventType.SIGNAL_FOUND,
            source  = "test",
            payload = {"ticker": "EURUSD", "direction": "long", "conf": 0.72}
        ))
        bus.publish(Event(
            type    = EventType.POSITION_OPENED,
            source  = "test",
            payload = {"ticker": "EURUSD", "entry": 1.08540}
        ))
        bus.publish(Event(
            type    = EventType.HEARTBEAT,
            source  = "test",
            payload = {"equity": 10127}
        ))

        # Čti je zpět
        events = bus.read_new("test_reader")
        print(f"Přečteno {len(events)} eventů:")
        for e in events:
            print(f"  [{e.id}] {e.type.value} ← {e.source}")

        print_bus_status()
        print("✅ Test OK")

    else:
        print("Použití:")
        print("  python system_bus.py status      — zobraz stav sběrnice")
        print("  python system_bus.py orchestrate — spusť celý systém")
        print("  python system_bus.py test        — otestuj komunikaci")