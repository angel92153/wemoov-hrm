# app/db/paths.py
from __future__ import annotations
from pathlib import Path
import os

# Carpeta de datos = la propia carpeta del paquete: app/db
BASE_DIR = Path(__file__).resolve().parent
BASE_DIR.mkdir(parents=True, exist_ok=True)

def _resolve_inside_base(env_key: str, default_name: str) -> str:
    """
    - Si la env var es absoluta -> úsala tal cual.
    - Si es relativa -> forzamos a dejarla dentro de BASE_DIR tomando solo el nombre del archivo.
    """
    val = os.getenv(env_key)
    if not val:
        return str(BASE_DIR / default_name)
    p = Path(val)
    if p.is_absolute():
        return str(p)
    return str(BASE_DIR / p.name)

USERS_DB_PATH    = _resolve_inside_base("USERS_DB", "users.db")
SESSIONS_DB_PATH = _resolve_inside_base("SESSIONS_DB", "sessions.db")

# Compatibilidad con código antiguo
DB_PATH = USERS_DB_PATH

if __name__ == "__main__":
    print("📦 BASE_DIR        :", BASE_DIR)
    print("👤 USERS_DB_PATH   :", USERS_DB_PATH)
    print("📅 SESSIONS_DB_PATH:", SESSIONS_DB_PATH)
