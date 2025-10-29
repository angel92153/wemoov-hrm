from __future__ import annotations
from flask import Flask, jsonify
from .config import Config
from .utils.filters import register_template_filters
from app.db.init_all import init_all

def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_class)

    # Exponer TODAS las rutas de DB en app.config
    app.config["USERS_DB_PATH"]         = config_class.USERS_DB_PATH
    app.config["SESSIONS_DB_PATH"]      = config_class.SESSIONS_DB_PATH
    app.config["SESSION_RUNS_DB_PATH"]  = config_class.SESSION_RUNS_DB_PATH
    app.config["SUMMARIES_DB_PATH"]     = config_class.SUMMARIES_DB_PATH

    # Parámetros de resumen/live (por si se usan en runtime)
    app.config["SUMMARY_BUCKET_MS"] = config_class.SUMMARY_BUCKET_MS
    app.config["SUMMARY_SHOW_MS"]   = config_class.SUMMARY_SHOW_MS
    app.config["LIVE_RECENT_MS"]    = config_class.LIVE_RECENT_MS
    app.config["LIVE_FADE_MS"]      = config_class.LIVE_FADE_MS

    # 1) Inicializa DBs y métricas dentro de app context
    with app.app_context():
        init_all()
        from app.services.metrics_core import init_from_app
        init_from_app(app)

    # 2) Filtros Jinja
    register_template_filters(app)

    # 3) Blueprints (después de init_all / init_from_app)
    from .routes.screens import bp as screens_bp
    from .routes.users import bp as users_bp
    from .routes.live import bp as live_bp
    from .routes.api import bp as api_bp
    from .routes.control import bp as control_bp
    from .routes.sessions import sessions_bp
    from .routes.users_summaries import bp as users_summaries_bp
    from .routes.config import bp as config_bp

    app.register_blueprint(screens_bp)                          # "/"
    app.register_blueprint(users_bp,    url_prefix="/users")    # "/users/*"
    app.register_blueprint(live_bp)                             # "/live*"
    app.register_blueprint(api_bp,      url_prefix="/api")      # "/api/*"
    app.register_blueprint(control_bp)                          # "/control*"
    app.register_blueprint(sessions_bp, url_prefix="/sessions") # "/sessions/*"
    app.register_blueprint(users_summaries_bp)  # expone /users/summaries y /api/*
    app.register_blueprint(config_bp)

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
        print(f"[DB] users           -> {app.config['USERS_DB_PATH']}")
        print(f"[DB] sessions        -> {app.config['SESSIONS_DB_PATH']}")
        print(f"[DB] session_runs    -> {app.config['SESSION_RUNS_DB_PATH']}")
        print(f"[DB] summaries       -> {app.config['SUMMARIES_DB_PATH']}")
        with app.app_context():
            print("\n== URL MAP ==")
            for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
                print(f"{rule.endpoint:26s} -> {rule.rule}")
            print("==============\n")

    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)  # o logging.ERROR

    return app
