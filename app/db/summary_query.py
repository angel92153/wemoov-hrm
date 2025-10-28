from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict, Counter
from statistics import mean
import json
import time

from app.db.repos import UsersRepo
from app.db.connection import get_conn, get_summaries_conn
from app.services.metrics_core import (
    _edad_from_user,
    hrmax_from_user_or_estimada,
    frac_hrmax,
)

# ─────────────────────────────────────────────────────────────
# Helpers (DB cruda -> on-the-fly)
# ─────────────────────────────────────────────────────────────

def _get_last_run(con) -> Optional[Dict[str, Any]]:
    cur = con.cursor()
    row = cur.execute(
        "SELECT id, started_at_ms, ended_at_ms FROM session_runs ORDER BY ended_at_ms DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    rid = row["id"] if isinstance(row, dict) else row[0]
    started = row["started_at_ms"] if isinstance(row, dict) else row[1]
    ended = row["ended_at_ms"] if isinstance(row, dict) else row[2]
    return {"id": rid, "started_at_ms": int(started or 0), "ended_at_ms": int(ended or 0)}


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
    """Devuelve: { dev_id: [ {ts, hr, zone}, ... ] }"""
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
      - por bucket: t, zone_mode (moda), hr_avg, frac(0..1)
      - global: session_frac_avg (media ponderada por nº muestras)
    """
    if not rows:
        return [], 0.0
    if bucket_ms <= 0:
        bucket_ms = 5000

    buckets: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        ts = r.get("ts"); hr = r.get("hr"); z = r.get("zone") or "Z1"
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
        num = sum(f * w for f, w in fracs); den = sum(w for _, w in fracs) or 1
        session_frac_avg = float(num) / float(den)
    else:
        session_frac_avg = 0.0

    return ordered, session_frac_avg


def load_last_run_summary(db_path: str, users_repo: UsersRepo, bucket_ms: int = 5000) -> Dict[str, Any]:
    """
    Devuelve resumen de la ÚLTIMA sesión (por ended_at_ms) con detalle por-device.
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


# ─────────────────────────────────────────────────────────────
# Persistencia (GLOBAL + PER-DEVICE en summaries.db)
# ─────────────────────────────────────────────────────────────

def _merge_device_timelines(devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Funde timelines por 't' (media de frac, moda de zone, media de hr)."""
    if not devices:
        return []
    by_t: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"fracs": [], "zones": [], "hrs": []})
    for d in devices:
        for item in (d.get("timeline") or []):
            t = item.get("t")
            if t is None:
                continue
            t = int(t)
            frac = item.get("frac")
            hr = item.get("hr_avg")
            z = item.get("zone_mode")
            if isinstance(frac, (int, float)):
                by_t[t]["fracs"].append(float(frac))
            if isinstance(hr, (int, float)):
                by_t[t]["hrs"].append(float(hr))
            if isinstance(z, str):
                by_t[t]["zones"].append(z)

    merged = []
    for t in sorted(by_t.keys()):
        fracs = by_t[t]["fracs"]
        hrs = by_t[t]["hrs"]
        zones = by_t[t]["zones"]
        if not (fracs or zones):
            continue
        avg_frac = float(mean(fracs)) if fracs else 0.0
        avg_hr = float(mean(hrs)) if hrs else 0.0
        zone = Counter(zones).most_common(1)[0][0] if zones else "Z1"
        merged.append({
            "t": t,
            "frac": round(avg_frac, 4),
            "hr_avg": round(avg_hr, 2),
            "zone_mode": zone,
        })
    return merged


def _upsert_run_summary(
    *,
    run_id: str,
    started_at_ms: int,
    ended_at_ms: int,
    pct_avg: float,          # 0..1
    kcal_total: float,
    moov_total: float,
    bucket_ms: int,
    timeline: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Inserta/actualiza resumen GLOBAL (tabla run_summaries).
    Sella generated_at_ms dentro de meta_json para poder evaluar frescura.
    """
    meta_out = dict(meta or {})
    meta_out.setdefault("generated_at_ms", int(time.time() * 1000))

    with get_summaries_conn() as con:
        con.execute(
            """
            INSERT INTO run_summaries
              (run_id, started_at_ms, ended_at_ms, pct_avg, kcal_total, moov_total, bucket_ms, timeline_json, meta_json)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
              started_at_ms=excluded.started_at_ms,
              ended_at_ms=excluded.ended_at_ms,
              pct_avg=excluded.pct_avg,
              kcal_total=excluded.kcal_total,
              moov_total=excluded.moov_total,
              bucket_ms=excluded.bucket_ms,
              timeline_json=excluded.timeline_json,
              meta_json=excluded.meta_json
            """,
            (
                run_id,
                int(started_at_ms),
                int(ended_at_ms),
                float(pct_avg),
                float(kcal_total),
                float(moov_total),
                int(bucket_ms),
                json.dumps(timeline or [], separators=(",", ":")),
                (json.dumps(meta_out, separators=(",", ":")) if meta_out is not None else None),
            ),
        )


def _upsert_run_summary_device(
    *,
    run_id: str,
    dev: int,
    user_id: int | None,
    apodo: str,
    pct_avg: int,   # 0..100
    kcal: float,
    points: float
) -> None:
    """Inserta/actualiza métricas por dispositivo (tabla run_summaries_devices)."""
    with get_summaries_conn() as con:
        con.execute(
            """
            INSERT INTO run_summaries_devices (run_id, dev, user_id, apodo, pct_avg, kcal, points)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(run_id, dev) DO UPDATE SET
              user_id=excluded.user_id,
              apodo=excluded.apodo,
              pct_avg=excluded.pct_avg,
              kcal=excluded.kcal,
              points=excluded.points
            """,
            (run_id, int(dev), user_id, apodo, int(pct_avg), float(kcal), float(points)),
        )


def _insert_run_summary_device_timeline(*, run_id: str, dev: int, timeline: List[Dict[str, Any]]) -> None:
    """Inserta timeline por dispositivo (tabla run_summaries_devices_timeline)."""
    if not timeline:
        return
    rows = [
        (run_id, int(dev), int(b.get("t") or 0),
         float(b.get("frac") or 0.0), str(b.get("zone_mode") or "Z1"))
        for b in timeline if isinstance(b, dict)
    ]
    with get_summaries_conn() as con:
        con.executemany(
            "INSERT INTO run_summaries_devices_timeline (run_id, dev, t, frac, zone_mode) VALUES (?,?,?,?,?)",
            rows,
        )


def compute_and_store_last_run_summary(
    sessions_runs_db_path: str,   # ← SESSION_RUNS_DB_PATH (crudo)
    users_repo: UsersRepo,
    *,
    bucket_ms: int = 5000,
    meta: Optional[dict] = None,
) -> Optional[str]:
    """
    1) Usa load_last_run_summary(...) (detalle por-dev)
    2) Agrega a nivel GLOBAL (kcal/points totales, %HRmax medio, timeline fusionada)
    3) Persiste:
       - GLOBAL en run_summaries
       - POR-DEVICE en run_summaries_devices + run_summaries_devices_timeline
    Devuelve run_id si se guardó algo.
    """
    front = load_last_run_summary(sessions_runs_db_path, users_repo, bucket_ms=bucket_ms)
    run = front.get("run") or {}
    devices = front.get("devices") or []
    if not run or not devices:
        return None

    run_id = run.get("id")
    started_at_ms = int(run.get("started_at_ms") or 0)
    ended_at_ms = int(run.get("ended_at_ms") or 0)

    total_kcal = 0.0
    total_points = 0.0
    pct_list: List[float] = []

    for d in devices:
        m = d.get("metrics") or {}
        total_kcal += float(m.get("kcal") or 0.0)
        total_points += float(m.get("points") or 0.0)
        if isinstance(m.get("pct_avg"), (int, float)):
            pct_list.append(float(m["pct_avg"]))  # viene 0..100

    pct_avg_global = (mean(pct_list) / 100.0) if pct_list else 0.0  # guardamos 0..1
    merged_timeline = _merge_device_timelines(devices)

    # Sello de generación
    meta_out = dict(meta or {})
    meta_out.setdefault("generated_at_ms", int(time.time() * 1000))

    _upsert_run_summary(
        run_id=run_id,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        pct_avg=float(round(pct_avg_global, 4)),
        kcal_total=float(round(total_kcal, 1)),
        moov_total=float(round(total_points, 1)),
        bucket_ms=int(bucket_ms),
        timeline=merged_timeline,
        meta=meta_out,
    )

    # Per-device + timelines (idempotente por run_id)
    with get_summaries_conn() as con:
        con.execute("DELETE FROM run_summaries_devices WHERE run_id = ?", (run_id,))
        con.execute("DELETE FROM run_summaries_devices_timeline WHERE run_id = ?", (run_id,))

    for d in devices:
        dev_id = int(d.get("dev"))
        user = d.get("user") or {}
        uid = user.get("id") if isinstance(user.get("id"), int) else None
        apodo = (user.get("apodo") or f"ID {dev_id}").strip()
        m = d.get("metrics") or {}
        pct_i = int(round(float(m.get("pct_avg") or 0)))
        kcal = float(m.get("kcal") or 0.0)
        pts  = float(m.get("points") or 0.0)
        tl   = d.get("timeline") or []

        _upsert_run_summary_device(
            run_id=run_id, dev=dev_id, user_id=uid, apodo=apodo,
            pct_avg=pct_i, kcal=kcal, points=pts
        )
        _insert_run_summary_device_timeline(run_id=run_id, dev=dev_id, timeline=tl)

    return run_id


# ─────────────────────────────────────────────────────────────
# Lectura de lo persistido (para el front) — PER-DEVICE
# ─────────────────────────────────────────────────────────────

def load_last_persisted_summary_devices() -> Dict[str, Any]:
    """
    Lee el ÚLTIMO run de summaries.db (por frescura real) y devuelve el shape por dispositivo.
    Ordenamos por COALESCE(ended_at_ms, meta.generated_at_ms, rowid) DESC para evitar
    coger resúmenes antiguos si ended_at_ms está vacío o repetido.
    """
    with get_summaries_conn() as con:
        row = con.execute(
            """
            SELECT run_id, started_at_ms, ended_at_ms, pct_avg, kcal_total, moov_total,
                   bucket_ms, timeline_json, meta_json
            FROM run_summaries
            ORDER BY COALESCE(ended_at_ms,
                              CAST(json_extract(meta_json,'$.generated_at_ms') AS INTEGER),
                              rowid) DESC
            LIMIT 1
            """
        ).fetchone()

        if not row:
            return {"run": None, "devices": [], "bucket_ms": 5000, "meta": None, "global": None, "timeline": []}

        return _build_payload_from_row(row)


def load_persisted_summary_devices_by_run_id(run_id: str) -> Optional[Dict[str, Any]]:
    """Devuelve el resumen POR-DEVICE para el run_id indicado. Si no existe, None."""
    with get_summaries_conn() as con:
        row = con.execute(
            """
            SELECT run_id, started_at_ms, ended_at_ms, pct_avg, kcal_total, moov_total,
                   bucket_ms, timeline_json, meta_json
            FROM run_summaries
            WHERE run_id = ?
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return None
        return _build_payload_from_row(row)


def _build_payload_from_row(row: Any) -> Dict[str, Any]:
    run_id     = row["run_id"] if isinstance(row, dict) else row[0]
    started_ms = row["started_at_ms"] if isinstance(row, dict) else row[1]
    ended_ms   = row["ended_at_ms"]   if isinstance(row, dict) else row[2]
    pct01      = row["pct_avg"]       if isinstance(row, dict) else row[3]
    kcal_tot   = row["kcal_total"]    if isinstance(row, dict) else row[4]
    moov_tot   = row["moov_total"]    if isinstance(row, dict) else row[5]
    bucket_ms  = row["bucket_ms"]     if isinstance(row, dict) else row[6]
    tline_js   = row["timeline_json"] if isinstance(row, dict) else row[7]
    meta_js    = row["meta_json"]     if isinstance(row, dict) else row[8]

    # meta (para exponer generated_at_ms si existe)
    try:
        meta = json.loads(meta_js) if meta_js else None
    except Exception:
        meta = None
    gen_ms = None
    if isinstance(meta, dict):
        g = meta.get("generated_at_ms")
        try:
            gen_ms = int(g) if isinstance(g, (int, float, str)) and str(g).isdigit() else None
        except Exception:
            gen_ms = None

    # leer per-device
    with get_summaries_conn() as con:
        dev_rows = con.execute(
            """
            SELECT dev, user_id, apodo, pct_avg, kcal, points
            FROM run_summaries_devices
            WHERE run_id = ?
            ORDER BY dev ASC
            """,
            (run_id,),
        ).fetchall()

        devices: List[Dict[str, Any]] = []
        for dr in dev_rows:
            dev    = dr["dev"] if isinstance(dr, dict) else dr[0]
            uid    = dr["user_id"] if isinstance(dr, dict) else dr[1]
            apodo  = dr["apodo"] if isinstance(dr, dict) else dr[2]
            pct    = dr["pct_avg"] if isinstance(dr, dict) else dr[3]  # 0..100
            kcal   = dr["kcal"] if isinstance(dr, dict) else dr[4]
            points = dr["points"] if isinstance(dr, dict) else dr[5]

            tl_rows = con.execute(
                """
                SELECT t, frac, zone_mode
                FROM run_summaries_devices_timeline
                WHERE run_id = ? AND dev = ?
                ORDER BY t ASC
                """,
                (run_id, dev),
            ).fetchall()

            timeline = []
            for tr in tl_rows:
                t    = tr["t"] if isinstance(tr, dict) else tr[0]
                frac = tr["frac"] if isinstance(tr, dict) else tr[1]   # 0..1
                zone = tr["zone_mode"] if isinstance(tr, dict) else tr[2]
                timeline.append({"t": int(t), "frac": float(frac), "zone_mode": str(zone or "Z1")})

            devices.append({
                "dev": int(dev),
                "user": {"id": (int(uid) if uid is not None else None), "apodo": (apodo or f"ID {dev}")},
                "metrics": {"pct_avg": int(pct or 0), "kcal": float(kcal or 0.0), "points": float(points or 0.0)},
                "timeline": timeline,
            })

    # compat legacy (global+timeline)
    try:
        tline_global = json.loads(tline_js) if tline_js else []
    except Exception:
        tline_global = []

    payload = {
        "run": {
            "id": run_id,
            "started_at_ms": int(started_ms or 0),
            "ended_at_ms": int(ended_ms or 0),
            **({"generated_at_ms": int(gen_ms)} if isinstance(gen_ms, int) else {}),
        },
        "devices": devices,
        "bucket_ms": int(bucket_ms or 5000),
        "meta": meta,
        "global": {
            "pct_avg": int(round(float(pct01 or 0.0) * 100.0)),
            "kcal": float(kcal_tot or 0.0),
            "points": float(moov_tot or 0.0),
        },
        "timeline": tline_global,
    }
    return payload
