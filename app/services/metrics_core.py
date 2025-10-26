# app/services/metrics_core.py
from __future__ import annotations
from typing import Optional, Tuple, Dict

# ─────────────────────────────────────────────────────────────
# Variables globales (configurables vía init_from_app)
# ─────────────────────────────────────────────────────────────
KCAL_MODE = "gross"        # "net" | "gross"
MET_REST  = 1.0            # MET basal (kcal/kg/h)
_SESSION_MODE = "mixed"    # "cardio" | "strength" | "mixed"

# No retro-integrar huecos mayores a este umbral (ms)
INTEGRATE_MAX_GAP_MS = 2000  # override con LIVE_INTEGRATE_MAX_GAP_MS en app.config

# Historial HR (configurable)
HR_HISTORY_ENABLED = True
HR_HISTORY_MAX_SAMPLES = 4000      # ~algo más de 1h si 1 muestra/s
HR_HISTORY_KEEP_MS = 3_600_000     # 1 hora

# Política de muestreo
HR_HISTORY_SAMPLE_MIN_MS = 1000    # mínimo 1s entre muestras
HR_HISTORY_MIN_DELTA = 2           # sólo guardar si cambia ≥2 bpm
HR_HISTORY_ON_ZONE_CHANGE = True   # siempre guardar si cambia la zona
+HISTORY_PURGE_ON_SESSION_END = False  # si True, no se purga por tiempo; se limpia al acabar sesión

# Zonas por %HRR (Karvonen)
HRR_Z1 = 0.50  # <50% HRR
HRR_Z2 = 0.60  # 50–59%
HRR_Z3 = 0.70  # 60–69%
HRR_Z4 = 0.80  # 70–89%
HRR_Z5 = 0.90  # ≥90%

# Zonas por %HRmax
HMX_Z1 = 0.60  # <60% HRmax
HMX_Z2 = 0.70  # 60–69%
HMX_Z3 = 0.80  # 70–79%
HMX_Z4 = 0.90  # 80–89%
# Z5 ≥90% HRmax

# Puntos por zona (se recalcula en init_from_app usando tu Config objetivo)
_MOOV_RATE: dict[str, float] = {
    "Z1": 0.0,
    "Z2": 10.0,
    "Z3": 10.0,
    "Z4": 20.0,
    "Z5": 20.0,
}

# Ajustes energéticos por intensidad (para SESSION_MODE="mixed"/"strength")
MIXED_ADJ_Z5      = 1.20
MIXED_ADJ_Z4      = 1.10
MIXED_ADJ_REC_HRR = 0.90  # <50% HRR
MIXED_ADJ_REC_HMX = 0.90  # <60% HRmax
STRENGTH_ADJ      = 1.30


# ─────────────────────────────────────────────────────────────
# Inicialización desde Flask app
# ─────────────────────────────────────────────────────────────
def configure_simple_points(target_points: int,
                            target_minutes_moderate: int,
                            intense_equiv: float = 2.0,
                            z2_factor: float = 0.66,
                            z3_factor: float = 1.33) -> None:
    global _MOOV_RATE
    tp = float(target_points)
    tm = float(target_minutes_moderate)
    k  = float(intense_equiv)
    f2 = float(z2_factor)
    f3 = float(z3_factor)
    if tp <= 0 or tm <= 0 or k <= 0:
        return

    rate_mod = tp / tm
    rate_int = rate_mod * k

    _MOOV_RATE = {
        "Z1": 0.0,
        "Z2": rate_mod * f2,
        "Z3": rate_mod * f3,
        "Z4": rate_int,
        "Z5": rate_int,
    }

def init_from_app(app) -> None:
    """
    Cargar parámetros desde app.config UNA sola vez al arrancar la app.
    """
    global KCAL_MODE, MET_REST, _SESSION_MODE, INTEGRATE_MAX_GAP_MS
    global HR_HISTORY_ENABLED, HR_HISTORY_MAX_SAMPLES, HR_HISTORY_KEEP_MS
    global HR_HISTORY_SAMPLE_MIN_MS, HR_HISTORY_MIN_DELTA, HR_HISTORY_ON_ZONE_CHANGE

    cfg = app.config

    KCAL_MODE     = cfg.get("KCAL_MODE", "gross")
    MET_REST      = float(cfg.get("MET_REST", 1.0))
    _SESSION_MODE = cfg.get("SESSION_MODE", "mixed")

    # Anti retro-integración
    INTEGRATE_MAX_GAP_MS = int(cfg.get("LIVE_INTEGRATE_MAX_GAP_MS", 2000))

    # Historial HR
    HR_HISTORY_ENABLED       = bool(cfg.get("HR_HISTORY_ENABLED", True))
    HR_HISTORY_MAX_SAMPLES   = int(cfg.get("HR_HISTORY_MAX_SAMPLES", 4000))
    HR_HISTORY_KEEP_MS       = int(cfg.get("HR_HISTORY_KEEP_MS", 3_600_000))
    HR_HISTORY_SAMPLE_MIN_MS = int(cfg.get("HR_HISTORY_SAMPLE_MIN_MS", 1000))
    HR_HISTORY_MIN_DELTA     = int(cfg.get("HR_HISTORY_MIN_DELTA", 2))
    HR_HISTORY_ON_ZONE_CHANGE= bool(cfg.get("HR_HISTORY_ON_ZONE_CHANGE", True))
    global HISTORY_PURGE_ON_SESSION_END
    HISTORY_PURGE_ON_SESSION_END = bool(cfg.get("HR_HISTORY_PURGE_ON_END", False))

    configure_simple_points(
        target_points           = int(cfg.get("MOOV_TARGET_POINTS", 3000)),
        target_minutes_moderate = int(cfg.get("MOOV_TARGET_MINUTES_MOD", 300)),
        intense_equiv           = float(cfg.get("MOOV_INTENSE_EQUIV", 2.0)),
        z2_factor               = float(cfg.get("MOOV_Z2_FACTOR", 0.66)),
        z3_factor               = float(cfg.get("MOOV_Z3_FACTOR", 1.33)),
    )


# ─────────────────────────────────────────────────────────────
# Utilidades tiempo/edad
# ─────────────────────────────────────────────────────────────
def _age_from_dob_iso(dob: Optional[str]) -> Optional[int]:
    if not dob:
        return None
    try:
        y, m, d = map(int, dob.split("-"))
        from datetime import date as _date
        b = _date(y, m, d)
        today = _date.today()
        years = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        return max(0, years)
    except Exception:
        return None

def _edad_from_user(user: Optional[dict]) -> Optional[int]:
    if not user:
        return None
    e_dob = _age_from_dob_iso(user.get("dob"))
    if isinstance(e_dob, int):
        return e_dob
    e = user.get("edad")
    return int(e) if isinstance(e, int) and e >= 0 else None


# ─────────────────────────────────────────────────────────────
# HRmax
# ─────────────────────────────────────────────────────────────
def hrmax_estimada(edad: int | None) -> int:
    if isinstance(edad, int) and edad > 0:
        return int(round(208 - 0.7 * edad))
    return 190

def hrmax_from_user_or_estimada(edad: Optional[int], hrmax_user: Optional[int]) -> int:
    if isinstance(hrmax_user, int) and 120 <= hrmax_user <= 230:
        return hrmax_user
    return hrmax_estimada(edad)


# ─────────────────────────────────────────────────────────────
# Elección de método
# ─────────────────────────────────────────────────────────────
def pick_method(user: Optional[dict]) -> Tuple[str, Optional[int]]:
    if user:
        hr_rest = user.get("hr_rest")
        if isinstance(hr_rest, int) and 30 <= hr_rest <= 100:
            return "hrr", hr_rest
    return "hrmax", None


# ─────────────────────────────────────────────────────────────
# Fracciones de intensidad
# ─────────────────────────────────────────────────────────────
def frac_hrr(hr: Optional[int], hr_max: int, hr_rest: int) -> float:
    if hr is None or hr_max <= 0:
        return 0.0
    hrr = max(1, hr_max - hr_rest)
    return max(0.0, min(1.0, (hr - hr_rest) / hrr))

def frac_hrmax(hr: Optional[int], hr_max: int) -> float:
    if hr is None or hr_max <= 0:
        return 0.0
    return max(0.0, min(1.0, hr / float(hr_max)))


# ─────────────────────────────────────────────────────────────
# Zonas
# ─────────────────────────────────────────────────────────────
def zone_code_from_frac(frac: float, method: str) -> str:
    if method == "hrr":
        if   frac < HRR_Z1: return "Z1"
        elif frac < HRR_Z2: return "Z2"
        elif frac < HRR_Z3: return "Z3"
        elif frac < HRR_Z4: return "Z4"
        elif frac < HRR_Z5: return "Z4"
        else:               return "Z5"
    else:  # hrmax
        if   frac < HMX_Z1: return "Z1"
        elif frac < HMX_Z2: return "Z2"
        elif frac < HMX_Z3: return "Z3"
        elif frac < HMX_Z4: return "Z4"
        else:               return "Z5"

def _zone_to_int(z: str) -> int:
    return {"Z1":1,"Z2":2,"Z3":3,"Z4":4,"Z5":5}.get(z, 1)

def _int_to_zone(i: int) -> str:
    return {1:"Z1",2:"Z2",3:"Z3",4:"Z4",5:"Z5"}.get(int(i), "Z1")


# ─────────────────────────────────────────────────────────────
# Calorías (Keytel 2005)
# ─────────────────────────────────────────────────────────────
def kcal_per_min_keytel(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None) -> float:
    edad = edad if isinstance(edad, int) else 30
    peso = float(peso_kg) if (isinstance(peso_kg, (int, float)) and peso_kg > 0) else 70.0
    s = (sexo or "").strip().upper()
    if s == "F":
        return (-20.4022 + 0.4472 * hr - 0.1263 * peso + 0.0740 * edad) / 4.184
    else:
        return (-55.0969 + 0.6309 * hr + 0.1988 * peso + 0.2017 * edad) / 4.184

def kcal_adjustment_factor(frac: float, method: str, mode: str) -> float:
    m = (mode or "cardio").lower()
    if m == "cardio":
        return 1.0
    if m == "strength":
        return STRENGTH_ADJ
    if method == "hrr":
        if frac >= HRR_Z5:  return MIXED_ADJ_Z5
        if frac >= HRR_Z4:  return MIXED_ADJ_Z4
        if frac < HRR_Z1:   return MIXED_ADJ_REC_HRR
        return 1.0
    else:
        if frac >= HMX_Z4:  return MIXED_ADJ_Z5
        if frac >= HMX_Z3:  return MIXED_ADJ_Z4
        if frac < HMX_Z1:   return MIXED_ADJ_REC_HMX
        return 1.0

def basal_kcal_per_min(edad: int | None, peso_kg: float | None, sexo: str | None,
                       met_rest: float = MET_REST) -> float:
    try:
        peso = float(peso_kg) if (isinstance(peso_kg, (int, float)) and peso_kg > 0) else 70.0
        met = float(met_rest) if met_rest and met_rest > 0 else 1.0
    except Exception:
        peso, met = 70.0, 1.0
    return max(0.0, met * peso / 60.0)

def kcal_per_min_adjusted(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None,
                          frac: float, method: str, mode: str = "mixed") -> float:
    base = kcal_per_min_keytel(hr, edad, peso_kg, sexo)
    adj = kcal_adjustment_factor(frac, method, mode)
    return max(0.0, base * adj)

def kcal_per_min_total(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None,
                       frac: float, method: str, mode: str | None = None) -> float:
    eff_mode = (mode or _SESSION_MODE)
    active = kcal_per_min_adjusted(hr, edad, peso_kg, sexo, frac, method, eff_mode)
    if KCAL_MODE == "gross":
        basal = basal_kcal_per_min(edad, peso_kg, sexo, MET_REST)
        return max(0.0, active + basal)
    return active


# ─────────────────────────────────────────────────────────────
# Puntos por zona
# ─────────────────────────────────────────────────────────────
def moov_rate_per_min_from_zone(zone: str) -> float:
    return float(_MOOV_RATE.get(zone, 0.0))
