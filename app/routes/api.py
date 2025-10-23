# app/routes/api.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app
import os
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

def _demo_map() -> dict[int, int]:
    m = current_app.config.get("_DEMO_MAP")
    if m is None:
        m = {}
        current_app.config["_DEMO_MAP"] = m
    return m

@bp.get("/ant/health")
def ant_health():
    """
    Salud del proveedor HR:
    - proveedor: 'real' | 'sim'
    - users_db: ruta absoluta
    - recent_devices: nº dispositivos vistos en los últimos 10s
    - started: True/False (si RealHRProvider arrancó el listener)
    """
    provider = _provider()
    mode = current_app.config.get("HR_PROVIDER", "sim").lower()
    users_db_abs = os.path.abspath(current_app.config["USERS_DB_PATH"])

    started = None
    try:
        # si es RealHRProvider podemos mirar la bandera de clase
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
    a ningún usuario real (ignora simulados) ni reservados como DEMO.
    GET /api/unassigned_devices?recent=45
    """
    try:
        recent = max(5, min(180, int(request.args.get("recent", "45"))))
    except ValueError:
        recent = 45

    provider = _provider()
    users_repo = UsersRepo(current_app.config["USERS_DB_PATH"])
    demo_map = _demo_map()

    # 1) Dispositivos vistos recientemente por el provider
    seen: List[Dict[str, Any]] = provider.recent_devices(recent) if hasattr(provider, "recent_devices") else []

    # 2) Conjunto de device_ids ya asignados a usuarios reales
    assigned: Set[int] = set()
    try:
        for u in users_repo.list(limit=1_000_000):
            if int(u.get("is_sim") or 0) == 1:
                continue
            dev = u.get("device_id")
            if dev is not None:
                assigned.add(int(dev))
    except Exception:
        # si hay error de DB, devolvemos vistos sin filtrar
        pass

    # 3) Reservados DEMO
    reserved_demo: Set[int] = set(int(v) for v in demo_map.values() if v is not None)

    # 4) Filtrar
    free = []
    for d in seen:
        dev = int(d.get("dev"))
        if dev not in assigned and dev not in reserved_demo:
            free.append(d)

    return jsonify({"devices": free})
