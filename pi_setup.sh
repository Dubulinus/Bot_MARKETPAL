#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║     MARKETPAL - PI SETUP v1.0                              ║
# ║     Spusť jednou na Pi → vše se nastaví samo               ║
# ╚══════════════════════════════════════════════════════════════╝
#
# POUŽITÍ (na Pi v terminálu nebo přes SSH):
#   chmod +x pi_setup.sh
#   ./pi_setup.sh
#
# CO DĚLÁ:
#   1. Nainstaluje Python závislosti
#   2. Nastaví systemd service (bot běží po restartu automaticky)
#   3. Nastaví ngrok (dashboard dostupný ze školy)
#   4. Nastaví automatický git pull každou hodinu
#   5. Nastaví log rotation
#   6. Otestuje že vše funguje

set -e  # zastav při jakékoliv chybě

# ── Barvy pro výstup ──────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}  ✅ $1${NC}"; }
info() { echo -e "${BLUE}  ℹ️  $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
err()  { echo -e "${RED}  ❌ $1${NC}"; }

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   MARKETPAL PI SETUP v1.0                          ║"
echo "║   Raspberry Pi 4 B — Ubuntu/Raspberry Pi OS 64bit ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Zjisti home dir a user ────────────────────────────────────
CURRENT_USER=$(whoami)
HOME_DIR=$(eval echo ~$CURRENT_USER)
PROJECT_DIR="$HOME_DIR/Bot_MARKETPAL"

info "User: $CURRENT_USER"
info "Home: $HOME_DIR"
info "Project: $PROJECT_DIR"
echo ""

# ═══════════════════════════════════════════════════════════════
# KROK 1: SYSTÉMOVÉ ZÁVISLOSTI
# ═══════════════════════════════════════════════════════════════
echo "── KROK 1: Systémové závislosti ──────────────────────"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-pip \
    python3-venv \
    git \
    curl \
    wget \
    htop \
    tmux \
    logrotate \
    2>/dev/null
ok "Systémové balíčky nainstalovány"

# ═══════════════════════════════════════════════════════════════
# KROK 2: KLONOVÁNÍ REPOZITÁŘE
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 2: Repozitář ─────────────────────────────────"
if [ -d "$PROJECT_DIR" ]; then
    info "Repozitář již existuje → git pull"
    cd "$PROJECT_DIR"
    git pull origin main
else
    info "Klonuji repozitář..."
    git clone https://github.com/Dubulinus/Bot_MARKETPAL.git "$PROJECT_DIR"
fi
ok "Repozitář aktuální"

# ═══════════════════════════════════════════════════════════════
# KROK 3: PYTHON VIRTUAL ENVIRONMENT
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 3: Python venv ───────────────────────────────"
cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    python3 -m venv venv
    ok "Venv vytvořen"
else
    ok "Venv již existuje"
fi

# Aktivuj venv
source venv/bin/activate

# Instalace závislostí
pip install --upgrade pip -q
pip install \
    pandas \
    numpy \
    pyarrow \
    requests \
    python-dotenv \
    streamlit \
    plotly \
    loguru \
    -q

ok "Python závislosti nainstalovány"
info "Python: $(python3 --version)"
info "Pandas: $(python3 -c 'import pandas; print(pandas.__version__)')"

# ═══════════════════════════════════════════════════════════════
# KROK 4: ADRESÁŘOVÁ STRUKTURA
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 4: Adresáře ──────────────────────────────────"
mkdir -p data/logs
mkdir -p data/02_EXPANDED_RAW/M1/forex
mkdir -p data/03_SILVER_CLEAN
mkdir -p data/04_GOLD_FEATURES
mkdir -p data/07_TRIPLE_BARRIER
mkdir -p data/11_META_LABELS
mkdir -p data/12_ALTERNATIVE
mkdir -p data/13_BACKTEST
ok "Adresářová struktura vytvořena"

# ═══════════════════════════════════════════════════════════════
# KROK 5: .env SOUBOR
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 5: Konfigurace (.env) ────────────────────────"
if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
# MARKETPAL — vyplň před spuštěním!
TELEGRAM_TOKEN=BOT_TOKEN_ZDE
TELEGRAM_CHAT_ID=CHAT_ID_ZDE
POLYGON_API_KEY=ZDE
ALPACA_API_KEY=ZDE
ALPACA_SECRET_KEY=ZDE
MARKETPAL_LOG_LEVEL=INFO
EOF
    warn ".env vytvořen — VYPLŇ před spuštěním bota!"
    warn "nano $PROJECT_DIR/.env"
else
    ok ".env již existuje"
fi

# ═══════════════════════════════════════════════════════════════
# KROK 6: SYSTEMD SERVICE — live signal generator
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 6: Systemd service ───────────────────────────"

# Signal generator service
cat > /tmp/marketpal-signals.service << EOF
[Unit]
Description=MARKETPAL Live Signal Generator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/venv/bin/python live_signal_generator.py
Restart=always
RestartSec=30
StandardOutput=append:$PROJECT_DIR/data/logs/signals.log
StandardError=append:$PROJECT_DIR/data/logs/signals_error.log

[Install]
WantedBy=multi-user.target
EOF

# Dashboard service
cat > /tmp/marketpal-dashboard.service << EOF
[Unit]
Description=MARKETPAL Streamlit Dashboard
After=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/streamlit run dashboard.py --server.port 8501 --server.headless true
Restart=always
RestartSec=10
StandardOutput=append:$PROJECT_DIR/data/logs/dashboard.log
StandardError=append:$PROJECT_DIR/data/logs/dashboard_error.log

[Install]
WantedBy=multi-user.target
EOF

sudo mv /tmp/marketpal-signals.service   /etc/systemd/system/
sudo mv /tmp/marketpal-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable marketpal-signals
sudo systemctl enable marketpal-dashboard

ok "Systemd services vytvořeny a povoleny"
info "Bot se teď spustí automaticky po každém restartu Pi"

# ═══════════════════════════════════════════════════════════════
# KROK 7: NGROK (dashboard ze školy)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 7: Ngrok ─────────────────────────────────────"

ARCH=$(uname -m)
if [ "$ARCH" = "aarch64" ]; then
    NGROK_URL="https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz"
else
    NGROK_URL="https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm.tgz"
fi

if ! command -v ngrok &> /dev/null; then
    info "Instaluji ngrok..."
    wget -q "$NGROK_URL" -O /tmp/ngrok.tgz
    sudo tar -xzf /tmp/ngrok.tgz -C /usr/local/bin
    rm /tmp/ngrok.tgz
    ok "Ngrok nainstalován"
else
    ok "Ngrok již nainstalován"
fi

# Ngrok service
cat > /tmp/marketpal-ngrok.service << EOF
[Unit]
Description=MARKETPAL Ngrok Tunnel
After=network-online.target marketpal-dashboard.service

[Service]
Type=simple
User=$CURRENT_USER
ExecStart=/usr/local/bin/ngrok http 8501 --log=stdout
Restart=always
RestartSec=10
StandardOutput=append:$PROJECT_DIR/data/logs/ngrok.log

[Install]
WantedBy=multi-user.target
EOF

sudo mv /tmp/marketpal-ngrok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable marketpal-ngrok

warn "Ngrok potřebuje account token!"
warn "1. Registruj se zdarma: https://ngrok.com"
warn "2. Zkopíruj token z dashboard"
warn "3. Spusť: ngrok config add-authtoken TVUJ_TOKEN"
warn "4. Pak: sudo systemctl start marketpal-ngrok"
warn "5. URL najdeš: curl localhost:4040/api/tunnels"

# ═══════════════════════════════════════════════════════════════
# KROK 8: AUTO GIT PULL (každou hodinu)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 8: Auto git pull ─────────────────────────────"

CRON_JOB="0 * * * * cd $PROJECT_DIR && git pull origin main >> $PROJECT_DIR/data/logs/gitpull.log 2>&1"

# Přidej cron job pokud neexistuje
(crontab -l 2>/dev/null | grep -v "Bot_MARKETPAL"; echo "$CRON_JOB") | crontab -

ok "Cron job nastaven — git pull každou hodinu"
info "Pushneš z Windows → Pi stáhne změny automaticky"

# ═══════════════════════════════════════════════════════════════
# KROK 9: LOG ROTATION
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 9: Log rotation ──────────────────────────────"

cat > /tmp/marketpal-logrotate << EOF
$PROJECT_DIR/data/logs/*.log {
    daily
    rotate 15
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF

sudo mv /tmp/marketpal-logrotate /etc/logrotate.d/marketpal
ok "Log rotation nastaven (15 dní, denní komprese)"

# ═══════════════════════════════════════════════════════════════
# KROK 10: PŘENOS DAT Z WINDOWS (instrukce)
# ═══════════════════════════════════════════════════════════════
echo ""
echo "── KROK 10: Přenos dat z Windows ─────────────────────"
PI_IP=$(hostname -I | awk '{print $1}')
info "IP adresa tohoto Pi: $PI_IP"
echo ""
echo "  Na Windows spusť (v CMD nebo PowerShell):"
echo ""
echo -e "${YELLOW}  # Přenes gold data (nejdůležitější):${NC}"
echo "  scp -r C:\\Bot_MARKETPAL\\data\\04_GOLD_FEATURES $CURRENT_USER@$PI_IP:$PROJECT_DIR/data/"
echo ""
echo -e "${YELLOW}  # Přenes meta modely:${NC}"
echo "  scp -r C:\\Bot_MARKETPAL\\data\\11_META_LABELS $CURRENT_USER@$PI_IP:$PROJECT_DIR/data/"
echo ""
echo -e "${YELLOW}  # Přenes silver data (volitelné, větší):${NC}"
echo "  scp -r C:\\Bot_MARKETPAL\\data\\03_SILVER_CLEAN $CURRENT_USER@$PI_IP:$PROJECT_DIR/data/"

# ═══════════════════════════════════════════════════════════════
# FINÁLNÍ SOUHRN
# ═══════════════════════════════════════════════════════════════
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   SETUP DOKONČEN ✅                                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  HNED TEĎ:"
echo "  1. Vyplň .env:  nano $PROJECT_DIR/.env"
echo "  2. Přenes data z Windows (viz výše)"
echo ""
echo "  SPUŠTĚNÍ BOTA:"
echo "  sudo systemctl start marketpal-signals"
echo "  sudo systemctl start marketpal-dashboard"
echo ""
echo "  KONTROLA:"
echo "  sudo systemctl status marketpal-signals"
echo "  tail -f $PROJECT_DIR/data/logs/signals.log"
echo ""
echo "  DASHBOARD (lokálně):"
echo "  http://$PI_IP:8501"
echo ""
echo "  DASHBOARD (ze školy, po ngrok setup):"
echo "  curl localhost:4040/api/tunnels | python3 -m json.tool"
echo ""
echo -e "${YELLOW}  ⚠️  Nezapomeň: ngrok token + .env vyplnit!${NC}"
echo ""