# MARKETPAL — Makefile
# Použití: make <příkaz>

.PHONY: help run test lint format commit pipeline clean

help:
	@echo ""
	@echo "MARKETPAL AOS — dostupné příkazy:"
	@echo "  make pipeline   — spustí celou pipeline (tezba → gold → backtest)"
	@echo "  make signals    — jednorázový signal check"
	@echo "  make bot        — spustí live signal generator"
	@echo "  make backtest   — spustí hardcore backtest"
	@echo "  make regime     — opraví regime analysis v gold souborech"
	@echo "  make lint       — zkontroluje kód (Ruff)"
	@echo "  make format     — naformátuje kód (Black)"
	@echo "  make test       — spustí integration testy"
	@echo "  make commit     — format + lint + commit"
	@echo "  make clean      — vymaže cache soubory"
	@echo "  make logs       — zobrazí posledních 50 řádků all.log"
	@echo ""

# ── Pipeline ──────────────────────────────────────────────────
pipeline:
	@echo "▶️  Spouštím pipeline..."
	python rafinerie_polygon.py
	python feature_engineering.py
	python feature_engineering_v2.py
	python triple_barrier.py
	python meta_labeling.py
	@echo "✅ Pipeline dokončena"

pipeline-dukascopy:
	@echo "▶️  Dukascopy pipeline..."
	python tezba_dukascopy.py
	python rafinerie_dukascopy.py
	python feature_engineering.py
	python feature_engineering_v2.py
	python triple_barrier.py
	python meta_labeling.py
	@echo "✅ Dukascopy pipeline dokončena"

# ── Live trading ──────────────────────────────────────────────
signals:
	python live_signal_generator.py once

bot:
	python live_signal_generator.py

mt5-test:
	python mt5_executor.py test

# ── Backtesting ───────────────────────────────────────────────
backtest:
	python backtest_v3.py

regime:
	python regime_fix.py

# ── Code quality ──────────────────────────────────────────────
lint:
	@echo "🔍 Ruff linter..."
	ruff check . --ignore=E501,F401

format:
	@echo "🖊️  Black formatter..."
	black . --line-length=100

# ── Testing ───────────────────────────────────────────────────
test:
	@echo "🧪 Integration testy..."
	python mt5_executor.py test
	python telegram_bot.py test
	python live_signal_generator.py once

# ── Git workflow ──────────────────────────────────────────────
commit:
	@echo "📝 Format → Lint → Commit..."
	$(MAKE) format
	$(MAKE) lint
	@read -p "Commit message: " msg; git add -A && git commit -m "$$msg"

push:
	git push origin main

status:
	git status
	git log --oneline -5

# ── Utility ───────────────────────────────────────────────────
logs:
	tail -50 data/logs/all.log

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ Cache vymazána"

install:
	pip install -r requirements.txt --break-system-packages
	python setup_dev.py
