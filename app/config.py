import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
    USERS_DB_PATH = os.environ.get("USERS_DB_PATH", "users.db")
    SESSIONS_DB_PATH = os.environ.get("SESSIONS_DB_PATH", "sessions.db")
    HR_PROVIDER = os.environ.get("HR_PROVIDER", "sim")  # "sim" o "real"
    HR_CACHE_TTL = int(os.environ.get("HR_CACHE_TTL", "1"))  # si luego cacheamos lecturas
    HR_PROVIDER = "sim"
