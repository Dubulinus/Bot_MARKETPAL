# CHANGELOG — MARKETPAL AOS

Format: [verze] datum — co se změnilo a proč

---

## [v0.4.0] — 2026-03-09
### Přidáno
- `rafinerie_dukascopy.py` — zpracování 10 let M1 Dukascopy dat
- `regime_fix.py` — oprava SIDEWAYS-only bug ve forex gold datech
- `telegram_bot.py` — Telegram notifikace + /status /pause /resume příkazy
- `live_signal_generator.py` — real-time signal check každých 15 minut
- `mt5_executor.py` — propojení s MetaTrader 5, place_order + monitor
- `logger.py` — centrální logging, RotatingFileHandler, PipelineAudit
- `setup_dev.py` — automatický dev setup (pre-commit, Makefile, .gitignore)

### Opraveno
- `meta_labeling.py` — SyntaxError: return outside function (FRED/COT blok)
- `backtest_v3.py` — regime analysis ukazoval jen SIDEWAYS pro forex

---

## [v0.3.0] — 2026-03-08
### Přidáno
- `backtest_v3.py` — hardcore backtest: Monte Carlo 1000×, Walk-forward 5 oken
- Anti-bias suite: look-ahead, survivorship (15% haircut stocks), Bonferroni
- Výsledek: GOOGL eliminován survivorship bias, 3 forex strategie prošly

### Nalezeno
- Hlavní problém: N=31 obchodů za 4 roky → statistická nesignifikance
- Řešení: Dukascopy 10 let dat → N>200 per strategie

---

## [v0.2.0] — 2026-03-07
### Přidáno
- `feature_engineering_v2.py` — +40 featur: Volume, Momentum, Patterns
- `tezba_alternative.py` — FRED makro + COT forex data
- `meta_labeling.py` v1.4 — Random Forest filtr, FRED/COT jako vstupy
- Meta výsledky: GOOGL +4.2% ⚠️, ostatní marginální (malý N)

---

## [v0.1.0] — 2026-03-03
### Přidáno
- Základní pipeline: Bronze → Silver → Gold
- `feature_engineering.py` — 52 technických featur
- `triple_barrier.py` — labeling 13 barrier konfigurací
- `backtest_v2.py` — první backtest, všechny 4 strategie splnily FTMO target
