import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .paths import USERS_DB_PATH, SESSIONS_DB_PATH  # y DB_PATH si aún lo necesitas

def _apply_pragmas(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("PRAGMA temp_store = MEMORY;")
    cur.execute("PRAGMA cache_size = -20000;")
    cur.close()

@contextmanager
def get_conn(db_path: str | None = None):
    # Por defecto: users.db
    path = Path(db_path or USERS_DB_PATH)
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

# Atajos convenientes (opcional)
@contextmanager
def get_users_conn():
    with get_conn(USERS_DB_PATH) as conn:
        yield conn

@contextmanager
def get_sessions_conn():
    with get_conn(SESSIONS_DB_PATH) as conn:
        yield conn
