# app/services/metrics.py
from __future__ import annotations
from datetime import datetime, date, timezone
from typing import Optional, Tuple, Dict, Any, List
from collections import deque
from statistics import mean

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
def _parse_ts(ts_iso: str | None):
    """ISO (con o sin Z) → datetime UTC | None."""
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def _age_from_dob_iso(dob: Optional[str]) -> Optional[int]:
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


# ─────────────────────────────────────────────────────────────
# Sesión (integrador) — interno al módulo (con historial)
# ─────────────────────────────────────────────────────────────
class _Sess:
    __slots__ = (
        "last_ts", "kcal_total", "moov_total",
        "hist", "last_hist_ts", "last_hist_hr", "last_hist_zone"
    )
    def __init__(self):
        self.last_ts = None
        self.kcal_total = 0.0
        self.moov_total = 0.0
        # historial compacto: (ts_ms:int, hr:int, zone:int[1..5])
        self.hist: deque[tuple[int, int, int]] = deque(maxlen=HR_HISTORY_MAX_SAMPLES if HR_HISTORY_ENABLED else 0)
        self.last_hist_ts: Optional[int] = None
        self.last_hist_hr: Optional[int] = None
        self.last_hist_zone: Optional[int] = None

class SessionStore:
    """
    Integrador por dispositivo/usuario + historial HR.
    """
    def __init__(self):
        self._by_dev: dict[int, _Sess] = {}

    # ---------- limpieza ----------
    def clear(self, dev_id: int | None = None):
        if dev_id is None:
            self._by_dev.clear()
        else:
            self._by_dev.pop(dev_id, None)

    # ---------- historial ----------
    def _purge_history_by_time(self, sess: _Sess) -> None:
        if not HR_HISTORY_ENABLED or HR_HISTORY_KEEP_MS <= 0 or not sess.hist:
            return
        try:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            cutoff = now_ms - HR_HISTORY_KEEP_MS
            # pop izquierda mientras sea viejo
            while sess.hist and sess.hist[0][0] < cutoff:
                sess.hist.popleft()
        except Exception:
            return

    def get_history(self, dev_id: int, *, since_ms: int | None = None,
                    limit: int | None = None, as_iso: bool = False) -> List[Dict[str, Any]]:
        sess = self._by_dev.get(dev_id)
        if not sess or not HR_HISTORY_ENABLED:
            return []
        self._purge_history_by_time(sess)

        data = list(sess.hist)
        if since_ms is not None and since_ms > 0:
            data = [s for s in data if s[0] >= since_ms]
        if isinstance(limit, int) and limit > 0:
            data = data[-limit:]

        out = []
        if as_iso:
            for (ts_ms, hr, zi) in data:
                ts_iso = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).isoformat().replace("+00:00","Z")
                out.append({"ts": ts_iso, "hr": hr, "zone": _int_to_zone(zi)})
        else:
            for (ts_ms, hr, zi) in data:
                out.append({"ts": ts_ms, "hr": hr, "zone": _int_to_zone(zi)})
        return out

    def get_all_histories(self, as_iso: bool = False) -> Dict[int, List[Dict[str, Any]]]:
        out: Dict[int, List[Dict[str, Any]]] = {}
        for dev_id in list(self._by_dev.keys()):
            out[dev_id] = self.get_history(dev_id, as_iso=as_iso)
        return out

    def get_history_buckets(self, dev_id: int, *, bucket_ms: int = 60000,
                            since_ms: int | None = None, max_points: int | None = None) -> List[Dict[str, Any]]:
        """
        Agrupa en ventanas de bucket_ms (p.ej. 60s) devolviendo:
          { t: bucket_start_ms, hr_avg, hr_min, hr_max, count, zone_mode }
        """
        rows = self.get_history(dev_id, since_ms=since_ms, as_iso=False)
        if not rows:
            return []
        if bucket_ms <= 0:
            bucket_ms = 60000

        buckets: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            ts = int(r["ts"])
            hr = int(r["hr"])
            z  = r["zone"]
            zi = _zone_to_int(z)
            t0 = (ts // bucket_ms) * bucket_ms
            b = buckets.get(t0)
            if b is None:
                b = {"t": t0, "hrs": [], "min": hr, "max": hr, "zones": {}}
                buckets[t0] = b
            b["hrs"].append(hr)
            if hr < b["min"]: b["min"] = hr
            if hr > b["max"]: b["max"] = hr
            b["zones"][zi] = b["zones"].get(zi, 0) + 1

        out = []
        for t0 in sorted(buckets.keys()):
            b = buckets[t0]
            zone_mode_i = max(b["zones"].items(), key=lambda kv: kv[1])[0]
            out.append({
                "t": t0,
                "hr_avg": round(mean(b["hrs"]), 2),
                "hr_min": b["min"],
                "hr_max": b["max"],
                "count": len(b["hrs"]),
                "zone_mode": _int_to_zone(zone_mode_i),
            })

        if isinstance(max_points, int) and max_points > 0 and len(out) > max_points:
            # decimación simple para no enviar demasiados puntos
            step = max(1, len(out)//max_points)
            out = out[::step]
        return out

    # ---------- integración + registro ----------
    def update(self, dev_id: int, user: dict | None, hr: int | None, ts_iso: str | None,
               mode: str | None = None):
        """
        Devuelve dict con hr_max, method, zone, kcal acumuladas y puntos acumulados.
        Además, registra (ts_ms, hr, zone) en el historial con política de muestreo.
        """
        # Edad y HRmax
        edad = _edad_from_user(user)
        hr_max_user = user.get("hr_max") if user else None
        hr_max = hrmax_from_user_or_estimada(edad, hr_max_user)

        method, hr_rest = pick_method(user)

        if dev_id is None:
            return {"hr_max": hr_max, "method": method, "zone": "Z1", "kcal": 0.0, "moov_points": 0.0}

        sess = self._by_dev.get(dev_id)
        if sess is None:
            sess = _Sess()
            if HR_HISTORY_ENABLED:
                sess.hist = deque(maxlen=HR_HISTORY_MAX_SAMPLES)
            self._by_dev[dev_id] = sess

        ts = _parse_ts(ts_iso)
        peso = user.get("peso") if user else None
        sexo = user.get("sexo") if user else None

        # Fracción y zona
        frac = frac_hrr(hr, hr_max, hr_rest) if method == "hrr" else frac_hrmax(hr, hr_max)
        zcode = zone_code_from_frac(frac, method)
        zint = _zone_to_int(zcode)

        # Integración temporal (si hay tiempo y HR)
        if ts and hr is not None:
            if sess.last_ts:
                dt_ms = max(0.0, (ts - sess.last_ts).total_seconds() * 1000.0)
                if dt_ms > 0:
                    if dt_ms <= INTEGRATE_MAX_GAP_MS:
                        dt_min = dt_ms / 60000.0
                        # Kcal
                        rate_kcal = kcal_per_min_total(hr, edad, peso, sexo, frac, method, mode)
                        if rate_kcal > 0:
                            sess.kcal_total += rate_kcal * dt_min
                        # Puntos por ZONA
                        rate_mp = moov_rate_per_min_from_zone(zcode)
                        if rate_mp > 0:
                            sess.moov_total += rate_mp * dt_min
                    # gap grande → no retro-integrar
            # avanzar last_ts
            sess.last_ts = ts

            # Registrar en historial (throttling + dedupe)
            if HR_HISTORY_ENABLED and isinstance(hr, int) and hr > 0:
                try:
                    ts_ms_now = int(ts.timestamp() * 1000)
                    save = False
                    if sess.last_hist_ts is None:
                        save = True
                    else:
                        dt_ms_hist = ts_ms_now - sess.last_hist_ts
                        if dt_ms_hist >= HR_HISTORY_SAMPLE_MIN_MS:
                            delta_ok = (sess.last_hist_hr is None) or (abs(hr - int(sess.last_hist_hr)) >= HR_HISTORY_MIN_DELTA)
                            zone_changed = (sess.last_hist_zone != zint)
                            if delta_ok or (HR_HISTORY_ON_ZONE_CHANGE and zone_changed):
                                save = True

                    if save:
                        sess.hist.append((ts_ms_now, int(hr), zint))
                        sess.last_hist_ts = ts_ms_now
                        sess.last_hist_hr = int(hr)
                        sess.last_hist_zone = zint
                        self._purge_history_by_time(sess)
                except Exception:
                    pass

        return {
            "hr_max": hr_max,
            "method": method,   # "hrr" o "hrmax"
            "zone": zcode,
            "kcal": round(sess.kcal_total, 3),
            "moov_points": round(sess.moov_total, 3),
        }


# ─────────────────────────────────────────────────────────────
# API pública para /live
# ─────────────────────────────────────────────────────────────
_STORE = SessionStore()

def live_full_summary(hr: int | None,
                      user: dict | None,
                      *,
                      dev_id: int | None = None,
                      ts_iso: str | None = None,
                      integrate: bool = True,
                      mode: str | None = None) -> Dict[str, Any]:
    edad = _edad_from_user(user)
    hrmax_user = user.get("hr_max") if user else None
    hr_max = hrmax_from_user_or_estimada(edad, hrmax_user)
    method, hr_rest = pick_method(user)

    if integrate and (dev_id is not None) and ts_iso:
        res = _STORE.update(dev_id=dev_id, user=user or {}, hr=hr, ts_iso=ts_iso, mode=mode)
        return {
            "hr_max":      res.get("hr_max", hr_max),
            "method":      res.get("method", method),
            "zone":        res.get("zone", "Z1"),
            "kcal":        float(res.get("kcal", 0.0)),
            "moov_points": float(res.get("moov_points", 0.0)),
        }

    if hr is None:
        frac = 0.0
        zone = "Z1"
    else:
        frac = frac_hrr(hr, hr_max, hr_rest) if method == "hrr" else frac_hrmax(hr, hr_max)
        zone = zone_code_from_frac(frac, method)

    peso = user.get("peso") if user else None
    sexo = user.get("sexo") if user else None
    kcal_per_min = kcal_per_min_total(hr or 0, edad, peso, sexo, frac, method, mode)
    moov_rate = moov_rate_per_min_from_zone(zone)

    return {
        "hr_max": hr_max,
        "method": method,
        "zone": zone,
        "kcal_per_min": round(kcal_per_min, 3),
        "moov_rate_per_min": round(moov_rate, 3),
    }

# ─────────────────────────────────────────────────────────────
# Utilidades de limpieza para /live
# ─────────────────────────────────────────────────────────────
def clear_sessions(dev_ids: set[int] | None = None) -> None:
    if dev_ids is None:
        _STORE.clear()
        return
    for did in list(dev_ids):
        _STORE.clear(did)

def clear_inactive_sessions(keep_ids: set[int], older_than_ms: int) -> int:
    if older_than_ms <= 0:
        return 0
    try:
        now = datetime.now(timezone.utc)
    except Exception:
        return 0

    removed = 0
    items = list(_STORE._by_dev.items())
    for dev_id, sess in items:
        if dev_id in keep_ids:
            continue
        last = getattr(sess, "last_ts", None)
        if last is None:
            _STORE.clear(dev_id)
            removed += 1
            continue
        try:
            delta_ms = (now - last).total_seconds() * 1000.0
        except Exception:
            delta_ms = older_than_ms + 1
        if delta_ms > older_than_ms:
            _STORE.clear(dev_id)
            removed += 1
    return removed