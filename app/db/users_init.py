# app/db/users_init.py
from __future__ import annotations
from .connection import get_conn

DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre      TEXT NOT NULL,
  apellido    TEXT NOT NULL,
  apodo       TEXT NOT NULL,
  sexo        TEXT NOT NULL,      -- "M" | "F"
  dob         TEXT NOT NULL,      -- "YYYY-MM-DD"
  peso        REAL NOT NULL,      -- kg
  device_id   INTEGER,            -- nullable
  hr_rest     INTEGER,            -- nullable
  hr_max      INTEGER,            -- nullable si hr_max_auto==1
  hr_max_auto INTEGER NOT NULL DEFAULT 1,
  is_sim      INTEGER NOT NULL DEFAULT 0
);
"""

INDEXES = [
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_users_device_id
    ON users(device_id)
    WHERE device_id IS NOT NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_users_is_sim ON users(is_sim)",
]

def init_users_db(db_path: str | None = None) -> None:
    with get_conn(db_path) as c:
        c.executescript(DDL_USERS)
        for ddl in INDEXES:
            c.execute(ddl)
