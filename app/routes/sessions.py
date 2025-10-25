# app/routes/sessions_routes.py
from __future__ import annotations

from flask import Blueprint, request, jsonify, render_template, current_app
from werkzeug.exceptions import BadRequest
from typing import Any, Dict, List
from app.db.repos import SessionsRepo

sessions_bp = Blueprint("sessions", __name__)

# ---------------------------
# Helpers
# ---------------------------
def _repo() -> SessionsRepo:
    """Crea una instancia del repositorio con la ruta correcta desde config."""
    return SessionsRepo(current_app.config["SESSIONS_DB_PATH"])

def _json_ok(**extra):
    out = {"ok": True}
    out.update(extra)
    return jsonify(out)

def _json_error(msg: str, status: int = 400):
    resp = jsonify({"ok": False, "error": msg})
    resp.status_code = status
    return resp

def _get_json() -> Dict[str, Any]:
    if not request.is_json:
        raise BadRequest("Se esperaba JSON")
    data = request.get_json(silent=True)
    if data is None:
        raise BadRequest("JSON inválido")
    return data

@sessions_bp.errorhandler(BadRequest)
def _handle_bad_request(e):
    """Intercepta errores 400 y responde siempre en formato JSON."""
    return _json_error(str(e), status=400)


# ===============================
#   VISTA PRINCIPAL
# ===============================
@sessions_bp.get("/")
def session_view():
    """Renderiza la interfaz principal de control."""
    return render_template("sessions.html")


# ===================== CLASES =====================

@sessions_bp.get("/classes")
def classes_list():
    repo = _repo()
    classes = repo.list_class_models()
    return jsonify({"classes": classes})


@sessions_bp.post("/classes")
def classes_upsert():
    try:
        data = _get_json()
        class_id = (data.get("id") or "").strip()
        label = (data.get("label") or "").strip()
        phases_in = data.get("phases") or []

        if not label:
            return _json_error("label es obligatorio")

        # Normaliza fases
        phases: List[Dict[str, Any]] = []
        for i, ph in enumerate(phases_in):
            try:
                phases.append({
                    "key": str(ph.get("key", f"PH{i+1}")),
                    "dur_s": int(ph.get("dur_s", 0)),
                    "color": str(ph.get("color") or ""),
                })
            except Exception:
                return _json_error(f"Fase #{i+1} inválida")

        # Genera id si no llega
        if not class_id:
            import re
            class_id = re.sub(r"[^a-z0-9_]", "", label.lower().replace(" ", "_")) or "class"

        _repo().upsert_class(class_id, label, phases)
        return _json_ok()
    except Exception as e:
        return _json_error(str(e))


@sessions_bp.delete("/classes/<class_id>")
def classes_delete(class_id: str):
    try:
        _repo().delete_class(class_id)
        return _json_ok()
    except Exception as e:
        return _json_error(str(e))


# ===================== DEFAULT CLASS =====================

@sessions_bp.get("/default_class")
def default_class_get():
    try:
        cid = _repo().get_default_class_id()
        return jsonify({"default_class_id": cid})
    except Exception as e:
        return _json_error(str(e))


@sessions_bp.post("/default_class")
def default_class_set():
    try:
        data = _get_json()
        cid = (data.get("class_id") or "").strip()
        if not cid:
            return _json_error("class_id es obligatorio")
        _repo().set_default_class_id(cid)
        return _json_ok()
    except Exception as e:
        return _json_error(str(e))


# ===================== CALENDARIO SEMANAL =====================

@sessions_bp.get("/calendar")
def calendar_list():
    try:
        items = _repo().list_schedule()
        return jsonify({"items": items})
    except Exception as e:
        return _json_error(str(e))


@sessions_bp.put("/calendar_bulk")
def calendar_replace_bulk():
    try:
        data = _get_json()
        items = data.get("items")
        if not isinstance(items, list):
            return _json_error("items debe ser una lista")

        # Validación ligera; el repo ya normaliza/ordena
        norm: List[Dict[str, Any]] = []
        for i, it in enumerate(items):
            try:
                dow = int(it["dow"])
                time_str = str(it["time_str"])[:5]
                if not (0 <= dow <= 6):
                    return _json_error(f"dow inválido en item #{i+1}")
                if len(time_str) != 5 or time_str[2] != ":":
                    return _json_error(f"time_str inválido en item #{i+1}")
                norm.append({"dow": dow, "time_str": time_str})
            except Exception:
                return _json_error(f"Item #{i+1} inválido")

        _repo().replace_schedule(norm)
        return _json_ok()
    except Exception as e:
        return _json_error(str(e))


# ===================== ONE-OFF (por día) =====================

@sessions_bp.get("/oneoff")
def oneoff_list():
    try:
        items = _repo().list_oneoff()
        return jsonify({"items": items})
    except Exception as e:
        return _json_error(str(e))


@sessions_bp.post("/oneoff")
def oneoff_add():
    try:
        data = _get_json()
        ymd = (data.get("ymd") or "").strip()
        cid = (data.get("class_id") or "").strip()
        if not ymd or not cid:
            return _json_error("ymd y class_id son obligatorios")
        _repo().add_oneoff(ymd, cid)
        return _json_ok()
    except Exception as e:
        return _json_error(str(e))


@sessions_bp.delete("/oneoff/<ymd>")
def oneoff_delete(ymd: str):
    try:
        _repo().delete_oneoff(ymd)
        return _json_ok()
    except Exception as e:
        return _json_error(str(e))
