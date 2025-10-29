# app/routes/api.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app
import os, time
from typing import List, Dict, Any, Set
import sqlite3

from app.db.repos import UsersRepo, DemoDevicesRepo
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
        from app.services.hrm.combined import CombinedHRProvider
        prov = CombinedHRProvider()
        current_app.config["_HR_PROVIDER_SINGLETON"] = prov
    return prov

def _dd_repo():
    """Repo de demo_devices usando la misma DB de usuarios."""
    return DemoDevicesRepo(current_app.config["USERS_DB_PATH"])


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
    a ningún usuario real NI marcados como DEMO (tabla demo_devices).
    Si se pasa allow_user_id, incluye también el device_id de ese usuario
    aunque esté asignado a él (pero NUNCA incluirá un DEMO).
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
            continue

    # 4) Conjunto de device_id ya asignados (reales + demo_device) e índice uid->device_id real
    assigned: Set[int] = set()
    by_user: Dict[int, int] = {}
    try:
        for u in users_repo.list(limit=1_000_000):
            try:
                if int(u.get("is_sim") or 0) == 1:
                    continue  # ignorar simulados
            except Exception:
                pass

            uid = u.get("id")

            # device_id asignado (real)
            dev = u.get("device_id")
            if dev not in (None, "", "null"):
                try:
                    dev_i = int(dev)
                    assigned.add(dev_i)
                    if uid is not None:
                        by_user[int(uid)] = dev_i
                except Exception:
                    pass

            # demo_device asignado (también bloquea disponibilidad)
            demo = u.get("demo_device")
            if demo not in (None, "", "null"):
                try:
                    demo_i = int(demo)
                    assigned.add(demo_i)
                except Exception:
                    pass

    except Exception as e:
        current_app.logger.exception("users_repo.list failed: %s", e)
        return jsonify({"ok": True, "devices": norm_seen, "recent": recent, "warning": "db_error"}), 200

    # 5) Cargar TODOS los DEMO de la tabla demo_devices y bloquearlos SIEMPRE
    try:
        from app.db.repos import DemoDevicesRepo
        dd_repo = DemoDevicesRepo(current_app.config["USERS_DB_PATH"])
        demos = dd_repo.list()  # [{id, device_id}]
        all_demo_ids: Set[int] = set()
        for row in demos:
            try:
                all_demo_ids.add(int(row["device_id"]))
            except Exception:
                continue
    except Exception as e:
        current_app.logger.exception("demo_devices list failed: %s", e)
        all_demo_ids = set()

    # 6) Si allow_user_id llega, quitar SU device del set de asignados SOLO si NO es DEMO
    if allow_user_id is not None:
        dev_for_self = by_user.get(allow_user_id)
        if dev_for_self is not None and dev_for_self not in all_demo_ids:
            assigned.discard(dev_for_self)

    # 7) Filtrar -> libres excluyendo asignados y TODOS los DEMO; ordenar por ts desc
    blocked = assigned | all_demo_ids  # bloqueados = asignados (real+demo) ∪ todos los demo definidos
    free = [d for d in norm_seen if d["dev"] not in blocked]
    free.sort(key=lambda x: x["ts"], reverse=True)

    resp = jsonify({"ok": True, "devices": free, "recent": recent})
    resp.cache_control.no_store = True
    return resp


# ============================================
# DEMO DEVICES API
# ============================================

@bp.get("/demo_devices")
def api_demo_devices_list():
    """
    Lista los demo devices.
    Respuesta: { devices: [{id, device_id}, ...] }
    """
    repo = _dd_repo()
    try:
        devices = repo.list()
        return jsonify({"devices": devices})
    except Exception as e:
        current_app.logger.exception("demo_devices_list failed: %s", e)
        return jsonify({"devices": [], "error": str(e)}), 500


@bp.post("/demo_devices")
def api_demo_devices_create():
    """
    Crea un demo device.
    Body JSON: { "device_id": <int> }
    Respuesta: { ok: true, id: <int> }
    """
    repo = _dd_repo()
    payload = request.get_json(silent=True) or {}
    device_id = payload.get("device_id")

    # Validación básica
    try:
        if device_id in (None, "", "null"):
            return jsonify({"ok": False, "error": "device_id requerido"}), 400
        device_id = int(device_id)
    except Exception:
        return jsonify({"ok": False, "error": "device_id debe ser numérico"}), 400

    try:
        new_id = repo.create(device_id)
        return jsonify({"ok": True, "id": new_id})
    except sqlite3.IntegrityError as e:
        # UNIQUE constraint failed: demo_devices.device_id
        return jsonify({"ok": False, "error": "device_id duplicado"}), 409
    except Exception as e:
        current_app.logger.exception("demo_devices_create failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.delete("/demo_devices/<int:row_id>")
def api_demo_devices_delete(row_id: int):
    """
    Elimina un demo device por id (PK).
    Respuesta: { ok: true }
    """
    repo = _dd_repo()
    try:
        repo.delete(row_id)
        return jsonify({"ok": True})
    except Exception as e:
        current_app.logger.exception("demo_devices_delete failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 400
