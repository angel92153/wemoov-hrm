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
  edad      INTEGER,
  peso      REAL,
  device_id INTEGER UNIQUE,
  sexo      TEXT,     -- 'M' o 'F'
  hr_rest   INTEGER,  -- FC en reposo (opcional)
  hr_max    INTEGER,  -- FC máxima (opcional)
  is_sim    INTEGER DEFAULT 0  -- 0=real, 1=simulado
);
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
    """Crea la tabla y aplica migraciones suaves (añadir columnas si faltan)."""
    with get_conn() as conn:
        conn.execute(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "sexo" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN sexo TEXT")
        if "hr_rest" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN hr_rest INTEGER")
        if "hr_max" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN hr_max INTEGER")
        if "is_sim" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_sim INTEGER DEFAULT 0")
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
                device_id=None, sexo=None, hr_rest=None, hr_max=None, is_sim=0):
    """Crea un nuevo usuario (FC reposo y FC máx opcionales; flag simulado)."""
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (nombre, apellido, apodo, edad, peso, device_id, sexo, hr_rest, hr_max, is_sim)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nombre, apellido, apodo, edad, peso, device_id, sexo, hr_rest, hr_max, is_sim))
        conn.commit()
        return cur.lastrowid

# -------------------------------------------------------

def list_users():
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo, hr_rest, hr_max, is_sim
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
            SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo, hr_rest, hr_max, is_sim
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
            SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo, hr_rest, hr_max, is_sim
            FROM users
            WHERE device_id=?
        """, (device_id,))
        row = cur.fetchone()
        return row_to_dict(row) if row else None

# -------------------------------------------------------

def update_user(user_id, **fields):
    """Actualiza campos permitidos, incluyendo hr_rest, hr_max e is_sim."""
    _ensure_schema()
    if not fields:
        return get_user(user_id)

    allowed = {"nombre","apellido","apodo","edad","peso","device_id","sexo","hr_rest","hr_max","is_sim"}
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
        "id":        r[0],
        "nombre":    r[1],
        "apellido":  r[2],
        "apodo":     r[3],
        "edad":      r[4],
        "peso":      r[5],
        "device_id": r[6],
        "sexo":      r[7],
        "hr_rest":   r[8],
        "hr_max":    r[9],
        "is_sim":    r[10],
    }
