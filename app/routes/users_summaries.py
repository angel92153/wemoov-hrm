# app/routes/users_summaries.py
from __future__ import annotations
from flask import Blueprint, jsonify, request, render_template, abort
from typing import Any, Dict, List
from app.db.connection import get_summaries_conn
from datetime import datetime, timezone

bp = Blueprint("users_summaries", __name__)

# -------------------------------
# Página (sirve el HTML)
# -------------------------------
@bp.get("/users/summaries")
def users_summaries_page():
    # Asegúrate de que el archivo exista: app/templates/users_summaries.html
    return render_template("users_summaries.html")

# -------------------------------
# API: usuarios con resúmenes
# -------------------------------
@bp.get("/api/users/with_summaries")
def users_with_summaries():
    try:
        with get_summaries_conn() as con:
            rows = con.execute("""
                SELECT DISTINCT user_id, apodo
                FROM run_summaries_devices
                WHERE user_id IS NOT NULL
                ORDER BY apodo ASC
            """).fetchall()
        data = [
            {"id": r["user_id"], "apodo": r["apodo"]}
            for r in rows if r["user_id"] is not None
        ]
        return jsonify(data)
    except Exception as e:
        abort(500, description=str(e))

# -------------------------------
# API: resúmenes por usuario
# -------------------------------
@bp.get("/api/user_summaries")
def user_summaries():
    try:
        user_id = request.args.get("user_id", type=int)
        if user_id is None:
            abort(400, description="user_id requerido")

        with get_summaries_conn() as con:
            rows = con.execute("""
                SELECT rs.run_id, rs.ended_at_ms, d.pct_avg, d.kcal, d.points
                FROM run_summaries_devices d
                JOIN run_summaries rs ON rs.run_id = d.run_id
                WHERE d.user_id = ?
                ORDER BY rs.ended_at_ms DESC
            """, (user_id,)).fetchall()

        data = [{
            "run_id": r["run_id"],
            "ended_at_ms": r["ended_at_ms"],
            "pct_avg": r["pct_avg"],
            "kcal": r["kcal"],
            "points": r["points"],
        } for r in rows]
        return jsonify(data)
    except Exception as e:
        abort(500, description=str(e))

# -------------------------------
# API: detalle del resumen
# -------------------------------
@bp.get("/api/user_summary_detail")
def user_summary_detail():
    try:
        user_id = request.args.get("user_id", type=int)
        run_id  = request.args.get("run_id", type=str)
        if user_id is None or not run_id:
            abort(400, description="user_id y run_id requeridos")

        with get_summaries_conn() as con:
            row = con.execute("""
                SELECT dev, apodo, pct_avg, kcal, points
                FROM run_summaries_devices
                WHERE user_id = ? AND run_id = ?
            """, (user_id, run_id)).fetchone()

            if not row:
                abort(404, description="Resumen no encontrado")

            tl_rows = con.execute("""
                SELECT t, frac, zone_mode
                FROM run_summaries_devices_timeline
                WHERE run_id = ? AND dev = ?
                ORDER BY t ASC
            """, (run_id, row["dev"])).fetchall()

        timeline = [{"t": t["t"], "frac": t["frac"], "zone_mode": t["zone_mode"]} for t in tl_rows]

        return jsonify({
            "dev": row["dev"],
            "user": {"id": user_id, "apodo": row["apodo"]},
            "metrics": {
                "pct_avg": row["pct_avg"],
                "kcal": row["kcal"],
                "points": row["points"],
            },
            "timeline": timeline,
            "bucket_ms": 5000
        })
    except Exception as e:
        abort(500, description=str(e))

@bp.get("/api/user_totals")
def user_totals():
    """
    Totales de un usuario:
    - all_time: sobre todos sus resúmenes (pct_avg ponderado por duración del run)
    - month: solo desde el primer día del mes actual (UTC) inclusive
    """
    user_id = request.args.get("user_id", type=int)
    if user_id is None:
        abort(400, description="user_id requerido")

    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    month_start_ms = int(month_start.timestamp() * 1000)

    with get_summaries_conn() as con:
        cur = con.cursor()

        # ALL TIME (ponderado por duración)
        row_all = cur.execute("""
            SELECT
              COUNT(*)                                              AS sessions,
              CASE
                WHEN SUM(dur) > 0
                  THEN SUM(pct_avg * dur) * 1.0 / SUM(dur)
                ELSE 0
              END                                                   AS pct_avg_w,
              COALESCE(SUM(kcal), 0)                                AS kcal_total,
              COALESCE(SUM(points), 0)                              AS points_total
            FROM (
              SELECT d.pct_avg, d.kcal, d.points,
                     CASE
                       WHEN rs.started_at_ms IS NOT NULL
                        AND rs.ended_at_ms   IS NOT NULL
                        AND rs.ended_at_ms > rs.started_at_ms
                         THEN (rs.ended_at_ms - rs.started_at_ms)
                       ELSE 0
                     END AS dur
              FROM run_summaries_devices d
              JOIN run_summaries rs ON rs.run_id = d.run_id
              WHERE d.user_id = ?
            ) q
        """, (user_id,)).fetchone()

        # MONTH-TO-DATE (ponderado por duración)
        row_m = cur.execute("""
            SELECT
              COUNT(*)                                              AS sessions,
              CASE
                WHEN SUM(dur) > 0
                  THEN SUM(pct_avg * dur) * 1.0 / SUM(dur)
                ELSE 0
              END                                                   AS pct_avg_w,
              COALESCE(SUM(kcal), 0)                                AS kcal_total,
              COALESCE(SUM(points), 0)                              AS points_total
            FROM (
              SELECT d.pct_avg, d.kcal, d.points,
                     CASE
                       WHEN rs.started_at_ms IS NOT NULL
                        AND rs.ended_at_ms   IS NOT NULL
                        AND rs.ended_at_ms > rs.started_at_ms
                         THEN (rs.ended_at_ms - rs.started_at_ms)
                       ELSE 0
                     END AS dur
              FROM run_summaries_devices d
              JOIN run_summaries rs ON rs.run_id = d.run_id
              WHERE d.user_id = ? AND rs.ended_at_ms >= ?
            ) q
        """, (user_id, month_start_ms)).fetchone()

    return jsonify({
        "all_time": {
            "sessions": int(row_all["sessions"] or 0),
            "pct_avg": round(float(row_all["pct_avg_w"] or 0.0), 1),  # 0..100 ya ponderado
            "kcal_total": float(row_all["kcal_total"] or 0.0),
            "points_total": float(row_all["points_total"] or 0.0),
        },
        "month": {
            "sessions": int(row_m["sessions"] or 0),
            "pct_avg": round(float(row_m["pct_avg_w"] or 0.0), 1),
            "kcal_total": float(row_m["kcal_total"] or 0.0),
            "points_total": float(row_m["points_total"] or 0.0),
            "since_ms": month_start_ms,
        }
    })