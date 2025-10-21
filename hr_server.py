import threading
import time
import logging
import os
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory, render_template, redirect, url_for

import ant_hr
import db
import metrics

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

# ======================= CONFIG SERVIDOR =======================
SIM_DEVICES   = int(os.getenv("HRM_SIM_DEVICES", "0"))
SIM_BASE_ID   = int(os.getenv("HRM_SIM_BASE_ID", "10000"))
SIM_UPDATE_HZ = float(os.getenv("HRM_SIM_UPDATE_HZ", "2"))
SIM_AUTOCREATE = os.getenv("HRM_SIM_AUTOCREATE", "1") in ("1", "true", "yes", "on")

MIN_REFRESH_S = float(os.getenv("HRM_MIN_REFRESH_S", "2.5"))

# Dispositivos “recientes” (para el desplegable de pulsómetros libres)
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

def _get_user(dev):
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
    """Parse ISO8601 -> datetime UTC (local helper para /api/unassigned_devices)."""
    if not ts_iso:
        return None
    try:
        return datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

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
        user = _get_user(dev)

        mrec = METRICS_CACHE.get(dev)
        if mrec and mrec.get("ts") == ts:
            m = mrec.get("metrics")
        else:
            # Nota: metrics.update usa hr_max manual si existe; si no, Tanaka.
            m = SESSION.update(dev, user, hr, ts)  # mode="mixed" por defecto en metrics
            METRICS_CACHE[dev] = {"ts": ts, "metrics": m}

        entries.append({
            "dev": dev,
            "hr": hr,
            "ts": ts,
            "user": user,
            "metrics": m
        })

    entries.sort(key=lambda e: (0 if e["user"] else 1, e["dev"]))
    entries = entries[:limit]

    _LIVE_CACHE.update({"payload": entries, "key": key, "ts": now})
    return jsonify(entries)

# ==================== API: DISPOSITIVOS LIBRES ====================
@app.route("/api/unassigned_devices")
def api_unassigned_devices():
    """
    Lista los pulsómetros detectados en STATE en los últimos N segundos
    (por defecto 45s) que NO están asociados a ningún usuario (DB).
      GET /api/unassigned_devices?recent=45
    Respuesta:
      { "devices": [ { "dev": 36466, "hr": 128, "ts": "..." }, ... ] }
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
                continue  # muy viejo
        # si no hay ts parseable, igualmente lo exponemos (por si acaso)
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
        db.create_user(
            nombre=form.get("nombre", ""),
            apellido=form.get("apellido", ""),
            apodo=form.get("apodo", ""),
            edad=int(form.get("edad") or 0),
            peso=float(form.get("peso") or 0),
            device_id=int(form.get("device_id") or 0),
            sexo=form.get("sexo", "M"),
            hr_rest=int(form.get("hr_rest") or 0) or None,
            hr_max=int(form.get("hr_max") or 0) or None,  # FC máx manual opcional
            is_sim=0
        )
        return redirect(url_for("users_list"))
    return render_template("user_form.html", user=None)

@app.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
def edit_user(user_id):
    user = db.get_user(user_id)
    if not user:
        return "Usuario no encontrado", 404
    if request.method == "POST":
        form = request.form
        db.update_user(
            user_id,
            nombre=form.get("nombre", ""),
            apellido=form.get("apellido", ""),
            apodo=form.get("apodo", ""),
            edad=int(form.get("edad") or 0),
            peso=float(form.get("peso") or 0),
            device_id=int(form.get("device_id") or 0),
            sexo=form.get("sexo", "M"),
            hr_rest=int(form.get("hr_rest") or 0) or None,
            hr_max=int(form.get("hr_max") or 0) or None   # FC máx manual opcional
        )
        return redirect(url_for("users_list"))
    return render_template("user_form.html", user=user)

@app.route("/users/delete/<int:user_id>")
def delete_user(user_id):
    user = db.get_user(user_id)
    if user:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()
    return redirect(url_for("users_list"))

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
        reset_sessions = bool(data.get("reset_sessions", False))  # por defecto NO resetea

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

    # Lanzar ANT real
    t_real = threading.Thread(target=ant_hr.run_ant_listener, args=(STATE,), daemon=True)
    t_real.start()

    # Lanzar simulador SIEMPRE (aunque SIM_DEVICES=0, así limpia is_sim)
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
