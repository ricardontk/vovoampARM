#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║  VovôAmp — Instalação automática para Pi Zero 2 W       ║
# ║  Execute com: sudo bash install.sh                       ║
# ╚══════════════════════════════════════════════════════════╝

set -e
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
info() { echo -e "${YELLOW}→ $1${NC}"; }
err()  { echo -e "${RED}✗ $1${NC}"; exit 1; }

echo ""
echo "  👂 VovôAmp — Instalador"
echo "  Raspberry Pi Zero 2 W"
echo "  ──────────────────────"
echo ""

[[ $EUID -ne 0 ]] && err "Execute com sudo: sudo bash install.sh"

# ── 1. ATUALIZA O SISTEMA ──────────────────────────────────
info "Atualizando pacotes..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    python3-numpy python3-scipy \
    libportaudio2 libportaudio-dev portaudio19-dev \
    bluez bluez-tools pulseaudio pulseaudio-module-bluetooth \
    avahi-daemon \
    git curl
ok "Pacotes instalados"

# ── 2. CRIA DIRETÓRIO DO APP ───────────────────────────────
info "Criando diretório /opt/vovoamp..."
mkdir -p /opt/vovoamp/static
ok "Diretório criado"

# ── 3. COPIA OS ARQUIVOS ───────────────────────────────────
info "Copiando arquivos do app..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/vovoamp.py"          /opt/vovoamp/
cp "$SCRIPT_DIR/static/index.html"   /opt/vovoamp/static/
ok "Arquivos copiados"

# ── 4. AMBIENTE PYTHON ─────────────────────────────────────
info "Instalando dependências Python..."
pip3 install --quiet sounddevice aiohttp scipy numpy 2>/dev/null || \
pip3 install --quiet --break-system-packages sounddevice aiohttp scipy numpy
ok "Dependências Python instaladas"

# ── 5. BLUETOOTH — configura PulseAudio ───────────────────
info "Configurando Bluetooth e PulseAudio..."

# Habilita auto-connect de dispositivos BT
cat > /etc/bluetooth/main.conf << 'EOF'
[Policy]
AutoEnable=true
EOF

# PulseAudio como serviço de sistema
mkdir -p /etc/pulse
cat > /etc/pulse/system.pa << 'EOF'
load-module module-native-protocol-unix
load-module module-bluetooth-policy
load-module module-bluetooth-discover
load-module module-udev-detect
load-module module-null-sink
EOF

# Adiciona usuário ao grupo bluetooth e audio
usermod -aG bluetooth,audio pi 2>/dev/null || true
ok "Bluetooth configurado"

# ── 6. CONFIGURA USB AUDIO ─────────────────────────────────
info "Configurando USB Audio..."
# Garante que módulo USB audio é carregado
echo "snd-usb-audio" >> /etc/modules 2>/dev/null || true
# Prioriza USB audio sobre áudio onboard (se houver)
cat > /etc/asound.conf << 'EOF'
pcm.!default {
    type plug
    slave.pcm "hw:1,0"
}
ctl.!default {
    type hw
    card 1
}
EOF
ok "USB Audio configurado"

# ── 7. HOSTNAME — para acessar como vovoamp.local ──────────
info "Configurando hostname vovoamp.local..."
hostnamectl set-hostname vovoamp 2>/dev/null || \
    echo "vovoamp" > /etc/hostname
sed -i 's/raspberrypi/vovoamp/g' /etc/hosts 2>/dev/null || true

# Avahi (mDNS) — permite o celular encontrar pelo nome
systemctl enable avahi-daemon
systemctl start avahi-daemon
ok "Hostname: vovoamp.local"

# ── 8. SYSTEMD SERVICE ─────────────────────────────────────
info "Criando serviço systemd..."
cat > /etc/systemd/system/vovoamp.service << 'EOF'
[Unit]
Description=VovôAmp — Amplificador de Microfone
After=network.target bluetooth.target sound.target pulseaudio.service
Wants=bluetooth.target sound.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/vovoamp
ExecStart=/usr/bin/python3 /opt/vovoamp/vovoamp.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vovoamp
ok "Serviço criado e habilitado"

# ── 9. FIREWALL — abre porta 80 ────────────────────────────
info "Abrindo porta 80..."
if command -v ufw &>/dev/null; then
    ufw allow 80/tcp 2>/dev/null || true
fi
ok "Porta 80 liberada"

# ── 10. INICIA O SERVIÇO ───────────────────────────────────
info "Iniciando VovôAmp..."
systemctl start vovoamp
sleep 2

if systemctl is-active --quiet vovoamp; then
    ok "VovôAmp está rodando!"
else
    echo ""
    echo "Logs do serviço:"
    journalctl -u vovoamp -n 20 --no-pager
fi

# ── RESUMO FINAL ───────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Instalação concluída!                       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Acesse no celular (mesma rede WiFi):"
echo ""
echo -e "  ${GREEN}http://vovoamp.local${NC}"
echo ""
echo "  Próximos passos:"
echo "  1. Conecte o microfone USB no Pi"
echo "  2. Pareie o fone Bluetooth com o Pi:"
echo "     sudo bluetoothctl"
echo "     > power on"
echo "     > agent on"
echo "     > scan on"
echo "     > pair XX:XX:XX:XX:XX:XX"
echo "     > connect XX:XX:XX:XX:XX:XX"
echo "     > trust XX:XX:XX:XX:XX:XX"
echo "     > quit"
echo ""
echo "  3. Abra http://vovoamp.local no Chrome do celular"
echo "  4. Selecione os dispositivos e clique Iniciar"
echo ""
