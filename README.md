# 👂 VovôAmp — Raspberry Pi Zero 2 W

Amplificador de microfone USB com controle via celular.
Sem monitor, sem teclado — controlado pelo navegador do celular.

```
[Microfone USB] → [Pi Zero 2W] → [Fone Bluetooth]
                        ↑
             [Celular: http://vovoamp.local]
```

---

## 📦 O que você precisa

| Item | Onde comprar | Preço estimado |
|------|-------------|---------------|
| Raspberry Pi Zero 2 W | Mercado Livre / AliExpress | R$ 120–180 |
| Cartão microSD 16GB (classe 10) | Qualquer loja | R$ 20–30 |
| Fonte USB-C 5V 2.5A | Mercado Livre | R$ 25–40 |
| Cabo micro-USB OTG | Mercado Livre | R$ 10–15 |
| Adaptador micro-USB → USB-A | Para o microfone | R$ 10 |
| Microfone USB | Já tem | — |
| Fone Bluetooth | Já tem | — |

**Total estimado: R$ 185–265**

---

## 🚀 Instalação — Passo a Passo

### ETAPA 1 — Gravar o sistema operacional

1. Baixe o **Raspberry Pi Imager**: https://www.raspberrypi.com/software/
2. Insira o cartão SD no computador
3. No Imager:
   - **Device**: Raspberry Pi Zero 2 W
   - **OS**: Raspberry Pi OS Lite (64-bit) — sem interface gráfica, mais leve
   - **Storage**: seu cartão SD
4. Clique em **Next** → **Edit Settings**:
   - **Hostname**: `vovoamp`
   - **Username**: `pi` / **Password**: (escolha uma senha)
   - **WiFi SSID**: nome da sua rede
   - **WiFi Password**: senha da sua rede
   - **Locale**: Brazil / pt_BR
   - Aba **Services**: ative **SSH**
5. Clique em **Save** → **Yes** → aguarde gravar

### ETAPA 2 — Primeiro boot

1. Insira o SD no Pi Zero 2 W
2. Conecte a fonte (aguarde 60–90 segundos para o primeiro boot)
3. No seu computador, abra o terminal e conecte via SSH:

```bash
ssh pi@vovoamp.local
```

### ETAPA 3 — Copiar e instalar o VovôAmp

No terminal SSH, execute:

```bash
# Cria pasta temporária
mkdir vovoamp-install && cd vovoamp-install
```

Agora copie os arquivos do projeto para o Pi. Do seu computador (outro terminal):

```bash
scp -r ./vovoamp-pi/* pi@vovoamp.local:~/vovoamp-install/
```

De volta ao SSH no Pi:

```bash
# Instala tudo automaticamente
sudo bash ~/vovoamp-install/install.sh
```

Aguarde ~3 minutos. No final verá:
```
✓ VovôAmp está rodando!
  Acesse: http://vovoamp.local
```

### ETAPA 4 — Parear o fone Bluetooth

No SSH do Pi:

```bash
sudo bluetoothctl
```

Dentro do bluetoothctl:
```
power on
agent on
default-agent
scan on
```
Aguarde aparecer o MAC do seu fone (ex: `A1:B2:C3:D4:E5:F6`), depois:
```
scan off
pair A1:B2:C3:D4:E5:F6
connect A1:B2:C3:D4:E5:F6
trust A1:B2:C3:D4:E5:F6
quit
```

### ETAPA 5 — Usar!

1. Conecte o microfone USB no Pi (via cabo OTG)
2. Abra o Chrome no celular
3. Acesse: **http://vovoamp.local**
4. Selecione os dispositivos de entrada e saída
5. Toque em **▶ Iniciar**

---

## 🔧 Comandos úteis

```bash
# Ver status do serviço
sudo systemctl status vovoamp

# Ver logs em tempo real
sudo journalctl -u vovoamp -f

# Reiniciar o serviço
sudo systemctl restart vovoamp

# Listar dispositivos de áudio
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Verificar Bluetooth
bluetoothctl devices
```

---

## ⚠️ Solução de problemas

| Problema | Solução |
|---------|---------|
| `http://vovoamp.local` não abre | Use o IP direto: veja no roteador ou `hostname -I` no Pi |
| Microfone não aparece na lista | Verifique o cabo OTG e tente `lsusb` no Pi |
| Fone BT não aparece | Repare o processo de pareamento do bluetoothctl |
| Som cortado | Reduza o ganho ou aumente o `BLOCK_SIZE` em vovoamp.py |
| Muita latência | Reduza `BLOCK_SIZE` para 512 em vovoamp.py |

---

## 📐 Diagrama de conexão física

```
        [Fonte 5V]
             │ USB-C
    ┌─────────────────┐
    │  Pi Zero 2 W    │──── WiFi ────► Celular
    │                 │              (controle)
    └────────┬────────┘
         micro-USB OTG
             │
    [Adaptador USB-A]
             │
    [Microfone USB]

    Pi ──── Bluetooth ────► [Fone da Vovó]
```

---

*Feito com ❤️ para facilitar a vida de quem a gente ama.*
