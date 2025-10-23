from flask import Blueprint, request, jsonify
from app.services.session_manager import SESSION, list_class_models

bp = Blueprint("sessions", __name__)

@bp.get("/status")                 # <- sin /sessions
def status():
    resp = jsonify(SESSION.status())
    resp.cache_control.no_store = True
    return resp

@bp.post("/start")                 # <- sin /sessions
def start():
    data = request.get_json(silent=True) or {}
    class_id = data.get("class_id") or "moov"
    try:
        SESSION.start(class_id)
        return jsonify({"ok": True, "status": SESSION.status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@bp.post("/stop")
def stop():
    SESSION.stop()
    return jsonify({"ok": True, "status": SESSION.status()})

@bp.post("/toggle_pause")
def toggle_pause():
    SESSION.toggle_pause()
    return jsonify({"ok": True, "status": SESSION.status()})

@bp.post("/next")
def next_phase():
    SESSION.next_phase()
    return jsonify({"ok": True, "status": SESSION.status()})

@bp.post("/prev")
def prev_phase():
    SESSION.prev_phase()
    return jsonify({"ok": True, "status": SESSION.status()})

@bp.post("/schedule")
def schedule():
    data = request.get_json(silent=True) or {}
    try:
        class_id = data["class_id"]
        start_epoch = float(data["start_epoch"])
        lead_s = int(data.get("lead_s", 0))
    except Exception:
        return jsonify({"ok": False, "error": "Parametros: class_id (str), start_epoch (epoch seg), lead_s (opcional)"}), 400
    try:
        SESSION.schedule(class_id, start_epoch, lead_s)
        return jsonify({"ok": True, "status": SESSION.status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@bp.post("/unschedule")
def unschedule():
    SESSION.unschedule()
    return jsonify({"ok": True, "status": SESSION.status()})

@bp.get("/classes")               # <- sin /sessions
def classes():
    return jsonify(list_class_models())
