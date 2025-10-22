# =========================== hr_server.py ===========================
import threading
import time
import logging
import os
from flask import Flask, jsonify, request, send_from_directory, render_template, redirect, url_for

import hr_real
import db
import metrics

# Gestor de clases/sesiones
import session_manager as sm

# --------- Simulador opcional ----------
try:
    import hr_sim  # provee start_simulator(...)
    _HAS_SIM = True
except Exception:
    _HAS_SIM = False

# Silenciar access-log de Werkzeug
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# Compresión opcional
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from flask_compress import Compress as _Compress
try:
    from flask_compress import Compress  # type: ignore
    _HAS_COMPRESS = True
except Exception:
    _HAS_COMPRESS = False
    class Compress:
        def __init__(self, *args, **kwargs): pass
        def __call__(self, app): return app

app = Flask(__name__, template_folder="templates")
if _HAS_COMPRESS:
    Compress(app)

app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

# ===== Filtros Jinja / utilidades edad & Tanaka =====
from datetime import date, datetime

def age_from_dob(dob_str):
    if not dob_str:
        return None
    try:
        y, m, d = map(int, dob_str.split('-'))
        dob = date(y, m, d)
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return max(0, age)
    except Exception:
        return None

def tanaka_from_age(age):
    try:
        e = int(age)
        if e > 0:
            return int(round(208 - 0.7 * e))
    except Exception:
        pass
    return None

def tanaka_from_dob(dob_str):
    a = age_from_dob(dob_str)
    return tanaka_from_age(a)

app.jinja_env.filters['age_from_dob'] = age_from_dob
app.jinja_env.filters['tanaka_dob']   = tanaka_from_dob
app.jinja_env.filters['tanaka']       = tanaka_from_age

# ======================= CONFIG SERVIDOR =======================
SIM_DEVICES   = int(os.getenv("HRM_SIM_DEVICES", "3"))
SIM_BASE_ID   = int(os.getenv("HRM_SIM_BASE_ID", "10000"))
SIM_UPDATE_HZ = float(os.getenv("HRM_SIM_UPDATE_HZ", "2"))
SIM_AUTOCREATE = os.getenv("HRM_SIM_AUTOCREATE", "1") in ("1", "true", "yes", "on")

MIN_REFRESH_S = float(os.getenv("HRM_MIN_REFRESH_S", "1"))
DEVICE_RECENT_SECS = float(os.getenv("HRM_DEVICE_RECENT_SECS", "45"))
# ===============================================================

# --- Estado en memoria ---
STATE = {}                   # {dev_id: {"hr": int, "ts": iso}}
SESSION = metrics.SessionStore()

# --- Throttling/caché para /live ---
_LIVE_CACHE = {"ts": 0.0, "key": None, "payload": None}

# --- Cache de usuario por dispositivo ---
USER_CACHE = {}
USER_CACHE_TTL = 60.0
_USER_CACHE_TS = 0.0

# --- Cache de métricas por dev y ts ---
METRICS_CACHE = {}

# ==================== UTILIDADES ====================
def _refresh_user_cache_if_needed():
    global _USER_CACHE_TS
    now = time.time()
    if now - _USER_CACHE_TS > USER_CACHE_TTL:
        _USER_CACHE_TS = now

def _get_user_for_dev(dev: int):
    """
    Devuelve el usuario asignado a un device_id:
    - Si es un DEMO y está asignado en memoria -> ese usuario.
    - Si no, el de DB por device_id (si existe).
    """
    # DEMO override
    if dev in DEMO_ASSIGN:
        uid = DEMO_ASSIGN[dev]
        if uid:
            return db.get_user(uid)

    # fallback DB por device_id
    u = USER_CACHE.get(dev)
    if u is None:
        u = db.get_user_by_device(dev)
        USER_CACHE[dev] = u
    return u


def _state_signature(limit: int) -> str:
    items = list(STATE.items())
    items.sort(key=lambda kv: kv[0])
    items = items[:max(1, min(256, limit))]
    return ','.join(f'{dev}|{v.get("hr")}|{v.get("ts")}' for dev, v in items)

def _parse_ts(ts_iso: str | None):
    from datetime import datetime, timezone
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

# ======================= DEMO SLOTS (solo en memoria) =======================
DEMO_SLOTS = [
    ("DEMO 1", 36466), ("DEMO 2", 91002), ("DEMO 3", 91003), ("DEMO 4", 91004), ("DEMO 5", 91005),
    ("DEMO 6", 91006), ("DEMO 7", 91007), ("DEMO 8", 91008), ("DEMO 9", 91009), ("DEMO 10", 91010),
]

# Asignaciones vivas solo en esta ejecución:
#  - demo_dev_id -> user_id
#  - user_id     -> demo_dev_id
DEMO_ASSIGN = {}
USER_TO_DEMO = {}

def assign_demo_to_user(demo_dev_id: int, user_id: int | None):
    """
    Asigna un DEMO a un usuario en memoria.
    - Si el DEMO ya estaba en otro usuario, lo desasigna de ese usuario.
    - Si el usuario tenía otro DEMO, lo libera.
    - Si user_id es None -> libera ese DEMO.
    """
    # liberar si user_id=None
    if user_id is None:
        prev_user = DEMO_ASSIGN.pop(demo_dev_id, None)
        if prev_user is not None:
            USER_TO_DEMO.pop(prev_user, None)
        return

    # 1) si el demo estaba asignado a otro usuario, liberarlo de ese usuario
    prev_user = DEMO_ASSIGN.get(demo_dev_id)
    if prev_user is not None and prev_user != user_id:
        USER_TO_DEMO.pop(prev_user, None)

    # 2) si el usuario tenía otro demo, liberarlo
    prev_demo = USER_TO_DEMO.get(user_id)
    if prev_demo is not None and prev_demo != demo_dev_id:
        DEMO_ASSIGN.pop(prev_demo, None)

    # 3) asignar
    DEMO_ASSIGN[demo_dev_id] = user_id
    USER_TO_DEMO[user_id] = demo_dev_id


def demo_label_for(dev_id: int) -> str | None:
    for label, d in DEMO_SLOTS:
        if d == dev_id:
            return label
    return None
# ===========================================================================


# ==================== RUTAS ESTÁTICAS ====================
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/fonts/<path:filename>")
def fonts(filename):
    return send_from_directory("fonts", filename)

@app.route("/static/<path:filename>")
def static_any(filename):
    return send_from_directory(".", filename)

# ==================== API: LIVE ====================
@app.route("/live")
def live():
    """
    Devuelve lista de dispositivos activos (máx 16).
    """
    try:
        limit = int(request.args.get("limit", "16"))
    except Exception:
        limit = 16
    limit = max(1, min(16, limit))

    now = time.time()
    sig = _state_signature(limit)
    key = (sig, limit)

    if _LIVE_CACHE["payload"] is not None:
        recently = (now - _LIVE_CACHE["ts"]) < MIN_REFRESH_S
        same = (_LIVE_CACHE["key"] == key)
        if recently or same:
            return jsonify(_LIVE_CACHE["payload"])

    _refresh_user_cache_if_needed()
    entries = []
    for dev, val in list(STATE.items())[:256]:
        hr = val.get("hr")
        ts = val.get("ts")
        user = _get_user_for_dev(dev)

        mrec = METRICS_CACHE.get(dev)
        if mrec and mrec.get("ts") == ts:
            m = mrec.get("metrics")
        else:
            m = SESSION.update(dev, user, hr, ts)
            METRICS_CACHE[dev] = {"ts": ts, "metrics": m}

        entries.append({"dev": dev, "hr": hr, "ts": ts, "user": user, "metrics": m})

    entries.sort(key=lambda e: (0 if e["user"] else 1, e["dev"]))
    entries = entries[:limit]

    _LIVE_CACHE.update({"payload": entries, "key": key, "ts": now})
    return jsonify(entries)

# ==================== API: DISPOSITIVOS LIBRES ====================
@app.route("/api/unassigned_devices")
def api_unassigned_devices():
    """
    Lista los pulsómetros detectados en los últimos N segundos que NO están asociados a usuario.
    GET /api/unassigned_devices?recent=45
    """
    try:
        recent = float(request.args.get("recent", str(DEVICE_RECENT_SECS)))
    except Exception:
        recent = DEVICE_RECENT_SECS

    now = time.time()
    out = []
    for dev, val in STATE.items():
        ts_iso = val.get("ts")
        ts = _parse_ts(ts_iso)
        if ts:
            age_s = now - ts.timestamp()
            if age_s > recent:
                continue
        if db.get_user_by_device(dev) is None:
            out.append({"dev": int(dev), "hr": val.get("hr"), "ts": ts_iso})
    out.sort(key=lambda d: d["dev"])
    return jsonify({"devices": out, "recent_window_s": recent})

# ==================== INTERFAZ USUARIOS ====================
@app.route("/users")
def users_list():
    users = db.list_users()
    return render_template("users.html", users=users)

@app.route("/users/add", methods=["GET", "POST"])
def add_user():
    if request.method == "POST":
        form = request.form

        # Auto/manual FC máx
        hr_max_auto = 1 if form.get("hr_max_auto") == "on" else 0
        hr_max_val = int(form.get("hr_max") or 0) or None
        if hr_max_auto:
            hr_max_val = None  # se calcula dinámicamente

        # Creamos usuario (device_id real se guarda si lo ponen; DEMO no se persiste)
        user_id = db.create_user(
            nombre=form.get("nombre", ""),
            apellido=form.get("apellido", ""),
            apodo=form.get("apodo", ""),
            edad=None,
            peso=float(form.get("peso") or 0) or None,
            device_id=int(form.get("device_id") or 0) or None,
            sexo=form.get("sexo", "M"),
            hr_rest=int(form.get("hr_rest") or 0) or None,
            hr_max=hr_max_val,
            is_sim=0,
            dob=form.get("dob") or None,
            hr_max_auto=hr_max_auto
        )

        # DEMO: si seleccionaron uno, asignarlo SOLO en memoria
        demo_dev = int(form.get("demo_dev") or 0) or None
        if demo_dev:
            assign_demo_to_user(demo_dev, user_id)

        return redirect(url_for("users_list"))

    # GET -> pasar slots al template
    return render_template("user_form.html", user=None, demo_slots=DEMO_SLOTS, demo_for_user=None)

@app.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
def edit_user(user_id):
    user = db.get_user(user_id)
    if not user:
        return "Usuario no encontrado", 404

    if request.method == "POST":
        form = request.form

        hr_max_auto = 1 if form.get("hr_max_auto") == "on" else 0
        hr_max_val = int(form.get("hr_max") or 0) or None
        if hr_max_auto:
            hr_max_val = None

        db.update_user(
            user_id,
            nombre=form.get("nombre", ""),
            apellido=form.get("apellido", ""),
            apodo=form.get("apodo", ""),
            edad=None,
            peso=float(form.get("peso") or 0) or None,
            device_id=int(form.get("device_id") or 0) or None,
            sexo=form.get("sexo", "M"),
            hr_rest=int(form.get("hr_rest") or 0) or None,
            hr_max=hr_max_val,
            dob=form.get("dob") or None,
            hr_max_auto=hr_max_auto
        )

        # DEMO: reasignar si viene alguno
        demo_dev = int(form.get("demo_dev") or 0) or None
        if demo_dev:
            assign_demo_to_user(demo_dev, user_id)
        else:
            # Si envían vacío y el usuario tenía DEMO, liberar
            if USER_TO_DEMO.get(user_id):
                assign_demo_to_user(USER_TO_DEMO[user_id], None)

        return redirect(url_for("users_list"))

    # GET -> indicar cuál tiene asignado este user ahora (si alguno)
    demo_for_user = USER_TO_DEMO.get(user_id)
    return render_template("user_form.html", user=user, demo_slots=DEMO_SLOTS, demo_for_user=demo_for_user)

@app.route("/users/delete/<int:user_id>")
def delete_user(user_id):
    user = db.get_user(user_id)
    if user:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()
    return redirect(url_for("users_list"))

# ==================== SESSION API (catálogo + control) ====================

# ---- Catálogo (listar) ----
@app.route("/session/classes", methods=["GET"])
def session_classes():
    """Catálogo de clases para UI (lista de modelos)."""
    return jsonify({"classes": sm.list_class_models()})

# ---- Catálogo (crear/editar) ----
@app.route("/session/classes", methods=["POST"])
def session_classes_upsert():
    data = request.get_json(silent=True) or {}
    class_id = (data.get("id") or "").strip()
    label    = (data.get("label") or "").strip()
    phases   = data.get("phases") or []

    if not class_id:
        return jsonify({"ok": False, "error": "id requerido"}), 400
    if not label:
        return jsonify({"ok": False, "error": "label requerido"}), 400
    if not isinstance(phases, list):
        return jsonify({"ok": False, "error": "phases debe ser lista"}), 400
    try:
        sm.upsert_class(class_id, label, phases)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# ---- Catálogo (eliminar) ----
@app.route("/session/classes/<class_id>", methods=["DELETE"])
def session_classes_delete(class_id):
    try:
        sm.delete_class(class_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# ---- Estado sesión ----
@app.route("/session/status")
def session_status():
    return jsonify(sm.SESSION.status())

# ---- Controles sesión ----
@app.route("/session/start", methods=["POST"])
def session_start():
    data = request.get_json(silent=True) or {}
    cid = data.get("class_id", "moov")
    try:
        sm.SESSION.start(cid)
        return jsonify({"ok": True, "class_id": cid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/session/stop", methods=["POST"])
def session_stop():
    sm.SESSION.stop()
    return jsonify({"ok": True})

@app.route("/session/next", methods=["POST"])
def session_next():
    sm.SESSION.next_phase()
    return jsonify({"ok": True})

@app.route("/session/prev", methods=["POST"])
def session_prev():
    sm.SESSION.prev_phase()
    return jsonify({"ok": True})

@app.route("/session/toggle_pause", methods=["POST"])
def session_toggle_pause():
    sm.SESSION.toggle_pause()
    return jsonify({"ok": True})

# ---- programación puntual legacy por epoch (opcional) ----
@app.route("/session/schedule", methods=["POST"])
def session_schedule():
    data = request.get_json(silent=True) or {}
    try:
        sm.SESSION.schedule(
            class_id=data.get("class_id", "moov"),
            start_epoch=float(data.get("start_epoch")),
            lead_s=int(data.get("lead_s", 0))
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/session/unschedule", methods=["POST"])
def session_unschedule():
    sm.SESSION.unschedule()
    return jsonify({"ok": True})

# ==================== SETTINGS: clase por defecto ====================
@app.route('/session/default_class', methods=["GET"])
def api_get_defclass():
    return jsonify({"default_class_id": sm.get_default_class_id()})

@app.route('/session/default_class', methods=["POST"])
def api_set_defclass():
    data = request.get_json(silent=True) or {}
    cid = data.get('class_id', 'moov')
    sm.set_default_class_id(cid)
    return jsonify({'ok': True, 'default_class_id': cid})

# ==================== CALENDARIO (solo horarios, con guardado masivo) ====================
@app.route("/session/calendar", methods=["GET"])
def api_list_schedule():
    return jsonify({"items": sm.SESSION.list_schedule()})

@app.route("/session/calendar", methods=["POST"])
def api_add_schedule():
    data = request.get_json(silent=True) or {}
    try:
        dow = int(data["dow"])
        time_str = str(data["time_str"])
        sched_id = sm.SESSION.add_schedule(dow, time_str)
        return jsonify({"ok": True, "sched_id": sched_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/session/calendar/<int:sched_id>", methods=["PUT"])
def api_update_schedule(sched_id: int):
    data = request.get_json(silent=True) or {}
    try:
        dow = data.get("dow")
        if dow is not None:
            dow = int(dow)
        time_str = data.get("time_str")
        sm.SESSION.update_schedule(sched_id, time_str=time_str, dow=dow)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/session/calendar/<int:sched_id>", methods=["DELETE"])
def api_delete_schedule(sched_id: int):
    sm.SESSION.delete_schedule(sched_id)
    return jsonify({"ok": True})

# ---- Reemplazo masivo (Guardar cambios) ----
@app.route("/session/calendar_bulk", methods=["PUT"])
def api_calendar_bulk():
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    try:
        sm.SESSION.replace_schedule(items)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

# ==================== ONE-OFF por día (sin hora) ====================
@app.route('/session/oneoff', methods=["GET"])
def api_oneoff_list():
    return jsonify({"items": sm.SESSION.list_oneoff()})

@app.route('/session/oneoff', methods=["POST"])
def api_oneoff_add():
    data = request.get_json(silent=True) or {}
    ymd = data.get('ymd')
    cid = data.get('class_id', 'moov')
    if not ymd:
        return jsonify({"ok": False, "error": "ymd requerido"}), 400
    sm.SESSION.add_oneoff(ymd, cid)
    return jsonify({'ok': True})

@app.route('/session/oneoff/<ymd>', methods=["DELETE"])
def api_oneoff_delete(ymd):
    sm.SESSION.delete_oneoff(ymd)
    return jsonify({'ok': True})

# ==================== VISTAS ====================
@app.route("/sessions")
def sessions_ui():
    """Página para editar clases y programar calendario."""
    return render_template("sessions.html")

@app.route("/sessionscontrol")
def sessions_control_ui():
    """Página de control de sesión (cronómetro y controles)."""
    return render_template("sessionscontrol.html")

# ==================== ADMIN: REFRESH CACHÉS ====================
@app.route("/admin/refresh", methods=["POST"])
def admin_refresh():
    """
    Resetea cachés para que /live refleje cambios de usuario.
    Body JSON opcional:
      - device_id: refrescar solo ese dispositivo (opcional)
      - reset_sessions: bool (por defecto False) -> si True, borra sesiones kcal/puntos
    """
    try:
        data = request.get_json(silent=True) or {}
        dev = data.get("device_id")
        reset_sessions = bool(data.get("reset_sessions", False))

        _LIVE_CACHE["payload"] = None
        _LIVE_CACHE["key"] = None
        _LIVE_CACHE["ts"] = 0.0

        if dev is None:
            USER_CACHE.clear()
            METRICS_CACHE.clear()
            if reset_sessions:
                SESSION.clear()
        else:
            USER_CACHE.pop(dev, None)
            METRICS_CACHE.pop(dev, None)
            if reset_sessions:
                SESSION.clear(dev)

        return jsonify({"ok": True, "device_id": dev, "reset_sessions": reset_sessions})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ==================== MAIN ====================
def main():
    db.init_db()
    sm.init_db_with_defaults()

    # Lanzar ANT real
    t_real = threading.Thread(target=hr_real.run_ant_listener, args=(STATE,), daemon=True)
    t_real.start()

    # Lanzar simulador SIEMPRE (aunque SIM_DEVICES=0, para limpieza de simulados en DB si aplica)
    if _HAS_SIM:
        hr_sim.start_simulator(
            STATE,
            n_devices=SIM_DEVICES,
            base_id=SIM_BASE_ID,
            update_hz=SIM_UPDATE_HZ,
            auto_create_users=SIM_AUTOCREATE,
            seed=42,
            cleanup_on_start=True
        )
        if SIM_DEVICES > 0:
            print(f"[SIM] Activado con {SIM_DEVICES} dispositivos desde {SIM_BASE_ID}; hz={SIM_UPDATE_HZ}.")
        else:
            print("[SIM] Desactivado (0 dispositivos). Solo limpieza de simulados.")
    else:
        if SIM_DEVICES > 0:
            print("[SIM] HRM: solicitado por HRM_SIM_DEVICES, pero 'hr_sim.py' no está disponible o importable.")

    print("Servidor en http://127.0.0.1:8000  (Ctrl+C para salir)")
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)

if __name__ == "__main__":
    main()
# ========================= fin hr_server.py =========================
