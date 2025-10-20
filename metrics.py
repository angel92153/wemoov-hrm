# metrics.py
from datetime import datetime, timezone

def hrmax_estimada(edad: int | None) -> int:
    if isinstance(edad, int) and edad > 0:
        return int(round(208 - 0.7 * edad))
    return 190

def zone_code(hr: int | None, hr_max: int) -> str:
    if hr is None or hr_max <= 0:
        return "Z1"
    pct = (hr / hr_max) * 100.0
    if pct < 60:  return "Z1"
    if pct < 70:  return "Z2"
    if pct < 80:  return "Z3"
    if pct < 90:  return "Z4"
    return "Z5"

def _parse_ts(ts_iso: str | None):
    if not ts_iso: return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def kcal_per_min_keytel(hr: int, edad: int | None, peso_kg: float | None, sexo: str | None) -> float:
    edad = edad if isinstance(edad, int) else 30
    peso = float(peso_kg) if (isinstance(peso_kg, (int,float)) and peso_kg>0) else 70.0
    s = (sexo or "").strip().upper()
    if s == "F":
        return (-20.4022 + 0.4472*hr - 0.1263*peso + 0.074*edad) / 4.184
    else:
        return (-55.0969 + 0.6309*hr + 0.1988*peso + 0.2017*edad) / 4.184

def moov_rate_per_min(zone: str) -> float:
    return {"Z5":3.0, "Z4":3.0, "Z3":2.0, "Z2":1.0}.get(zone, 0.0)

class _Sess:
    __slots__ = ("last_ts","kcal_total","moov_total")
    def __init__(self):
        self.last_ts = None
        self.kcal_total = 0.0
        self.moov_total = 0.0

class SessionStore:
    """Estado por dispositivo para integrar kcal y moov points en el tiempo."""
    def __init__(self):
        self._by_dev = {}

    def update(self, dev_id: int, user: dict | None, hr: int | None, ts_iso: str | None):
        if dev_id is None:
            return {"hr_max": hrmax_estimada(user.get("edad") if user else None),
                    "zone": "Z1", "kcal": 0.0, "moov_points": 0.0}

        sess = self._by_dev.get(dev_id)
        if sess is None:
            sess = _Sess()
            self._by_dev[dev_id] = sess

        ts = _parse_ts(ts_iso)
        edad = user.get("edad") if user else None
        peso = user.get("peso") if user else None
        sexo = user.get("sexo") if user else None

        hr_max = hrmax_estimada(edad)
        zcode  = zone_code(hr, hr_max)

        if ts and hr is not None:
            if sess.last_ts:
                dt_min = max(0.0, (ts - sess.last_ts).total_seconds() / 60.0)
                if dt_min > 0:
                    # kcal
                    rate_kcal = kcal_per_min_keytel(hr, edad, peso, sexo)
                    sess.kcal_total += max(0.0, rate_kcal * dt_min)
                    # moov points
                    rate_mp = moov_rate_per_min(zcode)
                    sess.moov_total += max(0.0, rate_mp * dt_min)
            sess.last_ts = ts

        return {
            "hr_max": hr_max,
            "zone": zcode,
            "kcal": round(sess.kcal_total, 3),       # precisión interna; el front redondea
            "moov_points": round(sess.moov_total,3)  # idem
        }
