# app/services/hrm/ant_hr.py
"""
Listener ANT+ para pulsómetros (HRM) con:
- Canal wildcard de escaneo continuo.
- Promoción a canales dedicados (hasta MAX_DEDICATED_CHANNELS).
- Limpieza de dedicados por inactividad.
- Estado compartido: state[dev_id] = {"hr": int, "ts": ISO-8601 UTC}.

Uso desde RealHRProvider:
    run_ant_listener(state_dict_compartido)

Requisitos:
    pip install openant
En Windows, usa Zadig para poner WinUSB/libusbK en el dongle ANT.
"""

from __future__ import annotations
import time
import datetime as dt
import threading
from typing import Dict, Any

# ======================= CONFIGURACIÓN =======================
ENABLE_WILDCARD_SCAN   = True
MAX_DEDICATED_CHANNELS = 7
INACTIVITY_RELEASE_SEC = 20    # s sin datos para liberar canal dedicado

# Radio (ANT+ HRM típico)
RF_FREQ       = 57             # 2457 MHz
PERIOD        = 8070           # ~4.06 Hz
DEVTYPE_HRM   = 120
NETWORK_NUM   = 0x00
NETWORK_KEY   = [0xB9,0xA5,0x21,0xFB,0xBD,0x72,0xC3,0x45]  # clave pública ANT+

# Mantenimiento
REAPER_SLEEP_S = 0.5
VERBOSE        = False
# ============================================================

monotonic = time.monotonic  # alias para tiempos relativos/intervalos


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _to_bytes(p) -> bytes:
    try:
        return p.tobytes()
    except AttributeError:
        return bytes(p)


def vlog(msg: str):
    if VERBOSE:
        print(msg)


class AntDynamicManager:
    """
    Gestiona:
      - un canal wildcard de escaneo
      - canales dedicados por device_id
      - tabla de último visto y estado con HR
    """
    def __init__(self, state: Dict[int, dict]):
        from openant.easy.node import Node
        self.Node = Node
        self.state = state
        self.node = None
        self.Channel = None
        self.wildcard = None                 # Channel de escaneo
        self.channels: Dict[int, Any] = {}   # dev_id -> Channel dedicado
        self.last_seen: Dict[int, float] = {}# dev_id -> monotonic
        self.lock = threading.Lock()
        self._stop = False

    # ---------- INIT ----------
    def _init_node(self):
        self.node = self.Node()
        self.node.set_network_key(NETWORK_NUM, NETWORK_KEY)
        self._open_wildcard()
        threading.Thread(target=self._reaper_loop, daemon=True).start()

    # ---------- wildcard ----------
    def _open_wildcard(self):
        from openant.easy.channel import Channel
        self.Channel = Channel

        def on_broadcast(payload):
            pb = _to_bytes(payload)
            # HR suele venir en pb[7]; con extended (len>=13) podemos extraer device_id:
            hr = int(pb[7]) if len(pb) >= 8 else None
            if hr is None:
                return

            # Si el paquete es "extended" (>=13 bytes), bytes 9-10 = device_id
            dev_id = None
            if len(pb) >= 13:
                dev_id = pb[9] | (pb[10] << 8)

            # Si no hay extended no podemos promover a dedicado (no conocemos dev_id),
            # pero al menos dejamos el HR "anónimo" fuera (no lo guardamos).
            if dev_id is None:
                vlog("[ANT] Paquete sin extended; no se puede promover (revisa driver/libusb si es habitual).")
                return

            now_m = monotonic()
            with self.lock:
                self.state[dev_id] = {"hr": hr, "ts": _now_iso()}
                self.last_seen[dev_id] = now_m
                is_dedicated = (dev_id in self.channels)

            if not is_dedicated:
                self._maybe_promote(dev_id)

        if self.wildcard:
            return

        ch = self.node.new_channel(self.Channel.Type.BIDIRECTIONAL_RECEIVE)
        ch.set_rf_freq(RF_FREQ)
        ch.set_period(PERIOD)
        ch.set_id(0, DEVTYPE_HRM, 0)  # wildcard
        # Extended en canal (Node puede no exponerlo en algunas versiones de openant; en canal basta)
        try:
            ch.enable_extended_messages(True)
            vlog("[ANT] Extended habilitado en Channel")
        except Exception as e:
            print(f"[ANT] No se pudo habilitar extended en Channel: {e}")

        try: ch.set_search_timeout(255)
        except Exception: pass
        try: ch.set_low_priority_search_timeout(255)
        except Exception: pass

        ch.on_broadcast_data = on_broadcast
        ch.open()
        self.wildcard = ch
        print("[ANT+] Wildcard abierto (búsqueda continua).")

    # ---------- dedicados ----------
    def _dedicated_handler(self, dev_id: int):
        def _h(payload):
            pb = _to_bytes(payload)
            if len(pb) >= 8:
                hr = int(pb[7])
                with self.lock:
                    self.state[dev_id] = {"hr": hr, "ts": _now_iso()}
                    self.last_seen[dev_id] = monotonic()
        return _h

    def _maybe_promote(self, dev_id: int):
        """Abre un canal dedicado para dev_id si hay hueco."""
        with self.lock:
            if dev_id in self.channels:
                return
            if len(self.channels) >= MAX_DEDICATED_CHANNELS:
                # libera el menos reciente
                to_close = min(self.channels.keys(), key=lambda d: self.last_seen.get(d, 0.0))
            else:
                to_close = None

        if to_close is not None and to_close != dev_id:
            self._close_channel(to_close)

        with self.lock:
            if len(self.channels) < MAX_DEDICATED_CHANNELS and dev_id not in self.channels:
                try:
                    ch = self.node.new_channel(self.Channel.Type.BIDIRECTIONAL_RECEIVE)
                    ch.set_period(PERIOD)
                    ch.set_rf_freq(RF_FREQ)
                    ch.set_id(dev_id, DEVTYPE_HRM, 0)
                    try: ch.enable_extended_messages(True)
                    except Exception: pass
                    ch.on_broadcast_data = self._dedicated_handler(dev_id)
                    ch.open()
                    self.channels[dev_id] = ch
                    print(f"[ANT+] Dedicado abierto dev={dev_id} ({len(self.channels)}/{MAX_DEDICATED_CHANNELS})")
                except Exception as e:
                    print(f"[ANT!] Error abriendo dedicado dev={dev_id}: {e}")

    def _close_channel(self, dev_id: int):
        ch = None
        with self.lock:
            ch = self.channels.pop(dev_id, None)
            self.last_seen.pop(dev_id, None)
            self.state.pop(dev_id, None)

        if not ch:
            return

        try: ch.on_broadcast_data = lambda *_: None
        except Exception: pass

        try: ch.close()
        except Exception as e:
            vlog(f"[ANT] Aviso al cerrar dev={dev_id}: {e}")
        try: ch.unassign()
        except Exception: pass

        vlog(f"[ANT] Dedicado liberado dev={dev_id}")

    # ---------- mantenimiento ----------
    def _reaper_loop(self):
        while not self._stop:
            now_m = monotonic()
            to_free = []
            with self.lock:
                for dev in list(self.channels.keys()):
                    last = self.last_seen.get(dev, 0.0)
                    if last and (now_m - last) > INACTIVITY_RELEASE_SEC:
                        to_free.append(dev)

            for dev in to_free:
                self._close_channel(dev)

            # Watchdog wildcard
            if not self.wildcard:
                try:
                    self._open_wildcard()
                except Exception as e:
                    print(f"[ANT] Watchdog: no se pudo abrir wildcard: {e}")

            time.sleep(REAPER_SLEEP_S)

    # ---------- ciclo principal ----------
    def run(self):
        self._init_node()
        try:
            self.node.start()
            print("[ANT+] Nodo iniciado. Escuchando HRM…")
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop = True
            with self.lock:
                devs = list(self.channels.keys())

            for dev in devs:
                self._close_channel(dev)

            # Cerrar wildcard
            try:
                if self.wildcard:
                    try: self.wildcard.on_broadcast_data = lambda *_: None
                    except Exception: pass
                    try: self.wildcard.close()
                    except Exception: pass
                    try: self.wildcard.unassign()
                    except Exception: pass
            except Exception:
                pass

            try:
                if self.node:
                    self.node.stop()
            except Exception:
                pass

            print("[ANT+] Parado.")


# -------------------- LANZADOR PÚBLICO --------------------
def run_ant_listener(state: dict):
    """
    Inicia el listener en un hilo daemon y devuelve inmediatamente.
    `state` es un dict compartido desde RealHRProvider.
    """
    if not ENABLE_WILDCARD_SCAN:
        print("[ANT] Escaneo deshabilitado.")
        return

    mgr = AntDynamicManager(state)
    t = threading.Thread(target=mgr.run, daemon=True)
    t.start()
