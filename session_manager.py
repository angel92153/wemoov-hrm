"""
session_manager.py

Gestor independiente de sesiones/clases:
- Catálogo de clases (fases con nombre, color y duración).
- Estado de sesión (activa, programada).
- API de control: start/stop/schedule/unschedule/status
- Sin dependencias de Flask, solo tiempo.

Usado por hr_server.py para exponer endpoints /session/* y /sessions (UI).
"""

import time
from typing import Optional, Dict, Any, List

# ======================= COLORES =======================
COLOR_GREEN  = "#16a34a"  # warm up
COLOR_PURPLE = "#6b21a8"  # demo / transiciones
COLOR_YELLOW = "#eab308"  # bloques
COLOR_BLUE   = "#1d4ed8"  # cooldown
COLOR_RED    = "#b91c1c"  # hi-intensity (ejemplo otros modelos)

# ======================= CATÁLOGO DE CLASES =======================
# Puedes añadir más modelos o editar existentes. key/label aparecen en la UI.
SESSION_CATALOG: Dict[str, Dict[str, Any]] = {
    "moov": {
        "label": "Moov Class (estándar)",
        "phases": [
            {"key": "WARM UP",  "dur_s": 3*60,  "color": COLOR_GREEN},
            {"key": "DEMO",     "dur_s": 3*60,  "color": COLOR_PURPLE},
            {"key": "B1",       "dur_s": 9*60,  "color": COLOR_YELLOW},
            {"key": "T1",       "dur_s": 2*60,  "color": COLOR_PURPLE},
            {"key": "B2",       "dur_s": 9*60,  "color": COLOR_YELLOW},
            {"key": "T2",       "dur_s": 3*60,  "color": COLOR_PURPLE},
            {"key": "B3",       "dur_s": 9*60,  "color": COLOR_YELLOW},
            {"key": "T3",       "dur_s": 2*60,  "color": COLOR_PURPLE},
            {"key": "B4",       "dur_s": 9*60,  "color": COLOR_YELLOW},
            {"key": "COOLDOWN", "dur_s": 3*60,  "color": COLOR_BLUE},
        ],
    },

    # Ejemplo de otro modelo (HIIT corto)
    "hiit10": {
        "label": "HIIT 10'",
        "phases": [
            {"key": "WARM UP", "dur_s": 2*60, "color": COLOR_GREEN},
            {"key": "INT 1",   "dur_s": 60,   "color": COLOR_RED},
            {"key": "REC 1",   "dur_s": 60,   "color": COLOR_PURPLE},
            {"key": "INT 2",   "dur_s": 60,   "color": COLOR_RED},
            {"key": "REC 2",   "dur_s": 60,   "color": COLOR_PURPLE},
            {"key": "INT 3",   "dur_s": 60,   "color": COLOR_RED},
            {"key": "REC 3",   "dur_s": 60,   "color": COLOR_PURPLE},
            {"key": "COOLDOWN","dur_s": 2*60, "color": COLOR_BLUE},
        ],
    },
}

def list_class_models() -> List[Dict[str, Any]]:
    """
    Devuelve una lista amigable para APIs/UI: [{id, label, total_s, phases:[{...}]}]
    """
    out = []
    for cid, meta in SESSION_CATALOG.items():
        phases = meta.get("phases", [])
        total = sum(p["dur_s"] for p in phases)
        out.append({
            "id": cid,
            "label": meta.get("label", cid),
            "total_s": total,
            "phases": phases,
        })
    # orden alfabético por label
    out.sort(key=lambda x: x["label"].lower())
    return out

# ======================= SESSION MANAGER =======================

class SessionManager:
    def __init__(self):
        self.reset()

    # -------- Estado ----------
    def reset(self):
        self.active: bool = False
        self.class_id: Optional[str] = None
        self.start_ts: Optional[float] = None
        self.phases: Optional[list] = None
        self.scheduled_ts: Optional[float] = None
        self.lead_s: int = 0

    # -------- Helpers internos ----------
    def _now(self) -> float:
        return time.time()

    def _get_phases(self, class_id: Optional[str]) -> list:
        if not class_id:
            return []
        meta = SESSION_CATALOG.get(class_id)
        return list(meta.get("phases", [])) if meta else []

    def _total_dur(self, phases: Optional[list]) -> int:
        return sum(p["dur_s"] for p in (phases or []))

    def _progress(self, phases, start_ts, now=None):
        """
        Calcula progreso dentro de la sesión.
        return: (elapsed_s, total_s, phase_idx, phase_elapsed, phase_remaining, phase_dict)
        phase_idx = None si terminó.
        """
        if not phases or start_ts is None:
            return (0, 0, None, 0, 0, None)
        if now is None:
            now = self._now()
        total = self._total_dur(phases)
        elapsed = max(0, int(now - start_ts))
        if elapsed >= total:
            return (total, total, None, 0, 0, None)

        acc = 0
        for i, ph in enumerate(phases):
            dur = ph["dur_s"]
            if elapsed < acc + dur:
                phase_elapsed = elapsed - acc
                phase_remaining = (acc + dur) - elapsed
                return (elapsed, total, i, phase_elapsed, phase_remaining, ph)
            acc += dur

        return (total, total, None, 0, 0, None)

    def _maybe_autostart(self):
        """Arranca si hay programación y llegamos a la hora."""
        if self.active or self.scheduled_ts is None:
            return
        if self._now() >= self.scheduled_ts:
            phases = self._get_phases(self.class_id or "moov")
            self.active = True
            self.start_ts = self._now()
            self.phases = phases

    # -------- API Pública ----------
    def start(self, class_id: str = "moov"):
        """Inicia una clase inmediatamente."""
        if class_id not in SESSION_CATALOG:
            raise ValueError(f"Clase desconocida: {class_id}")
        self.active = True
        self.class_id = class_id
        self.start_ts = self._now()
        self.phases = self._get_phases(class_id)
        # anula programación
        self.scheduled_ts = None
        self.lead_s = 0

    def stop(self):
        """Detiene clase y borra programación."""
        self.reset()

    def schedule(self, class_id: str, start_epoch: float, lead_s: int = 0):
        """Programa una clase futura (epoch en segundos)."""
        if class_id not in SESSION_CATALOG:
            raise ValueError("class_id inválido")
        if start_epoch <= self._now():
            raise ValueError("La hora debe ser futura")
        self.class_id = class_id
        self.scheduled_ts = float(start_epoch)
        self.lead_s = max(0, int(lead_s))
        self.active = False
        self.start_ts = None
        self.phases = None

    def unschedule(self):
        """Cancela programación (si la hay)."""
        self.scheduled_ts = None
        self.lead_s = 0

    def status(self) -> dict:
        """
        Devuelve el estado de sesión actual.
        - Si programada: countdown y bandera show_countdown según lead_s.
        - Si activa: fase, tiempo restante de fase, progreso total.
        """
        self._maybe_autostart()
        now = self._now()
        resp = {
            "active": self.active,
            "class_id": self.class_id,
            "scheduled_ts": self.scheduled_ts,
            "lead_s": self.lead_s,
            "now": now,
        }

        # programada (aún no activa)
        if not self.active and self.scheduled_ts is not None:
            delta = int(self.scheduled_ts - now)
            resp["countdown_s"] = max(0, delta)
            resp["show_countdown"] = (delta <= self.lead_s)
            resp["total_dur_s"] = self._total_dur(self._get_phases(self.class_id or "moov"))
            return resp

        # activa
        if self.active:
            phases = self.phases or self._get_phases(self.class_id or "moov")
            elapsed, total, idx, phase_elapsed, phase_rem, phase = self._progress(phases, self.start_ts, now)
            if idx is None:
                # terminó -> apagar
                self.stop()
                resp.update({"active": False, "finished": True})
            else:
                resp.update({
                    "elapsed_s": elapsed,
                    "total_s": total,
                    "phase_idx": idx,
                    "phase_key": phase["key"],
                    "phase_remaining_s": phase_rem,
                    "phase_elapsed_s": phase_elapsed,
                    "phase_color": phase["color"],
                })
            return resp

        # inactiva y sin programación
        resp["active"] = False
        return resp


# Instancia global para usar desde el servidor
SESSION = SessionManager()
