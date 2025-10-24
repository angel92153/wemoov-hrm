# app/routes/live.py
from flask import Blueprint, jsonify, request, current_app, make_response, Response, stream_with_context
import time, os, logging, json, hashlib
from app.db.repos import UsersRepo
from app.services.hrm.sim import SimHRProvider
try:
    from app.services.hrm.real import RealHRProvider  # type: ignore
except Exception:
    class RealHRProvider(SimHRProvider):
        # fallback en dev (mantiene interfaz)
        def read_current_by_device(self, device_id: int):
            return self.read_current(device_id)

bp = Blueprint("live", __name__)
log = logging.getLogger(__name__)

# -------------------------
# Helpers singleton provider
# -------------------------
def _provider():
    prov = current_app.config.get("_HR_PROVIDER_SINGLETON")
    if prov is None:
        mode = (current_app.config.get("HR_PROVIDER", "sim") or "sim").lower()
        prov = RealHRProvider() if mode == "real" else SimHRProvider()
        current_app.config["_HR_PROVIDER_SINGLETON"] = prov
    return prov

def _mode_is_real() -> bool:
    return (current_app.config.get("HR_PROVIDER", "sim") or "sim").lower() == "real"

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

def _zone_from_bpm(bpm: int, hr_max: int) -> str:
    if hr_max <= 0: return "Z1"
    p = bpm / hr_max
    if p < 0.60: return "Z1"
    if p < 0.70: return "Z2"
    if p < 0.80: return "Z3"
    if p < 0.90: return "Z4"
    return "Z5"

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

def _read_entry_for_user(u: dict, prov, now_ms: int, recent_ms: int):
    """
    Devuelve {'bpm': int, 'ts_ms': int|None} si el usuario debe mostrarse,
    o None si NO debe mostrarse.

    Reglas:
    - Si el proveedor global es SIM (HR_PROVIDER != 'real'): TODOS los usuarios se leen del sim.
    - Si el proveedor global es REAL:
        * Usuarios con is_sim==1 -> sim (bpm>0).
        * Resto -> real: requiere device_id, bpm>0 y frescura.
    """
    uid = int(u["id"])
    device_id = u.get("device_id")
    is_user_sim = int(u.get("is_sim") or 0) == 1

    # ¿Estamos en modo SIM global?
    if not _mode_is_real():
        # SIM para todos
        try:
            r = prov.read_current(uid)
            if not isinstance(r, dict): return None
            bpm = int(r.get("bpm") or 0)
            if bpm <= 0:
                return None
            ts_ms = _to_ms(r.get("ts"))
            return {"bpm": bpm, "ts_ms": ts_ms}
        except Exception:
            return None

    # MODO REAL global:
    # 1) Usuarios marcados como simulados -> sim
    if is_user_sim:
        try:
            r = prov.read_current(uid)
            if not isinstance(r, dict): return None
            bpm = int(r.get("bpm") or 0)
            if bpm <= 0:
                return None
            ts_ms = _to_ms(r.get("ts"))
            return {"bpm": bpm, "ts_ms": ts_ms}
        except Exception:
            return None

    # 2) Usuarios reales -> real estricto
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
    recent_ms_conf = int(current_app.config.get("LIVE_RECENT_MS", 5000))  # por si se llama fuera
    if not _is_fresh(ts_ms, now_ms, recent_ms_conf):
        return None
    return {"bpm": bpm, "ts_ms": ts_ms}

# -------------------------
# /live (polling)
# -------------------------
@bp.get("/live")
def live():
    try:
        limit = max(1, min(64, int(request.args.get("limit", "16"))))
    except ValueError:
        limit = 16

    users_db = current_app.config["USERS_DB_PATH"]
    users_repo = UsersRepo(users_db)
    prov = _provider()

    # Configurable: ventana de frescura en ms (solo aplica a lecturas reales)
    recent_ms = int(current_app.config.get("LIVE_RECENT_MS", 5000))  # 5s por defecto

    try:
        users = users_repo.list(limit=limit)
    except Exception as e:
        return jsonify({"error": "db_error", "message": str(e), "users_db": os.path.abspath(users_db)}), 500

    now_ms = int(time.time() * 1000)
    out = []

    for u in users:
        uid = int(u["id"])
        apodo = (u.get("apodo") or f"{u.get('nombre','') or ''} {u.get('apellido','') or ''}".strip() or f"ID {uid}").strip()
        hr_max_eff = _hr_max_effective(u, fallback=190)

        reading = _read_entry_for_user(u, prov, now_ms, recent_ms)
        if reading is None:
            continue

        bpm = int(reading["bpm"])
        out.append({
            "dev": uid,  # el front usa 'dev' como id
            "hr": bpm,
            "ts": now_ms,  # reloj del servidor (opcional: podrías usar reading['ts_ms'])
            "user": {"apodo": apodo},
            "metrics": {"hr_max": hr_max_eff, "zone": _zone_from_bpm(bpm, hr_max_eff)}
        })

    payload = json.dumps(out, separators=(',', ':')).encode("utf-8")
    etag = hashlib.md5(payload).hexdigest()
    inm = request.headers.get("If-None-Match")
    if inm and inm == etag:
        resp = make_response("", 304)
    else:
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

                now_ms = int(time.time() * 1000)
                out = []

                for u in users:
                    uid = int(u["id"])
                    apodo = (u.get("apodo") or f"{u.get('nombre','') or ''} {u.get('apellido','') or ''}".strip() or f"ID {uid}").strip()
                    hr_max_eff = _hr_max_effective(u, fallback=190)

                    reading = _read_entry_for_user(u, prov, now_ms, recent_ms)
                    if reading is None:
                        continue

                    bpm = int(reading["bpm"])
                    out.append({
                        "dev": uid,
                        "hr": bpm,
                        "ts": now_ms,
                        "user": {"apodo": apodo},
                        "metrics": {"hr_max": hr_max_eff, "zone": _zone_from_bpm(bpm, hr_max_eff)}
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
