#!/usr/bin/env python3
"""
VovôAmp - Servidor de áudio para Raspberry Pi Zero 2 W
Captura microfone USB → amplifica → filtra ruído → envia para Bluetooth
Interface de controle via web (celular na mesma rede WiFi)
"""

import asyncio
import json
import logging
import numpy as np
import sounddevice as sd
from aiohttp import web
import aiohttp
from scipy import signal as scipy_signal

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("vovoamp")

# ── CONFIGURAÇÕES DE ÁUDIO ─────────────────────────────────
SAMPLE_RATE   = 44100
BLOCK_SIZE    = 1024   # ~23ms de latência
CHANNELS      = 1
DTYPE         = 'float32'

# ── ESTADO GLOBAL ──────────────────────────────────────────
state = {
    "running":      False,
    "gain":         3.0,
    "hp_enabled":   True,
    "hp_freq":      120,      # Hz — corte de graves
    "comp_enabled": True,
    "gate_enabled": False,
    "gate_thresh":  -40,      # dBFS
    "level":        0.0,      # nível RMS atual (para VU meter)
    "input_device": None,
    "output_device": None,
    "devices":      [],
}

stream = None
ws_clients = set()

# ── FILTROS ────────────────────────────────────────────────
def make_highpass(freq, fs):
    """Filtro Butterworth passa-alta ordem 4"""
    nyq = fs / 2.0
    norm = freq / nyq
    norm = max(0.001, min(norm, 0.999))
    b, a = scipy_signal.butter(4, norm, btype='high')
    return b, a

# Estado dos filtros (zi = condição inicial para processar bloco a bloco)
hp_zi = None
hp_ba = None

def reset_filters():
    global hp_zi, hp_ba
    b, a = make_highpass(state["hp_freq"], SAMPLE_RATE)
    hp_ba = (b, a)
    hp_zi = scipy_signal.lfilter_zi(b, a) * 0.0

def apply_filters(block):
    """Aplica highpass + compressor + gate a um bloco de amostras"""
    global hp_zi

    out = block.copy()

    # 1. Highpass filter
    if state["hp_enabled"] and hp_ba is not None:
        out, hp_zi[:] = scipy_signal.lfilter(hp_ba[0], hp_ba[1], out, zi=hp_zi)

    # 2. Compressor suave (soft-knee)
    if state["comp_enabled"]:
        threshold = 0.1   # ~-20dBFS
        ratio     = 8.0
        abs_out   = np.abs(out)
        mask      = abs_out > threshold
        out[mask] = np.sign(out[mask]) * (
            threshold + (abs_out[mask] - threshold) / ratio
        )

    # 3. Ganho
    out = out * state["gain"]

    # 4. Gate de ruído
    rms = float(np.sqrt(np.mean(out ** 2)))
    db  = 20 * np.log10(rms + 1e-9)
    if state["gate_enabled"] and db < state["gate_thresh"]:
        out = out * 0.0

    # 5. Limitar para evitar clipping
    out = np.clip(out, -1.0, 1.0)

    # Nível para VU meter (RMS pré-gate, em escala 0-1)
    raw_rms = float(np.sqrt(np.mean(block ** 2)))
    state["level"] = min(raw_rms * 6, 1.0)

    return out

# ── STREAM DE ÁUDIO ────────────────────────────────────────
def audio_callback(indata, outdata, frames, time, status):
    if status:
        log.warning(f"Audio status: {status}")
    mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
    processed = apply_filters(mono)
    if outdata.ndim > 1:
        outdata[:, 0] = processed
        if outdata.shape[1] > 1:
            outdata[:, 1] = processed
    else:
        outdata[:] = processed.reshape(outdata.shape)

def get_devices():
    devs = []
    for i, d in enumerate(sd.query_devices()):
        devs.append({
            "id":       i,
            "name":     d["name"],
            "inputs":   d["max_input_channels"],
            "outputs":  d["max_output_channels"],
        })
    return devs

def find_usb_input():
    for i, d in enumerate(sd.query_devices()):
        name = d["name"].lower()
        if d["max_input_channels"] > 0 and ("usb" in name or "audio" in name):
            return i
    # fallback: primeiro dispositivo com entrada
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            return i
    return None

def find_bt_output():
    for i, d in enumerate(sd.query_devices()):
        name = d["name"].lower()
        if d["max_output_channels"] > 0 and ("blue" in name or "bt" in name or "a2dp" in name or "hsp" in name):
            return i
    # fallback: saída padrão
    return sd.default.device[1]

def start_stream():
    global stream
    if stream is not None:
        stop_stream()

    reset_filters()

    in_dev  = state["input_device"]
    out_dev = state["output_device"]

    if in_dev is None:
        in_dev = find_usb_input()
    if out_dev is None:
        out_dev = find_bt_output()

    log.info(f"Abrindo stream: entrada={in_dev} saída={out_dev}")

    try:
        stream = sd.Stream(
            samplerate   = SAMPLE_RATE,
            blocksize    = BLOCK_SIZE,
            dtype        = DTYPE,
            channels     = CHANNELS,
            device       = (in_dev, out_dev),
            callback     = audio_callback,
            latency      = 'low',
        )
        stream.start()
        state["running"] = True
        log.info("Stream iniciado ✓")
        return True, "ok"
    except Exception as e:
        log.error(f"Erro ao abrir stream: {e}")
        state["running"] = False
        return False, str(e)

def stop_stream():
    global stream
    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            log.warning(f"Erro ao fechar stream: {e}")
        stream = None
    state["running"] = False
    state["level"]   = 0.0
    log.info("Stream parado.")

# ── WEBSOCKET — atualização do VU meter em tempo real ──────
async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ws_clients.add(ws)
    log.info(f"WebSocket conectado ({len(ws_clients)} clientes)")
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.ERROR:
                break
    finally:
        ws_clients.discard(ws)
        log.info(f"WebSocket desconectado ({len(ws_clients)} clientes)")
    return ws

async def broadcast_level():
    """Envia nível de áudio para todos os clientes a 20fps"""
    while True:
        await asyncio.sleep(0.05)
        if ws_clients and state["running"]:
            msg = json.dumps({"level": round(state["level"], 3)})
            dead = set()
            for ws in ws_clients:
                try:
                    await ws.send_str(msg)
                except Exception:
                    dead.add(ws)
            ws_clients -= dead

# ── ROTAS HTTP ─────────────────────────────────────────────
async def route_index(request):
    raise web.HTTPFound('/static/index.html')

async def route_status(request):
    devs = get_devices()
    return web.json_response({**state, "devices": devs, "level": round(state["level"], 3)})

async def route_start(request):
    ok, msg = start_stream()
    return web.json_response({"ok": ok, "msg": msg})

async def route_stop(request):
    stop_stream()
    return web.json_response({"ok": True})

async def route_set(request):
    data = await request.json()
    changed_hp = False

    if "gain" in data:
        state["gain"] = float(max(1.0, min(data["gain"], 10.0)))
    if "hp_enabled" in data:
        state["hp_enabled"] = bool(data["hp_enabled"])
    if "hp_freq" in data:
        new_freq = int(max(60, min(data["hp_freq"], 300)))
        if new_freq != state["hp_freq"]:
            state["hp_freq"] = new_freq
            changed_hp = True
    if "comp_enabled" in data:
        state["comp_enabled"] = bool(data["comp_enabled"])
    if "gate_enabled" in data:
        state["gate_enabled"] = bool(data["gate_enabled"])
    if "gate_thresh" in data:
        state["gate_thresh"] = int(max(-70, min(data["gate_thresh"], -10)))
    if "input_device" in data:
        state["input_device"] = data["input_device"]
    if "output_device" in data:
        state["output_device"] = data["output_device"]

    if changed_hp:
        reset_filters()

    return web.json_response({"ok": True, "state": state})

async def route_restart(request):
    if state["running"]:
        stop_stream()
        await asyncio.sleep(0.3)
        ok, msg = start_stream()
        return web.json_response({"ok": ok, "msg": msg})
    return web.json_response({"ok": False, "msg": "não estava rodando"})

# ── APP ────────────────────────────────────────────────────
async def on_startup(app):
    asyncio.create_task(broadcast_level())
    log.info("VovôAmp iniciado — acesse http://vovoamp.local")

def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.router.add_get ('/',          route_index)
    app.router.add_get ('/status',    route_status)
    app.router.add_post('/start',     route_start)
    app.router.add_post('/stop',      route_stop)
    app.router.add_post('/set',       route_set)
    app.router.add_post('/restart',   route_restart)
    app.router.add_get ('/ws',        ws_handler)
    app.router.add_static('/static',  '/opt/vovoamp/static')
    return app

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("  VovôAmp v1.0 — Pi Zero 2 W")
    log.info("=" * 50)
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=80)
