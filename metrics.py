from datetime import datetime, timezone
from typing import Optional

# ==================== PARÁMETROS ====================
# Zonas en %HRR (Karvonen)
Z1_FRAC = 0.50
Z2_FRAC = 0.60
Z3_FRAC = 0.70
Z4_FRAC = 0.80
Z5_FRAC = 0.90  # ≥90% -> Z5

# Moov points exponenciales en función de %HRR
MOOV_ALPHA = 4.0
MOOV_BETA  = 2.5
MOOV_FRAC_MIN = 0.50

# Ajuste de Keytel por modo de entrenamiento
# - "cardio": sin ajuste (Keytel puro)
# - "mixed": ajuste por intensidad (%HRR) para HIIT / fuerza funcional
# - "strength": fuerza pura (FC no refleja todo el coste -> +30% fijo)
MIXED_ADJ_Z5 = 1.20  # ≥90% HRR
MIXED_ADJ_Z4 = 1.10  # 80–89% HRR
MIXED_ADJ_REC = 0.90 # ≤50% HRR (transiciones/recuperación)
STRENGTH_ADJ = 1.30  # fuerza pura
# ====================================================


def _parse_ts(ts_iso: str | None):
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# -------------------- HRmax / HRrest --------------------
def hrmax_estimada(edad: int | None) -> int:
    """Tanaka (2001): HRmax ≈ 208 - 0.7*edad (fallback si no hay edad)."""
    if isinstance(edad, int) and edad > 0:
        return int(round(208 - 0.7 * edad))
    return 190


def hrmax_from_user_or_estimada(edad: Optional[int], hrmax_user: Optional[int]) -> int:
    """
    Devuelve FC máxima priorizando la almacenada en DB (hr_max).
    Si no hay, usa Tanaka. Rangos razonables 120..230 para descartar errores.
    """
    if isinstance(hrmax_user, int) and 120 <= hrmax_user <= 230:
        return hrmax_user
    return hrmax_estimada(edad)


def hrrest_estimada(sexo: Optional[str], edad: Optional[int], hrrest_user: Optional[int]) -> int:
    """
    Devuelve FC reposo (HRrest) priorizando la almacenada en DB (hr_rest).
    Si no hay, estima por sexo/edad con límites razonables (45..75).
    """
    if isinstance(hrrest_user, int) and 30 <= hrrest_user <= 100:
        return hrrest_user
    s = (sexo or "").strip().lower()
    base = 62.0 if s.startswith("f") else 58.0
    a = edad if isinstance(edad, int) and edad > 0 else 35
    est = base + 0.05 * max(0, a - 30)
    return int(round(max(45.0, min(75.0, est))))


def karvonen_frac(hr: Optional[int], hr_max: int, hr_rest: int) -> float:
    """%HRR en [0..1]. HRR = HRmax - HRrest; frac = (HR - HRrest) / HRR"""
    if hr is None or hr_max <= 0:
        return 0.0
    hrr = max(1, hr_max - hr_rest)
    return max(0.0, min(1.0, (hr - hr_rest) / hrr))


# -------------------- Zonas (Karvonen, Z5 ≥90%) --------------------
def zone_code_from_frac(frac: float) -> str:
    if   frac < Z1_FRAC: return "Z1"
    elif frac < Z2_FRAC: return "Z2"
    elif frac < Z3_FRAC: return "Z3"
    elif frac < Z4_FRAC: return "Z4"
    elif frac < Z5_FRAC: return "Z4"  # 80–89% aún Z4
    else:                return "Z5"


# -------------------- Calorías (Keytel 2005) --------------------
def kcal_per_min_keytel(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None) -> float:
    """
    Ecuaciones Keytel validadas (kcal/min) a partir de FC.
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


def kcal_adjustment_factor(frac: float, mode: str) -> float:
    """
    Factor multiplicativo para ajustar Keytel según el modo de entrenamiento.
    """
    m = (mode or "cardio").lower()
    if m == "cardio":
        return 1.0
    if m == "strength":
        return STRENGTH_ADJ
    # mixed (por intensidad)
    if frac >= Z5_FRAC:
        return MIXED_ADJ_Z5
    if frac >= Z4_FRAC:
        return MIXED_ADJ_Z4
    if frac <= Z1_FRAC:
        return MIXED_ADJ_REC
    return 1.0


def kcal_per_min_adjusted(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None,
                          frac: float, mode: str = "mixed") -> float:
    """
    Keytel ajustada por modo:
      - cardio:     sin ajuste
      - mixed:      ajuste por %HRR (↑ en Z4/Z5, ↓ en recuperación)
      - strength:   +30% fijo
    """
    base = kcal_per_min_keytel(hr, edad, peso_kg, sexo)
    adj = kcal_adjustment_factor(frac, mode)
    return max(0.0, base * adj)


# -------------------- Moov points (exponencial) --------------------
def moov_rate_per_min_from_frac(frac: float) -> float:
    """
    Tasa de puntos/min en función de %HRR:
      - < MOOV_FRAC_MIN: 0
      - ≥ MOOV_FRAC_MIN: MOOV_ALPHA * (frac ** MOOV_BETA)
    """
    if frac < MOOV_FRAC_MIN:
        return 0.0
    return MOOV_ALPHA * (frac ** MOOV_BETA)


# -------------------- Sesión --------------------
class _Sess:
    __slots__ = ("last_ts", "kcal_total", "moov_total")
    def __init__(self):
        self.last_ts = None
        self.kcal_total = 0.0
        self.moov_total = 0.0


class SessionStore:
    """
    Estado por dispositivo para integrar kcal y moov points en el tiempo (Karvonen + Keytel).
    - Usa FCmáx y FCreposo de DB si existen; si no, estima (Tanaka para máx; función basada en sexo/edad para reposo).
    - Permite ajustar el gasto energético con 'mode' según el tipo de sesión (cardio/mixed/strength).
    """
    def __init__(self):
        self._by_dev = {}

    def clear(self, dev_id: int | None = None):
        """Borra la sesión de un dispositivo o todas (kcal/puntos/last_ts)."""
        if dev_id is None:
            self._by_dev.clear()
        else:
            self._by_dev.pop(dev_id, None)

    def update(self, dev_id: int, user: dict | None, hr: int | None, ts_iso: str | None,
               mode: str = "mixed"):
        """
        user esperado (si existe): {"edad": int, "peso": float, "sexo": "M"/"F",
                                    "hr_rest": int?, "hr_max": int?}
        - hr_max se toma de DB si está; si no, Tanaka.
        - hr_rest se toma de DB si está; si no, estimación por sexo/edad.
        - mode: "cardio" | "mixed" | "strength" (por defecto, "mixed").
        """
        edad = user.get("edad") if user else None

        # FC máx: manual si hay, si no Tanaka
        hr_max_user = user.get("hr_max") if user else None
        hr_max = hrmax_from_user_or_estimada(edad, hr_max_user)

        if dev_id is None:
            return {"hr_max": hr_max, "zone": "Z1", "kcal": 0.0, "moov_points": 0.0}

        sess = self._by_dev.get(dev_id)
        if sess is None:
            sess = _Sess()
            self._by_dev[dev_id] = sess

        ts = _parse_ts(ts_iso)
        peso = user.get("peso") if user else None
        sexo = user.get("sexo") if user else None
        hr_rest_user = user.get("hr_rest") if user else None

        # FC reposo: manual si hay, si no estimada
        hr_rest = hrrest_estimada(sexo, edad, hr_rest_user)

        # %HRR y zona
        frac = karvonen_frac(hr, hr_max, hr_rest)
        zcode = zone_code_from_frac(frac)

        # Integración temporal
        if ts and (hr is not None):
            if sess.last_ts:
                dt_min = max(0.0, (ts - sess.last_ts).total_seconds() / 60.0)
                if dt_min > 0.0:
                    # kcal/min: Keytel ajustada por modo/intensidad
                    rate_kcal = kcal_per_min_adjusted(hr, edad, peso, sexo, frac, mode=mode)
                    if rate_kcal > 0:
                        sess.kcal_total += rate_kcal * dt_min
                    # moov/min por %HRR actual
                    rate_mp = moov_rate_per_min_from_frac(frac)
                    if rate_mp > 0:
                        sess.moov_total += rate_mp * dt_min
            sess.last_ts = ts

        return {
            "hr_max": hr_max,
            "zone": zcode,
            "kcal": round(sess.kcal_total, 3),
            "moov_points": round(sess.moov_total, 3),
            # Útiles para depuración (opcional):
            # "hr_rest": hr_rest,
            # "hrr_frac": round(frac, 4),
            # "hr_max_manual": bool(hr_max_user),
            # "hr_rest_manual": bool(hr_rest_user),
        }
