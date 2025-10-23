from .connection import get_conn

DDL_BASE = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  -- columnas mínimas para asegurar tabla (luego migramos)
  nombre    TEXT,
  apellido  TEXT,
  apodo     TEXT,
  device_id INTEGER UNIQUE
);
"""

def _ensure_schema(c):
    # Crea base si no existe
    c.executescript(DDL_BASE)

    # Columnas actuales
    cols = {row["name"] for row in c.execute("PRAGMA table_info(users)").fetchall()}

    # Migraciones suaves -> añade si faltan (idempotente)
    add_if_missing = [
        ("nombre",      "TEXT"),
        ("apellido",    "TEXT"),
        ("apodo",       "TEXT"),
        ("edad",        "INTEGER"),
        ("peso",        "REAL"),
        ("device_id",   "INTEGER"),  # <-- sin UNIQUE aquí
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

    # Índices (créalo después de garantizar la columna)
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_is_sim ON users(is_sim)")
    # Índice único parcial: permite múltiples NULL y asegura unicidad cuando hay valor
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_device_id
        ON users(device_id)
        WHERE device_id IS NOT NULL
    """)


def init_users_db(db_path: str, seed: bool = False):
    with get_conn(db_path) as c:
        _ensure_schema(c)
        if seed:
            row = c.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            if row["c"] == 0:
                c.executemany(
                    """INSERT INTO users(nombre, apellido, apodo, edad, peso, device_id,
                                          sexo, hr_rest, hr_max, is_sim, dob, hr_max_auto)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        ("Alicia","García","Ali",   28, 62.0, 101, "F", None, None, 1, "1997-01-05", 1),
                        ("Bruno", "López", "Brun",  35, 78.5, 102, "M", None, 192,  0, "1990-07-22", 0),
                        ("Carla", "Ríos",  "Car",   30, 55.2, 103, "F", None, None, 1, "1995-09-10", 1),
                        ("Diego", "Paz",   "Dieg",  26, 80.1, 104, "M", None, None, 1, "1999-03-15", 1),
                    ],
                )
