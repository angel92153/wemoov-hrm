from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app, make_response, Response, stream_with_context
import time, logging, json, hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.repos import UsersRepo
from app.db.connection import get_conn
from app.services.hrm.sim import SimHRProvider
from app.services.session_store import (
    live_full_summary,
    clear_inactive_sessions,
    clear_sessions,
    export_and_clear_sessions,
    _STORE,
)
from app.db.session_dump import dump_session_to_db
from app.db.summary_query import (
    load_last_run_summary,                     # cálculo on-the-fly (compat)
    load_persisted_summary_devices_by_run_id,  # 📌 lectura determinista por run_id
)

# 🔸 Importa SESSION para leer phase/status (opcional)
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

def _session_state():
    if SESSION is None:
        return None
    try:
        return SESSION.status()
    except Exception:
        return None

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
# Helpers de persistencia
# -------------------------
def _extract_run_info_from_status(status: dict | None) -> tuple[Optional[str], Optional[int]]:
    """Saca run_id y started_at_ms del status (con fallbacks)."""
    if not isinstance(status, dict):
        return None, None
    run_id = status.get("session_id") or status.get("id")
    started_at_ms = None
    for k in ("class_start_ms", "t0_ms"):
        v = status.get(k)
        if isinstance(v, int):
            started_at_ms = v
            break
    if started_at_ms is None:
        for k in ("class_t0", "phase_t0"):
            v = status.get(k)
            if isinstance(v, str):
                try:
                    started_at_ms = int(datetime.fromisoformat(v.replace("Z","+00:00"))
                                        .astimezone(timezone.utc).timestamp() * 1000)
                    break
                except Exception:
                    pass
    return run_id, started_at_ms

def _persist_session_and_summary(data: dict, status: dict | None) -> None:
    """
    1) Vuelca crudo a SESSION_RUNS_DB_PATH (session_dump)
    2) Calcula y guarda summary (global + per-user) en summaries.db (best-effort)
    """
    from app.db.repos import UsersRepo
    from app.db.summary_query import compute_and_store_last_run_summary

    # Extraer info de run
    run_id, started_at_ms = _extract_run_info_from_status(status)
    ended_at_ms = int(time.time() * 1000)

    # Serializar meta (si viene)
    try:
        meta_str = json.dumps(status, separators=(",", ":")) if status else None
    except Exception:
        meta_str = None

    # 1) Guardar crudo
    dump_session_to_db(
        current_app.config["SESSION_RUNS_DB_PATH"],
        data,
        run_id=run_id,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        meta_json=meta_str,
    )

    # 2) Guardar summary (best-effort)
    try:
        users_repo = UsersRepo(current_app.config["USERS_DB_PATH"])
        compute_and_store_last_run_summary(
            current_app.config["SESSION_RUNS_DB_PATH"],           # lee del crudo
            users_repo,
            bucket_ms=int(current_app.config.get("SUMMARY_BUCKET_MS", 5000)),
            meta=status,                                          # meta como dict
        )
    except Exception as e:
        current_app.logger.warning(f"summary_store_failed: {e}")


# -------------------------
# Snapshot builder
# -------------------------
def _build_live_snapshot(users_repo, prov, limit, recent_ms):
    try:
        _update_sim_phase(prov)
    except Exception:
        pass

    users = users_repo.list(limit=limit)
    now_ms = int(time.time() * 1000)
    out, assigned_devs, seen_users = [], set(), set()

    for u in users:
        dev = u.get("device_id")
        if dev is not None:
            try:
                assigned_devs.add(int(dev))
            except Exception:
                pass

    for u in users:
        uid = int(u["id"])
        apodo = (
            u.get("apodo")
            or f"{u.get('nombre', '')} {u.get('apellido', '')}".strip()
            or f"ID {uid}"
        ).strip()

        # lectura más reciente de HR
        reading = _reading_for_user(u, prov, now_ms, recent_ms)
        if reading is None:
            continue

        seen_users.add(uid)
        bpm = int(reading["bpm"])
        ts_iso = _now_iso_utc()

        # determinar si es simulador o dispositivo real
        is_sim = int(u.get("is_sim") or 0) == 1
        device_id = u.get("device_id")
        display_dev = uid if is_sim or not device_id else int(device_id)

        # integrar usando el mismo ID que el front (display_dev)
        ms = live_full_summary(
            hr=bpm,
            user=u,
            dev_id=display_dev,
            ts_iso=ts_iso,
            integrate=True,
        )

        out.append({
            "dev": display_dev,
            "hr": bpm,
            "ts": now_ms,
            "user": {"apodo": apodo},
            "metrics": {
                "hr_max": ms["hr_max"],
                "zone": ms["zone"],
                "kcal": ms.get("kcal"),
                "points": ms.get("moov_points"),
            },
        })

    # recent devices sin usuario
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
            ms = live_full_summary(hr=hr, user=None, integrate=False)
            out.append({
                "dev": dev_id,
                "hr": hr,
                "ts": now_ms,
                "user": {"apodo": f"ID {dev_id}"},
                "metrics": {"hr_max": ms["hr_max"], "zone": ms["zone"]},
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

    try:
        out, assigned_devs, seen_users, now_ms = _build_live_snapshot(users_repo, prov, limit, recent_ms)
    except Exception as e:
        return jsonify({"error": "db_error", "message": str(e)}), 500

    st = _session_state()
    session_active = bool((st or {}).get("active"))
    prev_active = getattr(current_app, "_LAST_SESSION_ACTIVE", None)

    if prev_active is False and session_active:
        # inicio de sesión -> limpia residuos previos
        clear_sessions()

    if prev_active is True and not session_active:
        # fin de sesión -> exporta a DB (crudo) + guarda summary; luego limpia el store
        def _persist_fn(data):
            _persist_session_and_summary(data, st or {})
        export_and_clear_sessions(_persist_fn)

    current_app._LAST_SESSION_ACTIVE = session_active

    if not session_active:
        FADE_MS = int(current_app.config.get("LIVE_FADE_MS", 60000))
        clear_inactive_sessions(keep_ids=seen_users, older_than_ms=FADE_MS)

    if with_status and SESSION is not None:
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
        out, _, seen_users, _ = _build_live_snapshot(users_repo, prov, limit, recent_ms)
        st = _session_state()
        session_active = bool((st or {}).get("active"))

        if not session_active:
            FADE_MS = int(current_app.config.get("LIVE_FADE_MS", 60000))
            clear_inactive_sessions(keep_ids=seen_users, older_than_ms=FADE_MS)

        prev_active = getattr(current_app, "_LAST_SESSION_ACTIVE", None)
        if prev_active is False and session_active:
            clear_sessions()
        if prev_active is True and not session_active:
            # fin de sesión -> persiste
            def _persist_fn(data):
                status = _session_state() or {}
                _persist_session_and_summary(data, status)
            export_and_clear_sessions(_persist_fn)

        current_app._LAST_SESSION_ACTIVE = session_active
        return out

    def gen():
        try:
            while True:
                try:
                    out = build_once()
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
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-store", "Connection": "keep-alive"}
    return Response(stream_with_context(gen()), headers=headers)


# -------------------------
# /live/config
# -------------------------
@bp.get("/live/config")
def live_config():
    return jsonify({
        "fade_ms": int(current_app.config.get("LIVE_FADE_MS", 60000)),
        "recent_ms": int(current_app.config.get("LIVE_RECENT_MS", 5000)),
        "summary_ms": int(current_app.config.get("SUMMARY_SHOW_MS", 15000)),
    })


# -------------------------
# /live/zone_timeline
# -------------------------
@bp.get("/live/zone_timeline")
def live_zone_timeline():
    """
    Devuelve la línea temporal de zonas por dev_id,
    agrupada por tramos de N milisegundos (por defecto 5s).
    """
    try:
        dev_id = int(request.args.get("dev", "0"))
    except Exception:
        return jsonify({"error": "invalid_dev"}), 400

    bucket_ms = int(request.args.get("bucket_ms", "5000"))
    window_ms = int(request.args.get("window_ms", "60000"))  # últimos 60s por defecto
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - window_ms

    try:
        buckets = _STORE.get_history_buckets(dev_id, bucket_ms=bucket_ms, since_ms=since_ms)
    except Exception as e:
        return jsonify({"error": "zone_timeline_error", "message": str(e)}), 500

    # Si no hay clase activa, devolvemos vacío
    st = _session_state()
    if not ((st or {}).get("active")):
        return Response(status=204)

    return jsonify({
        "dev": dev_id,
        "bucket_ms": bucket_ms,
        "anchor_ms": since_ms,
        "now_ms": now_ms,
        "timeline": buckets,
    })


# -------------------------
# /live/summary (on-the-fly, compat)
# -------------------------
@bp.get("/live/summary")
def live_summary():
    """
    Devuelve el resumen on-the-fly desde la DB cruda (compat).
    Nota: el front debe usar /live/summary/persisted.
    """
    users_repo = UsersRepo(current_app.config["USERS_DB_PATH"])
    try:
        bucket_ms = int(request.args.get("bucket_ms", "5000"))
    except Exception:
        bucket_ms = 5000

    try:
        data = load_last_run_summary(
            current_app.config["SESSION_RUNS_DB_PATH"],  # lee de la DB de runs cruda
            users_repo,
            bucket_ms=bucket_ms
        )
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": "summary_error", "message": str(e)}), 500


# -------------------------
# /live/summary/persisted (lee de summaries.db) — ÚNICA FUENTE para front
# -------------------------
@bp.get("/live/summary/persisted")
def live_summary_persisted():
    """
    Devuelve SIEMPRE el resumen de la última sesión REAL, identificado por run_id de session_runs.
    Si aún no existe en summaries.db, responde 204 (pendiente) con Retry-After.
    ETag basado en run_id + ended_at_ms (estable entre polls).
    """
    # 1) localizar último run real (session_runs)
    try:
        with get_conn(current_app.config["SESSION_RUNS_DB_PATH"]) as con:
            row = con.execute(
                "SELECT id, ended_at_ms FROM session_runs ORDER BY ended_at_ms DESC LIMIT 1"
            ).fetchone()
    except Exception as e:
        return jsonify({"error": "summary_persisted_error", "message": str(e)}), 500

    if not row:
        # no hay sesiones registradas aún
        resp = Response(status=204)
        resp.headers["Retry-After"] = "2"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    run_id = row["id"] if isinstance(row, dict) else row[0]
    ended_at_ms = row["ended_at_ms"] if isinstance(row, dict) else row[1]

    # 2) cargar ese run_id en summaries.db
    try:
        data = load_persisted_summary_devices_by_run_id(run_id)
    except Exception as e:
        return jsonify({"error": "summary_persisted_error", "message": str(e)}), 500

    if not data or not (data.get("devices") or []):
        # aún no persistido o vacío -> invita a reintentar
        resp = Response(status=204)
        resp.headers["Retry-After"] = "2"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # 3) ETag/304
    etag_seed = f"{run_id}:{int(ended_at_ms or 0)}"
    etag = hashlib.md5(etag_seed.encode("utf-8")).hexdigest()

    inm = request.headers.get("If-None-Match")
    if inm and inm.strip('"') == etag:
        resp = Response(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # 4) 200 con JSON + ETag
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    resp = make_response(payload, 200)
    resp.mimetype = "application/json"
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-store"
    return resp