from __future__ import annotations
from .connection import get_conn

# --- Tabla USERS (con email y demo_device incluidos) ---
DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre       TEXT NOT NULL,
  apellido     TEXT NOT NULL,
  apodo        TEXT NOT NULL,
  email        TEXT,              -- opcional
  sexo         TEXT NOT NULL,     -- "M" | "F"
  dob          TEXT NOT NULL,     -- "YYYY-MM-DD"
  peso         REAL NOT NULL,     -- kg
  device_id    INTEGER,           -- pulsómetro real (nullable)
  demo_device  INTEGER,           -- pulsómetro demo temporal (nullable)
  hr_rest      INTEGER,           -- nullable
  hr_max       INTEGER,           -- nullable si hr_max_auto==1
  hr_max_auto  INTEGER NOT NULL DEFAULT 1,
  is_sim       INTEGER NOT NULL DEFAULT 0
);
"""

# --- Tabla DEMO_DEVICES ---
DDL_DEMO_DEVICES = """
CREATE TABLE IF NOT EXISTS demo_devices (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id  INTEGER NOT NULL UNIQUE
);
"""

# --- Índices ---
INDEXES = [
    # Unicidad para los pulsómetros reales
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_users_device_id
    ON users(device_id)
    WHERE device_id IS NOT NULL
    """,
    # Búsquedas frecuentes
    "CREATE INDEX IF NOT EXISTS idx_users_is_sim ON users(is_sim)",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS idx_users_demo_device ON users(demo_device)",
    # Tabla de demo devices
    "CREATE INDEX IF NOT EXISTS idx_demo_devices_device_id ON demo_devices(device_id)"
]


def init_users_db(db_path: str | None = None) -> None:
    """Inicializa las tablas de usuarios y demo_devices."""
    with get_conn(db_path) as c:
        # Crear tablas
        c.executescript(DDL_USERS)
        c.executescript(DDL_DEMO_DEVICES)

        # Crear índices
        for ddl in INDEXES:
            c.execute(ddl)
