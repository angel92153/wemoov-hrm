# app/db/session_dump.py
from __future__ import annotations
import sqlite3
import time
import json
from typing import Dict, Any, List, Optional, Tuple

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
CREATE INDEX IF NOT EXISTS idx_hist_run_user ON session_device_history(run_id, user_id);
CREATE INDEX IF NOT EXISTS idx_totals_run_user ON session_device_totals(run_id, user_id);
"""

def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

def _infer_started_ended_ms(
    data: Dict[int, Dict[str, Any]],
    started_at_ms: Optional[int],
    ended_at_ms: Optional[int],
) -> Tuple[int, int]:
    # Si no nos pasan inicio/fin, inferimos del historial.
    min_ts = None
    max_ts = None
    for payload in (data or {}).values():
        for r in payload.get("history") or []:
            ts = r.get("ts")
            if isinstance(ts, int):
                min_ts = ts if min_ts is None else min(min_ts, ts)
                max_ts = ts if max_ts is None else max(max_ts, ts)
    now_ms = int(time.time() * 1000)
    start = int(started_at_ms) if isinstance(started_at_ms, int) else (min_ts if isinstance(min_ts, int) else now_ms)
    end   = int(ended_at_ms)   if isinstance(ended_at_ms, int)   else (max_ts if isinstance(max_ts, int) else now_ms)
    if end < start:
        end = start
    return start, end

def dump_session_to_db(
    db_path: str,
    data: Dict[int, Dict[str, Any]],
    *,
    run_id: Optional[str] = None,
    started_at_ms: Optional[int] = None,
    ended_at_ms: Optional[int] = None,
    meta_json: Optional[str] = None,
) -> None:
    """
    Persiste una sesión completa (totales + historial por dispositivo/usuario).

    Parámetros:
      - db_path: ruta del SQLite (SESSIONS_DB_PATH).
      - data: resultado de SessionStore.export_state(), con formato:
          {
            dev_id: {
              'user': {'id': int, 'apodo': str, ...} | {},
              'kcal_total': float,
              'moov_total': float,
              'history': [ {'ts': int, 'hr': int, 'zone': 'Z*'}, ... ]
            },
            ...
          }
      - run_id: identificador lógico de la sesión (opcional).
      - started_at_ms / ended_at_ms: tiempos de la sesión en ms (opcional).
      - meta_json: JSON string con metadatos del run (opcional).
    """
    if not data:
        return

    rid = run_id or f"run_{int(time.time()*1000)}"
    started, ended = _infer_started_ended_ms(data, started_at_ms, ended_at_ms)

    conn = sqlite3.connect(db_path)
    try:
        _ensure_schema(conn)
        cur = conn.cursor()

        # Upsert cabecera del run
        cur.execute(
            "INSERT OR REPLACE INTO session_runs (id, started_at_ms, ended_at_ms, meta_json) VALUES (?,?,?,?)",
            (rid, started, ended, meta_json or None),
        )

        # Totales por device (incluye user_id y apodo)
        for dev_id, payload in data.items():
            user = payload.get("user") or {}
            user_id = user.get("id")
            apodo = user.get("apodo")
            cur.execute(
                "INSERT OR REPLACE INTO session_device_totals (run_id, dev_id, user_id, apodo, kcal_total, moov_total) "
                "VALUES (?,?,?,?,?,?)",
                (
                    rid,
                    int(dev_id),
                    int(user_id) if isinstance(user_id, int) else None,
                    str(apodo) if isinstance(apodo, str) else None,
                    float(payload.get("kcal_total") or 0.0),
                    float(payload.get("moov_total") or 0.0),
                ),
            )

        # Historial por device/usuario
        rows: List[Tuple] = []
        for dev_id, payload in data.items():
            user = payload.get("user") or {}
            user_id = user.get("id")
            for r in payload.get("history") or []:
                ts = r.get("ts")
                hr = r.get("hr")
                zone = r.get("zone") or "Z1"
                rows.append(
                    (
                        rid,
                        int(dev_id),
                        int(user_id) if isinstance(user_id, int) else None,
                        int(ts) if isinstance(ts, int) else None,
                        int(hr) if isinstance(hr, int) else None,
                        str(zone),
                    )
                )
        if rows:
            cur.executemany(
                "INSERT INTO session_device_history (run_id, dev_id, user_id, ts_ms, hr, zone) VALUES (?,?,?,?,?,?)",
                rows,
            )

        conn.commit()
    finally:
        conn.close()

def dump_status_meta(
    db_path: str,
    run_id: str,
    status_obj: Optional[dict],
    *,
    merge: bool = True,
) -> None:
    """
    (Opcional) Actualiza meta_json de session_runs con un objeto `status_obj`.
    Si merge=True, intentará combinar con el meta_json existente.
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        if merge:
            cur.execute("SELECT meta_json FROM session_runs WHERE id = ?", (run_id,))
            row = cur.fetchone()
            base = {}
            if row and row[0]:
                try:
                    base = json.loads(row[0])
                except Exception:
                    base = {}
            if isinstance(status_obj, dict):
                base.update(status_obj)
            meta = json.dumps(base, separators=(",", ":"))
        else:
            meta = json.dumps(status_obj or {}, separators=(",", ":"))

        cur.execute("UPDATE session_runs SET meta_json=? WHERE id=?", (meta, run_id))
        conn.commit()
    finally:
        conn.close()
