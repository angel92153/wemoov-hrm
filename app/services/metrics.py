# app/services/metrics.py
from __future__ import annotations
from datetime import datetime, date, timezone
from typing import Optional, Tuple

# ─────────────────────────────────────────────────────────────
# Variables globales (se cargan en init_from_app)
# ─────────────────────────────────────────────────────────────
KCAL_MODE = "gross"   # "net" | "gross"
MET_REST  = 1.0       # MET basal (kcal/kg/h)
_SESSION_MODE = "mixed"  # "cardio" | "strength" | "mixed"

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
MIXED_ADJ_Z5    = 1.20
MIXED_ADJ_Z4    = 1.10
MIXED_ADJ_REC_HRR = 0.90  # <50% HRR
MIXED_ADJ_REC_HMX = 0.90  # <60% HRmax
STRENGTH_ADJ    = 1.30


# ─────────────────────────────────────────────────────────────
# Inicialización desde Flask app
# ─────────────────────────────────────────────────────────────
def configure_simple_points(target_points: int,
                            target_minutes_moderate: int,
                            intense_equiv: float = 2.0,
                            z2_factor: float = 0.66,
                            z3_factor: float = 1.33) -> None:
    """
    Fija puntos/min por zona a partir del objetivo:
      - rate_mod = target_points / target_minutes_moderate
      - Z2 = rate_mod * z2_factor
      - Z3 = rate_mod * z3_factor
      - Z4 = Z5 = rate_mod * intense_equiv
      - Z1 = 0
    """
    global _MOOV_RATE
    tp = float(target_points)
    tm = float(target_minutes_moderate)
    k  = float(intense_equiv)
    f2 = float(z2_factor)
    f3 = float(z3_factor)
    if tp <= 0 or tm <= 0 or k <= 0:
        return

    rate_mod = tp / tm          # p.ej., 3000/300 = 10 pts/min
    rate_int = rate_mod * k     # p.ej., 10*2 = 20 pts/min

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
    Requiere que se llame desde create_app() dentro de app.app_context().
    """
    global KCAL_MODE, MET_REST, _SESSION_MODE
    cfg = app.config

    KCAL_MODE    = cfg.get("KCAL_MODE", "gross")
    MET_REST     = float(cfg.get("MET_REST", 1.0))
    _SESSION_MODE = cfg.get("SESSION_MODE", "mixed")

    configure_simple_points(
        target_points= int(cfg.get("MOOV_TARGET_POINTS", 3000)),
        target_minutes_moderate= int(cfg.get("MOOV_TARGET_MINUTES_MOD", 300)),
        intense_equiv= float(cfg.get("MOOV_INTENSE_EQUIV", 2.0)),
        z2_factor= float(cfg.get("MOOV_Z2_FACTOR", 0.66)),
        z3_factor= float(cfg.get("MOOV_Z3_FACTOR", 1.33)),
    )


# ─────────────────────────────────────────────────────────────
# Utilidades tiempo/edad
# ─────────────────────────────────────────────────────────────
def _parse_ts(ts_iso: str | None):
    """ISO (con o sin Z) → datetime UTC | None."""
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def _age_from_dob_iso(dob: Optional[str]) -> Optional[int]:
    """'YYYY-MM-DD' → edad en años (entera) o None si inválido."""
    if not dob:
        return None
    try:
        y, m, d = map(int, dob.split("-"))
        b = date(y, m, d)
        today = date.today()
        years = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
        return max(0, years)
    except Exception:
        return None

def _edad_from_user(user: Optional[dict]) -> Optional[int]:
    """Prioriza edad derivada de dob; si no hay dob válido, usa user['edad'] si existe."""
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
    """Tanaka (2001): HRmax ≈ 208 - 0.7*edad."""
    if isinstance(edad, int) and edad > 0:
        return int(round(208 - 0.7 * edad))
    return 190

def hrmax_from_user_or_estimada(edad: Optional[int], hrmax_user: Optional[int]) -> int:
    """Usa hr_max del perfil si es válido; si no, Tanaka."""
    if isinstance(hrmax_user, int) and 120 <= hrmax_user <= 230:
        return hrmax_user
    return hrmax_estimada(edad)


# ─────────────────────────────────────────────────────────────
# Elección de método
# ─────────────────────────────────────────────────────────────
def pick_method(user: Optional[dict]) -> Tuple[str, Optional[int]]:
    """
    Devuelve ("hrr"|"hrmax", hr_rest|None).
    - hrr sólo si user['hr_rest'] es válido (30..100).
    - si no, hrmax.
    """
    if user:
        hr_rest = user.get("hr_rest")
        if isinstance(hr_rest, int) and 30 <= hr_rest <= 100:
            return "hrr", hr_rest
    return "hrmax", None


# ─────────────────────────────────────────────────────────────
# Fracciones de intensidad
# ─────────────────────────────────────────────────────────────
def frac_hrr(hr: Optional[int], hr_max: int, hr_rest: int) -> float:
    """%HRR en [0..1]."""
    if hr is None or hr_max <= 0:
        return 0.0
    hrr = max(1, hr_max - hr_rest)
    return max(0.0, min(1.0, (hr - hr_rest) / hrr))

def frac_hrmax(hr: Optional[int], hr_max: int) -> float:
    """%HRmax en [0..1]."""
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


# ─────────────────────────────────────────────────────────────
# Calorías (Keytel 2005)
# ─────────────────────────────────────────────────────────────
def kcal_per_min_keytel(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None) -> float:
    """
    - H: (-55.0969 + 0.6309*HR + 0.1988*peso + 0.2017*edad) / 4.184
    - M: (-20.4022 + 0.4472*HR - 0.1263*peso + 0.0740*edad) / 4.184
    """
    edad = edad if isinstance(edad, int) else 30
    peso = float(peso_kg) if (isinstance(peso_kg, (int, float)) and peso_kg > 0) else 70.0
    s = (sexo or "").strip().upper()
    if s == "F":
        return (-20.4022 + 0.4472 * hr - 0.1263 * peso + 0.0740 * edad) / 4.184
    else:
        return (-55.0969 + 0.6309 * hr + 0.1988 * peso + 0.2017 * edad) / 4.184

def kcal_adjustment_factor(frac: float, method: str, mode: str) -> float:
    """Ajuste por modo/intensidad. Umbrales cambian según el método."""
    m = (mode or "cardio").lower()
    if m == "cardio":
        return 1.0
    if m == "strength":
        return STRENGTH_ADJ

    # mixed
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
    """
    Basal por minuto usando MET: ~1.0 kcal/kg/h en reposo ⇒ met * peso / 60.
    (Evita usar Keytel(hr=0) porque puede ser negativo.)
    """
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
    """
    Calorías totales = activas (Keytel ajustado) + basal (MET), si KCAL_MODE == 'gross'.
    'mode' por defecto toma _SESSION_MODE configurado en init_from_app().
    """
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
    """Puntos/min según zona (usando el esquema simple por objetivo)."""
    return float(_MOOV_RATE.get(zone, 0.0))


# ─────────────────────────────────────────────────────────────
# Sesión (integrador)
# ─────────────────────────────────────────────────────────────
class _Sess:
    __slots__ = ("last_ts", "kcal_total", "moov_total")
    def __init__(self):
        self.last_ts = None
        self.kcal_total = 0.0
        self.moov_total = 0.0

class SessionStore:
    """
    Integrador por dispositivo/usuario.
      - Método de intensidad:
          * Karvonen (%HRR) si user['hr_rest'] es válido (30..100)
          * si no, %HRmax.
      - Kcal: Keytel ajustado por intensidad (+ basal si KCAL_MODE='gross').
      - Puntos: **por zona** (mapea Z1..Z5 a puntos por minuto) usando _MOOV_RATE.
    """
    def __init__(self):
        self._by_dev: dict[int, _Sess] = {}

    def clear(self, dev_id: int | None = None):
        if dev_id is None:
            self._by_dev.clear()
        else:
            self._by_dev.pop(dev_id, None)

    def update(self, dev_id: int, user: dict | None, hr: int | None, ts_iso: str | None,
               mode: str | None = None):
        """
        user esperado (si existe): {
            "dob": "YYYY-MM-DD", "edad": int (opcional/fallback),
            "peso": float, "sexo": "M"/"F",
            "hr_max": int?, "hr_rest": int?
        }
        """
        # Edad: prioriza DOB
        edad = _edad_from_user(user)
        hr_max_user = user.get("hr_max") if user else None
        hr_max = hrmax_from_user_or_estimada(edad, hr_max_user)

        method, hr_rest = pick_method(user)

        if dev_id is None:
            return {"hr_max": hr_max, "method": method, "zone": "Z1", "kcal": 0.0, "moov_points": 0.0}

        sess = self._by_dev.get(dev_id)
        if sess is None:
            sess = _Sess()
            self._by_dev[dev_id] = sess

        ts = _parse_ts(ts_iso)
        peso = user.get("peso") if user else None
        sexo = user.get("sexo") if user else None

        # Fracción y zona
        frac = frac_hrr(hr, hr_max, hr_rest) if method == "hrr" else frac_hrmax(hr, hr_max)
        zcode = zone_code_from_frac(frac, method)

        # Integración temporal
        if ts and hr is not None:
            if sess.last_ts:
                dt_min = max(0.0, (ts - sess.last_ts).total_seconds() / 60.0)
                if dt_min > 0.0:
                    # Kcal
                    rate_kcal = kcal_per_min_total(hr, edad, peso, sexo, frac, method, mode)
                    if rate_kcal > 0:
                        sess.kcal_total += rate_kcal * dt_min
                    # Puntos por ZONA
                    rate_mp = moov_rate_per_min_from_zone(zcode)
                    if rate_mp > 0:
                        sess.moov_total += rate_mp * dt_min
            sess.last_ts = ts

        return {
            "hr_max": hr_max,
            "method": method,   # "hrr" o "hrmax"
            "zone": zcode,
            "kcal": round(sess.kcal_total, 3),
            "moov_points": round(sess.moov_total, 3),
        }
