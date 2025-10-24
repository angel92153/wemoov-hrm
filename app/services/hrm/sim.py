from __future__ import annotations
import time
import math
import random
from typing import Dict, Any, List


class SimHRProvider:
    """
    Simulador sencillo de frecuencia cardiaca:
    - read_current(user_id): devuelve un HR 'vivo' (onda + jitter) estable por usuario.
    - read_current_by_device(device_id): compat -> mismo comportamiento.
    - recent_devices(recent): vacío (los 'libres' sólo aplican a dispositivos reales).
    """

    def __init__(self) -> None:
        # Semilla base para que el patrón sea estable entre ejecuciones del proceso
        random.seed(42)
        self._t0 = time.time()

    def _synthetic_bpm(self, key: int) -> int:
        """
        Generador determinista por 'key' (user_id o device_id).
        Produce una onda suave con pequeña variabilidad.
        """
        t = time.time() - self._t0
        # base distinta por usuario (en 60..85), fase distinta, amplitud 6..12
        base = 60 + (hash((key, "base")) % 26)  # 60..85
        amp = 6 + (hash((key, "amp")) % 7)      # 6..12
        phase = (hash((key, "phase")) % 6283) / 1000.0  # 0..~6.283
        val = base + amp * math.sin(0.08 * t + phase) + random.uniform(-1.5, 1.5)
        return max(45, min(180, int(round(val))))

    def read_current(self, dev_or_user_id: int) -> Dict[str, int]:
        bpm = self._synthetic_bpm(int(dev_or_user_id))
        return {"bpm": bpm, "ts": int(time.time())}

    # Compatibilidad: algunos sitios llaman por device_id
    def read_current_by_device(self, device_id: int) -> Dict[str, int]:
        return self.read_current(int(device_id))

    # Para /api/unassigned_devices: el simulador no expone "dispositivos reales"
    def recent_devices(self, recent: int = 45) -> List[Dict[str, Any]]:
        return []
