import os

class Config:
    # --- Seguridad y rutas ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

    # Bases de datos
    USERS_DB_PATH = os.environ.get("USERS_DB_PATH", "users.db")
    SESSIONS_DB_PATH = os.environ.get("SESSIONS_DB_PATH", "sessions.db")

    # --- HR Provider (fuente de datos de frecuencia cardíaca) ---
    # "sim" = simulador | "real" = lector ANT+ real
    HR_PROVIDER = os.environ.get("HR_PROVIDER", "real")

    # Tiempo máximo de vida para una lectura de HR en caché (no usado aún)
    HR_CACHE_TTL = int(os.environ.get("HR_CACHE_TTL", "1"))

    # --- Parámetros del sistema ---
    # Intervalo en milisegundos considerado "reciente" para mostrar dispositivos activos
    LIVE_RECENT_MS = int(os.environ.get("LIVE_RECENT_MS", "45000"))  # 45 s

    # Modo de cálculo de calorías: "net" (activas) o "gross" (activas + basal)
    KCAL_MODE = os.environ.get("KCAL_MODE", "gross")
    MET_REST  = float(os.environ.get("MET_REST", "1.0"))  # 1 MET por defecto

    # Modo de sesión: "mixed", "cardio" o "strength"
    SESSION_MODE = os.environ.get("SESSION_MODE", "mixed")
