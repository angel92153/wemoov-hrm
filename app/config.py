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
    # ─────────────────────────────────────────────────────────────
    # Seguridad
    # ─────────────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

    # ─────────────────────────────────────────────────────────────
    # Bases de datos (siempre dentro de app/db salvo rutas absolutas)
    # ─────────────────────────────────────────────────────────────
    USERS_DB_PATH    = _resolve_db("USERS_DB", "users.db")
    SESSIONS_DB_PATH = _resolve_db("SESSIONS_DB", "sessions.db")

    # ─────────────────────────────────────────────────────────────
    # Métricas / Energía / Puntos
    # (Agrupado para que todo lo “de números” esté junto)
    # ─────────────────────────────────────────────────────────────
    # KCAL_MODE:
    #   - "net"   → kcal activas (Keytel ajustado)
    #   - "gross" → kcal totales (activas + basal por MET)
    KCAL_MODE = os.environ.get("KCAL_MODE", "gross")

    # MET_REST:
    #   - MET de reposo para el cómputo de basal en modo "gross".
    #   - 1.0 ≈ ~1 kcal/kg/h (estándar)
    MET_REST = float(os.environ.get("MET_REST", "1.0"))

    # SESSION_MODE:
    #   - Cómo ajustar el gasto por intensidad: "cardio" | "strength" | "mixed"
    #   - mixed aplica factores por zona para simular fuerza/cardio combinado
    SESSION_MODE = os.environ.get("SESSION_MODE", "mixed")

    # Objetivo de puntos (traducción directa del mínimo/óptimo OMS a puntos)
    #   MOOV_TARGET_POINTS: total de puntos del período (p.ej. al mes)
    #   MOOV_TARGET_MINUTES_MOD: minutos de actividad MODERADA que “valen” ese total
    #   MOOV_INTENSE_EQUIV: equivalencia intensa→moderada (OMS ≈ 2.0)
    MOOV_TARGET_POINTS = int(os.environ.get("MOOV_TARGET_POINTS", "3000"))
    MOOV_TARGET_MINUTES_MOD = int(os.environ.get("MOOV_TARGET_MINUTES_MOD", "300"))
    MOOV_INTENSE_EQUIV = float(os.environ.get("MOOV_INTENSE_EQUIV", "2.0"))

    # Escalado alrededor del valor “moderado”:
    #   - Z2 un poco por debajo del valor moderado
    #   - Z3 un poco por encima del valor moderado
    #   - Z4 y Z5 puntúan como “intensa” (rate_mod * MOOV_INTENSE_EQUIV)
    MOOV_Z2_FACTOR = float(os.environ.get("MOOV_Z2_FACTOR", "0.66"))
    MOOV_Z3_FACTOR = float(os.environ.get("MOOV_Z3_FACTOR", "1.33"))

    # ─────────────────────────────────────────────────────────────
    # Parámetros del sistema (timings, caches, etc.)
    # ─────────────────────────────────────────────────────────────
    # TTL cache de HR (s) para proveedores reales/simulados
    HR_CACHE_TTL = int(os.environ.get("HR_CACHE_TTL", "1"))

    # Ventana de “reciente” para lecturas HR (ms) en /live
    LIVE_RECENT_MS = int(os.environ.get("LIVE_RECENT_MS", "45000"))
