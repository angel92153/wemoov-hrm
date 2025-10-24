# app/db/users_init.py
from .connection import get_conn

DDL_BASE = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre    TEXT,
  apellido  TEXT,
  apodo     TEXT,
  device_id INTEGER
);
"""

def _ensure_schema(c):
    # Base
    c.executescript(DDL_BASE)

    # Columnas actuales
    cols = {row["name"] for row in c.execute("PRAGMA table_info(users)").fetchall()}

    # Migraciones idempotentes
    add_if_missing = [
        ("nombre",      "TEXT"),
        ("apellido",    "TEXT"),
        ("apodo",       "TEXT"),
        ("edad",        "INTEGER"),
        ("peso",        "REAL"),
        ("device_id",   "INTEGER"),
        ("sexo",        "TEXT"),
        ("hr_rest",     "INTEGER"),
        ("hr_max",      "INTEGER"),
        ("is_sim",      "INTEGER DEFAULT 0"),
        ("dob",         "TEXT"),
        ("hr_max_auto", "INTEGER DEFAULT 1"),
    ]
    for col, typ in add_if_missing:
        if col not in cols:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")

    # Índices
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_is_sim ON users(is_sim)")
    # Único parcial: permite múltiples NULL y asegura unicidad cuando hay valor
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_device_id
        ON users(device_id)
        WHERE device_id IS NOT NULL
    """)

def init_users_db(db_path: str) -> None:
    """Asegura el esquema. No inserta datos de ejemplo."""
    with get_conn(db_path) as c:
        _ensure_schema(c)
