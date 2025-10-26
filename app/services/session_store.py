# app/services/session_store.py
from __future__ import annotations
from datetime import datetime, timezone
from collections import deque
from typing import Optional, Dict, Any, List

# Importa SOLO desde metrics_core para evitar circulares
from .metrics_core import (
    # Config/globales
    INTEGRATE_MAX_GAP_MS,
    HR_HISTORY_ENABLED, HR_HISTORY_MAX_SAMPLES, HR_HISTORY_KEEP_MS,
    HR_HISTORY_SAMPLE_MIN_MS, HR_HISTORY_MIN_DELTA, HR_HISTORY_ON_ZONE_CHANGE, HISTORY_PURGE_ON_SESSION_END, HISTORY_REPEAT_LAST_IF_IDLE,
    # Utilidades
    _edad_from_user, hrmax_from_user_or_estimada, pick_method,
    frac_hrr, frac_hrmax, zone_code_from_frac, _zone_to_int, _int_to_zone,
    kcal_per_min_total, moov_rate_per_min_from_zone
)

# ─────────────────────────────────────────────────────────────
# Utilidades locales
# ─────────────────────────────────────────────────────────────
def _parse_ts(ts_iso: str | None):
    """ISO (con o sin Z) → datetime UTC | None."""
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


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
        # vínculo del último usuario asociado a cada dev (para exportar a DB)
        self._user_by_dev: dict[int, dict] = {}

    # ---------- limpieza ----------
    def clear(self, dev_id: int | None = None):
        if dev_id is None:
            self._by_dev.clear()
            self._user_by_dev.clear()
        else:
            self._by_dev.pop(dev_id, None)
            self._user_by_dev.pop(dev_id, None)

    # ---------- export ----------
    def export_state(self, dev_id: int | None = None):
        """
        Devuelve un dict listo para persistir:
        {
          dev_id: {
            'user': {'id': int, 'apodo': str, ...}  # si está disponible
            'kcal_total': float,
            'moov_total': float,
            'history': [ {'ts': int, 'hr': int, 'zone': 'Z*'}, ... ]
          }, ...
        }
        """
        out: Dict[int, Dict[str, Any]] = {}
        items = self._by_dev.items() if dev_id is None else [(dev_id, self._by_dev.get(dev_id))]
        for did, sess in items:
            if not sess:
                continue
            hist: List[Dict[str, Any]] = []
            if sess.hist:
                for (ts_ms, hr, zi) in list(sess.hist):
                    hist.append({"ts": int(ts_ms), "hr": int(hr), "zone": _int_to_zone(int(zi))})
            user_info = self._user_by_dev.get(int(did)) or {}
            out[int(did)] = {
                "user": user_info,
                "kcal_total": float(getattr(sess, "kcal_total", 0.0) or 0.0),
                "moov_total": float(getattr(sess, "moov_total", 0.0) or 0.0),
                "history": hist,
            }
        return out

    # ---------- historial ----------
    def _purge_history_by_time(self, sess: _Sess) -> None:
        # Si queremos purgar SOLO al finalizar la sesión, no purgamos por tiempo.
        if HISTORY_PURGE_ON_SESSION_END:
            return

        if (not HR_HISTORY_ENABLED) or (HR_HISTORY_KEEP_MS <= 0) or (not sess.hist):
            return

        try:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            cutoff = now_ms - HR_HISTORY_KEEP_MS
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

        out: List[Dict[str, Any]] = []
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

    def get_history_buckets(self, dev_id: int, *, bucket_ms: int = 5000,
                        since_ms: int | None = None, max_points: int | None = None) -> List[Dict[str, Any]]:
        rows = self.get_history(dev_id, since_ms=since_ms, as_iso=False)
        if not rows:
            return []
        if bucket_ms <= 0:
            bucket_ms = 5000

        # 1) Agrega las muestras existentes por bucket
        buckets: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            ts = int(r["ts"])
            hr = int(r["hr"])
            z  = r["zone"]
            zi = _zone_to_int(z)
            t0 = (ts // bucket_ms) * bucket_ms
            b = buckets.get(t0)
            if b is None:
                b = {"t": t0, "hrs": [], "min": hr, "max": hr, "zones": {}, "last_ts": ts, "last_hr": hr, "last_zi": zi}
                buckets[t0] = b
            b["hrs"].append(hr)
            if hr < b["min"]: b["min"] = hr
            if hr > b["max"]: b["max"] = hr
            b["zones"][zi] = b["zones"].get(zi, 0) + 1
            # conservar la última observación real del bucket
            if ts >= b["last_ts"]:
                b["last_ts"] = ts
                b["last_hr"] = hr
                b["last_zi"] = zi

        # 2) Rango continuo de buckets [first_t0 .. last_t0]
        first_t0 = min(buckets.keys())
        last_t0  = max(buckets.keys())

        # 3) Repetición “carry-forward” si falta un bucket y el último dato no es viejo
        #    tolerancia: 2 * bucket_ms (ajústalo si quieres)
        REPEAT_TOLERANCE_MS = 2 * bucket_ms

        # Llevar última lectura conocida (ts, hr, zi) mientras recorremos
        # Arrancamos con la del primer bucket real
        last_seen_ts = buckets[first_t0]["last_ts"]
        last_seen_hr = buckets[first_t0]["last_hr"]
        last_seen_zi = buckets[first_t0]["last_zi"]

        t = first_t0
        while t <= last_t0:
            if t not in buckets and HISTORY_REPEAT_LAST_IF_IDLE:
                # ¿podemos repetir?
                # Si no hay last_seen todavía, no rellenamos
                if last_seen_ts is not None:
                    # Edad de la última muestra cuando empieza este bucket
                    age_ms = (t - (last_seen_ts // bucket_ms) * bucket_ms)
                    if age_ms <= REPEAT_TOLERANCE_MS:
                        # crear bucket sintético con 1 muestra
                        buckets[t] = {
                            "t": t,
                            "hrs": [last_seen_hr],
                            "min": last_seen_hr,
                            "max": last_seen_hr,
                            "zones": { last_seen_zi: 1 },
                            "last_ts": last_seen_ts,
                            "last_hr": last_seen_hr,
                            "last_zi": last_seen_zi,
                            "_synthetic": True,
                        }
            # si existe bucket, actualizamos last_seen al final de paso
            if t in buckets:
                b = buckets[t]
                # mantener “última observación” como la del bucket
                last_seen_ts = b["last_ts"]
                last_seen_hr = b["last_hr"]
                last_seen_zi = b["last_zi"]
            t += bucket_ms

        # 4) Salida compacta
        from statistics import mean
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

        # Guardar el último usuario asociado al dev_id
        if user and isinstance(user, dict):
            uid = user.get("id")
            apodo = user.get("apodo") or f"{user.get('nombre', '')} {user.get('apellido', '')}".strip()
            self._user_by_dev[int(dev_id)] = {
                "id": uid,
                "apodo": (apodo.strip() or f"ID {uid}") if uid else "Desconocido",
            }

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
            sess.last_ts = ts

            # Registrar en historial (throttling + dedupe + repeat-last si procede)
            if HR_HISTORY_ENABLED and isinstance(hr, int) and hr > 0:
                try:
                    ts_ms_now = int(ts.timestamp() * 1000)
                    save = False
                    to_store_hr = int(hr)
                    to_store_zone = zint

                    if sess.last_hist_ts is None:
                        # primera muestra
                        save = True
                    else:
                        dt_ms_hist = ts_ms_now - sess.last_hist_ts
                        if dt_ms_hist >= HR_HISTORY_SAMPLE_MIN_MS:
                            delta_ok = (sess.last_hist_hr is None) or (
                                abs(int(hr) - int(sess.last_hist_hr)) >= HR_HISTORY_MIN_DELTA
                            )
                            zone_changed = (sess.last_hist_zone != zint)

                            if zone_changed and HR_HISTORY_ON_ZONE_CHANGE:
                                save = True
                            elif delta_ok:
                                save = True
                            elif HISTORY_REPEAT_LAST_IF_IDLE:
                                # No cambió nada "material" pero queremos continuidad:
                                # repetimos la última muestra con timestamp actual.
                                to_store_hr = int(sess.last_hist_hr if sess.last_hist_hr is not None else hr)
                                to_store_zone = int(sess.last_hist_zone if sess.last_hist_zone is not None else zint)
                                save = True
                            # si no, no guardamos nada (para ahorrar aún más)

                    if save:
                        sess.hist.append((ts_ms_now, to_store_hr, to_store_zone))
                        sess.last_hist_ts = ts_ms_now
                        sess.last_hist_hr = to_store_hr
                        sess.last_hist_zone = to_store_zone
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

# Exporta TODO y limpia el store (para usar al finalizar sesión)
def export_and_clear_sessions(persist_fn) -> None:
    """
    Exporta el estado actual (por dev_id) llamando persist_fn(data) y luego limpia.
    persist_fn: callable que recibe el dict dev->payload exportado (ver export_state()).
    """
    data = _STORE.export_state()
    try:
        if callable(persist_fn):
            persist_fn(data)
    finally:
        _STORE.clear()
