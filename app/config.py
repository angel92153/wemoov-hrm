# app/config.py
import os
from pathlib import Path

# --- Carpeta base para las DB: app/db ---
BASE_DIR = Path(__file__).resolve().parent / "db"
BASE_DIR.mkdir(parents=True, exist_ok=True)

def _resolve_db(env_key: str, default_name: str) -> str:
    val = os.getenv(env_key)
    if not val:
        return str(BASE_DIR / default_name)
    p = Path(val)
    return str(p) if p.is_absolute() else str(BASE_DIR / p.name)

class Config:
    # ─────────────────────────────────────────────────────────────
    # Seguridad
    # ─────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

    # ─────────────────────────────────────────────────────────────
    # Bases de datos
    # ─────────────────────────────────────────────────────────────
    USERS_DB_PATH    = _resolve_db("USERS_DB", "users.db")
    SESSIONS_DB_PATH = _resolve_db("SESSIONS_DB", "sessions.db")

    # ─────────────────────────────────────────────────────────────
    # Métricas / Energía / Puntos
    # ─────────────────────────────────────────────────────────────
    KCAL_MODE = os.environ.get("KCAL_MODE", "gross")
    MET_REST = float(os.environ.get("MET_REST", "1.0"))
    SESSION_MODE = os.environ.get("SESSION_MODE", "mixed")

    MOOV_TARGET_POINTS = int(os.environ.get("MOOV_TARGET_POINTS", "3000"))
    MOOV_TARGET_MINUTES_MOD = int(os.environ.get("MOOV_TARGET_MINUTES_MOD", "300"))
    MOOV_INTENSE_EQUIV = float(os.environ.get("MOOV_INTENSE_EQUIV", "2.0"))
    MOOV_Z2_FACTOR = float(os.environ.get("MOOV_Z2_FACTOR", "0.66"))
    MOOV_Z3_FACTOR = float(os.environ.get("MOOV_Z3_FACTOR", "1.33"))

    # ─────────────────────────────────────────────────────────────
    # Parámetros del sistema (timings, caches, etc.)
    # ─────────────────────────────────────────────────────────────
    HR_CACHE_TTL = int(os.environ.get("HR_CACHE_TTL", "1"))

    # Ventana de “reciente” para lecturas HR (ms) en /live
    LIVE_RECENT_MS = int(os.environ.get("LIVE_RECENT_MS", "45000"))
    # Umbral de “desconexión visual” y reset de acumulados
    LIVE_FADE_MS = int(os.environ.get("LIVE_FADE_MS", "60000"))
    # Umbral para no retro-integrar huecos largos (ms)
    LIVE_INTEGRATE_MAX_GAP_MS = int(os.environ.get("LIVE_INTEGRATE_MAX_GAP_MS", "10000"))
