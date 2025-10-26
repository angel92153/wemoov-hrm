from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app, make_response, Response, stream_with_context
import time, logging, json, hashlib
from datetime import datetime, timezone
from typing import Any
from app.db.repos import UsersRepo
from app.services.hrm.sim import SimHRProvider
from app.services.metrics import SessionStore

# 🔸 Importa SESSION para leer phase/status
try:
    from app.services.sessions_manager import SESSION
except Exception:
    SESSION = None  # fallback defensivo

try:
    from app.services.hrm.real import RealHRProvider  # type: ignore
except Exception:
    class RealHRProvider(SimHRProvider):
        def read_current_by_device(self, device_id: int):
            return self.read_current(device_id)

bp = Blueprint("live", __name__)
log = logging.getLogger(__name__)

_sessions = SessionStore()

# -------------------------
# Helpers
# -------------------------
def _provider():
    prov = current_app.config.get("_HR_PROVIDER_SINGLETON")
    if prov is None:
        from app.services.hrm.combined import CombinedHRProvider
        prov = CombinedHRProvider()
        current_app.config["_HR_PROVIDER_SINGLETON"] = prov
    return prov

def _class_is_active() -> bool:
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

    try:
        if SESSION is not None:
            st = SESSION.status()
            return bool(st.get("active"))
    except Exception:
        pass

    return False

def _to_ms(ts):
    if not isinstance(ts, (int, float)):
        return None
    return int(ts * 1000) if ts < 10_000_000_000 else int(ts)

def _is_fresh(ts_ms, now_ms, recent_ms) -> bool:
    if not isinstance(ts_ms, (int, float)):
        return True
    try:
        return (now_ms - int(ts_ms)) <= int(recent_ms)
    except Exception:
        return True

def _reading_for_user(u: dict, prov, now_ms: int, recent_ms: int):
    uid = int(u["id"])
    device_id = u.get("device_id")
    is_user_sim = int(u.get("is_sim") or 0) == 1

    if is_user_sim:
        try:
            r = prov.read_current(uid)
            bpm = int(r.get("bpm") or 0) if isinstance(r, dict) else 0
            ts_ms = _to_ms(r.get("ts")) if isinstance(r, dict) else now_ms
        except Exception:
            bpm, ts_ms = 0, now_ms
        return {"bpm": bpm, "ts_ms": ts_ms}

    if device_id is None or not hasattr(prov, "read_current_by_device"):
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

def _now_iso_utc() -> str:
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

def _zone_from_bpm(hr: int, hr_max: int = 190) -> str:
    if hr_max <= 0:
        return "Z1"
    p = hr / float(hr_max)
    if p < 0.60: return "Z1"
    if p < 0.70: return "Z2"
    if p < 0.80: return "Z3"
    if p < 0.90: return "Z4"
    return "Z5"

# ---------- Propagar fase al simulador ----------
def _update_sim_phase(prov: Any) -> None:
    if SESSION is None:
        return
    try:
        status = SESSION.status()
    except Exception:
        return

    try:
        if hasattr(prov, "update_from_status") and callable(prov.update_from_status):
            prov.update_from_status(status)  # type: ignore[attr-defined]
            return
    except Exception:
        pass

    for attr in ("sim", "_sim", "sim_provider", "simulator"):
        sim = getattr(prov, attr, None)
        if sim is None:
            continue
        try:
            if hasattr(sim, "update_from_status") and callable(sim.update_from_status):
                sim.update_from_status(status)  # type: ignore[attr-defined]
                return
        except Exception:
            continue

    subs = getattr(prov, "providers", None)
    if isinstance(subs, (list, tuple)):
        for p in subs:
            try:
                if hasattr(p, "update_from_status") and callable(p.update_from_status):
                    p.update_from_status(status)  # type: ignore[attr-defined]
            except Exception:
                continue


# -------------------------
# Snapshot builder (compartido)
# -------------------------
def _build_live_snapshot(users_repo, prov, limit, recent_ms):
    try:
        _update_sim_phase(prov)
    except Exception:
        pass

    try:
        users = users_repo.list(limit=limit)
    except Exception as e:
        raise

    now_ms = int(time.time() * 1000)
    out, seen_users, assigned_devs = [], set(), set()

    for u in users:
        dev = u.get("device_id")
        if dev is not None:
            try:
                assigned_devs.add(int(dev))
            except Exception:
                pass

    for u in users:
        uid = int(u["id"])
        seen_users.add(uid)
        apodo = (u.get("apodo") or f"{u.get('nombre','')} {u.get('apellido','')}".strip() or f"ID {uid}").strip()
        reading = _reading_for_user(u, prov, now_ms, recent_ms)
        if reading is None:
            continue

        bpm = int(reading["bpm"])
        ts_iso = _now_iso_utc()

        m = _sessions.update(dev_id=uid, user=u, hr=bpm, ts_iso=ts_iso)

        is_sim = int(u.get("is_sim") or 0) == 1
        device_id = u.get("device_id")
        display_dev = uid if is_sim or not device_id else int(device_id)

        out.append({
            "dev": display_dev,
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

    try:
        recent_s = max(3, int(round(recent_ms / 1000)))
        seen = prov.recent_devices(recent=recent_s) if hasattr(prov, "recent_devices") else []
    except Exception:
        seen = []

    for d in seen:
        try:
            dev_id = int(d.get("dev"))
            if dev_id in assigned_devs:
                continue
            hr = int(d.get("hr") or 0)
            if hr <= 0:
                continue
            out.append({
                "dev": dev_id,
                "hr": hr,
                "ts": now_ms,
                "user": {"apodo": f"ID {dev_id}"},
                "metrics": {
                    "hr_max": 190,
                    "zone": _zone_from_bpm(hr, 190),
                    "kcal": None,
                    "points": None,
                },
            })
        except Exception:
            continue

    return out, assigned_devs, seen_users, now_ms


# -------------------------
# /live (polling)
# -------------------------
@bp.get("/live")
def live():
    try:
        limit = max(1, min(64, int(request.args.get("limit", "16"))))
    except ValueError:
        limit = 16

    with_status = request.args.get("with_status") == "1"

    users_repo = UsersRepo(current_app.config["USERS_DB_PATH"])
    prov = _provider()
    recent_ms = int(current_app.config.get("LIVE_RECENT_MS", 5000))
    class_active = _class_is_active()

    try:
        out, assigned_devs, seen_users, now_ms = _build_live_snapshot(
            users_repo, prov, limit, recent_ms
        )
    except Exception as e:
        return jsonify({"error": "db_error", "message": str(e)}), 500

    if not class_active:
        GRACE_MS = 10_000
        for dev_id, sess in list(_sessions._by_dev.items()):
            last = getattr(sess, "last_ts", None)
            if last is not None:
                dt = (datetime.now(timezone.utc) - last).total_seconds() * 1000
                if dt > GRACE_MS and dev_id not in seen_users:
                    _sessions.clear(dev_id)

    if with_status and SESSION is not None:
        try:
            st = SESSION.status()
        except Exception:
            st = None
        payload_obj = {"status": st, "data": out}
        payload = json.dumps(payload_obj, separators=(',', ':')).encode("utf-8")
    else:
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

    def build_once():
        try:
            _update_sim_phase(prov)
        except Exception:
            pass

        try:
            users = users_repo.list(limit=limit)
        except Exception as e:
            raise e

        class_active = _class_is_active()
        now_ms = int(time.time() * 1000)
        out, assigned_devs = [], set()

        for u in users:
            dev = u.get("device_id")
            if dev is not None:
                try:
                    assigned_devs.add(int(dev))
                except Exception:
                    pass

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

            m = _sessions.update(dev_id=uid, user=u, hr=bpm, ts_iso=ts_iso)

            is_sim = int(u.get("is_sim") or 0) == 1
            device_id = u.get("device_id")
            display_dev = uid if is_sim or not device_id else int(device_id)

            out.append({
                "dev": display_dev,
                "hr": bpm,
                "ts": now_ms,
                "user": {"apodo": apodo},
                "metrics": {
                    "hr_max": m["hr_max"],
                    "zone": m["zone"],
                    "kcal": m["kcal"],
                    "points": m["moov_points"],
                }
            })

        try:
            recent_s = max(3, int(round(recent_ms / 1000)))
            seen = prov.recent_devices(recent=recent_s) if hasattr(prov, "recent_devices") else []
        except Exception:
            seen = []

        for d in seen:
            try:
                dev_id = int(d.get("dev"))
                if dev_id in assigned_devs:
                    continue
                hr = int(d.get("hr") or 0)
                if hr <= 0:
                    continue
                out.append({
                    "dev": dev_id,
                    "hr": hr,
                    "ts": now_ms,
                    "user": {"apodo": f"ID {dev_id}"},
                    "metrics": {
                        "hr_max": 190,
                        "zone": _zone_from_bpm(hr, 190),
                        "kcal": None,
                        "points": None,
                    },
                })
            except Exception:
                continue

        return out, now_ms

    def gen():
        try:
            while True:
                try:
                    out, _ = build_once()
                except Exception as e:
                    log.exception("live_stream db error: %s", e)
                    yield "data: []\n\n"
                    time.sleep(1)
                    continue

                yield f"data: {json.dumps(out, separators=(',',':'))}\n\n"

                if SESSION is not None:
                    try:
                        st = SESSION.status()
                        yield f"event: status\ndata: {json.dumps(st, separators=(',',':'))}\n\n"
                    except Exception:
                        pass

                time.sleep(1)
        except GeneratorExit:
            return
        except Exception as e:
            log.exception("live_stream error: %s", e)
            try:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            except Exception:
                pass

    headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-store", "Connection": "keep-alive"}
    return Response(stream_with_context(gen()), headers=headers)
