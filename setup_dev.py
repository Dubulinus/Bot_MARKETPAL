"""
╔══════════════════════════════════════════════════════════════╗
║     MARKETPAL - DEV SETUP v1.0                             ║
║     Spusť jednou → celé dev prostředí připraveno           ║
╚══════════════════════════════════════════════════════════════╝

CO DĚLÁ:
  1. Nainstaluje dev závislosti (Black, Ruff, pre-commit)
  2. Vytvoří .env.example
  3. Nastaví pre-commit hooks (auto-format před každým commitem)
  4. Vytvoří Makefile (make run, make lint, make commit, ...)
  5. Vytvoří .gitignore (parquet, env, cache, logy)
  6. Vytvoří CHANGELOG.md skeleton
  7. Vytvoří GitHub Actions CI workflow

SPUŠTĚNÍ:
  python setup_dev.py
"""

import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(".")   # spusť z kořene projektu


# ═══════════════════════════════════════════════════════════════
def run(cmd: str, check: bool = True) -> bool:
    """Spustí shell příkaz a zobrazí výstup."""
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=False)
    if check and result.returncode != 0:
        print(f"  ⚠️  Příkaz skončil s kódem {result.returncode}")
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# 1. DEV ZÁVISLOSTI
# ═══════════════════════════════════════════════════════════════

def install_dev_deps():
    print("\n📦 Instaluji dev závislosti...")
    packages = [
        "black",           # formátování kódu
        "ruff",            # linter (rychlejší než flake8)
        "pre-commit",      # git hooks
        "python-dotenv",   # načítání .env souboru
        "loguru",          # alternativní logging (optional)
    ]
    run(f"pip install {' '.join(packages)} --break-system-packages -q")
    print("  ✅ Dev závislosti nainstalovány")

    # Windows: skriptů se nainstalují do složky která není v PATH
    # Opravíme PATH pro aktuální session automaticky
    if sys.platform == "win32":
        import site, subprocess as sp
        # Zjisti kde jsou Scripts
        result = sp.run(
            ["python", "-c", "import site; print(site.getusersitepackages())"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            site_pkg = result.stdout.strip()
            scripts  = site_pkg.replace("site-packages", "Scripts")
            current  = os.environ.get("PATH", "")
            if scripts not in current:
                os.environ["PATH"] = scripts + os.pathsep + current
            print(f"  💡 PATH tip: přidej trvale do systému:")
            print(f"     {scripts}")
            print(f"     (Win+R → sysdm.cpl → Upřesnit → Proměnné prostředí)")


# ═══════════════════════════════════════════════════════════════
# 2. .env.example
# ═══════════════════════════════════════════════════════════════

ENV_EXAMPLE = """\
# MARKETPAL — Environment Variables
# Zkopíruj jako .env a vyplň hodnoty
# NIKDY necommituj .env na GitHub!

# ── Telegram Bot ──────────────────────────────
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# ── Polygon.io ────────────────────────────────
POLYGON_API_KEY=your_polygon_key_here

# ── Alpaca ────────────────────────────────────
ALPACA_API_KEY=your_alpaca_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# ── MT5 Broker ────────────────────────────────
MT5_LOGIN=your_account_number
MT5_PASSWORD=your_password
MT5_SERVER=your_broker_server

# ── Logging ───────────────────────────────────
MARKETPAL_LOG_LEVEL=INFO
"""

def create_env_example():
    print("\n📄 Vytvářím .env.example...")
    path = ROOT / ".env.example"
    if not path.exists():
        path.write_text(ENV_EXAMPLE, encoding="utf-8")
        print("  ✅ .env.example vytvořen")
    else:
        print("  ✓  .env.example již existuje")

    # Vytvoř .env pokud neexistuje
    env_path = ROOT / ".env"
    if not env_path.exists():
        env_path.write_text(ENV_EXAMPLE.replace("your_", "FILL_IN_"), encoding="utf-8")
        print("  ✅ .env vytvořen — vyplň hodnoty!")


# ═══════════════════════════════════════════════════════════════
# 3. PRE-COMMIT CONFIG
# ═══════════════════════════════════════════════════════════════

PRE_COMMIT_CONFIG = """\
# .pre-commit-config.yaml
# Automaticky spustí Black + Ruff před každým git commitem
# Instalace: pre-commit install

repos:
  - repo: https://github.com/psf/black
    rev: 24.3.0
    hooks:
      - id: black
        language_version: python3
        args: ["--line-length=100"]

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.1
    hooks:
      - id: ruff
        args: ["--fix", "--ignore=E501,F401"]

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-merge-conflict
      - id: check-added-large-files
        args: ["--maxkb=5000"]   # blokuj soubory > 5 MB (parquet!)
      - id: check-json
      - id: debug-statements      # blokuj breakpoint() v kódu
"""

def setup_pre_commit():
    print("\n🔧 Nastavuji pre-commit hooks...")
    config_path = ROOT / ".pre-commit-config.yaml"
    if not config_path.exists():
        config_path.write_text(PRE_COMMIT_CONFIG, encoding="utf-8")
        print("  ✅ .pre-commit-config.yaml vytvořen")

    # Nainstaluj hooks do .git
    if (ROOT / ".git").exists():
        run("pre-commit install", check=False)
        print("  ✅ Pre-commit hooks nainstalovány")
        print("  💡 Teď každý 'git commit' automaticky spustí Black + Ruff")
    else:
        print("  ⚠️  .git nenalezeno — spusť 'git init' nebo klonuj repo")


# ═══════════════════════════════════════════════════════════════
# 4. MAKEFILE
# ═══════════════════════════════════════════════════════════════

MAKEFILE = """\
# MARKETPAL — Makefile
# Použití: make <příkaz>

.PHONY: help run test lint format commit pipeline clean

help:
\t@echo ""
\t@echo "MARKETPAL AOS — dostupné příkazy:"
\t@echo "  make pipeline   — spustí celou pipeline (tezba → gold → backtest)"
\t@echo "  make signals    — jednorázový signal check"
\t@echo "  make bot        — spustí live signal generator"
\t@echo "  make backtest   — spustí hardcore backtest"
\t@echo "  make regime     — opraví regime analysis v gold souborech"
\t@echo "  make lint       — zkontroluje kód (Ruff)"
\t@echo "  make format     — naformátuje kód (Black)"
\t@echo "  make test       — spustí integration testy"
\t@echo "  make commit     — format + lint + commit"
\t@echo "  make clean      — vymaže cache soubory"
\t@echo "  make logs       — zobrazí posledních 50 řádků all.log"
\t@echo ""

# ── Pipeline ──────────────────────────────────────────────────
pipeline:
\t@echo "▶️  Spouštím pipeline..."
\tpython rafinerie_polygon.py
\tpython feature_engineering.py
\tpython feature_engineering_v2.py
\tpython triple_barrier.py
\tpython meta_labeling.py
\t@echo "✅ Pipeline dokončena"

pipeline-dukascopy:
\t@echo "▶️  Dukascopy pipeline..."
\tpython tezba_dukascopy.py
\tpython rafinerie_dukascopy.py
\tpython feature_engineering.py
\tpython feature_engineering_v2.py
\tpython triple_barrier.py
\tpython meta_labeling.py
\t@echo "✅ Dukascopy pipeline dokončena"

# ── Live trading ──────────────────────────────────────────────
signals:
\tpython live_signal_generator.py once

bot:
\tpython live_signal_generator.py

mt5-test:
\tpython mt5_executor.py test

# ── Backtesting ───────────────────────────────────────────────
backtest:
\tpython backtest_v3.py

regime:
\tpython regime_fix.py

# ── Code quality ──────────────────────────────────────────────
lint:
\t@echo "🔍 Ruff linter..."
\truff check . --ignore=E501,F401

format:
\t@echo "🖊️  Black formatter..."
\tblack . --line-length=100

# ── Testing ───────────────────────────────────────────────────
test:
\t@echo "🧪 Integration testy..."
\tpython mt5_executor.py test
\tpython telegram_bot.py test
\tpython live_signal_generator.py once

# ── Git workflow ──────────────────────────────────────────────
commit:
\t@echo "📝 Format → Lint → Commit..."
\t$(MAKE) format
\t$(MAKE) lint
\t@read -p "Commit message: " msg; git add -A && git commit -m "$$msg"

push:
\tgit push origin main

status:
\tgit status
\tgit log --oneline -5

# ── Utility ───────────────────────────────────────────────────
logs:
\ttail -50 data/logs/all.log

clean:
\tfind . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
\tfind . -name "*.pyc" -delete 2>/dev/null || true
\t@echo "✅ Cache vymazána"

install:
\tpip install -r requirements.txt --break-system-packages
\tpython setup_dev.py
"""

def create_makefile():
    print("\n⚙️  Vytvářím Makefile...")
    path = ROOT / "Makefile"
    if not path.exists():
        path.write_text(MAKEFILE, encoding="utf-8")
        print("  ✅ Makefile vytvořen")
        print("  💡 Teď: make pipeline / make signals / make commit")
    else:
        print("  ✓  Makefile již existuje")


# ═══════════════════════════════════════════════════════════════
# 5. .gitignore
# ═══════════════════════════════════════════════════════════════

GITIGNORE = """\
# MARKETPAL .gitignore

# ── Prostředí ─────────────────────────────────────────────────
.env
.venv/
venv/
*.egg-info/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.ruff_cache/

# ── Data (nikdy na GitHub — jsou velká!) ──────────────────────
data/00_DUKASCOPY_CACHE/
data/02_EXPANDED_RAW/
data/03_SILVER_CLEAN/
data/04_GOLD_FEATURES/
data/07_TRIPLE_BARRIER/
data/11_META_LABELS/*.pkl
data/12_ALTERNATIVE/
data/13_BACKTEST/
*.parquet
*.h5
*.hdf5

# ── Logy ──────────────────────────────────────────────────────
data/logs/
*.log

# ── Bot state ─────────────────────────────────────────────────
data/bot_state.json
data/signal_log.json
data/trade_log.json

# ── IDE ───────────────────────────────────────────────────────
.vscode/settings.json
.idea/
*.swp

# ── OS ────────────────────────────────────────────────────────
.DS_Store
Thumbs.db

# ── Co NA GitHub PATŘÍ ────────────────────────────────────────
# ✅ *.py skripty
# ✅ requirements.txt
# ✅ .env.example (bez hodnot!)
# ✅ Makefile
# ✅ README.md
# ✅ CHANGELOG.md
# ✅ data/edge_matrix_results.csv (výsledky backtestů)
"""

def create_gitignore():
    print("\n🔒 Vytvářím .gitignore...")
    path = ROOT / ".gitignore"
    if not path.exists():
        path.write_text(GITIGNORE, encoding="utf-8")
        print("  ✅ .gitignore vytvořen")
    else:
        print("  ✓  .gitignore již existuje")


# ═══════════════════════════════════════════════════════════════
# 6. CHANGELOG.md
# ═══════════════════════════════════════════════════════════════

CHANGELOG = f"""\
# CHANGELOG — MARKETPAL AOS

Format: [verze] datum — co se změnilo a proč

---

## [v0.4.0] — {__import__('datetime').date.today()}
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
"""

def create_changelog():
    print("\n📋 Vytvářím CHANGELOG.md...")
    path = ROOT / "CHANGELOG.md"
    if not path.exists():
        path.write_text(CHANGELOG, encoding="utf-8")
        print("  ✅ CHANGELOG.md vytvořen")
    else:
        print("  ✓  CHANGELOG.md již existuje")


# ═══════════════════════════════════════════════════════════════
# 7. GITHUB ACTIONS CI
# ═══════════════════════════════════════════════════════════════

GITHUB_CI = """\
# .github/workflows/ci.yml
# Spustí se automaticky po každém git push
# Kontroluje: syntax, import errors, základní testy

name: MARKETPAL CI

on:
  push:
    branches: [ main, dev ]
  pull_request:
    branches: [ main ]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install ruff black pandas numpy scikit-learn

      - name: Ruff lint
        run: ruff check . --ignore=E501,F401

      - name: Black format check
        run: black --check . --line-length=100

      - name: Import check (žádné rozbité importy)
        run: |
          python -c "import feature_engineering"
          python -c "import meta_labeling"
          python -c "import logger"

      - name: MT5 executor test (simulation)
        run: python mt5_executor.py test

      - name: Telegram bot test (mock)
        run: python telegram_bot.py test
"""

def create_github_actions():
    print("\n🤖 Vytvářím GitHub Actions CI...")
    ci_dir  = ROOT / ".github" / "workflows"
    ci_dir.mkdir(parents=True, exist_ok=True)
    ci_path = ci_dir / "ci.yml"

    if not ci_path.exists():
        ci_path.write_text(GITHUB_CI, encoding="utf-8")
        print("  ✅ .github/workflows/ci.yml vytvořen")
        print("  💡 Po každém 'git push' GitHub automaticky spustí lint + testy")
    else:
        print("  ✓  CI workflow již existuje")


# ═══════════════════════════════════════════════════════════════
# 8. REQUIREMENTS.TXT
# ═══════════════════════════════════════════════════════════════

REQUIREMENTS = """\
# MARKETPAL — Python závislosti
# Instalace: pip install -r requirements.txt

# ── Data ──────────────────────────────────────────────────────
pandas>=2.0.0
numpy>=1.24.0
pyarrow>=14.0.0          # parquet
requests>=2.31.0

# ── ML ────────────────────────────────────────────────────────
scikit-learn>=1.3.0
scipy>=1.11.0

# ── Vizualizace ───────────────────────────────────────────────
plotly>=5.18.0
streamlit>=1.30.0

# ── Brokeři & API ─────────────────────────────────────────────
alpaca-py>=0.20.0
polygon-api-client>=1.13.0
# MetaTrader5>=5.0.45    # jen na Windows s MT5

# ── Notifikace ────────────────────────────────────────────────
python-telegram-bot>=20.0

# ── Utility ───────────────────────────────────────────────────
python-dotenv>=1.0.0
loguru>=0.7.0
tqdm>=4.66.0

# ── Dev (neinstalovat na produkčním serveru) ──────────────────
# black>=24.0.0
# ruff>=0.4.0
# pre-commit>=3.5.0
# pytest>=7.4.0
"""

def create_requirements():
    print("\n📦 Vytvářím requirements.txt...")
    path = ROOT / "requirements.txt"
    if not path.exists():
        path.write_text(REQUIREMENTS, encoding="utf-8")
        print("  ✅ requirements.txt vytvořen")
    else:
        print("  ✓  requirements.txt již existuje")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║   MARKETPAL DEV SETUP v1.0                         ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    print("Nastavuji celé dev prostředí...")

    install_dev_deps()
    create_env_example()
    create_gitignore()
    create_requirements()
    create_makefile()
    setup_pre_commit()
    create_changelog()
    create_github_actions()

    print("\n" + "═"*55)
    print("✅ DEV SETUP DOKONČEN\n")
    print("Další kroky:")
    print("  1. Vyplň .env (Telegram token, API klíče)")
    print("  2. make pipeline     — spustí celou pipeline")
    print("  3. make signals      — zkontroluje signály")
    print("  4. make commit       — format + lint + git commit")
    print("  5. make bot          — spustí live trading")
    print("\nZkratky:")
    print("  make help            — všechny příkazy")
    print("  make logs            — posledních 50 řádků logů")
    print("  make clean           — vymaže cache")


if __name__ == "__main__":
    main()