#!/bin/bash
# VovôAmp — Atualizar do GitHub
REPO="https://raw.githubusercontent.com/ricardontk/vovoampARM/main"
DEST="/opt/vovoamp"

echo "→ Baixando versão mais recente..."
curl -fsSL "$REPO/vovoamp.py"        -o "$DEST/vovoamp.py"        && echo "✓ vovoamp.py"
curl -fsSL "$REPO/static/index.html" -o "$DEST/static/index.html" && echo "✓ index.html"
curl -fsSL "$REPO/update.sh"         -o "/usr/local/bin/vovoamp-update" && chmod +x /usr/local/bin/vovoamp-update

echo "→ Reiniciando serviço..."
systemctl restart vovoamp
sleep 2
systemctl is-active --quiet vovoamp \
    && echo "✓ VovôAmp atualizado e rodando!" \
    || echo "✗ Erro — veja: sudo journalctl -u vovoamp -n 30"
