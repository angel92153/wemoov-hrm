# app/db/summary_query.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
from statistics import mean

from app.db.repos import UsersRepo
from app.db.connection import get_conn
from app.services.metrics_core import (
    _edad_from_user,
    hrmax_from_user_or_estimada,
    frac_hrmax,
)

def _get_last_run(con) -> Optional[Dict[str, Any]]:
    cur = con.cursor()
    row = cur.execute(
        "SELECT id, started_at_ms, ended_at_ms FROM session_runs ORDER BY ended_at_ms DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    # row puede ser Row o dict; normalizamos a dict
    rid = row["id"] if isinstance(row, dict) else row[0]
    started = row["started_at_ms"] if isinstance(row, dict) else row[1]
    ended = row["ended_at_ms"] if isinstance(row, dict) else row[2]
    return {"id": rid, "started_at_ms": started, "ended_at_ms": ended}

def _load_totals(con, run_id: str) -> List[Dict[str, Any]]:
    cur = con.cursor()
    rows = cur.execute(
        """
        SELECT dev_id, user_id, apodo, kcal_total, moov_total
        FROM session_device_totals
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchall()
    out = []
    for r in rows:
        dev_id = r["dev_id"] if isinstance(r, dict) else r[0]
        user_id = r["user_id"] if isinstance(r, dict) else r[1]
        apodo = r["apodo"] if isinstance(r, dict) else r[2]
        kcal = r["kcal_total"] if isinstance(r, dict) else r[3]
        moov = r["moov_total"] if isinstance(r, dict) else r[4]
        out.append({
            "dev_id": int(dev_id),
            "user_id": int(user_id) if user_id is not None else None,
            "apodo": apodo or (f"ID {dev_id}"),
            "kcal_total": float(kcal or 0.0),
            "moov_total": float(moov or 0.0),
        })
    return out

def _load_history(con, run_id: str) -> Dict[int, List[Dict[str, Any]]]:
    """
    Devuelve: { dev_id: [ {ts, hr, zone}, ... ] }
    """
    cur = con.cursor()
    rows = cur.execute(
        """
        SELECT dev_id, ts_ms, hr, zone
        FROM session_device_history
        WHERE run_id = ?
        ORDER BY dev_id, ts_ms ASC
        """,
        (run_id,),
    ).fetchall()
    out: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        dev_id = r["dev_id"] if isinstance(r, dict) else r[0]
        ts_ms  = r["ts_ms"]  if isinstance(r, dict) else r[1]
        hr     = r["hr"]     if isinstance(r, dict) else r[2]
        zone   = r["zone"]   if isinstance(r, dict) else r[3]
        out[int(dev_id)].append({
            "ts": int(ts_ms) if ts_ms is not None else None,
            "hr": int(hr) if hr is not None else None,
            "zone": str(zone or "Z1"),
        })
    return out

def _bucketize(rows: List[Dict[str, Any]], bucket_ms: int, hr_max: int) -> Tuple[List[Dict[str, Any]], float]:
    """
    Bucketiza por bucket_ms y calcula:
      - por bucket: t, zone_mode (más frecuente), hr_avg, frac (0..1)
      - global: session_frac_avg (media ponderada por muestras)
    """
    if not rows:
        return [], 0.0
    if bucket_ms <= 0:
        bucket_ms = 5000

    buckets: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        ts = r.get("ts")
        hr = r.get("hr")
        z  = r.get("zone") or "Z1"
        if ts is None or hr is None:
            continue
        t0 = (int(ts) // bucket_ms) * bucket_ms
        b = buckets.get(t0)
        if b is None:
            b = {"t": t0, "hrs": [], "zones": {}}
            buckets[t0] = b
        b["hrs"].append(int(hr))
        zi = {"Z1":1, "Z2":2, "Z3":3, "Z4":4, "Z5":5}.get(z, 1)
        b["zones"][zi] = b["zones"].get(zi, 0) + 1

    ordered = []
    fracs = []
    for t0 in sorted(buckets.keys()):
        b = buckets[t0]
        if not b["hrs"]:
            continue
        hr_avg = mean(b["hrs"])
        zone_mode_i = max(b["zones"].items(), key=lambda kv: kv[1])[0] if b["zones"] else 1
        zone_mode = {1:"Z1",2:"Z2",3:"Z3",4:"Z4",5:"Z5"}.get(zone_mode_i, "Z1")
        frac = frac_hrmax(hr_avg, hr_max) if hr_max else 0.0
        ordered.append({"t": t0, "zone_mode": zone_mode, "hr_avg": round(hr_avg,2), "frac": round(float(frac), 4)})
        fracs.append((frac, len(b["hrs"])))

    if fracs:
        num = sum(f * w for f, w in fracs)
        den = sum(w for _, w in fracs) or 1
        session_frac_avg = float(num) / float(den)
    else:
        session_frac_avg = 0.0

    return ordered, session_frac_avg

def load_last_run_summary(db_path: str, users_repo: UsersRepo, bucket_ms: int = 5000) -> Dict[str, Any]:
    """
    Devuelve resumen de la ÚLTIMA sesión (por ended_at_ms):
    {
      "run": {"id": str, "started_at_ms": int, "ended_at_ms": int},
      "devices": [
        {
          "dev": int,
          "user": {"id": int|None, "apodo": str},
          "metrics": {
            "pct_avg": int,    # media %HRmax sesión
            "kcal": float,
            "points": float
          },
          "timeline": [       # buckets para relief: usar 'zone_mode' y 'frac' (0..1)
            {"t": int, "zone_mode": "Z3", "frac": 0.74},
            ...
          ]
        },
        ...
      ]
    }
    """
    with get_conn(db_path) as con:
        run = _get_last_run(con)
        if not run:
            return {"run": None, "devices": []}

        totals = _load_totals(con, run["id"])
        hist = _load_history(con, run["id"])

        devices_out: List[Dict[str, Any]] = []
        for row in totals:
            dev_id = row["dev_id"]
            uid = row["user_id"]
            # cargar usuario para calcular HRmax
            user = None
            if isinstance(uid, int):
                try:
                    user = users_repo.get(uid)
                except Exception:
                    user = None
            user = user or {}

            edad = _edad_from_user(user)
            hrmax_user = user.get("hr_max") if user else None
            hr_max = hrmax_from_user_or_estimada(edad, hrmax_user)

            buckets, session_frac_avg = _bucketize(hist.get(dev_id, []), bucket_ms, hr_max)

            devices_out.append({
                "dev": dev_id,
                "user": {"id": uid, "apodo": row["apodo"]},
                "metrics": {
                    "pct_avg": int(round(session_frac_avg * 100.0)),
                    "kcal": round(row["kcal_total"], 1),
                    "points": round(row["moov_total"], 1),
                },
                "timeline": buckets,
            })

        return {"run": run, "devices": devices_out}
