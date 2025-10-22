# ant_hr.py

import time
import datetime as dt
import threading
from typing import Dict, Any

# ======================= CONFIGURACIÓN =======================
# --- Real (ANT+) ---
ENABLE_WILDCARD_SCAN = True
MAX_DEDICATED_CHANNELS = 7
INACTIVITY_RELEASE_SEC = 20  # s sin datos para liberar canal dedicado

RF_FREQ = 57                 # 2457 MHz
PERIOD = 8070                # ~4.06 Hz típico HRM
DEVTYPE_HRM = 120
NETWORK_NUMBER = 0x00
NETWORK_KEY = [0xB9,0xA5,0x21,0xFB,0xBD,0x72,0xC3,0x45]

# --- Reaper / Logs ---
REAPER_SLEEP_S = 0.5         # ciclo de mantenimiento (↓ CPU)
VERBOSE = False              # ponlo a True para ver más logs informativos
# ============================================================

monotonic = time.monotonic  # alias para tiempos relativos/intervalos


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _to_bytes(p):
    try:
        return p.tobytes()
    except AttributeError:
        return bytes(p)


def vlog(msg: str):
    if VERBOSE:
        print(msg)


# -------------------- GESTOR ANT DINÁMICO --------------------
class AntDynamicManager:
    def __init__(self, state: Dict[int, dict]):
        from openant.easy.node import Node
        self.Node = Node
        self.state = state
        self.node = None
        self.wildcard_channel: Any = None
        self.channels: Dict[int, Any] = {}    # dev_id -> Channel
        self.last_seen: Dict[int, float] = {} # dev_id -> monotonic seconds
        self.lock = threading.Lock()
        self._stop = False
        self.Channel = None

        # Rearme controlado (fuera del callback)
        self._want_restart_scan = False
        self._restart_guard = False
        self._last_scan_rearm_mono = 0.0
        self._rearm_reason = "initial"  # para logs

        # Anti-thrashing / anti-latch
        self._rearmer_backoff_s = 1.2        # mínimo entre rearms
        self._ignore_after_rearm_s = 0.9     # ventana para ignorar IDs ya dedicados tras rearme
        self._ignore_until_mono = 0.0

        # Histeresis idle-latch
        self._idle_latch_hits = 0
        self._idle_latch_threshold = 3       # tras 3 hits seguidos (con backoff) se rearma
        self._last_idle_seen_set = set()

    # ---------- wildcard: abrir ----------
    def _open_scan_channel(self):
        from openant.easy.channel import Channel
        self.Channel = Channel

        def on_broadcast(payload):
            pb = _to_bytes(payload)
            if len(pb) < 13:
                return
            dev_id = pb[9] | (pb[10] << 8)
            hr = int(pb[7]) if len(pb) >= 8 else None
            if hr is None:
                return

            now_m = monotonic()
            with self.lock:
                already_dedicated = (dev_id in self.channels)

            # Ignora lecturas de IDs ya dedicados justo tras un rearme (evita latch temprano)
            if now_m < self._ignore_until_mono and already_dedicated:
                return

            # Actualiza estado y last_seen
            with self.lock:
                self.state[dev_id] = {"hr": hr, "ts": _now_iso()}
                self.last_seen[dev_id] = now_m

            # Promoción si no está dedicado
            promoted = False
            with self.lock:
                not_dedicated = (dev_id not in self.channels)
            if not_dedicated:
                promoted = self._maybe_promote(dev_id)
                # reset de histéresis al ver un no-dedicado
                self._idle_latch_hits = 0
                self._last_idle_seen_set = set()

            # Gestión idle-latch (sólo cuando vemos dedicados)
            elif already_dedicated:
                with self.lock:
                    current_set = set(self.channels.keys())
                if current_set == self._last_idle_seen_set:
                    self._idle_latch_hits += 1
                else:
                    # cambia el conjunto de dedicados vistos -> resetea contador
                    self._idle_latch_hits = 1
                    self._last_idle_seen_set = current_set

                # Rearme sólo si superamos el umbral y respetando el backoff
                if (self._idle_latch_hits >= self._idle_latch_threshold
                        and (now_m - self._last_scan_rearm_mono) > self._rearmer_backoff_s):
                    self._schedule_rearm(now_m, "idle-latch")
                    # tras programarlo, baja el contador pero no a 0 para evitar ráfagas
                    self._idle_latch_hits = 1

            # Rearme tras promoción (buscar siguiente)
            if promoted:
                self._schedule_rearm(now_m, "promoted")

        if self.wildcard_channel:
            return  # ya abierto

        ch = self.node.new_channel(self.Channel.Type.BIDIRECTIONAL_RECEIVE)
        ch.set_rf_freq(RF_FREQ)
        ch.set_period(PERIOD)
        ch.set_id(0, DEVTYPE_HRM, 0)  # wildcard
        try: ch.enable_extended_messages(True)
        except Exception: pass
        try: self.node.enable_extended_messages(True)  # type: ignore[attr-defined]
        except Exception: pass
        try: ch.set_search_timeout(255)
        except Exception: pass
        try: ch.set_low_priority_search_timeout(255)
        except Exception: pass

        ch.on_broadcast_data = on_broadcast
        ch.open()
        self.wildcard_channel = ch
        print("[ANT+] Wildcard abierto (búsqueda continua).")

    # ---------- marcar rearme (debounced) ----------
    def _schedule_rearm(self, now_m: float, reason: str = "opportunistic"):
        """Marca rearme del wildcard con anti-thrashing y guarda motivo para logs."""
        if self._want_restart_scan:
            return
        if (now_m - self._last_scan_rearm_mono) < self._rearmer_backoff_s:
            return
        self._want_restart_scan = True
        self._rearm_reason = reason

    # ---------- rearme reutilizando MISMO canal, con fallback ----------
    def _rearm_scan_channel_reusing_same(self):
        """Rearmar wildcard reutilizando el MISMO canal; si falla, cerrar+unassign+recrear."""
        if self._restart_guard:
            return
        self._restart_guard = True
        try:
            ch = self.wildcard_channel
            if not ch:
                self._open_scan_channel()
                now_m = monotonic()
                self._last_scan_rearm_mono = now_m
                self._ignore_until_mono = now_m + self._ignore_after_rearm_s
                vlog(f"[ANT+] Wildcard abierto (motivo: {getattr(self, '_rearm_reason', 'open')}).")
                return

            # Desactivar handler para evitar eventos durante el rearme
            try:
                ch.on_broadcast_data = lambda *_: None
            except Exception:
                pass

            # Intentar rearme sobre MISMO canal con pequeños reintentos
            def _try_rearm_same() -> bool:
                # Close con tolerancia a estados
                tries = 3
                for i in range(tries):
                    try:
                        ch.close()
                        break
                    except Exception as e:
                        if "CHANNEL_IN_WRONG_STATE" in str(e):
                            time.sleep(0.08 + 0.04 * i)
                        else:
                            raise
                time.sleep(0.06)

                # Reconfigurar -> abrir
                try: ch.set_rf_freq(RF_FREQ)
                except Exception: pass
                try: ch.set_period(PERIOD)
                except Exception: pass
                try: ch.set_id(0, DEVTYPE_HRM, 0)
                except Exception: pass
                try: ch.enable_extended_messages(True)
                except Exception: pass
                try: self.node.enable_extended_messages(True)  # type: ignore[attr-defined]
                except Exception: pass
                try: ch.set_search_timeout(255)
                except Exception: pass
                try: ch.set_low_priority_search_timeout(255)
                except Exception: pass

                # Reasignar handler (misma lógica que en _open_scan_channel)
                def on_broadcast(payload):
                    pb = _to_bytes(payload)
                    if len(pb) < 13:
                        return
                    dev_id = pb[9] | (pb[10] << 8)
                    hr = int(pb[7]) if len(pb) >= 8 else None
                    if hr is None:
                        return

                    now_m2 = monotonic()
                    with self.lock:
                        already_dedicated = (dev_id in self.channels)
                    if now_m2 < self._ignore_until_mono and already_dedicated:
                        return

                    with self.lock:
                        self.state[dev_id] = {"hr": hr, "ts": _now_iso()}
                        self.last_seen[dev_id] = now_m2

                    promoted2 = False
                    with self.lock:
                        not_dedicated2 = (dev_id not in self.channels)
                    if not_dedicated2:
                        promoted2 = self._maybe_promote(dev_id)
                        self._idle_latch_hits = 0
                        self._last_idle_seen_set = set()
                    elif already_dedicated:
                        with self.lock:
                            current_set = set(self.channels.keys())
                        if current_set == self._last_idle_seen_set:
                            self._idle_latch_hits += 1
                        else:
                            self._idle_latch_hits = 1
                            self._last_idle_seen_set = current_set
                        if (self._idle_latch_hits >= self._idle_latch_threshold
                                and (now_m2 - self._last_scan_rearm_mono) > self._rearmer_backoff_s):
                            self._schedule_rearm(now_m2, "idle-latch")
                            self._idle_latch_hits = 1

                    if promoted2:
                        self._schedule_rearm(now_m2, "promoted")

                ch.on_broadcast_data = on_broadcast

                tries = 3
                for i in range(tries):
                    try:
                        ch.open()
                        return True
                    except Exception as e:
                        if "CHANNEL_IN_WRONG_STATE" in str(e):
                            time.sleep(0.08 + 0.04 * i)
                        else:
                            raise
                return False

            ok = False
            try:
                ok = _try_rearm_same()
            except Exception as e:
                if "CHANNEL_IN_WRONG_STATE" not in str(e):
                    pass

            if ok:
                now_m = monotonic()
                self._last_scan_rearm_mono = now_m
                self._ignore_until_mono = now_m + self._ignore_after_rearm_s
                vlog(f"[ANT+] Wildcard rearmado (mismo canal, motivo: {getattr(self, '_rearm_reason', 'unknown')}).")
                return

            # Fallback: cerrar + unassign + recrear
            try:
                try: ch.close()
                except Exception: pass
                try: ch.unassign()
                except Exception: pass
            finally:
                self.wildcard_channel = None
                time.sleep(0.10)

            self._open_scan_channel()
            now_m = monotonic()
            self._last_scan_rearm_mono = now_m
            self._ignore_until_mono = now_m + self._ignore_after_rearm_s
            vlog(f"[ANT+] Wildcard rearmado (fallback recreate, motivo: {getattr(self, '_rearm_reason', 'unknown')}).")

        finally:
            self._restart_guard = False

    # ---------- INIT ----------
    def _init_node(self):
        self.node = self.Node()
        self.node.set_network_key(NETWORK_NUMBER, NETWORK_KEY)
        self._open_scan_channel()
        threading.Thread(target=self._reaper_loop, daemon=True).start()

    # ---------- dedicados ----------
    def _make_dedicated_handler(self, dev_id: int):
        def _h(payload):
            pb = _to_bytes(payload)
            if len(pb) >= 8:
                hr = int(pb[7])
                with self.lock:
                    self.state[dev_id] = {"hr": hr, "ts": _now_iso()}
                    self.last_seen[dev_id] = monotonic()
        return _h

    def _maybe_promote(self, dev_id: int) -> bool:
        to_close = None
        with self.lock:
            if dev_id in self.channels:
                return False
            if len(self.channels) >= MAX_DEDICATED_CHANNELS:
                to_close = min(self.channels.keys(), key=lambda d: self.last_seen.get(d, 0.0))

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
                    ch.on_broadcast_data = self._make_dedicated_handler(dev_id)
                    ch.open()
                    self.channels[dev_id] = ch
                    print(f"[ANT+] Dedicado abierto dev={dev_id} ({len(self.channels)}/{MAX_DEDICATED_CHANNELS})")
                    return True
                except Exception as e:
                    print(f"[ANT!] Error abriendo dedicado dev={dev_id}: {e}")
        return False

    def _close_channel(self, dev_id: int):
        ch = None
        with self.lock:
            ch = self.channels.pop(dev_id, None)
            self.last_seen.pop(dev_id, None)
            if dev_id in self.state:
                self.state.pop(dev_id, None)
                print(f"[HRM] Eliminado del estado dev={dev_id}")

        if not ch:
            return

        try: ch.on_broadcast_data = lambda *_: None
        except Exception: pass

        try:
            ch.close()
        except Exception as e:
            msg = str(e)
            if "CHANNEL_IN_WRONG_STATE" in msg or "error 21" in msg or " 21" in msg:
                vlog(f"[ANT] Aviso al cerrar dev={dev_id}: {msg}")
            else:
                print(f"[ANT!] Error cerrando dev={dev_id}: {e}")

        try:
            ch.unassign()
        except Exception:
            pass

        time.sleep(0.05)
        print(f"[ANT+] Dedicado liberado y desasignado dev={dev_id}")

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

            # Rearme pendiente (debounced con backoff)
            if self._want_restart_scan and (now_m - self._last_scan_rearm_mono) >= self._rearmer_backoff_s:
                self._want_restart_scan = False
                try:
                    self._rearm_scan_channel_reusing_same()
                except Exception as e:
                    print(f"[ANT] Fallo rearmando wildcard (final): {e}")

            # Watchdog: si no hay wildcard, abrirlo
            if not self.wildcard_channel:
                try:
                    self._open_scan_channel()
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

            # Cerrar y desasignar wildcard
            try:
                if self.wildcard_channel:
                    try: self.wildcard_channel.on_broadcast_data = lambda *_: None
                    except Exception: pass
                    try: self.wildcard_channel.close()
                    except Exception: pass
                    try: self.wildcard_channel.unassign()
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
    if not ENABLE_WILDCARD_SCAN:
        print("[ANT] Escaneo deshabilitado.")
        return

    mgr = AntDynamicManager(state)
    t_real = threading.Thread(target=mgr.run, daemon=True)
    t_real.start()

    # En este módulo “real” no hacemos join; el caller (server) mantiene vivo el proceso.
