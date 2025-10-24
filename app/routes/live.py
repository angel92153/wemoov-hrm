# app/routes/live.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app, make_response, Response, stream_with_context
import time, os, logging, json, hashlib
from datetime import datetime, timezone
from app.db.repos import UsersRepo
from app.services.hrm.sim import SimHRProvider
from app.services.metrics import SessionStore  # kcal & moov points acumulados
from app.services import metrics as MET


try:
    from app.services.hrm.real import RealHRProvider  # type: ignore
except Exception:
    class RealHRProvider(SimHRProvider):
        # fallback en dev (mantiene interfaz)
        def read_current_by_device(self, device_id: int):
            return self.read_current(device_id)

bp = Blueprint("live", __name__)
log = logging.getLogger(__name__)

# Sesiones acumuladas (kcal / moov_points) por usuario (mismo id que 'dev' en el front)
_sessions = SessionStore()

# -------------------------
# Helpers singleton provider
# -------------------------
def _provider():
    prov = current_app.config.get("_HR_PROVIDER_SINGLETON")
    if prov is None:
        from app.services.hrm.combined import CombinedHRProvider
        prov = CombinedHRProvider()
        current_app.config["_HR_PROVIDER_SINGLETON"] = prov
    return prov


def _class_is_active() -> bool:
    """
    Reutiliza la MISMA señal que usa tu /session/status.
    Prioridades:
      1) SESSION_ACTIVE_FN -> callable que devuelve bool
      2) _SESSION_ACTIVE   -> bool en config (tu /session/status puede mantenerlo)
      3) CLASS_ACTIVE      -> bool en config
      4) SESSION.active    -> dict en config con clave 'active'
      5) False
    """
    try:
        fn = current_app.config.get("SESSION_ACTIVE_FN")
        if callable(fn):
            return bool(fn())
    except Exception:
        pass

    for key in ("_SESSION_ACTIVE", "CLASS_ACTIVE"):
        if key in current_app.config:
            try:
                return bool(current_app.config.get(key))
            except Exception:
                pass

    sess_cfg = current_app.config.get("SESSION")
    if isinstance(sess_cfg, dict) and "active" in sess_cfg:
        try:
            return bool(sess_cfg.get("active"))
        except Exception:
            pass

    return False

# -------------------------
# HR helpers
# -------------------------
def _hr_max_effective(u: dict, fallback: int = 190) -> int:
    """
    - Manual: hr_max_auto == 0 y hr_max -> hr_max
    - Auto:   si edad -> Tanaka (208 - 0.7*edad)
              si no hay edad y hay hr_max -> hr_max
              si no -> fallback
    """
    hr_max = u.get("hr_max")
    hr_max_auto = u.get("hr_max_auto", 1)
    edad = u.get("edad")

    if hr_max_auto == 0:
        return int(hr_max) if hr_max is not None else fallback

    if isinstance(edad, int) and edad > 0:
        return int(round(208 - 0.7 * edad))
    if hr_max is not None:
        return int(hr_max)
    return fallback

def _to_ms(ts):
    """Convierte timestamps en segundos a milisegundos si hace falta."""
    if not isinstance(ts, (int, float)):
        return None
    return int(ts * 1000) if ts < 10_000_000_000 else int(ts)

def _is_fresh(ts_ms, now_ms, recent_ms) -> bool:
    """Comprueba frescura en ms; si no hay ts, no filtramos por frescura."""
    if not isinstance(ts_ms, (int, float)):
        return True
    try:
        return (now_ms - int(ts_ms)) <= int(recent_ms)
    except Exception:
        return True

def _reading_for_user(u: dict, prov, now_ms: int, recent_ms: int):
    """
    Devuelve {'bpm': int, 'ts_ms': int|None} si el usuario debe mostrarse,
    o None si NO debe mostrarse.

    Reglas:
    - Usuarios simulados (is_sim==1): SIEMPRE aparecen; su HR viene de prov.read_current(uid).
      Si el sim devolviera <=0, el Combined provider ya inyecta HR sintético.
    - Usuarios reales (is_sim==0): SOLO aparecen si hay device_id, bpm>0 y lectura FRESCA.
      No hay fallback a simulador.
    """
    uid = int(u["id"])
    device_id = u.get("device_id")
    is_user_sim = int(u.get("is_sim") or 0) == 1

    if is_user_sim:
        # SIMULADO: siempre visible
        try:
            r = prov.read_current(uid)
            bpm = int(r.get("bpm") or 0) if isinstance(r, dict) else 0
            ts_ms = _to_ms(r.get("ts")) if isinstance(r, dict) else now_ms
        except Exception:
            bpm, ts_ms = 0, now_ms
        return {"bpm": bpm, "ts_ms": ts_ms}

    # REAL: estricto → requiere device_id y lectura fresca >0 bpm
    if device_id is None:
        return None
    if not hasattr(prov, "read_current_by_device"):
        return None
    try:
        r = prov.read_current_by_device(int(device_id))
    except Exception:
        return None
    if not isinstance(r, dict):
        return None

    bpm = int(r.get("bpm") or 0)
    if bpm <= 0:
        return None
    ts_ms = _to_ms(r.get("ts"))
    if not _is_fresh(ts_ms, now_ms, recent_ms):
        return None

    return {"bpm": bpm, "ts_ms": ts_ms}


def _ts_ms_to_iso_utc(ts_ms: int | None, fallback_now_ms: int) -> str:
    base_ms = ts_ms if isinstance(ts_ms, (int, float)) else fallback_now_ms
    dt = datetime.utcfromtimestamp(int(base_ms) / 1000.0).replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

def _now_iso_utc() -> str:
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


# -------------------------
# /live (polling)
# -------------------------
# --- Dentro de /live ---
@bp.get("/live")
def live():
    try:
        limit = max(1, min(64, int(request.args.get("limit", "16"))))
    except ValueError:
        limit = 16

    users_db = current_app.config["USERS_DB_PATH"]
    users_repo = UsersRepo(users_db)
    prov = _provider()

    recent_ms = int(current_app.config.get("LIVE_RECENT_MS", 5000))
    mode_for_metrics = str(current_app.config.get("SESSION_MODE", "mixed"))
    class_active = _class_is_active()

    try:
        users = users_repo.list(limit=limit)
    except Exception as e:
        return jsonify({"error": "db_error", "message": str(e)}), 500

    now_ms = int(time.time() * 1000)
    out = []
    seen = set()

    for u in users:
        uid = int(u["id"])
        seen.add(uid)
        apodo = (u.get("apodo") or f"{u.get('nombre','')} {u.get('apellido','')}".strip() or f"ID {uid}").strip()

        reading = _reading_for_user(u, prov, now_ms, recent_ms)
        if reading is None:
            continue

        bpm = int(reading["bpm"])
        ts_ms = reading.get("ts_ms") or now_ms  # fallback si falta ts
        ts_iso = _now_iso_utc()

        MET.KCAL_MODE = current_app.config.get("KCAL_MODE", "net")
        MET.MET_REST  = float(current_app.config.get("MET_REST", 1.0))

        m = _sessions.update(
            dev_id=uid,
            user=u,
            hr=bpm,
            ts_iso=ts_iso,
            mode=mode_for_metrics,
        )

        out.append({
            "dev": uid,
            "hr": bpm,
            "ts": now_ms,
            "user": {"apodo": apodo},
            "metrics": {
                "hr_max": m["hr_max"],
                "zone": m["zone"],
                "kcal": m["kcal"],
                "points": m["moov_points"],
            },
        })

    # --- limpieza diferida de sesiones antiguas ---
    if not class_active:
        GRACE_MS = 10_000  # espera 10 s antes de limpiar acumulados
        for dev_id, sess in list(_sessions._by_dev.items()):
            last = getattr(sess, "last_ts", None)
            if last is not None:
                dt = (datetime.now(timezone.utc) - last).total_seconds() * 1000
                if dt > GRACE_MS and dev_id not in seen:
                    _sessions.clear(dev_id)

    payload = json.dumps(out, separators=(',', ':')).encode("utf-8")
    etag = hashlib.md5(payload).hexdigest()
    resp = make_response(payload, 200)
    resp.mimetype = "application/json"
    resp.set_etag(etag)
    resp.cache_control.no_store = True
    return resp


# -------------------------
# /live/stream (SSE)
# -------------------------
@bp.get("/live/stream")
def live_stream():
    try:
        limit = max(1, min(64, int(request.args.get("limit", "16"))))
    except ValueError:
        limit = 16

    users_repo = UsersRepo(current_app.config["USERS_DB_PATH"])
    prov = _provider()

    recent_ms = int(current_app.config.get("LIVE_RECENT_MS", 5000))
    mode_for_metrics = str(current_app.config.get("SESSION_MODE", "mixed"))

    def gen():
        try:
            while True:
                try:
                    users = users_repo.list(limit=limit)
                except Exception as e:
                    log.exception("live_stream db error: %s", e)
                    yield "data: []\n\n"
                    time.sleep(1)
                    continue

                class_active = _class_is_active()
                now_ms = int(time.time() * 1000)
                out = []

                for u in users:
                    uid = int(u["id"])
                    apodo = (u.get("apodo") or f"{u.get('nombre','') or ''} {u.get('apellido','') or ''}".strip() or f"ID {uid}").strip()

                    reading = _reading_for_user(u, prov, now_ms, recent_ms)
                    if reading is None:
                        if not class_active:
                            _sessions.clear(uid)
                        continue

                    bpm = int(reading["bpm"])
                    ts_iso = _now_iso_utc()

                    MET.KCAL_MODE = current_app.config.get("KCAL_MODE", "net")
                    MET.MET_REST  = float(current_app.config.get("MET_REST", 1.0))

                    m = _sessions.update(
                        dev_id=uid,
                        user=u,
                        hr=bpm,
                        ts_iso=ts_iso,
                        mode=mode_for_metrics,
                    )

                    out.append({
                        "dev": uid,
                        "hr": bpm,
                        "ts": now_ms,
                        "user": {"apodo": apodo},
                        "metrics": {
                            "hr_max": m["hr_max"],
                            "zone":   m["zone"],
                            "kcal":   m["kcal"],
                            "points": m["moov_points"],
                        }
                    })

                yield f"data: {json.dumps(out, separators=(',',':'))}\n\n"
                time.sleep(1)
        except GeneratorExit:
            return
        except Exception as e:
            log.exception("live_stream error: %s", e)
            try:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            except Exception:
                pass

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
        "Connection": "keep-alive",
    }
    return Response(stream_with_context(gen()), headers=headers)

# -------------------------
# /live/health
# -------------------------
@bp.get("/live/health")
def live_health():
    users_db_abs = os.path.abspath(current_app.config["USERS_DB_PATH"])
    try:
        n = len(UsersRepo(current_app.config["USERS_DB_PATH"]).list(limit=1_000_000))
        return jsonify({"ok": True, "users_count": n, "users_db": users_db_abs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "users_db": users_db_abs}), 500
