import sqlite3
from contextlib import contextmanager
from pathlib import Path

def _apply_pragmas(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # Integridad
    cur.execute("PRAGMA foreign_keys = ON;")
    # Rendimiento
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("PRAGMA temp_store = MEMORY;")
    cur.execute("PRAGMA cache_size = -20000;")  # ~20MB
    cur.close()

@contextmanager
def get_conn(db_path: str):
    Path(db_path).touch(exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
