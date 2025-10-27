# app/db/session_runs_schema.py
from app.db.connection import get_conn

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS session_runs (
  id TEXT PRIMARY KEY,
  started_at_ms INTEGER,
  ended_at_ms   INTEGER,
  meta_json     TEXT
);

CREATE TABLE IF NOT EXISTS session_device_totals (
  run_id     TEXT,
  dev_id     INTEGER,
  user_id    INTEGER,
  apodo      TEXT,
  kcal_total REAL,
  moov_total REAL,
  PRIMARY KEY (run_id, dev_id),
  FOREIGN KEY (run_id) REFERENCES session_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_device_history (
  run_id TEXT,
  dev_id INTEGER,
  user_id INTEGER,
  ts_ms  INTEGER,
  hr     INTEGER,
  zone   TEXT,
  FOREIGN KEY (run_id) REFERENCES session_runs(id) ON DELETE CASCADE
);

-- Índices útiles para consultas típicas
CREATE INDEX IF NOT EXISTS idx_hist_run_dev_ts ON session_device_history(run_id, dev_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_hist_run_user    ON session_device_history(run_id, user_id);
CREATE INDEX IF NOT EXISTS idx_totals_run_user  ON session_device_totals(run_id, user_id);
"""

def ensure_session_runs_schema(db_path: str) -> None:
    """Crea el esquema de la DB de runs si no existe."""
    with get_conn(db_path) as con:
        con.executescript(SCHEMA)
