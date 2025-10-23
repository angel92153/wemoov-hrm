# app/routes/live.py
from flask import Blueprint, jsonify, request, current_app, make_response, Response, stream_with_context
import time, os, logging, json, hashlib
from app.db.repos import UsersRepo
from app.services.hrm.sim import SimHRProvider
try:
    from app.services.hrm.real import RealHRProvider  # type: ignore
except Exception:
    class RealHRProvider(SimHRProvider):
        # fallback en dev
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
        mode = current_app.config.get("HR_PROVIDER", "sim").lower()
        prov = RealHRProvider() if mode == "real" else SimHRProvider()
        current_app.config["_HR_PROVIDER_SINGLETON"] = prov
    return prov

# -------------------------
# DEMO map (en memoria)
# -------------------------
def _demo_map():
    m = current_app.config.get("_DEMO_MAP")
    if m is None:
        m = {}
        current_app.config["_DEMO_MAP"] = m
    return m

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
    dem = _demo_map()

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

        # 1) DEMO tiene prioridad si existe
        demo_dev = dem.get(uid)
        if demo_dev is not None:
            if hasattr(prov, "read_current_by_device"):
                r = prov.read_current_by_device(int(demo_dev))
            else:
                r = prov.read_current(int(demo_dev))
        else:
            # 2) Caso normal (real/sim)
            device_id = u.get("device_id")
            is_sim = int(u.get("is_sim") or 0) == 1
            if is_sim:
                r = prov.read_current(uid)
            else:
                if hasattr(prov, "read_current_by_device") and device_id is not None:
                    r = prov.read_current_by_device(int(device_id))
                else:
                    r = prov.read_current(uid)

        bpm = int(r["bpm"])
        out.append({
            "dev": uid,  # mantenemos 'dev' (tu front ya lo usa)
            "hr": bpm,
            "ts": now_ms,
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

    def gen():
        try:
            while True:
                users = users_repo.list(limit=limit)
                dem = _demo_map()
                now_ms = int(time.time() * 1000)
                out = []

                for u in users:
                    uid = int(u["id"])
                    apodo = (u.get("apodo") or f"{u.get('nombre','') or ''} {u.get('apellido','') or ''}".strip() or f"ID {uid}").strip()
                    hr_max_eff = _hr_max_effective(u, fallback=190)

                    demo_dev = dem.get(uid)
                    if demo_dev is not None:
                        if hasattr(prov, "read_current_by_device"):
                            r = prov.read_current_by_device(int(demo_dev))
                        else:
                            r = prov.read_current(int(demo_dev))
                    else:
                        device_id = u.get("device_id")
                        is_sim = int(u.get("is_sim") or 0) == 1
                        if is_sim:
                            r = prov.read_current(uid)
                        else:
                            if hasattr(prov, "read_current_by_device") and device_id is not None:
                                r = prov.read_current_by_device(int(device_id))
                            else:
                                r = prov.read_current(uid)

                    bpm = int(r["bpm"])
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
            # cliente cerró la conexión
            return
        except Exception as e:
            log.exception("live_stream error: %s", e)
            # opcional: enviar un evento de error
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

# --- Pulsómetros libres (no asignados a ningún usuario) ---
@bp.get("/api/unassigned_devices")
def api_unassigned_devices():
    """
    Devuelve dispositivos ANT+ vistos en los últimos `recent` segundos
    y que NO están asignados a ningún usuario.
    Salida: {"devices":[{"dev":int,"hr":int|null,"ts":epoch},...]}
    """
    try:
        recent = int(request.args.get("recent", "45"))
    except ValueError:
        recent = 45

    users_repo = UsersRepo(current_app.config["USERS_DB_PATH"])
    prov = _provider()

    # 1) Dispositivos vistos recientemente por el provider real
    try:
        seen = prov.recent_devices(recent=recent)  # [{"dev", "hr", "ts"}, ...]
    except Exception as e:
        return jsonify({"error": "provider_error", "message": str(e)}), 500

    # 2) Conjunto de device_id ya asignados a usuarios
    try:
        assigned = { int(u["device_id"]) for u in users_repo.list(limit=1_000_000)
                     if u.get("device_id") is not None }
    except Exception as e:
        return jsonify({"error": "db_error", "message": str(e)}), 500

    # 3) Filtra -> libres
    out = [ d for d in seen if int(d["dev"]) not in assigned ]

    # Orden descendente por ts (más reciente primero)
    out.sort(key=lambda x: x["ts"], reverse=True)

    resp = jsonify({"devices": out, "recent": recent})
    resp.cache_control.no_store = True
    return resp
