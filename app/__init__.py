# app/__init__.py
from __future__ import annotations

from flask import Flask, jsonify
from .config import Config

# Blueprints
from .routes.dashboard import bp as dashboard_bp
from .routes.sessions import bp as sessions_bp
from .routes.users import bp as users_bp
from .routes.live import bp as live_bp
from .routes.compat import bp as compat_bp
from .routes.control import bp as control_bp
from .routes.api import bp as api_bp  # /api/unassigned_devices

# DB inits
from app.services.session_manager import init_db_with_defaults  # sessions.db
from app.db.users_init import init_users_db                     # users.db

# Jinja filters
from .utils.filters import register_template_filters


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_class)

    # ----------------------------------
    # Inicialización de bases de datos
    # ----------------------------------
    init_db_with_defaults()  # crea/actualiza sessions.db (clases, etc.)
    init_users_db(app.config["USERS_DB_PATH"])

    # ----------------------------------
    # Filtros de plantilla (edad / Tanaka)
    # ----------------------------------
    register_template_filters(app)

    # ----------------------------------
    # Blueprints
    # ----------------------------------
    app.register_blueprint(dashboard_bp)                      # "/"
    app.register_blueprint(users_bp,    url_prefix="/users")  # "/users/*"
    app.register_blueprint(sessions_bp, url_prefix="/sessions")
    app.register_blueprint(live_bp)                           # "/live"
    app.register_blueprint(compat_bp)                         # "/session/status" (alias compat)
    app.register_blueprint(control_bp)                        # "/control"
    app.register_blueprint(api_bp,      url_prefix="/api")    # "/api/*"

    # ----------------------------------
    # Cabeceras de caché
    # ----------------------------------
    @app.after_request
    def add_headers(resp):
        # Sirve imágenes, fuentes y CSS con cache de 1 día; el resto no-cache
        mt = (resp.mimetype or "")
        if resp.cache_control.public is None and mt.startswith(("image", "font", "text/css")):
            resp.cache_control.public = True
            resp.cache_control.max_age = 86400  # 1 día
        else:
            resp.cache_control.no_store = True
        return resp

    # ----------------------------------
    # Manejadores de error
    # ----------------------------------
    @app.errorhandler(404)
    def _404(_e):
        return jsonify(error="not_found"), 404

    @app.errorhandler(500)
    def _500(_e):
        return jsonify(error="server_error"), 500

    # ----------------------------------
    # Debug helpers
    # ----------------------------------
    if app.debug:
        # Recarga de plantillas en caliente y mapa de URLs
        app.jinja_env.auto_reload = True
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        with app.app_context():
            print("\n== URL MAP ==")
            for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
                print(f"{rule.endpoint:26s} -> {rule.rule}")
            print("==============\n")

    return app
