# app/services/sim_hr.py
from __future__ import annotations
import time
import math
import random
from typing import Dict, Any, List, Optional, Tuple


class SimHRProvider:
    """
    Simulador de HR con objetivos **absolutos en BPM** pensados para un
    público medio de 35 años (HRmáx ≈ 185 bpm). Sin porcentajes.
    Fases:
      - idle (sin sesión)
      - countdown
      - warmup
      - transition (demo / transiciones)
      - cooldown
      - block (entrenamiento)

    Propiedades:
      • Rampa suave (sube más rápido que baja).
      • Variabilidad muy contenida (onda + jitter pequeños).
      • Techos por fase para evitar overshoot (warm-up nunca > 140 bpm, etc.).
      • Ligera variación por usuario (±3 bpm) para que no todos sean idénticos.
    """

    # ========= Clasificación por clave/color de fase =========
    KEYS_WARMUP = {"warmup", "warm_up", "warm up", "calentamiento"}
    KEYS_TRANSITION = {"transition", "transición", "transicion", "demo", "demos", "transitions", "t1", "t2", "t3"}
    KEYS_COOLDOWN = {"cooldown", "cool_down", "cool down", "vuelta a la calma"}
    KEYS_BLOCK = {
        "block", "bloque", "train", "sweat", "burn", "work",
        "z2", "z3", "z4", "z5", "b1", "b2", "b3", "b4"
    }

    # Colores (nuevo esquema)
    COLOR_GREEN  = "#16a34a"  # warm up
    COLOR_PURPLE = "#6b21a8"  # bloques
    COLOR_BLUE   = "#1d4ed8"  # demo / transiciones / cooldown

    # Sugerencias por color (si no hay match por clave). Nota: BLUE sirve para
    # transition y cooldown; si solo se conoce el color, se prioriza "transition".
    COLOR_HINTS = {
        "warmup":     (COLOR_GREEN,),
        "block":      (COLOR_PURPLE,),
        "transition": (COLOR_BLUE,),
        # "cooldown": (COLOR_BLUE,),  # el key lo distingue; por color solo, cae en transition
    }

    # ========= Objetivos ABSOLUTOS por fase (BPM) =========
    # Basados en HRmáx ≈ 185. Ajusta si tu público típico es distinto.
    TARGET_BPM = {
        "idle":       80,   # ~43% de 185, reposo “activo” en sala
        "countdown":  80,   # ~43%
        "warmup":     130,  # ~70%
        "transition": 115,  # ~62%
        "cooldown":   100,  # ~54%
        "block":      167,  # ~90%
    }

    # Techos por fase (BPM) para evitar picos visuales
    CAP_BPM = {
        "idle":       115,
        "countdown":  118,
        "warmup":     140,
        "transition": 125,
        "cooldown":   122,
        "block":      175,
    }

    # ========= Variabilidad muy contenida =========
    # Onda lenta para no “temblar”
    WAVE_OMEGA = 0.06
    VAR_SCALE = 0.55  # escala global de amplitud

    # Amplitud base por fase (bpm) — baja
    AMP_BY_CAT = {
        "idle":        1.2,
        "countdown":   1.2,
        "warmup":      2.0,
        "transition":  1.6,
        "cooldown":    1.6,
        "block":       3.0,
    }

    # Jitter por fase (rangos cortos, en bpm)
    JITTER_BY_CAT = {
        "idle":       (-0.4, 0.6),
        "countdown":  (-0.4, 0.6),
        "warmup":     (-0.5, 0.7),
        "transition": (-0.5, 0.7),
        "cooldown":   (-0.5, 0.7),
        "block":      (-0.6, 0.8),
    }

    # ========= Rampas y límites absolutos =========
    MAX_RISE_BPM_PER_S = 3.0
    MAX_FALL_BPM_PER_S = 2.0
    MIN_BPM = 45
    MAX_BPM = 200

    def __init__(self) -> None:
        random.seed(42)
        self._t0 = time.time()

        # Estado de fase actual
        self._category: str = "idle"
        self._phase_key: Optional[str] = None
        self._phase_color: Optional[str] = None
        self._phase_changed_at: float = self._t0

        # Estado por usuario: bpm y ts
        # key -> {"bpm": float, "t": float}
        self._state: Dict[int, Dict[str, float]] = {}

    # ----------------- API pública -----------------
    def update_from_status(self, status: Dict[str, Any]) -> None:
        """
        Lee SESSION.status() y decide la categoría actual.
        """
        if (not status.get("active")) and status.get("show_countdown"):
            self._set_category("countdown", phase_key="COUNTDOWN", phase_color="#ffffff")
            return

        if status.get("active"):
            pkey = (status.get("phase_key") or "").strip()
            pcol = (status.get("phase_color") or "").strip()
            cat = self._classify_phase(pkey, pcol)
            self._set_category(cat, phase_key=pkey, phase_color=pcol)
            return

        self._set_category("idle", phase_key=None, phase_color=None)

    def read_current(self, dev_or_user_id: int) -> Dict[str, int]:
        """
        Genera HR con rampa hacia el objetivo ABSOLUTO en BPM de la categoría,
        con onda y jitter discretos y techos por fase.
        """
        key = int(dev_or_user_id)
        tnow = time.time()

        target = self._target_for_category(key)         # objetivo BPM (con micro offset de usuario)
        curr, last_t = self._get_or_init_state(key, target, tnow)

        # Rampa (limitada por dt)
        dt = max(1/30, tnow - last_t)
        rise = self.MAX_RISE_BPM_PER_S * dt
        fall = self.MAX_FALL_BPM_PER_S * dt
        diff = target - curr
        step = min(diff, rise) if diff >= 0 else max(diff, -fall)
        curr += step

        # Onda + jitter (muy discretos)
        amp = self._amplitude_for_category(key) * self.VAR_SCALE
        shift = self._phase_shift_for_user(key)
        wave = amp * math.sin(self.WAVE_OMEGA * (tnow - self._t0) + shift)

        jlo, jhi = self.JITTER_BY_CAT.get(self._category, (-0.5, 0.7))
        jitter = random.uniform(jlo, jhi)

        val = curr + wave + jitter

        # Techo por fase (evita que warmup o cooldown se “cuelen” arriba)
        val = self._cap_by_category(val)

        bpm = int(round(max(self.MIN_BPM, min(self.MAX_BPM, val))))

        # Persistir estado
        self._state[key] = {"bpm": float(bpm), "t": tnow}

        return {"bpm": bpm, "ts": int(tnow)}

    def read_current_by_device(self, device_id: int) -> Dict[str, int]:
        return self.read_current(int(device_id))

    def recent_devices(self, recent: int = 45) -> List[Dict[str, Any]]:
        return []

    # ----------------- Internas -----------------
    def _set_category(self, cat: str, phase_key: Optional[str], phase_color: Optional[str]) -> None:
        cat = cat or "idle"
        if cat != self._category:
            self._category = cat
            self._phase_changed_at = time.time()
        self._phase_key = phase_key
        self._phase_color = phase_color

    def _classify_phase(self, phase_key: str, phase_color: str) -> str:
        k = (phase_key or "").strip().casefold()
        c = (phase_color or "").strip().casefold()

        # 1) Por clave explícita
        if k in self.KEYS_BLOCK:
            return "block"
        if k in self.KEYS_WARMUP:
            return "warmup"
        if k in self.KEYS_COOLDOWN:
            return "cooldown"
        if k in self.KEYS_TRANSITION:
            return "transition"

        # 2) Por color sugerido (si no hay clave)
        if c:
            for cat, hints in self.COLOR_HINTS.items():
                for h in hints:
                    if c.startswith(h.casefold()):
                        return cat

        # 3) Heurísticas por substring
        if any(s in k for s in ("block", "bloque", "train", "sweat", "burn", "z3", "z4", "z5", "b1", "b2", "b3", "b4")):
            return "block"
        if any(s in k for s in ("warm", "calent", "prep")):
            return "warmup"
        if "cool" in k or "vuelta" in k:
            return "cooldown"
        if "demo" in k or "trans" in k or k.startswith("t"):
            return "transition"

        # 4) Fallback: transition
        return "transition"

    def _target_for_category(self, key: int) -> float:
        """
        Objetivo absoluto por fase + ligero offset por usuario (±3 bpm)
        para que no todos sean idénticos.
        """
        base = float(self.TARGET_BPM.get(self._category, 115))
        # offsets más discretos en reposo; un pelín más en block
        uoff = self._user_offset(key)
        if self._category in ("idle", "countdown", "cooldown", "transition"):
            base += max(-2, min(2, uoff))
        elif self._category == "warmup":
            base += max(-2, min(2, uoff))
        else:  # block
            base += max(-3, min(3, uoff))
        # Impulso inicial muy discreto en block para que “arranque”
        if self._category == "block":
            secs = time.time() - self._phase_changed_at
            if secs < 6:
                base += max(0.0, 3.0 - 0.6 * secs)  # +0..~3 bpm
        return base

    def _cap_by_category(self, hr_value: float) -> float:
        cap = float(self.CAP_BPM.get(self._category, 180))
        return min(hr_value, cap)

    def _amplitude_for_category(self, key: int) -> float:
        # Amplitud base por fase, con ligera dispersión por usuario (0..+0.6)
        cat_amp = float(self.AMP_BY_CAT.get(self._category, 1.6))
        user_bump = (hash((key, "amp_small")) % 7) * 0.1  # 0.0..0.6
        if self._category in ("idle", "countdown"):
            return min(1.6, cat_amp + user_bump * 0.4)
        return min(3.5, cat_amp + user_bump * 0.7)

    def _user_offset(self, key: int) -> int:
        # Offset discreto por usuario: -3..+3 bpm
        return (hash((key, "uoff")) % 7) - 3

    def _phase_shift_for_user(self, key: int) -> float:
        return (hash((key, "phase")) % 6283) / 1000.0

    def _get_or_init_state(self, key: int, target: float, tnow: float) -> Tuple[float, float]:
        st = self._state.get(key)
        if st is None:
            # Empieza muy cerca del objetivo para evitar saltos
            init = float(target + random.uniform(-2.0, 2.0))
            self._state[key] = {"bpm": init, "t": tnow}
            return init, tnow
        return float(st["bpm"]), float(st["t"])
