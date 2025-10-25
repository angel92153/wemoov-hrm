# app/config.py
import os
from pathlib import Path

# --- Carpeta base para las DB: app/db ---
BASE_DIR = Path(__file__).resolve().parent / "db"
BASE_DIR.mkdir(parents=True, exist_ok=True)

def _resolve_db(env_key: str, default_name: str) -> str:
    """
    - Si la variable de entorno es una ruta ABSOLUTA -> usar tal cual.
    - Si es relativa o solo nombre -> ubicar dentro de BASE_DIR.
    - Si no existe -> default_name dentro de BASE_DIR.
    """
    val = os.getenv(env_key)
    if not val:
        return str(BASE_DIR / default_name)
    p = Path(val)
    return str(p) if p.is_absolute() else str(BASE_DIR / p.name)


class Config:
    # --- Seguridad ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

    # --- Rutas de DB (siempre dentro de app/db salvo que pases ABSOLUTAS) ---
    USERS_DB_PATH    = _resolve_db("USERS_DB", "users.db")
    SESSIONS_DB_PATH = _resolve_db("SESSIONS_DB", "sessions.db")

    # --- Parámetros del sistema ---
    HR_CACHE_TTL   = int(os.environ.get("HR_CACHE_TTL", "1"))
    LIVE_RECENT_MS = int(os.environ.get("LIVE_RECENT_MS", "45000"))
    KCAL_MODE      = os.environ.get("KCAL_MODE", "gross")
    MET_REST       = float(os.environ.get("MET_REST", "1.0"))
    SESSION_MODE   = os.environ.get("SESSION_MODE", "mixed")
