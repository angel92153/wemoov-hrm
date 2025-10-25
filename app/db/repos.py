# app/db/repos.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
import sqlite3
from flask import current_app

from app.db.connection import get_conn

# ==========================================================
#                      USERS REPO
# ==========================================================

class UsersRepo:
    """Repositorio de acceso a la tabla 'users'."""

    def __init__(self, db_path: Optional[str] = None):
        # Toma la ruta de la config si no se pasa explícitamente
        self.db_path = db_path or current_app.config["USERS_DB_PATH"]

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
        return dict(r)

    def list(self, limit: int = 1000) -> List[Dict[str, Any]]:
        with get_conn(self.db_path) as c:
            rows = c.execute(
                """SELECT id,nombre,apellido,apodo,sexo,dob,peso,
                          device_id,hr_rest,hr_max,hr_max_auto,is_sim
                   FROM users
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        with get_conn(self.db_path) as c:
            r = c.execute(
                """SELECT id,nombre,apellido,apodo,sexo,dob,peso,
                          device_id,hr_rest,hr_max,hr_max_auto,is_sim
                   FROM users WHERE id=?""",
                (user_id,),
            ).fetchone()
            return self._row_to_dict(r) if r else None

    def create(self, **data) -> int:
        required = ["nombre", "apellido", "apodo", "sexo", "dob", "peso"]
        for k in required:
            if not data.get(k):
                raise ValueError(f"El campo '{k}' es obligatorio.")
        with get_conn(self.db_path) as c:
            cur = c.execute(
                """INSERT INTO users
                   (nombre,apellido,apodo,sexo,dob,peso,
                    device_id,hr_rest,hr_max,hr_max_auto,is_sim)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["nombre"], data["apellido"], data["apodo"], data["sexo"].upper(),
                    data["dob"], float(data["peso"]),
                    data.get("device_id"), data.get("hr_rest"), data.get("hr_max"),
                    int(data.get("hr_max_auto", 1)), int(data.get("is_sim", 0))
                ),
            )
            return int(cur.lastrowid)

    def update(self, user_id: int, **fields) -> Dict[str, Any]:
        allowed = {"nombre","apellido","apodo","sexo","dob","peso",
                   "device_id","hr_rest","hr_max","hr_max_auto","is_sim"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            vals.append(v)
        if not sets:
            return self.get(user_id)
        with get_conn(self.db_path) as c:
            c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*vals, user_id))
        return self.get(user_id)

    def delete(self, user_id: int) -> None:
        with get_conn(self.db_path) as c:
            c.execute("DELETE FROM users WHERE id=?", (user_id,))

# ==========================================================
#                    SESSIONS REPO
# ==========================================================

COLOR_GREEN  = "#16a34a"
COLOR_PURPLE = "#6b21a8"
COLOR_YELLOW = "#eab308"
COLOR_BLUE   = "#1d4ed8"

class SessionsRepo:
    """Repositorio de acceso a las tablas de sesiones."""

    def __init__(self, db_path: Optional[str] = None):
        # Usa la ruta correcta de la configuración
        self.db_path = db_path or current_app.config["SESSIONS_DB_PATH"]

    # ---------- CLASES ----------
    def list_class_models(self) -> List[Dict[str, Any]]:
        with get_conn(self.db_path) as con:
            cur = con.cursor()
            rows = cur.execute("SELECT id,label FROM classes ORDER BY LOWER(label)").fetchall()
            out = []
            for r in rows:
                cid, label = r["id"], r["label"]
                ph = cur.execute(
                    "SELECT idx,phase_key,dur_s,color FROM class_phases WHERE class_id=? ORDER BY idx",
                    (cid,)
                ).fetchall()
                phases = [{"idx":p["idx"],"key":p["phase_key"],"dur_s":p["dur_s"],"color":p["color"]} for p in ph]
                total = sum(p["dur_s"] for p in phases)
                out.append({"id":cid,"label":label,"total_s":total,"phases":phases})
            return out

    def upsert_class(self, class_id: str, label: str, phases: List[Dict[str, Any]]) -> None:
        if class_id == "moov":
            raise ValueError("Moov Class no se puede editar.")
        with get_conn(self.db_path) as con:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO classes(id,label) VALUES(?,?) "
                "ON CONFLICT(id) DO UPDATE SET label=excluded.label",
                (class_id, label),
            )
            cur.execute("DELETE FROM class_phases WHERE class_id=?", (class_id,))
            for i, ph in enumerate(phases):
                cur.execute(
                    "INSERT INTO class_phases(class_id,idx,phase_key,dur_s,color) VALUES(?,?,?,?,?)",
                    (class_id, i, str(ph["key"]), int(ph["dur_s"]), ph.get("color") or COLOR_YELLOW),
                )

    def delete_class(self, class_id: str) -> None:
        if class_id == "moov":
            raise ValueError("Moov Class no se puede eliminar.")
        with get_conn(self.db_path) as con:
            con.execute("DELETE FROM classes WHERE id=?", (class_id,))

    # ---------- SETTINGS ----------
    def get_default_class_id(self) -> str:
        with get_conn(self.db_path) as con:
            row = con.execute("SELECT value FROM settings WHERE key='default_class_id'").fetchone()
            return (row["value"] if row else "moov")

    def set_default_class_id(self, cid: str) -> None:
        with get_conn(self.db_path) as con:
            con.execute(
                "INSERT INTO settings(key,value) VALUES('default_class_id',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (cid,),
            )

    # ---------- CALENDARIO ----------
    def list_schedule(self) -> List[Dict[str, Any]]:
        with get_conn(self.db_path) as con:
            rows = con.execute(
                "SELECT sched_id,dow,time_str FROM weekly_schedule ORDER BY dow,time_str"
            ).fetchall()
            return [dict(r) for r in rows]

    def replace_schedule(self, items: List[Dict[str, Any]]) -> None:
        norm = []
        seen = set()
        for it in items:
            dow = int(it["dow"])
            ts = str(it["time_str"])[:5]
            key = (dow, ts)
            if 0 <= dow <= 6 and key not in seen:
                seen.add(key)
                norm.append({"dow": dow, "time_str": ts})
        with get_conn(self.db_path) as con:
            cur = con.cursor()
            cur.execute("DELETE FROM weekly_schedule")
            cur.executemany(
                "INSERT INTO weekly_schedule(dow,time_str) VALUES(?,?)",
                [(x["dow"], x["time_str"]) for x in sorted(norm, key=lambda a:(a["dow"], a["time_str"]))],
            )

    # ---------- ONE-OFF ----------
    def list_oneoff(self) -> List[Dict[str, Any]]:
        with get_conn(self.db_path) as con:
            rows = con.execute("SELECT ymd,class_id FROM one_off_schedule ORDER BY ymd").fetchall()
            return [dict(r) for r in rows]

    def add_oneoff(self, ymd: str, class_id: str) -> None:
        with get_conn(self.db_path) as con:
            con.execute(
                "INSERT INTO one_off_schedule(ymd,class_id) VALUES(?,?) "
                "ON CONFLICT(ymd) DO UPDATE SET class_id=excluded.class_id",
                (ymd, class_id),
            )

    def delete_oneoff(self, ymd: str) -> None:
        with get_conn(self.db_path) as con:
            con.execute("DELETE FROM one_off_schedule WHERE ymd=?", (ymd,))
