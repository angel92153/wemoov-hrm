# app/db/summaries_init.py
from app.db.connection import get_conn

_SUMMARIES_SCHEMA = """
PRAGMA journal_mode=WAL;

-- ===============================
-- Tabla principal (resumen GLOBAL)
-- ===============================
CREATE TABLE IF NOT EXISTS run_summaries (
  run_id         TEXT PRIMARY KEY,
  started_at_ms  INTEGER,
  ended_at_ms    INTEGER,
  pct_avg        REAL,          -- 0..1 global
  kcal_total     REAL,
  moov_total     REAL,
  bucket_ms      INTEGER NOT NULL,
  timeline_json  TEXT,          -- merge global (legacy)
  meta_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_summ_by_time ON run_summaries(ended_at_ms DESC);

-- ===============================================
-- Detalle por dispositivo/usuario (cabeceras por dev)
-- ===============================================
CREATE TABLE IF NOT EXISTS run_summaries_devices (
  run_id   TEXT NOT NULL,
  dev      INTEGER NOT NULL,
  user_id  INTEGER,
  apodo    TEXT,
  pct_avg  INTEGER,             -- 0..100
  kcal     REAL,
  points   REAL,
  PRIMARY KEY (run_id, dev)
);

-- ==========================================
-- Timeline por dispositivo (detalle temporal)
-- ==========================================
CREATE TABLE IF NOT EXISTS run_summaries_devices_timeline (
  run_id     TEXT NOT NULL,
  dev        INTEGER NOT NULL,
  t          INTEGER NOT NULL,  -- epoch en ms
  frac       REAL NOT NULL,     -- 0..1
  zone_mode  TEXT NOT NULL,     -- Z1..Z5
  PRIMARY KEY (run_id, dev, t)
);
"""

def ensure_summaries_schema(db_path: str) -> None:
    """Crea las tablas de summaries (global + por-device) si no existen."""
    with get_conn(db_path) as con:
        con.executescript(_SUMMARIES_SCHEMA)
