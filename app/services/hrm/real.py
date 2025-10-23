# app/services/hrm/real.py
from __future__ import annotations
import time
import threading
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from .ant_hr import run_ant_listener  # tu módulo real

def _iso_to_epoch(ts_iso: str) -> Optional[int]:
    try:
        # tu ant_hr guarda UTC ISO con tzinfo → soportamos ambos
        dt = datetime.fromisoformat(ts_iso.replace("Z","+00:00"))  # por si viene con Z
        return int(dt.timestamp())
    except Exception:
        return None

class RealHRProvider:
    _started_lock = threading.Lock()
    _started = False
    _shared_state = {}

    def __init__(self):
        # Solo iniciar en el proceso hijo del reloader (o si no hay reloader)
        in_reloader_child = (os.environ.get("WERKZEUG_RUN_MAIN") == "true")
        reloader_active = ("WERKZEUG_RUN_MAIN" in os.environ)

        # Si hay reloader y estamos en el padre -> no arrancar
        if reloader_active and not in_reloader_child:
            self._state = RealHRProvider._shared_state
            return

        if not RealHRProvider._started:
            with RealHRProvider._started_lock:
                if not RealHRProvider._started:
                    run_ant_listener(RealHRProvider._shared_state)
                    RealHRProvider._started = True

        self._state = RealHRProvider._shared_state

    # ---------- API principal por device ----------
    def read_current_by_device(self, device_id: int) -> Dict[str, int]:
        """
        Devuelve la última lectura disponible para ese device.
        Si no existe, devuelve bpm=0 y ts=now (para no romper int(...) en /live).
        """
        now = int(time.time())
        try:
            d = self._state.get(int(device_id))
            if d is None:
                return {"bpm": 0, "ts": now}
            hr = int(d.get("hr") or 0)
            ts_iso = d.get("ts")
            ts_epoch = _iso_to_epoch(ts_iso) if isinstance(ts_iso, str) else now
            return {"bpm": hr, "ts": int(ts_epoch or now)}
        except Exception:
            return {"bpm": 0, "ts": now}

    # ---------- Compat: por “user_id” (usa mismo id como device) ----------
    def read_current(self, dev_or_user_id: int) -> Dict[str, int]:
        return self.read_current_by_device(int(dev_or_user_id))

    # ---------- Dispositivos vistos recientemente ----------
    def recent_devices(self, recent: int = 45) -> List[Dict[str, Any]]:
        """
        Lista dispositivos vistos en los últimos 'recent' segundos.
        Salida: [{"dev": int, "hr": int|None, "ts": epoch}, ...] ordenados por ts desc.
        """
        now = int(time.time())
        cutoff = now - int(recent)
        out: List[Dict[str, Any]] = []
        for dev, d in list(self._state.items()):
            ts_iso = d.get("ts")
            ts_epoch = _iso_to_epoch(ts_iso) if isinstance(ts_iso, str) else None
            if ts_epoch is None:
                continue
            if ts_epoch >= cutoff:
                hr_val = d.get("hr")
                hr_int = int(hr_val) if hr_val is not None else None
                out.append({"dev": int(dev), "hr": hr_int, "ts": int(ts_epoch)})
        out.sort(key=lambda x: x["ts"], reverse=True)
        return out
