# app/routes/api.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app
import os, time
from typing import List, Dict, Any, Set

from app.db.repos import UsersRepo
from app.services.hrm.sim import SimHRProvider
try:
    from app.services.hrm.real import RealHRProvider  # type: ignore
except Exception:
    class RealHRProvider(SimHRProvider):
        def read_current_by_device(self, device_id: int):
            return self.read_current(device_id)

bp = Blueprint("api", __name__)

def _provider():
    prov = current_app.config.get("_HR_PROVIDER_SINGLETON")
    if prov is None:
        mode = current_app.config.get("HR_PROVIDER", "sim").lower()
        prov = RealHRProvider() if mode == "real" else SimHRProvider()
        current_app.config["_HR_PROVIDER_SINGLETON"] = prov
    return prov

@bp.get("/ant/health")
def ant_health():
    """
    Salud del proveedor HR:
    - provider: 'real' | 'sim'
    - users_db: ruta absoluta
    - recent_devices: nº dispositivos vistos en los últimos 10s
    - started: True/False (si RealHRProvider arrancó el listener)
    """
    provider = _provider()
    mode = current_app.config.get("HR_PROVIDER", "sim").lower()
    users_db_abs = os.path.abspath(current_app.config["USERS_DB_PATH"])

    started = None
    try:
        started = bool(getattr(provider.__class__, "_started", None))
    except Exception:
        started = None

    try:
        recent = provider.recent_devices(10) if hasattr(provider, "recent_devices") else []
        return jsonify({
            "ok": True,
            "provider": mode,
            "users_db": users_db_abs,
            "recent_devices": len(recent),
            "started": started,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "provider": mode, "users_db": users_db_abs}), 500


@bp.get("/unassigned_devices")
def unassigned_devices():
    """
    Lista dispositivos detectados recientemente que NO están asignados
    a ningún usuario real. Si se pasa allow_user_id, incluye también el
    device_id de ese usuario aunque esté asignado a él.
    GET /api/unassigned_devices?recent=45&allow_user_id=<uid>
    """
    # 1) Parámetros robustos
    try:
        recent = max(5, min(300, int(request.args.get("recent", "45"))))
    except Exception:
        recent = 45

    try:
        allow_user_id_raw = request.args.get("allow_user_id")
        allow_user_id = int(allow_user_id_raw) if allow_user_id_raw not in (None, "", "null") else None
    except Exception:
        allow_user_id = None

    provider = _provider()
    users_repo = UsersRepo(current_app.config["USERS_DB_PATH"])

    # 2) Obtener "seen" del provider de forma segura
    try:
        if hasattr(provider, "recent_devices") and callable(getattr(provider, "recent_devices")):
            seen_raw = provider.recent_devices(recent)
        else:
            seen_raw = []
    except Exception as e:
        current_app.logger.exception("provider.recent_devices failed: %s", e)
        return jsonify({"ok": False, "error": "provider_error", "message": str(e)}), 502

    # 3) Normalizar "seen": dev(int), hr(int|None), ts(ms int)
    def _to_ms(ts):
        if not isinstance(ts, (int, float)):
            return None
        return int(ts * 1000) if ts < 10_000_000_000 else int(ts)

    norm_seen: List[Dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    for d in (seen_raw or []):
        try:
            dev_val = d.get("dev")
            if dev_val is None:
                continue
            dev = int(dev_val)
            hr = d.get("hr")
            hr_int = int(hr) if hr is not None else None
            ts = d.get("ts")
            ts_ms = _to_ms(ts) or now_ms
            norm_seen.append({"dev": dev, "hr": hr_int, "ts": ts_ms})
        except Exception:
            # entrada corrupta → se omite
            continue

    # 4) Conjunto de device_id ya asignados a usuarios reales (ignora simulados) y mapa uid->device_id
    assigned: Set[int] = set()
    by_user: Dict[int, int] = {}
    try:
        for u in users_repo.list(limit=1_000_000):
            # saltar usuarios simulados
            try:
                if int(u.get("is_sim") or 0) == 1:
                    continue
            except Exception:
                pass
            dev = u.get("device_id")
            uid = u.get("id")
            if dev not in (None, "", "null"):
                try:
                    dev_i = int(dev)
                    assigned.add(dev_i)
                    if uid is not None:
                        by_user[int(uid)] = dev_i
                except Exception:
                    continue
    except Exception as e:
        current_app.logger.exception("users_repo.list failed: %s", e)
        # Si falla la DB, devolvemos vistos SIN filtrar por asignados (mejor que 500)
        return jsonify({"ok": True, "devices": norm_seen, "recent": recent, "warning": "db_error"}), 200

    # 5) Si allow_user_id llega, quitar SU device del set de asignados (para que aparezca)
    if allow_user_id is not None:
        dev_for_self = by_user.get(allow_user_id)
        if dev_for_self is not None:
            assigned.discard(dev_for_self)

    # 6) Filtrar -> libres (y el propio si se permitió) y ordenar por ts desc
    free = [d for d in norm_seen if d["dev"] not in assigned]
    free.sort(key=lambda x: x["ts"], reverse=True)

    resp = jsonify({"ok": True, "devices": free, "recent": recent})
    resp.cache_control.no_store = True
    return resp
