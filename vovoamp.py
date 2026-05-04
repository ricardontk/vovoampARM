#!/usr/bin/env python3
"""
VovôAmp - Servidor de áudio para Raspberry Pi Zero 2 W
Bluetooth scan/connect via web + seleção de entrada de áudio
"""

import asyncio
import json
import logging
import re
import subprocess
import numpy as np
import sounddevice as sd
from aiohttp import web
import aiohttp
from scipy import signal as scipy_signal

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger("vovoamp")

SAMPLE_RATE = 44100
BLOCK_SIZE  = 1024
CHANNELS    = 1
DTYPE       = 'float32'

# ── ESTADO GLOBAL ──────────────────────────────────────────
state = {
    "running":      False,
    "gain":         3.0,
    "hp_enabled":   True,
    "hp_freq":      120,
    "comp_enabled": True,
    "gate_enabled": False,
    "gate_thresh":  -40,
    "level":        0.0,
    "input_device": None,
    "output_device": None,
}

stream      = None
ws_clients  = set()
hp_zi       = None
hp_ba       = None

# ── FILTROS ────────────────────────────────────────────────
def make_highpass(freq, fs):
    nyq  = fs / 2.0
    norm = max(0.001, min(freq / nyq, 0.999))
    b, a = scipy_signal.butter(4, norm, btype='high')
    return b, a

def reset_filters():
    global hp_zi, hp_ba
    b, a  = make_highpass(state["hp_freq"], SAMPLE_RATE)
    hp_ba = (b, a)
    hp_zi = scipy_signal.lfilter_zi(b, a) * 0.0

def apply_filters(block):
    global hp_zi
    out = block.copy()
    if state["hp_enabled"] and hp_ba is not None:
        out, hp_zi[:] = scipy_signal.lfilter(hp_ba[0], hp_ba[1], out, zi=hp_zi)
    if state["comp_enabled"]:
        threshold = 0.1
        ratio     = 8.0
        abs_out   = np.abs(out)
        mask      = abs_out > threshold
        out[mask] = np.sign(out[mask]) * (threshold + (abs_out[mask] - threshold) / ratio)
    out = out * state["gain"]
    rms = float(np.sqrt(np.mean(out ** 2)))
    db  = 20 * np.log10(rms + 1e-9)
    if state["gate_enabled"] and db < state["gate_thresh"]:
        out = out * 0.0
    out = np.clip(out, -1.0, 1.0)
    state["level"] = min(float(np.sqrt(np.mean(block ** 2))) * 6, 1.0)
    return out

# ── STREAM DE ÁUDIO ────────────────────────────────────────
def audio_callback(indata, outdata, frames, time, status):
    if status:
        log.warning(f"Audio: {status}")
    mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
    processed = apply_filters(mono)
    if outdata.ndim > 1:
        outdata[:, 0] = processed
        if outdata.shape[1] > 1:
            outdata[:, 1] = processed
    else:
        outdata[:] = processed.reshape(outdata.shape)

def get_audio_devices():
    devs = []
    for i, d in enumerate(sd.query_devices()):
        devs.append({
            "id":      i,
            "name":    d["name"],
            "inputs":  d["max_input_channels"],
            "outputs": d["max_output_channels"],
        })
    return devs

def start_stream():
    global stream
    if stream:
        stop_stream()
    reset_filters()
    try:
        stream = sd.Stream(
            samplerate = SAMPLE_RATE,
            blocksize  = BLOCK_SIZE,
            dtype      = DTYPE,
            channels   = CHANNELS,
            device     = (state["input_device"], state["output_device"]),
            callback   = audio_callback,
            latency    = 'low',
        )
        stream.start()
        state["running"] = True
        log.info("Stream iniciado ✓")
        return True, "ok"
    except Exception as e:
        state["running"] = False
        log.error(f"Erro stream: {e}")
        return False, str(e)

def stop_stream():
    global stream
    if stream:
        try: stream.stop()
        except: pass
        try: stream.close()
        except: pass
        stream = None
    state["running"] = False
    state["level"]   = 0.0

# ── BLUETOOTH ──────────────────────────────────────────────
async def bt_cmd(cmd, timeout=8):
    """Envia comandos ao bluetoothctl via subprocess"""
    try:
        proc = await asyncio.create_subprocess_exec(
            'bluetoothctl',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        cmds = f"power on\n{cmd}\nquit\n"
        stdout, _ = await asyncio.wait_for(
            proc.communicate(input=cmds.encode()), timeout=timeout
        )
        return stdout.decode(errors='replace')
    except asyncio.TimeoutError:
        return ""
    except Exception as e:
        log.error(f"bt_cmd error: {e}")
        return ""

async def bt_scan():
    """Escaneia dispositivos BT por 8 segundos"""
    try:
        proc = await asyncio.create_subprocess_exec(
            'bluetoothctl',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        cmds = b"power on\nagent on\nscan on\n"
        proc.stdin.write(cmds)
        await proc.stdin.drain()
        await asyncio.sleep(8)
        proc.stdin.write(b"scan off\nquit\n")
        await proc.stdin.drain()
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
        return stdout.decode(errors='replace')
    except Exception as e:
        log.error(f"bt_scan error: {e}")
        return ""

def parse_bt_devices(output):
    """Extrai lista de dispositivos do output do bluetoothctl"""
    devices = {}
    # Captura linhas: [NEW] Device AA:BB:CC:DD:EE:FF Nome
    for m in re.finditer(r'Device ([0-9A-F:]{17})\s+(.+)', output, re.IGNORECASE):
        mac  = m.group(1).upper()
        name = m.group(2).strip()
        if name and name != mac:
            devices[mac] = name
    return [{"mac": k, "name": v} for k, v in devices.items()]

async def get_paired_devices():
    """Lista dispositivos já pareados"""
    out = await bt_cmd("devices Paired", timeout=5)
    return parse_bt_devices(out)

async def get_connected_devices():
    """Lista dispositivos conectados"""
    out = await bt_cmd("devices Connected", timeout=5)
    return parse_bt_devices(out)

# ── ROTAS HTTP ─────────────────────────────────────────────

async def route_index(request):
    raise web.HTTPFound('/static/index.html')

async def route_status(request):
    return web.json_response({
        **state,
        "level":   round(state["level"], 3),
        "devices": get_audio_devices(),
    })

async def route_start(request):
    ok, msg = start_stream()
    return web.json_response({"ok": ok, "msg": msg})

async def route_stop(request):
    stop_stream()
    return web.json_response({"ok": True})

async def route_set(request):
    data = await request.json()
    changed_hp = False
    if "gain"         in data: state["gain"]         = float(max(1.0, min(data["gain"], 10.0)))
    if "hp_enabled"   in data: state["hp_enabled"]   = bool(data["hp_enabled"])
    if "comp_enabled" in data: state["comp_enabled"] = bool(data["comp_enabled"])
    if "gate_enabled" in data: state["gate_enabled"] = bool(data["gate_enabled"])
    if "gate_thresh"  in data: state["gate_thresh"]  = int(max(-70, min(data["gate_thresh"], -10)))
    if "input_device" in data: state["input_device"] = data["input_device"]
    if "output_device"in data: state["output_device"]= data["output_device"]
    if "hp_freq" in data:
        new = int(max(60, min(data["hp_freq"], 300)))
        if new != state["hp_freq"]:
            state["hp_freq"] = new
            changed_hp = True
    if changed_hp:
        reset_filters()
    return web.json_response({"ok": True})

async def route_restart(request):
    if state["running"]:
        stop_stream()
        await asyncio.sleep(0.3)
    ok, msg = start_stream()
    return web.json_response({"ok": ok, "msg": msg})

# ── ROTAS BLUETOOTH ────────────────────────────────────────

async def route_bt_scan(request):
    """POST /bt/scan — escaneia por 8s e retorna dispositivos encontrados"""
    log.info("Iniciando scan Bluetooth...")
    output = await bt_scan()
    devices = parse_bt_devices(output)
    paired  = await get_paired_devices()
    paired_macs = {d["mac"] for d in paired}
    for d in devices:
        d["paired"] = d["mac"] in paired_macs
    log.info(f"Scan concluído: {len(devices)} dispositivos")
    return web.json_response({"ok": True, "devices": devices})

async def route_bt_paired(request):
    """GET /bt/paired — lista pareados"""
    paired    = await get_paired_devices()
    connected = await get_connected_devices()
    connected_macs = {d["mac"] for d in connected}
    for d in paired:
        d["connected"] = d["mac"] in connected_macs
    return web.json_response({"ok": True, "devices": paired})

async def route_bt_pair(request):
    """POST /bt/pair — pareia com um dispositivo"""
    data = await request.json()
    mac  = data.get("mac", "")
    if not re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', mac):
        return web.json_response({"ok": False, "msg": "MAC inválido"})
    log.info(f"Pareando com {mac}...")
    out = await bt_cmd(f"pair {mac}", timeout=15)
    ok  = "successful" in out.lower() or "already" in out.lower()
    if ok:
        await bt_cmd(f"trust {mac}", timeout=5)
        log.info(f"Pareado com {mac} ✓")
    return web.json_response({"ok": ok, "msg": out[-200:] if not ok else "Pareado com sucesso!"})

async def route_bt_connect(request):
    """POST /bt/connect — conecta a um dispositivo pareado"""
    data = await request.json()
    mac  = data.get("mac", "")
    if not re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', mac):
        return web.json_response({"ok": False, "msg": "MAC inválido"})
    log.info(f"Conectando a {mac}...")
    out = await bt_cmd(f"connect {mac}", timeout=10)
    ok  = "successful" in out.lower() or "already" in out.lower()
    log.info(f"Conectado: {ok}")
    return web.json_response({"ok": ok, "msg": "Conectado!" if ok else out[-200:]})

async def route_bt_disconnect(request):
    """POST /bt/disconnect — desconecta"""
    data = await request.json()
    mac  = data.get("mac", "")
    out  = await bt_cmd(f"disconnect {mac}", timeout=6)
    ok   = "successful" in out.lower()
    return web.json_response({"ok": ok, "msg": "Desconectado!" if ok else out[-200:]})

async def route_bt_remove(request):
    """POST /bt/remove — remove pareamento"""
    data = await request.json()
    mac  = data.get("mac", "")
    out  = await bt_cmd(f"remove {mac}", timeout=6)
    ok   = "successful" in out.lower()
    return web.json_response({"ok": ok, "msg": "Removido!" if ok else out[-200:]})

# ── WEBSOCKET ──────────────────────────────────────────────
async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ws_clients.add(ws)
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.ERROR:
                break
    finally:
        ws_clients.discard(ws)
    return ws

async def broadcast_level():
    while True:
        await asyncio.sleep(0.05)
        if ws_clients and state["running"]:
            msg  = json.dumps({"level": round(state["level"], 3)})
            dead = set()
            for ws in ws_clients:
                try:
                    await ws.send_str(msg)
                except:
                    dead.add(ws)
            ws_clients -= dead

# ── APP ────────────────────────────────────────────────────
async def on_startup(app):
    asyncio.create_task(broadcast_level())
    log.info("VovôAmp iniciado — http://vovoamp.local")

def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.router.add_get ('/',               route_index)
    app.router.add_get ('/status',         route_status)
    app.router.add_post('/start',          route_start)
    app.router.add_post('/stop',           route_stop)
    app.router.add_post('/set',            route_set)
    app.router.add_post('/restart',        route_restart)
    app.router.add_get ('/ws',             ws_handler)
    # Bluetooth
    app.router.add_post('/bt/scan',        route_bt_scan)
    app.router.add_get ('/bt/paired',      route_bt_paired)
    app.router.add_post('/bt/pair',        route_bt_pair)
    app.router.add_post('/bt/connect',     route_bt_connect)
    app.router.add_post('/bt/disconnect',  route_bt_disconnect)
    app.router.add_post('/bt/remove',      route_bt_remove)
    app.router.add_static('/static',       '/opt/vovoamp/static')
    return app

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("  VovôAmp v2.0 — Pi Zero 2 W")
    log.info("=" * 50)
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=80)
