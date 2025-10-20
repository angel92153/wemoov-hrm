# ant_hr.py
"""
ANT+ HRM multi-fuente (real + simulados)
- SIMULATED_DEVICES: nº de simulados (0..15)
- INCLUDE_REAL: True para usar el receptor real además de los simulados
- El estado compartido es un dict: state[dev_id] = {"hr": int, "ts": iso}
"""

import time, datetime as dt, math, random, threading

# -------- CONFIG --------
SIMULATED_DEVICES = 12   # 🔧 cambia aquí: hasta 15 simulados
INCLUDE_REAL = True      # 🔧 True = usa también el receptor real
BASE_HR = 118
AMPLITUDE = 22
NOISE = 3
UPDATE_HZ = 2
# ------------------------

def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()

# ====================== SIMULADO ======================
def _sim_thread(state: dict, n_devices: int):
    ids = [10000 + i for i in range(n_devices)]
    if not ids:
        return
    print(f"[SIM] {n_devices} dispositivos: {ids}")
    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            for dev in ids:
                hr = BASE_HR + AMPLITUDE * math.sin(2 * math.pi * 0.05 * (t + dev*0.1)) + random.uniform(-NOISE, NOISE)
                hr = int(round(max(40, min(200, hr))))
                state[dev] = {"hr": hr, "ts": _now_iso()}
            time.sleep(1 / max(1, UPDATE_HZ))
    except KeyboardInterrupt:
        print("[SIM] detenido.")

# ====================== REAL (ANT+) ======================
def _to_bytes(p):
    try: return p.tobytes()
    except AttributeError: return bytes(p)

def _real_thread(state: dict):
    from openant.easy.node import Node
    from openant.easy.channel import Channel

    NETWORK_KEY = [0xB9,0xA5,0x21,0xFB,0xBD,0x72,0xC3,0x45]
    FREQ, PERIOD, DEVTYPE = 57, 8070, 120

    node = Node()
    node.set_network_key(0x00, NETWORK_KEY)
    ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
    ch.set_period(PERIOD)
    ch.set_rf_freq(FREQ)
    ch.set_id(0, DEVTYPE, 0)
    try: ch.enable_extended_messages(True)
    except Exception: pass

    current_dev = None

    def on_broadcast(payload):
        nonlocal current_dev
        pb = _to_bytes(payload)
        if len(pb) == 13:
            current_dev = pb[9] | (pb[10] << 8)
        if len(pb) >= 8 and current_dev is not None:
            hr = pb[7]
            state[current_dev] = {"hr": int(hr), "ts": _now_iso()}

    ch.on_broadcast_data = on_broadcast
    ch.open()
    try:
        node.start()
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        try: ch.close()
        except: pass
        try: node.stop()
        except: pass
        print("[ANT+] detenido.")

# ====================== API pública ======================
def run_ant_listener(state: dict):
    """
    Lanza los hilos necesarios y actualiza 'state' con múltiples dispositivos.
    'state' es un dict compartido: {dev_id: {"hr": int, "ts": iso}}
    """
    threads = []

    if SIMULATED_DEVICES > 0:
        t_sim = threading.Thread(target=_sim_thread, args=(state, SIMULATED_DEVICES), daemon=True)
        t_sim.start()
        threads.append(t_sim)

    if INCLUDE_REAL:
        t_real = threading.Thread(target=_real_thread, args=(state,), daemon=True)
        t_real.start()
        threads.append(t_real)

    # bucle de vida
    try:
        while any(t.is_alive() for t in threads) or not threads:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

# Modo prueba: imprime el estado periódicamente
if __name__ == "__main__":
    shared = {}
    t = threading.Thread(target=run_ant_listener, args=(shared,), daemon=True)
    t.start()
    try:
        while True:
            print(shared)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
