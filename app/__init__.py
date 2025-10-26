# app/__init__.py
from __future__ import annotations

from flask import Flask, jsonify
from .config import Config
from .utils.filters import register_template_filters
from app.db.init_all import init_all

def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_class)

    # Exponer rutas de DB explícitamente (ya vienen de Config)
    app.config["USERS_DB_PATH"] = config_class.USERS_DB_PATH
    app.config["SESSIONS_DB_PATH"] = config_class.SESSIONS_DB_PATH

    # 1) Inicializa bases de datos y métricas dentro de app context
    with app.app_context():
        init_all()
        # Inicializar métricas desde la config de Flask
        from app.services.metrics_core import init_from_app
        init_from_app(app)


    # 2) Filtros Jinja
    register_template_filters(app)

    # 3) Importa y registra blueprints DESPUÉS de init_all() e init_from_app()
    from .routes.screens import bp as screens_bp
    from .routes.users import bp as users_bp
    from .routes.live import bp as live_bp
    from .routes.api import bp as api_bp
    from .routes.control import bp as control_bp
    from .routes.sessions import sessions_bp

    app.register_blueprint(screens_bp)                          # "/"
    app.register_blueprint(users_bp,    url_prefix="/users")    # "/users/*"
    app.register_blueprint(live_bp)                             # define su propio prefix si aplica
    app.register_blueprint(api_bp,      url_prefix="/api")      # "/api/*"
    app.register_blueprint(control_bp)
    app.register_blueprint(sessions_bp, url_prefix="/sessions") # "/sessions/*"

    # 4) Cabeceras de caché
    @app.after_request
    def add_headers(resp):
        mt = (resp.mimetype or "")
        if resp.cache_control.public is None and mt.startswith(("image", "font", "text/css")):
            resp.cache_control.public = True
            resp.cache_control.max_age = 86400  # 1 día
        else:
            resp.cache_control.no_store = True
        return resp

    # 5) Manejadores de error
    @app.errorhandler(404)
    def _404(_e):
        return jsonify(error="not_found"), 404

    @app.errorhandler(500)
    def _500(_e):
        return jsonify(error="server_error"), 500

    # 6) Debug helpers
    if app.debug:
        app.jinja_env.auto_reload = True
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        print(f"[DB] users   -> {app.config['USERS_DB_PATH']}")
        print(f"[DB] sessions-> {app.config['SESSIONS_DB_PATH']}")
        with app.app_context():
            print("\n== URL MAP ==")
            for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
                print(f"{rule.endpoint:26s} -> {rule.rule}")
            print("==============\n")

    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)  # o logging.ERROR para ocultar todo

    return app
