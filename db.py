import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).with_name("users.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre    TEXT NOT NULL,
  apellido  TEXT NOT NULL,
  apodo     TEXT NOT NULL,
  edad      INTEGER,            -- legacy (opcional)
  peso      REAL,
  device_id INTEGER UNIQUE,
  sexo      TEXT,               -- 'M' o 'F' (sin CHECK para no romper migraciones)
  hr_rest   INTEGER,            -- FC en reposo (opcional; puede ser NULL)
  hr_max    INTEGER,            -- FC máxima (opcional)
  is_sim    INTEGER DEFAULT 0,  -- 0=real, 1=simulado
  dob       TEXT,               -- YYYY-MM-DD (opcional)
  hr_max_auto INTEGER DEFAULT 1 -- 1=auto (Tanaka), 0=manual
);
"""

IDX = """
CREATE INDEX IF NOT EXISTS idx_users_is_sim ON users(is_sim);
"""

@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

def _ensure_schema():
    """Crea la tabla y aplica migraciones suaves (añadir columnas/índices si faltan)."""
    with get_conn() as conn:
        conn.execute(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        # Migraciones idempotentes por si vienes de un esquema antiguo
        if "sexo" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN sexo TEXT")
        if "hr_rest" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN hr_rest INTEGER")
        if "hr_max" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN hr_max INTEGER")
        if "is_sim" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_sim INTEGER DEFAULT 0")
        if "dob" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN dob TEXT")
        if "hr_max_auto" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN hr_max_auto INTEGER DEFAULT 1")
        # Índices
        conn.execute(IDX)
        conn.commit()

def init_db():
    _ensure_schema()

# -------------------------------------------------------

def clear_simulated_users():
    """Elimina todos los usuarios marcados como simulados."""
    _ensure_schema()
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE is_sim=1")
        conn.commit()
        print("[DB] Usuarios simulados anteriores eliminados.")

def create_user(nombre, apellido, apodo, edad=None, peso=None,
                device_id=None, sexo=None, hr_rest=None, hr_max=None,
                is_sim=0, dob=None, hr_max_auto=1):
    """
    Crea un nuevo usuario.
    - hr_rest/hr_max son opcionales (None).
    - dob (YYYY-MM-DD) opcional.
    - hr_max_auto: 1=auto (usar Tanaka si falta hr_max), 0=manual.
    """
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users
              (nombre, apellido, apodo, edad, peso, device_id, sexo, hr_rest, hr_max, is_sim, dob, hr_max_auto)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nombre, apellido, apodo, edad, peso, device_id, sexo, hr_rest, hr_max, is_sim, dob, hr_max_auto))
        conn.commit()
        return cur.lastrowid

# -------------------------------------------------------

def list_users():
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo,
                   hr_rest, hr_max, is_sim, dob, hr_max_auto
            FROM users
            ORDER BY id DESC
        """)
        rows = cur.fetchall()
        return [row_to_dict(r) for r in rows]

def get_user(user_id):
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo,
                   hr_rest, hr_max, is_sim, dob, hr_max_auto
            FROM users
            WHERE id=?
        """, (user_id,))
        row = cur.fetchone()
        return row_to_dict(row) if row else None

def get_user_by_device(device_id: int):
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo,
                   hr_rest, hr_max, is_sim, dob, hr_max_auto
            FROM users
            WHERE device_id=?
        """, (device_id,))
        row = cur.fetchone()
        return row_to_dict(row) if row else None

# -------------------------------------------------------

def update_user(user_id, **fields):
    """Actualiza campos permitidos, incluyendo dob y hr_max_auto."""
    _ensure_schema()
    if not fields:
        return get_user(user_id)

    allowed = {
        "nombre","apellido","apodo","edad","peso","device_id","sexo",
        "hr_rest","hr_max","is_sim","dob","hr_max_auto"
    }
    cols, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            cols.append(f"{k}=?")
            vals.append(v)

    if not cols:
        return get_user(user_id)

    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE id=?", (*vals, user_id))
        conn.commit()

    return get_user(user_id)

# -------------------------------------------------------

def row_to_dict(r):
    return {
        "id":          r[0],
        "nombre":      r[1],
        "apellido":    r[2],
        "apodo":       r[3],
        "edad":        r[4],
        "peso":        r[5],
        "device_id":   r[6],
        "sexo":        r[7],
        "hr_rest":     r[8],
        "hr_max":      r[9],
        "is_sim":      r[10],
        "dob":         r[11],
        "hr_max_auto": r[12],
    }
