#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  VovôAmp — Instalador para Armbian (Orange Pi 3)            ║
# ║                                                              ║
# ║  Instalar com UM comando:                                    ║
# ║                                                              ║
# ║  curl -sSL https://raw.githubusercontent.com/ricardontk/    ║
# ║    vovoampARM/main/install.sh | sudo bash                    ║
# ╚══════════════════════════════════════════════════════════════╝

set -e
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${YELLOW}  → $1${NC}"; }
err()  { echo -e "${RED}  ✗ ERRO: $1${NC}"; exit 1; }
hdr()  { echo -e "\n${CYAN}[$1]${NC}"; }

REPO="https://raw.githubusercontent.com/ricardontk/vovoampARM/main"
DEST="/opt/vovoamp"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  👂 VovôAmp — Instalador             ║${NC}"
echo -e "${CYAN}║  Armbian · Orange Pi 3               ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""

# ── PRÉ-CHECKS ────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Execute com sudo"

# Detecta usuário real (quem chamou o sudo)
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
info "Usuário detectado: $REAL_USER (home: $REAL_HOME)"

# Detecta sistema de áudio: PipeWire ou PulseAudio
if systemctl --user -M "$REAL_USER@.service" is-active pipewire &>/dev/null 2>&1 || \
   dpkg -l pipewire-pulse &>/dev/null 2>&1; then
    AUDIO_STACK="pipewire"
else
    AUDIO_STACK="pulseaudio"
fi
info "Stack de áudio detectado: $AUDIO_STACK"

# Detecta distro base
DISTRO=$(lsb_release -is 2>/dev/null || echo "Unknown")
CODENAME=$(lsb_release -cs 2>/dev/null || echo "unknown")
info "Distro: $DISTRO $CODENAME (Armbian)"

# ── 1. ATUALIZA SISTEMA ───────────────────────────────────
hdr "1/9  Atualizando pacotes"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-dev \
    python3-numpy python3-scipy \
    libportaudio2 portaudio19-dev \
    bluez bluez-tools \
    avahi-daemon \
    curl lsb-release
ok "Pacotes base instalados"

# ── 2. ÁUDIO: PipeWire ou PulseAudio ─────────────────────
hdr "2/9  Configurando stack de áudio ($AUDIO_STACK)"

if [[ "$AUDIO_STACK" == "pipewire" ]]; then
    apt-get install -y -qq \
        pipewire pipewire-audio \
        pipewire-pulse wireplumber \
        libspa-0.2-bluetooth 2>/dev/null || \
    apt-get install -y -qq \
        pipewire pipewire-pulse wireplumber 2>/dev/null || true

    # Garante que PipeWire sobe como serviço de usuário e inicia no boot
    loginctl enable-linger "$REAL_USER" 2>/dev/null || true
    sudo -u "$REAL_USER" XDG_RUNTIME_DIR="/run/user/$(id -u $REAL_USER)" \
        systemctl --user enable pipewire pipewire-pulse wireplumber 2>/dev/null || true
    ok "PipeWire configurado"
else
    apt-get install -y -qq \
        pulseaudio pulseaudio-module-bluetooth 2>/dev/null || true
    ok "PulseAudio configurado"
fi

# ── 3. BLUETOOTH ──────────────────────────────────────────
hdr "3/9  Configurando Bluetooth"

# main.conf — auto-enable, discoverable permanente
cat > /etc/bluetooth/main.conf << 'EOF'
[General]
Name = VovoAmp
Class = 0x200414
DiscoverableTimeout = 0
AlwaysPairable = true
AutoEnable = true

[Policy]
AutoEnable = true
EOF

# Garante que o serviço bluetooth está rodando
systemctl enable bluetooth
systemctl restart bluetooth
sleep 1

# Ativa o adaptador BT
bluetoothctl power on 2>/dev/null || true

# Adiciona usuário aos grupos necessários
usermod -aG bluetooth,audio "$REAL_USER" 2>/dev/null || true
ok "Bluetooth configurado"

# ── 4. USB AUDIO ──────────────────────────────────────────
hdr "4/9  Configurando USB Audio"

# Carrega módulo USB audio no boot
grep -qxF 'snd-usb-audio' /etc/modules || echo "snd-usb-audio" >> /etc/modules

# No Armbian, NÃO sobrescrevemos /etc/asound.conf com hw:1,0
# porque o índice pode mudar. O VovôAmp detecta dinamicamente.
# Apenas garantimos que o módulo está ativo agora:
modprobe snd-usb-audio 2>/dev/null || true
ok "USB Audio configurado"

# ── 5. BAIXA OS ARQUIVOS ──────────────────────────────────
hdr "5/9  Baixando arquivos do GitHub"
mkdir -p "$DEST/static"

curl -fsSL "$REPO/vovoamp.py"          -o "$DEST/vovoamp.py"         || err "Falha ao baixar vovoamp.py"
curl -fsSL "$REPO/static/index.html"   -o "$DEST/static/index.html"  || err "Falha ao baixar index.html"
curl -fsSL "$REPO/update.sh"           -o "/usr/local/bin/vovoamp-update" 2>/dev/null || true
chmod +x /usr/local/bin/vovoamp-update 2>/dev/null || true
ok "Arquivos baixados de github.com/ricardontk/vovoampARM"

# ── 6. PYTHON DEPS ────────────────────────────────────────
hdr "6/9  Instalando dependências Python"

# Armbian/Ubuntu recente exige --break-system-packages
pip3 install --quiet --break-system-packages \
    sounddevice aiohttp scipy numpy 2>/dev/null || \
pip3 install --quiet \
    sounddevice aiohttp scipy numpy || \
err "Falha ao instalar dependências Python"
ok "sounddevice, aiohttp, scipy, numpy instalados"

# ── 7. HOSTNAME & mDNS ────────────────────────────────────
hdr "7/9  Configurando hostname"
OLD_HOSTNAME=$(hostname)
hostnamectl set-hostname vovoamp 2>/dev/null || echo "vovoamp" > /etc/hostname
# Atualiza /etc/hosts sem duplicar
sed -i "s/$OLD_HOSTNAME/vovoamp/g" /etc/hosts 2>/dev/null || true
grep -q "vovoamp" /etc/hosts || echo "127.0.1.1  vovoamp" >> /etc/hosts

systemctl enable avahi-daemon
systemctl restart avahi-daemon
ok "Hostname: vovoamp.local"

# ── 8. SERVIÇO SYSTEMD ────────────────────────────────────
hdr "8/9  Criando serviço systemd"

# No Armbian com PipeWire, o áudio roda como serviço de usuário.
# O vovoamp precisa rodar como o mesmo usuário para acessar PipeWire.
if [[ "$AUDIO_STACK" == "pipewire" ]]; then
    SERVICE_USER="$REAL_USER"
else
    SERVICE_USER="root"
fi

cat > /etc/systemd/system/vovoamp.service << EOF
[Unit]
Description=VovôAmp — Amplificador de Microfone
After=network.target bluetooth.target sound.target
Wants=bluetooth.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$DEST
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/python3 $DEST/vovoamp.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=HOME=$REAL_HOME
$([ "$AUDIO_STACK" == "pipewire" ] && echo "Environment=XDG_RUNTIME_DIR=/run/user/$(id -u $REAL_USER)")

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vovoamp
ok "Serviço criado — usuário: $SERVICE_USER"

# ── 9. INICIA ─────────────────────────────────────────────
hdr "9/9  Iniciando VovôAmp"
systemctl start vovoamp
sleep 3

STATUS=$(systemctl is-active vovoamp 2>/dev/null || echo "failed")
if [[ "$STATUS" == "active" ]]; then
    ok "VovôAmp está rodando!"
else
    echo ""
    echo -e "${YELLOW}  Serviço não iniciou ainda (pode ser normal se PipeWire ainda não subiu).${NC}"
    echo -e "${YELLOW}  Logs:${NC}"
    journalctl -u vovoamp -n 20 --no-pager 2>/dev/null || true
    echo ""
    echo -e "${YELLOW}  Tente reiniciar e verificar:  sudo systemctl status vovoamp${NC}"
fi

# ── RESUMO ────────────────────────────────────────────────
IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "?")

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✓ Instalação concluída!                     ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Abra no celular (mesma rede WiFi):"
echo ""
echo -e "  ${GREEN}▶  http://vovoamp.local${NC}"
echo -e "  ${GREEN}▶  http://$IP${NC}  (use este se .local não funcionar)"
echo ""
echo "  Comandos úteis:"
echo -e "  ${CYAN}sudo systemctl status vovoamp${NC}     # ver status"
echo -e "  ${CYAN}sudo journalctl -u vovoamp -f${NC}     # ver logs ao vivo"
echo -e "  ${CYAN}sudo vovoamp-update${NC}               # atualizar do GitHub"
echo ""
echo "  Stack de áudio: $AUDIO_STACK"
echo "  Usuário do serviço: $SERVICE_USER"
echo ""

# Aviso se BT precisa de atenção no Orange Pi 3
echo -e "${YELLOW}  ⚠  Orange Pi 3: se o Bluetooth não aparecer na interface,${NC}"
echo -e "${YELLOW}     execute:  sudo bluetoothctl power on${NC}"
echo ""
