from datetime import datetime, timezone
from typing import Optional, Tuple

# ==================== PARÁMETROS ====================
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

# Moov points (misma forma, distinto umbral de inicio por método)
MOOV_ALPHA = 4.0
MOOV_BETA  = 2.5
MOOV_MIN_HRR = 0.50  # empieza a puntuar ≥50% HRR
MOOV_MIN_HMX = 0.60  # empieza a puntuar ≥60% HRmax

# Ajuste de Keytel por intensidad (bandas según método)
MIXED_ADJ_Z5 = 1.20
MIXED_ADJ_Z4 = 1.10
MIXED_ADJ_REC_HRR = 0.90  # <50% HRR
MIXED_ADJ_REC_HMX = 0.90  # <60% HRmax
STRENGTH_ADJ = 1.30
# ====================================================


def _parse_ts(ts_iso: str | None):
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# -------------------- HRmax --------------------
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


# -------------------- Elección de método --------------------
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


# -------------------- Fracciones de intensidad --------------------
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


# -------------------- Zonas --------------------
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


# -------------------- Calorías (Keytel 2005) --------------------
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
    """
    Ajuste por modo/intensidad. Umbrales cambian según el método.
    """
    m = (mode or "cardio").lower()
    if m == "cardio":
        return 1.0
    if m == "strength":
        return STRENGTH_ADJ

    # mixed
    if method == "hrr":
        if frac >= HRR_Z5:  # ≥90% HRR
            return MIXED_ADJ_Z5
        if frac >= HRR_Z4:  # 80–89% HRR
            return MIXED_ADJ_Z4
        if frac < HRR_Z1:   # <50% HRR
            return MIXED_ADJ_REC_HRR
        return 1.0
    else:
        if frac >= HMX_Z4:  # ≥90% HRmax
            return MIXED_ADJ_Z5
        if frac >= HMX_Z3:  # 80–89% HRmax
            return MIXED_ADJ_Z4
        if frac < HMX_Z1:   # <60% HRmax
            return MIXED_ADJ_REC_HMX
        return 1.0


def kcal_per_min_adjusted(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None,
                          frac: float, method: str, mode: str = "mixed") -> float:
    base = kcal_per_min_keytel(hr, edad, peso_kg, sexo)
    adj = kcal_adjustment_factor(frac, method, mode)
    return max(0.0, base * adj)


# -------------------- Moov points --------------------
def moov_rate_per_min_from_frac(frac: float, method: str) -> float:
    """
    Tasa de puntos/min:
      - HRR: arranca a 50%
      - HRmax: arranca a 60%
    """
    thr = MOOV_MIN_HRR if method == "hrr" else MOOV_MIN_HMX
    if frac < thr:
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
    Híbrido:
      - Usa Karvonen (%HRR) SI el perfil trae hr_rest válido.
      - Si no, %HRmax.
    Ajustes energéticos y puntos se adaptan al método activo.
    """
    def __init__(self):
        self._by_dev = {}

    def clear(self, dev_id: int | None = None):
        if dev_id is None:
            self._by_dev.clear()
        else:
            self._by_dev.pop(dev_id, None)

    def update(self, dev_id: int, user: dict | None, hr: int | None, ts_iso: str | None,
               mode: str = "mixed"):
        """
        user esperado (si existe): {"edad": int, "peso": float, "sexo": "M"/"F",
                                    "hr_max": int?, "hr_rest": int?}
        """
        edad = user.get("edad") if user else None
        hr_max_user = user.get("hr_max") if user else None
        hr_max = hrmax_from_user_or_estimada(edad, hr_max_user)

        method, hr_rest = pick_method(user)  # "hrr" usa hr_rest del perfil; si no, "hrmax"

        if dev_id is None:
            return {
                "hr_max": hr_max,
                "method": method,
                "zone": "Z1",
                "kcal": 0.0,
                "moov_points": 0.0,
            }

        sess = self._by_dev.get(dev_id)
        if sess is None:
            sess = _Sess()
            self._by_dev[dev_id] = sess

        ts = _parse_ts(ts_iso)
        peso = user.get("peso") if user else None
        sexo = user.get("sexo") if user else None

        # Fracción de intensidad y zona
        if method == "hrr":
            frac = frac_hrr(hr, hr_max, hr_rest)  # type: ignore[arg-type]
        else:
            frac = frac_hrmax(hr, hr_max)
        zcode = zone_code_from_frac(frac, method)

        # Integración temporal
        if ts and (hr is not None):
            if sess.last_ts:
                dt_min = max(0.0, (ts - sess.last_ts).total_seconds() / 60.0)
                if dt_min > 0.0:
                    rate_kcal = kcal_per_min_adjusted(hr, edad, peso, sexo, frac, method, mode=mode)
                    if rate_kcal > 0:
                        sess.kcal_total += rate_kcal * dt_min
                    rate_mp = moov_rate_per_min_from_frac(frac, method)
                    if rate_mp > 0:
                        sess.moov_total += rate_mp * dt_min
            sess.last_ts = ts

        return {
            "hr_max": hr_max,
            "method": method,   # "hrr" o "hrmax" (útil para UI/log)
            "zone": zcode,
            "kcal": round(sess.kcal_total, 3),
            "moov_points": round(sess.moov_total, 3),
            # "h_frac": round(frac, 4),  # descomenta si quieres depurar
        }
