from flask import current_app
from .users_init import init_users_db
from .sessions_init import init_sessions_db
from .runs_init import ensure_session_runs_schema
from .summaries_init import ensure_summaries_schema

def init_all():
    """Inicializa todas las bases de datos requeridas."""
    users_db_path = current_app.config["USERS_DB_PATH"]
    sessions_db_path = current_app.config["SESSIONS_DB_PATH"]
    runs_db_path = current_app.config["SESSION_RUNS_DB_PATH"]
    summaries_db_path = current_app.config["SUMMARIES_DB_PATH"]

    # Usuarios y sesiones antiguas
    init_users_db(users_db_path)
    init_sessions_db(sessions_db_path)

    # Nuevas bases: runs y summaries
    ensure_session_runs_schema(runs_db_path)
    ensure_summaries_schema(summaries_db_path)
