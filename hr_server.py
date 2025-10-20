# hr_server.py
import threading
from flask import Flask, jsonify, request, send_from_directory, send_file
import ant_hr
import db
import metrics

# --- Flask app primero (para que exista 'app' antes de usar @app.route) ---
app = Flask(__name__)  # sirve index.html con send_from_directory más abajo

# --- Estado en memoria ---
STATE = {}                 # {dev_id: {"hr": int, "ts": iso}}
SESSION = metrics.SessionStore()

# ---------- Rutas estáticas ----------
@app.route("/")
def index():
    # index.html está en el mismo directorio que este archivo
    return send_from_directory(".", "index.html")

@app.route("/fonts/<path:filename>")
def fonts(filename):
    # sirve archivos de la carpeta ./fonts (debe existir)
    return send_from_directory("fonts", filename)

# (opcional) sirve cualquier archivo del directorio actual
@app.route("/static/<path:filename>")
def static_any(filename):
    return send_from_directory(".", filename)

# ---------- API de datos ----------
@app.route("/live")
def live():
    """
    Devuelve lista de dispositivos activos (máx 16):
    [
      { dev, hr, ts, user, metrics:{hr_max, zone, kcal, moov_points} },
      ...
    ]
    """
    db.init_db()
    try:
        limit = int(request.args.get("limit", "16"))
    except Exception:
        limit = 16

    entries = []
    for dev, val in list(STATE.items())[:256]:
        hr = val.get("hr")
        ts = val.get("ts")
        user = db.get_user_by_device(dev)
        m = SESSION.update(dev, user, hr, ts)
        entries.append({
            "dev": dev,
            "hr": hr,
            "ts": ts,
            "user": user,      # contiene apodo, edad, peso, sexo...
            "metrics": m       # {hr_max, zone, kcal, moov_points}
        })

    # primero los vinculados a usuario, luego por id
    entries.sort(key=lambda e: (0 if e["user"] else 1, e["dev"]))
    entries = entries[:max(1, min(16, limit))]
    return jsonify(entries)

def main():
    db.init_db()
    t = threading.Thread(target=ant_hr.run_ant_listener, args=(STATE,), daemon=True)
    t.start()
    print("Servidor en http://127.0.0.1:8000  (Ctrl+C para salir)")
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)

if __name__ == "__main__":
    main()
