"""
Simulador de HRM realista (Karvonen + autocreación opcional de usuarios simulados).

- Genera HR fisiológicamente verosímil por dispositivo siguiendo un patrón de entrenamiento.
- Usa HRmax (Tanaka) y HRrest (realista según sexo/edad o guardada en DB).
- Limpia usuarios simulados anteriores (is_sim=1) al arrancar si hay DB.
- Si auto_create_users=True:
    * Crea nuevos usuarios ficticios (is_sim=1) con datos coherentes.
"""

import time
import math
import random
import threading
import datetime as dt
from typing import Dict, Callable, Optional, List, Tuple

# ---- DB opcional ----
try:
    import db
    _HAS_DB = True
except Exception:
    _HAS_DB = False


# -------------------- Utilidades --------------------
def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

def _tanaka_hrmax(age: Optional[int]) -> float:
    """Tanaka (2001): HRmax = 208 - 0.7 × edad"""
    return 208.0 - 0.7 * float(age or 35)

def _estimate_hrrest(sexo: Optional[str], edad: Optional[int]) -> int:
    """Estimación de FC reposo realista según sexo y edad."""
    s = (sexo or "").strip().upper()
    if edad is None:
        edad = 35
    if edad < 25:
        base = 52 if s == "F" else 48
    elif edad < 40:
        base = 56 if s == "F" else 52
    elif edad < 55:
        base = 62 if s == "F" else 58
    else:
        base = 68 if s == "F" else 62
    return int(round(random.gauss(base, 3)))  # pequeña variación individual

def _clip_hr(x: float) -> int:
    return int(round(max(40.0, min(205.0, x))))

def _karvonen(hrrest: float, hrmax: float, frac: float) -> float:
    """Karvonen: HR = HRrest + frac * (HRmax - HRrest)"""
    return hrrest + frac * (hrmax - hrrest)


# -------------------- Nombres realistas --------------------
_MALE_NAMES = [
    "Álvaro","Carlos","David","Edu","Fran","Guille","Hugo","Iván","Javi","Luis",
    "Manu","Nico","Pablo","Raúl","Sergio","Tomás","Unai","Víctor","Alex","Diego"
]
_FEMALE_NAMES = [
    "Ana","Bea","Carla","Clara","Diana","Elena","Irene","Julia","Laura","Lucía",
    "Marta","Nerea","Paula","Raquel","Sara","Teresa","Vera","Noa","Sofía","Eva"
]

def _ensure_user_for_dev(dev: int) -> Optional[dict]:
    """Asegura usuario simulado en DB con hr_rest realista (marcado is_sim=1)."""
    if not _HAS_DB:
        return None

    u = db.get_user_by_device(dev)
    if u:
        return u

    rnd = random.Random(dev)
    sexo = "M" if rnd.random() < 0.5 else "F"
    edad = rnd.randint(22, 55)
    peso = rnd.uniform(55, 85) if sexo == "M" else rnd.uniform(45, 70)
    hr_rest = _estimate_hrrest(sexo, edad)

    nombre = (rnd.choice(_MALE_NAMES) if sexo == "M" else rnd.choice(_FEMALE_NAMES))
    apodo = nombre
    apellido = "Sim"

    try:
        db.create_user(
            nombre=nombre,
            apellido=apellido,
            apodo=apodo,
            edad=edad,
            peso=round(peso, 1),
            device_id=dev,
            sexo=sexo,
            hr_rest=hr_rest,
            is_sim=1
        )
    except Exception as e:
        print(f"[SIM][DB] No se pudo crear usuario para dev={dev}: {e}")

    return db.get_user_by_device(dev)


# -------------------- Plan de sesión --------------------
def _build_session_plan(seed: int) -> List[Tuple[str, float, Tuple[float, float]]]:
    """Secuencia (fase, duración_s, rango_frac_HRR)."""
    rnd = random.Random(seed)
    return [
        ("warmup", rnd.uniform(120, 240), (0.45, 0.60)),
        ("steady", rnd.uniform(180, 360), (0.65, 0.80)),
        ("intervals", rnd.uniform(240, 420), (0.70, 0.90)),
        ("cooldown", rnd.uniform(90, 180), (0.40, 0.55)),
    ]


# -------------------- Simulador por dispositivo --------------------
class _DeviceSim:
    def __init__(self, dev_id: int, user_fetcher: Optional[Callable[[int], Optional[dict]]],
                 update_hz: float, seed: int):
        self.dev = dev_id
        self.user_fetcher = user_fetcher
        self.hz = max(1.0, float(update_hz))
        self.dt = 1.0 / self.hz
        self.rnd = random.Random(seed)

        user = (self.user_fetcher(self.dev) if self.user_fetcher else None) or {}
        self.sex = (user.get("sexo") or "M").lower()
        self.age = user.get("edad") or 35
        self.weight = user.get("peso") or 70.0
        self.hrrest = user.get("hr_rest") or _estimate_hrrest(self.sex, self.age)
        self.hrmax = _tanaka_hrmax(self.age)

        self.plan = _build_session_plan(seed)
        self.phase_idx = 0
        self.phase_elapsed = 0.0
        self.hr = float(self.hrrest + 3.0)
        self.target_frac = 0.5

        self.tau_rise = self.rnd.uniform(6.0, 9.0)
        self.tau_fall = self.rnd.uniform(4.0, 7.0)
        self.noise_sigma = 1.5
        self.artifact_prob = 0.02
        self.artifact_bpm = 3.0

    def _advance_phase_if_needed(self):
        name, dur, _ = self.plan[self.phase_idx]
        if self.phase_elapsed >= dur:
            self.phase_idx = (self.phase_idx + 1) % len(self.plan)
            self.phase_elapsed = 0.0

    def _pick_target_frac(self) -> float:
        name, _dur, (fmin, fmax) = self.plan[self.phase_idx]
        frac = self.rnd.uniform(fmin, fmax)
        frac += 0.02 * math.sin(2 * math.pi * 0.2 * self.phase_elapsed)
        frac = max(0.35, min(0.95, frac))
        if "interval" in name:
            period = self.rnd.uniform(20, 40)
            saw = (self.phase_elapsed % period) / period
            frac = (0.9 if saw > 0.5 else 0.65)
        return frac

    def _step(self) -> int:
        self.phase_elapsed += self.dt
        self._advance_phase_if_needed()
        self.target_frac = self._pick_target_frac()
        target = _karvonen(self.hrrest, self.hrmax, self.target_frac)

        tau = self.tau_rise if target > self.hr else self.tau_fall
        alpha = 1.0 - math.exp(-self.dt / max(0.001, tau))
        self.hr += alpha * (target - self.hr)

        self.hr += self.rnd.gauss(0.0, self.noise_sigma)
        if self.rnd.random() < self.artifact_prob:
            self.hr += self.rnd.uniform(-self.artifact_bpm, self.artifact_bpm)

        return _clip_hr(self.hr)


# -------------------- Bucle principal --------------------
def _sim_loop(state: Dict[int, dict], device_ids: List[int],
              update_hz: float, user_fetcher: Optional[Callable[[int], Optional[dict]]],
              seed: Optional[int]):
    rnd = random.Random(seed)
    print(f"[SIM] {len(device_ids)} dispositivos: {device_ids}")
    sims = [_DeviceSim(dev, user_fetcher, update_hz, seed=rnd.randrange(1_000_000)) for dev in device_ids]
    dt = 1.0 / max(1.0, float(update_hz))
    try:
        while True:
            t0 = time.time()
            for sim in sims:
                state[sim.dev] = {"hr": sim._step(), "ts": _now_iso()}
            elapsed = time.time() - t0
            time.sleep(max(0.0, dt - elapsed))
    except KeyboardInterrupt:
        print("[SIM] detenido.")


# -------------------- Interfaz pública --------------------
def start_simulator(state: Dict[int, dict],
                    n_devices: int = 2,
                    update_hz: float = 2.0,
                    base_id: int = 10000,
                    device_ids: Optional[List[int]] = None,
                    user_provider: Optional[Callable[[int], Optional[dict]]] = None,
                    auto_create_users: bool = True,
                    seed: Optional[int] = None,
                    cleanup_on_start: bool = True) -> Optional[threading.Thread]:
    """
    Lanza un hilo daemon con el simulador y lo devuelve.
    - Siempre que se llama (y hay DB), limpia usuarios simulados previos si cleanup_on_start=True.
    - Si n_devices==0 (o device_ids vacío), NO lanza hilo ni escribe en STATE.
    """
    # 🔹 limpiar usuarios simulados anteriores SIEMPRE que se llame
    if cleanup_on_start and _HAS_DB:
        try:
            db.clear_simulated_users()
        except Exception as e:
            print(f"[SIM][DB] No se pudieron limpiar usuarios simulados previos: {e}")

    # construir IDs si no vienen
    if device_ids is None:
        device_ids = [base_id + i for i in range(max(0, n_devices))]

    # si no hay dispositivos, no arrancamos hilo
    if not device_ids:
        print("[SIM] Limpieza realizada. Simulador desactivado (0 dispositivos).")
        return None

    def _db_fetcher(dev: int) -> Optional[dict]:
        return db.get_user_by_device(dev) if _HAS_DB else None

    user_fetcher = None
    if user_provider:
        user_fetcher = user_provider
    elif auto_create_users and _HAS_DB:
        for dev in device_ids:
            _ensure_user_for_dev(dev)
        user_fetcher = _db_fetcher
    elif _HAS_DB:
        user_fetcher = _db_fetcher

    t = threading.Thread(
        target=_sim_loop,
        args=(state, device_ids, update_hz, user_fetcher, seed),
        daemon=True
    )
    t.start()
    return t
