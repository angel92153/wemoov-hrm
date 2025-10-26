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
from typing import Dict, Any, List

# ======================= CONFIGURACIÓN =======================
ENABLE_WILDCARD_SCAN   = True

# Límite lógico de dedicados en este gestor
MAX_DEDICATED_CHANNELS = 7

# Límite físico típico del dongle ANT: 8 canales totales (0..7).
# Deja margen por si el stack usa alguno extra; por defecto reservamos 1 para wildcard.
MAX_TOTAL_CHANNELS     = 8
MAX_DEDICATED_SAFE     = min(MAX_DEDICATED_CHANNELS, max(0, MAX_TOTAL_CHANNELS - 1))

INACTIVITY_RELEASE_SEC = 20    # s sin datos para liberar canal dedicado

# Radio (ANT+ HRM típico)
RF_FREQ       = 57             # 2457 MHz
PERIOD        = 8070           # ~4.06 Hz
DEVTYPE_HRM   = 120
NETWORK_NUM   = 0x00
NETWORK_KEY   = [0xB9,0xA5,0x21,0xFB,0xBD,0x72,0xC3,0x45]  # clave pública ANT+

# Mantenimiento
REAPER_SLEEP_S = 0.8  # menos wakeups -> menos CPU

# Verbose de depuración (True para flood de logs)
VERBOSE        = False

# ---------- Rearme y anti-latch (workaround para wildcard “pegajoso”) ----------
# Rearma el wildcard tras promocionar y cuando detectamos que solo “vemos”
# IDs ya dedicados durante varios ciclos.
REARM_BACKOFF_S = 3.0            # mínimo entre rearms (debounce) -> subido para menos ruido
IGNORE_AFTER_REARM_S = 0.9       # ventana post-rearme para ignorar IDs ya dedicados
IDLE_LATCH_HITS_THRESHOLD = 3    # si solo vemos dedicados N veces seguidas -> candidatos a rearme
IDLE_GRACE_S = 10.0              # no rearme por idle-latch si hubo dev nuevo reciente
MAX_IDLE_REARMS_PER_MIN = 6      # rate limit de rearms por idle-latch por minuto
# ============================================================

# ---------- Antirace / Debounce de promoción ----------
PROMOTE_DEBOUNCE_S = 0.30        # no intentar promover el mismo dev dentro de esta ventana

# ---------- Debounce de escritura de estado ----------
MIN_STATE_UPDATE_INTERVAL_S = 0.4 # limita escrituras de state por dev para bajar lock/CPU

monotonic = time.monotonic  # alias para tiempos relativos/intervalos


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _to_bytes(p) -> bytes:
    try:
        return p.tobytes()
    except AttributeError:
        return bytes(p)


# ---- Logs
def vlog(msg: str):
    if VERBOSE:
        print(msg)


def ilog(msg: str):
    print(msg)


def wlog(msg: str):
    print(f"[ANT][WARN] {msg}")


def elog(msg: str):
    print(f"[ANT][ERROR] {msg}")


# ---- Número/etiqueta de canal
def _channel_num(ch) -> str:
    """Intenta extraer el 'channel number' para logs."""
    for attr in ("number", "_channel_number", "channel_number"):
        if hasattr(ch, attr):
            try:
                val = getattr(ch, attr)
                return str(val() if callable(val) else val)
            except Exception:
                pass
    return "?"


def _channel_label(ch, synthetic_id: int | None = None) -> str:
    """
    Etiqueta amigable para logs. Si hay número real, lo muestra (#N).
    Si no, usa un ID sintético estable (ch@SYN).
    """
    n = _channel_num(ch)
    if n != "?":
        return f"#{n}"
    return f"ch@{synthetic_id if synthetic_id is not None else hex(id(ch))}"


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

        # ---- Estado para rearme controlado del wildcard ----
        self._want_restart_scan = False
        self._restart_guard = False
        self._last_scan_rearm_mono = 0.0
        self._ignore_until_mono = 0.0
        self._rearm_reason = "initial"

        # Histeresis para detectar “idle-latch”
        self._idle_latch_hits = 0
        self._last_idle_seen_set = set()

        # ---- Antirace para promoción ----
        self._promoting = set()              # dev_ids en promoción en curso
        self._last_promoted_at: Dict[int, float] = {}  # dev_id -> monotonic (debounce)

        # ---- Rate limit de rearms por idle-latch ----
        self._idle_rearms_timestamps: List[float] = []

        # ---- IDs sintéticos para canales (cuando la lib no expone número) ----
        self._ch_seq = 0                     # contador de IDs sintéticos
        self._ch_ids: Dict[Any, int] = {}    # Channel -> synthetic_id

        # ---- Timestamps para minimizar escrituras en state ----
        self._last_state_write_mono: Dict[int, float] = {}  # dev_id -> monotonic
        # ---- Último dev nuevo visto (para gracia de idle) ----
        self._last_new_dev_mono: float = 0.0

    # ---------- helpers internos ----------
    def _assign_synth_id(self, ch) -> int:
        sid = self._ch_ids.get(ch)
        if sid is not None:
            return sid
        self._ch_seq += 1
        sid = self._ch_seq
        self._ch_ids[ch] = sid
        return sid

    def _rate_limit_idle_rearm(self, now_m: float) -> bool:
        """Devuelve True si está permitido rearmar por idle-latch (aplica rate limit)."""
        # purge de entradas viejas (más de 60 s)
        self._idle_rearms_timestamps = [t for t in self._idle_rearms_timestamps if now_m - t < 60.0]
        if len(self._idle_rearms_timestamps) >= MAX_IDLE_REARMS_PER_MIN:
            return False
        self._idle_rearms_timestamps.append(now_m)
        return True

    def _update_state(self, dev_id: int, hr: int, now_m: float):
        """
        Actualiza state[dev_id] con un mínimo intervalo entre escrituras
        para reducir lock/CPU bajo ráfagas de paquetes.
        """
        last_w = self._last_state_write_mono.get(dev_id, 0.0)
        if now_m - last_w < MIN_STATE_UPDATE_INTERVAL_S:
            # Aun así, actualizamos last_seen para que el reaper no libere
            self.last_seen[dev_id] = now_m
            return
        self.state[dev_id] = {"hr": hr, "ts": _now_iso()}
        self.last_seen[dev_id] = now_m
        self._last_state_write_mono[dev_id] = now_m

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
            # HR en pb[7]; con extended (len>=13) podemos extraer device_id (pb[9..10])
            if len(pb) < 13:
                return
            hr = int(pb[7]) if len(pb) >= 8 else None
            if hr is None:
                return
            dev_id = pb[9] | (pb[10] << 8)

            now_m = monotonic()
            with self.lock:
                already_dedicated = (dev_id in self.channels)

            # Evita re-engancharse al recién promocionado justo tras rearme
            if now_m < self._ignore_until_mono and already_dedicated:
                return

            # Actualiza estado y last_seen (con debounce de escritura)
            with self.lock:
                self._update_state(dev_id, hr, now_m)

            promoted = False
            with self.lock:
                not_dedicated = (dev_id not in self.channels)

            if not_dedicated:
                self._maybe_promote(dev_id)
                promoted = True
                # reset de histéresis al ver un no-dedicado
                self._idle_latch_hits = 0
                self._last_idle_seen_set = set()
                # marcamos momento de "nuevo dev" (para gracia de idle)
                self._last_new_dev_mono = now_m
            else:
                # Solo vemos dedicados -> posible “idle-latch”
                with self.lock:
                    current_set = set(self.channels.keys())
                if current_set == self._last_idle_seen_set:
                    self._idle_latch_hits += 1
                else:
                    self._idle_latch_hits = 1
                    self._last_idle_seen_set = current_set

                if (self._idle_latch_hits >= IDLE_LATCH_HITS_THRESHOLD
                        and (now_m - self._last_scan_rearm_mono) > REARM_BACKOFF_S
                        and (now_m - self._last_new_dev_mono) > IDLE_GRACE_S):
                    # aplica rate limit
                    with self.lock:
                        allowed = self._rate_limit_idle_rearm(now_m)
                    if allowed:
                        self._schedule_rearm(now_m, "idle-latch")
                    # deja el contador en 1 para evitar ráfagas
                    self._idle_latch_hits = 1

            # Tras promocionar, agenda rearme para buscar más HRM
            if promoted:
                self._schedule_rearm(now_m, "promoted")

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
            wlog(f"No se pudo habilitar extended en Channel: {e}")

        try:
            ch.set_search_timeout(255)
        except Exception as e:
            wlog(f"set_search_timeout(255) falló: {e}")
        try:
            # algunas versiones de openant no lo tienen
            if hasattr(ch, "set_low_priority_search_timeout"):
                ch.set_low_priority_search_timeout(255)
            else:
                vlog("set_low_priority_search_timeout() no disponible en esta versión de Channel")
        except Exception as e:
            wlog(f"set_low_priority_search_timeout(255) falló: {e}")

        ch.on_broadcast_data = on_broadcast
        ch.open()
        self.wildcard = ch
        sid = self._assign_synth_id(ch)
        ilog(f"[ANT+] Wildcard abierto (búsqueda continua) en canal {_channel_label(ch, sid)}.")

    # ---------- rearme: marcado (debounced) ----------
    def _schedule_rearm(self, now_m: float, reason: str = "opportunistic"):
        if self._want_restart_scan:
            return
        if (now_m - self._last_scan_rearm_mono) < REARM_BACKOFF_S:
            return
        self._want_restart_scan = True
        self._rearm_reason = reason

    # ---------- rearme: reusar MISMO canal con fallback a recrear ----------
    def _rearm_wildcard_reuse_same(self):
        if self._restart_guard:
            return
        self._restart_guard = True
        try:
            ch = self.wildcard
            if not ch:
                self._open_wildcard()
                now_m = monotonic()
                self._last_scan_rearm_mono = now_m
                self._ignore_until_mono = now_m + IGNORE_AFTER_REARM_S
                ilog(f"[ANT+] Wildcard abierto (motivo: {self._rearm_reason}).")
                return

            # silencia callbacks durante el rearme
            try:
                ch.on_broadcast_data = lambda *_: None
            except Exception:
                pass

            # 1) intentar rearme sobre el mismo canal
            ok = False
            try:
                # close tolerante a estados
                for i in range(3):
                    try:
                        ch.close()
                        break
                    except Exception as e:
                        if "CHANNEL_IN_WRONG_STATE" in str(e):
                            time.sleep(0.08 + 0.04 * i)
                        else:
                            wlog(f"Wildcard close() aviso: {e}")
                            break
                time.sleep(0.06)

                # reconfigurar y reabrir
                for setter, name, val in (
                    (ch.set_rf_freq, "set_rf_freq", RF_FREQ),
                    (ch.set_period, "set_period", PERIOD),
                    (ch.set_id, "set_id", (0, DEVTYPE_HRM, 0)),
                ):
                    try:
                        if name == "set_id":
                            setter(*val)
                        else:
                            setter(val)
                    except Exception as e:
                        wlog(f"Wildcard {name} fallo durante rearme: {e}")
                try:
                    ch.enable_extended_messages(True)
                except Exception as e:
                    wlog(f"Wildcard enable_extended_messages fallo rearme: {e}")
                try:
                    ch.set_search_timeout(255)
                except Exception as e:
                    wlog(f"Wildcard set_search_timeout fallo rearme: {e}")
                try:
                    if hasattr(ch, "set_low_priority_search_timeout"):
                        ch.set_low_priority_search_timeout(255)
                    else:
                        vlog("set_low_priority_search_timeout() no disponible (rearme)")
                except Exception as e:
                    wlog(f"Wildcard set_low_priority_search_timeout fallo rearme: {e}")

                # reenganchar handler del wildcard (misma lógica que _open_wildcard)
                def on_broadcast(payload):
                    pb = _to_bytes(payload)
                    if len(pb) < 13:
                        return
                    hr = int(pb[7]) if len(pb) >= 8 else None
                    if hr is None:
                        return
                    dev_id = pb[9] | (pb[10] << 8)
                    now_m2 = monotonic()
                    with self.lock:
                        already_dedicated = (dev_id in self.channels)
                    if now_m2 < self._ignore_until_mono and already_dedicated:
                        return
                    with self.lock:
                        self._update_state(dev_id, hr, now_m2)
                    promoted2 = False
                    with self.lock:
                        not_dedicated2 = (dev_id not in self.channels)
                    if not_dedicated2:
                        self._maybe_promote(dev_id)
                        promoted2 = True
                        self._idle_latch_hits = 0
                        self._last_idle_seen_set = set()
                        self._last_new_dev_mono = now_m2
                    else:
                        with self.lock:
                            current_set = set(self.channels.keys())
                        if current_set == self._last_idle_seen_set:
                            self._idle_latch_hits += 1
                        else:
                            self._idle_latch_hits = 1
                            self._last_idle_seen_set = current_set
                        if (self._idle_latch_hits >= IDLE_LATCH_HITS_THRESHOLD
                                and (now_m2 - self._last_scan_rearm_mono) > REARM_BACKOFF_S
                                and (now_m2 - self._last_new_dev_mono) > IDLE_GRACE_S):
                            with self.lock:
                                allowed = self._rate_limit_idle_rearm(now_m2)
                            if allowed:
                                self._schedule_rearm(now_m2, "idle-latch")
                            self._idle_latch_hits = 1
                    if promoted2:
                        self._schedule_rearm(now_m2, "promoted")
                ch.on_broadcast_data = on_broadcast

                for i in range(3):
                    try:
                        ch.open()
                        ok = True
                        break
                    except Exception as e:
                        if "CHANNEL_IN_WRONG_STATE" in str(e):
                            time.sleep(0.08 + 0.04 * i)
                        else:
                            wlog(f"Wildcard open() durante rearme: {e}")
                            break
            except Exception as e:
                wlog(f"Rearme wildcard (reusar): {e}")
                ok = False

            if ok:
                now_m = monotonic()
                self._last_scan_rearm_mono = now_m
                self._ignore_until_mono = now_m + IGNORE_AFTER_REARM_S
                ilog(f"[ANT+] Wildcard rearmado (mismo canal {_channel_label(ch, self._ch_ids.get(ch))}, motivo: {self._rearm_reason}).")
                return

            # 2) fallback: cerrar+unassign+recrear
            try:
                try:
                    ch.close()
                except Exception as e:
                    wlog(f"Wildcard close() fallback: {e}")
                try:
                    ch.unassign()
                except Exception as e:
                    wlog(f"Wildcard unassign() fallback: {e}")
            finally:
                self.wildcard = None
                time.sleep(0.10)

            self._open_wildcard()
            now_m = monotonic()
            self._last_scan_rearm_mono = now_m
            self._ignore_until_mono = now_m + IGNORE_AFTER_REARM_S
            sid = self._ch_ids.get(self.wildcard)
            ilog(f"[ANT+] Wildcard rearmado (fallback recreate {_channel_label(self.wildcard, sid)}, motivo: {self._rearm_reason}).")
        finally:
            self._restart_guard = False

    # ---------- dedicados ----------
    def _dedicated_handler(self, dev_id: int):
        def _h(payload):
            pb = _to_bytes(payload)
            if len(pb) >= 8:
                hr = int(pb[7])
                now_m = monotonic()
                with self.lock:
                    self._update_state(dev_id, hr, now_m)
        return _h

    def _maybe_promote(self, dev_id: int):
        """Abre un canal dedicado para dev_id si hay hueco, con antirace + tope total."""
        now_m = monotonic()

        # Debounce por dev (reduce ráfagas de promoción)
        if self._last_promoted_at.get(dev_id, 0.0) + PROMOTE_DEBOUNCE_S > now_m:
            vlog(f"[ANT] Debounce promoción dev={dev_id}")
            return

        pass_to_close = None

        with self.lock:
            # Si ya está en canales o en promoción, salimos.
            if dev_id in self.channels or dev_id in self._promoting:
                return

            # Tope duro: no exceder dedicados seguros si wildcard está presente
            if self.wildcard is not None and len(self.channels) >= MAX_DEDICATED_SAFE:
                # libera el menos reciente (si es diferente a dev_id)
                to_close = min(self.channels.keys(), key=lambda d: self.last_seen.get(d, 0.0))
                if to_close != dev_id:
                    pass_to_close = to_close
                else:
                    # Si el menos reciente es el mismo (raro), no cerramos para no ciclar
                    vlog(f"[ANT] Tope de dedicados alcanzado; no hay candidato distinto a cerrar para dev={dev_id}")
                    return

            # Marca este dev como en promoción (antirace)
            self._promoting.add(dev_id)

        # Cerrar fuera del lock para evitar bloqueos largos
        if pass_to_close is not None:
            self._close_channel(pass_to_close)

        try:
            # Doble-check bajo lock por si otra ruta lo añadió mientras cerrábamos
            with self.lock:
                if dev_id in self.channels:
                    return

            ch = self.node.new_channel(self.Channel.Type.BIDIRECTIONAL_RECEIVE)
            ch.set_period(PERIOD)
            ch.set_rf_freq(RF_FREQ)
            ch.set_id(dev_id, DEVTYPE_HRM, 0)
            try:
                ch.enable_extended_messages(True)
            except Exception as e:
                wlog(f"Dedicated enable_extended_messages dev={dev_id}: {e}")
            ch.on_broadcast_data = self._dedicated_handler(dev_id)
            ch.open()

            # Commit bajo lock, con doble-check
            with self.lock:
                if dev_id not in self.channels:
                    self.channels[dev_id] = ch
                    self._last_promoted_at[dev_id] = now_m
                    sid = self._assign_synth_id(ch)
                    ilog(f"[ANT+] Dedicado abierto dev={dev_id} "
                         f"({_channel_label(ch, sid)}; {len(self.channels)}/{MAX_DEDICATED_SAFE})")
                else:
                    # Otro hilo lo agregó: cerramos el duplicado para no “comernos” un canal
                    try:
                        ch.on_broadcast_data = lambda *_: None
                    except Exception:
                        pass
                    try:
                        ch.close()
                    except Exception as e:
                        wlog(f"Cierre duplicado dev={dev_id}: close() {e}")
                    try:
                        ch.unassign()
                    except Exception as e:
                        wlog(f"Cierre duplicado dev={dev_id}: unassign() {e}")
                    vlog(f"[ANT] Dedicado duplicado dev={dev_id} descartado.")
        except Exception as e:
            elog(f"Error abriendo dedicado dev={dev_id}: {e}")
        finally:
            with self.lock:
                self._promoting.discard(dev_id)

    def _close_channel(self, dev_id: int):
        ch = None
        with self.lock:
            ch = self.channels.pop(dev_id, None)
            self.last_seen.pop(dev_id, None)
            self.state.pop(dev_id, None)
            self._last_state_write_mono.pop(dev_id, None)

        if not ch:
            return

        chlbl = _channel_label(ch, self._ch_ids.get(ch))

        try:
            ch.on_broadcast_data = lambda *_: None
        except Exception as e:
            wlog(f"dev={dev_id} canal {chlbl} limpiar callback: {e}")

        try:
            ch.close()
        except Exception as e:
            wlog(f"dev={dev_id} canal {chlbl} close(): {e}")
        try:
            ch.unassign()
        except Exception as e:
            wlog(f"dev={dev_id} canal {chlbl} unassign(): {e}")

        vlog(f"[ANT] Dedicado liberado dev={dev_id} (canal {chlbl})")

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
                ilog(f"[ANT] Inactividad: liberando dev={dev}")
                self._close_channel(dev)

            # Rearme pendiente (debounced con backoff)
            if self._want_restart_scan and (now_m - self._last_scan_rearm_mono) >= REARM_BACKOFF_S:
                self._want_restart_scan = False
                try:
                    self._rearm_wildcard_reuse_same()
                except Exception as e:
                    elog(f"Fallo rearmando wildcard: {e}")

            # Watchdog wildcard
            if not self.wildcard:
                try:
                    self._open_wildcard()
                except Exception as e:
                    elog(f"Watchdog: no se pudo abrir wildcard: {e}")

            time.sleep(REAPER_SLEEP_S)

    # ---------- ciclo principal ----------
    def run(self):
        self._init_node()
        try:
            self.node.start()
            ilog("[ANT+] Nodo iniciado. Escuchando HRM…")
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
                    try:
                        self.wildcard.on_broadcast_data = lambda *_: None
                    except Exception as e:
                        wlog(f"Wildcard limpiar callback: {e}")
                    try:
                        self.wildcard.close()
                    except Exception as e:
                        wlog(f"Wildcard close(): {e}")
                    try:
                        self.wildcard.unassign()
                    except Exception as e:
                        wlog(f"Wildcard unassign(): {e}")
            except Exception as e:
                wlog(f"Wildcard teardown: {e}")

            try:
                if self.node:
                    self.node.stop()
            except Exception as e:
                wlog(f"Node stop(): {e}")

            ilog("[ANT+] Parado.")


# -------------------- LANZADOR PÚBLICO --------------------
def run_ant_listener(state: dict):
    """
    Inicia el listener en un hilo daemon y devuelve inmediatamente.
    `state` es un dict compartido desde RealHRProvider.
    """
    if not ENABLE_WILDCARD_SCAN:
        ilog("[ANT] Escaneo deshabilitado.")
        return

    mgr = AntDynamicManager(state)
    t = threading.Thread(target=mgr.run, daemon=True)
    t.start()
