from __future__ import annotations
import random, math, time
from typing import Dict, Any, List, Optional

from app.services.hrm.sim import SimHRProvider
try:
    from app.services.hrm.real import RealHRProvider  # type: ignore
except Exception:
    # Fallback suave para entornos sin real
    class RealHRProvider(SimHRProvider):
        def read_current_by_device(self, device_id: int):
            return self.read_current(device_id)


class _HRWave:
    """Generador simple de HR ‘vivo’ para simulados."""
    __slots__ = ("t0","phase","amp","base")
    def __init__(self, base=70, amp=8):
        self.t0 = time.time()
        self.phase = random.uniform(0, 2*math.pi)
        self.amp = amp
        self.base = base

    def bpm(self) -> int:
        t = time.time() - self.t0
        # sinus + jitter suave
        val = self.base + self.amp * math.sin(0.08 * t + self.phase) + random.uniform(-1.5, 1.5)
        return max(45, min(180, int(round(val))))


class CombinedHRProvider:
    """
    Proveedor unificado:
      - read_current_by_device(dev) -> SIEMPRE al real (si existe).
      - read_current(user_id)       -> SIEMPRE al sim (para usuarios simulados).
      - recent_devices(recent)      -> dispositivos reales recientes.
    Además, si el sim devuelve <=0, inyectamos una señal estable para que SIEMPRE “haya vida”.
    """
    def __init__(self):
        self.real = RealHRProvider()
        self.sim  = SimHRProvider()
        self._waves: dict[int, _HRWave] = {}

    # -------- Real por device ----------
    def read_current_by_device(self, device_id: int) -> Dict[str, int]:
        if hasattr(self.real, "read_current_by_device"):
            return self.real.read_current_by_device(int(device_id))
        # fallback: no debería ocurrir normalmente
        return {"bpm": 0, "ts": int(time.time())}

    # -------- Sim por usuario ----------
    def read_current(self, dev_or_user_id: int) -> Dict[str, int]:
        """Usado para usuarios simulados. Si el sim devuelve <=0, generamos HR sintético."""
        uid = int(dev_or_user_id)
        try:
            r = self.sim.read_current(uid)
            bpm = int(r.get("bpm") or 0) if isinstance(r, dict) else 0
            ts  = int(r.get("ts") or time.time()) if isinstance(r, dict) else int(time.time())
        except Exception:
            bpm, ts = 0, int(time.time())

        if bpm <= 0:
            wave = self._waves.get(uid)
            if wave is None:
                # base aleatoria (60–85) para que no todos vibren igual
                wave = _HRWave(base=random.randint(60, 85), amp=random.randint(6, 12))
                self._waves[uid] = wave
            bpm = wave.bpm()
            ts = int(time.time())

        return {"bpm": bpm, "ts": ts}

    # -------- Dispositivos reales recientes ----------
    def recent_devices(self, recent: int = 45) -> List[Dict[str, Any]]:
        if hasattr(self.real, "recent_devices"):
            return self.real.recent_devices(recent)
        return []
