# app/routes/control.py
from flask import Blueprint, request, jsonify, render_template
from app.services.sessions_manager import SESSION, list_class_models

bp = Blueprint("control", __name__, url_prefix="/control")

# ===============================
#   VISTA PRINCIPAL
# ===============================
@bp.get("/")
def control_view():
    """Renderiza la interfaz principal de control."""
    return render_template("control.html")


# ===============================
#   API DE CONTROL DE SESIÓN
#   (todas bajo /control/*)
# ===============================

@bp.get("/status")
def status():
    """Devuelve el estado actual de la sesión."""
    resp = jsonify(SESSION.status())
    resp.cache_control.no_store = True
    return resp


@bp.post("/start")
def start():
    """Inicia una sesión con la clase indicada."""
    data = request.get_json(silent=True) or {}
    class_id = data.get("class_id") or "moov"
    try:
        SESSION.start(class_id)
        return jsonify({"ok": True, "status": SESSION.status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.post("/stop")
def stop():
    """Detiene la sesión actual."""
    SESSION.stop()
    return jsonify({"ok": True, "status": SESSION.status()})


@bp.post("/toggle_pause")
def toggle_pause():
    """Pausa o reanuda la sesión."""
    SESSION.toggle_pause()
    return jsonify({"ok": True, "status": SESSION.status()})


@bp.post("/next")
def next_phase():
    """Avanza a la siguiente fase."""
    SESSION.next_phase()
    return jsonify({"ok": True, "status": SESSION.status()})


@bp.post("/prev")
def prev_phase():
    """Retrocede a la fase anterior."""
    SESSION.prev_phase()
    return jsonify({"ok": True, "status": SESSION.status()})


@bp.get("/classes")
def classes():
    """Devuelve la lista de clases disponibles."""
    return jsonify(list_class_models())

@bp.post("/countdown/toggle")
def countdown_toggle():
    """Pausa o reanuda el countdown manual (no afecta al semanal)."""
    SESSION.toggle_countdown_pause()
    return jsonify({"ok": True, "status": SESSION.status()})
