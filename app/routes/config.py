# app/routes/config.py
from flask import Blueprint, render_template, request, redirect, url_for

bp = Blueprint("config", __name__)

# página de configuración principal (solo “demo devices” de momento)
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
