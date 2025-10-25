from flask import current_app
from .users_init import init_users_db
from .sessions_init import init_sessions_db

def init_all():
    """Inicializa ambas bases de datos: usuarios y sesiones."""
    users_db_path = current_app.config["USERS_DB_PATH"]
    sessions_db_path = current_app.config["SESSIONS_DB_PATH"]

    init_users_db(users_db_path)
    init_sessions_db(sessions_db_path)
