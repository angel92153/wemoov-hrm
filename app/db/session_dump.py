# app/db/session_dump.py
from __future__ import annotations
import time
import json
from typing import Dict, Any, List, Optional, Tuple

from app.db.connection import get_conn  # ✅ usamos el helper común (PRAGMAs coherentes)

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
      - db_path: ruta del SQLite (SESSION_RUNS_DB_PATH).
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
      - started_at_ms / ended_at_ms: tiempos en ms (opcional).
      - meta_json: JSON string con metadatos del run (opcional).
    """
    if not data:
        return

    rid = run_id or f"run_{int(time.time()*1000)}"
    started, ended = _infer_started_ended_ms(data, started_at_ms, ended_at_ms)

    # ✅ Usamos get_conn para tener PRAGMAs coherentes + commit automático
    with get_conn(db_path) as conn:
        cur = conn.cursor()

        # Cabecera del run (upsert)
        cur.execute(
            "INSERT OR REPLACE INTO session_runs (id, started_at_ms, ended_at_ms, meta_json) VALUES (?,?,?,?)",
            (rid, started, ended, meta_json or None),
        )

        # Totales por device
        for dev_id, payload in data.items():
            user = payload.get("user") or {}
            user_id = user.get("id")
            apodo = user.get("apodo")
            cur.execute(
                "INSERT OR REPLACE INTO session_device_totals "
                "(run_id, dev_id, user_id, apodo, kcal_total, moov_total) "
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
    with get_conn(db_path) as conn:
        cur = conn.cursor()

        if merge:
            cur.execute("SELECT meta_json FROM session_runs WHERE id = ?", (run_id,))
            row = cur.fetchone()
            base = {}
            # row puede ser sqlite3.Row o tuplas
            existing = row["meta_json"] if (row and isinstance(row, dict)) else (row[0] if row else None)
            if existing:
                try:
                    base = json.loads(existing)
                except Exception:
                    base = {}
            if isinstance(status_obj, dict):
                base.update(status_obj)
            meta = json.dumps(base, separators=(",", ":"))
        else:
            meta = json.dumps(status_obj or {}, separators=(",", ":"))

        cur.execute("UPDATE session_runs SET meta_json=? WHERE id=?", (meta, run_id))
