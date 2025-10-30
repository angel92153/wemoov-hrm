# app/routes/config.py
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, current_app

bp = Blueprint("config", __name__)

# ─────────────────────────────────────────────────────────────
# Página de configuración principal (solo “demo devices” de momento)
# ─────────────────────────────────────────────────────────────
@bp.route("/config/demo-devices")
def config_demo_devices():
    # pide la contraseña “admin” vía query o formulario simple
    pwd = request.args.get("pwd")
    if pwd != "admin":
        return redirect(url_for("config.login"))

    return render_template("demo_devices.html")


@bp.route("/config/login")
def login():
    # formulario mínimo de login para acceder a configuración
    return """
    <html>
      <body style="background:#0a0a0a;color:white;font-family:Montserrat;text-align:center;margin-top:80px;">
        <h2>Acceso configuración</h2>
        <form method="get" action="/config/demo-devices">
          <input type="password" name="pwd" placeholder="Contraseña" style="padding:10px;border-radius:8px;border:none;">
          <button type="submit" style="padding:10px 16px;margin-left:8px;border:none;border-radius:8px;background:#8b5cf6;color:white;">Entrar</button>
        </form>
      </body>
    </html>
    """

# ─────────────────────────────────────────────────────────────
# Config pública (JSON) — usada por el frontend JS
# ─────────────────────────────────────────────────────────────
@bp.route("/live/config")
def live_config():
    """
    Devuelve la configuración activa del sistema para el frontend Live.
    Se alimenta desde app/config.py (clase Config cargada en current_app.config)
    """
    cfg = current_app.config
    return jsonify({
        "fade_ms": cfg.get("LIVE_FADE_MS"),
        "recent_ms": cfg.get("LIVE_RECENT_MS"),
        "summary_ms": cfg.get("SUMMARY_SHOW_MS"),
        "bucket_ms": cfg.get("SUMMARY_BUCKET_MS"),
    })
