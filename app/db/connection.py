import sqlite3
from contextlib import contextmanager
from pathlib import Path
from flask import current_app, has_app_context


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("PRAGMA temp_store = MEMORY;")
    cur.execute("PRAGMA cache_size = -20000;")
    cur.close()


# ---------- rutas ----------
def _users_db_path() -> Path:
    if not has_app_context():
        raise RuntimeError("get_users_conn() requiere app.app_context() activo")
    return Path(current_app.config["USERS_DB_PATH"])


def _sessions_db_path() -> Path:
    if not has_app_context():
        raise RuntimeError("get_sessions_conn() requiere app.app_context() activo")
    return Path(current_app.config["SESSIONS_DB_PATH"])


def _session_runs_db_path() -> Path:
    if not has_app_context():
        raise RuntimeError("get_session_runs_conn() requiere app.app_context() activo")
    # Nueva DB para las sesiones crudas (donde escribe session_dump)
    return Path(current_app.config["SESSION_RUNS_DB_PATH"])


def _summaries_db_path() -> Path:
    if not has_app_context():
        raise RuntimeError("get_summaries_conn() requiere app.app_context() activo")
    # DB ligera para resúmenes
    return Path(current_app.config["SUMMARIES_DB_PATH"])


# ---------- conexión base ----------
@contextmanager
def get_conn(db_path: str | Path):
    path = Path(db_path)
    path.parent.mkdir(exist_ok=True, parents=True)
    path.touch(exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- contextos específicos ----------
@contextmanager
def get_users_conn():
    with get_conn(_users_db_path()) as conn:
        yield conn


@contextmanager
def get_sessions_conn():
    with get_conn(_sessions_db_path()) as conn:
        yield conn


@contextmanager
def get_session_runs_conn():
    with get_conn(_session_runs_db_path()) as conn:
        yield conn


@contextmanager
def get_summaries_conn():
    with get_conn(_summaries_db_path()) as conn:
        yield conn
