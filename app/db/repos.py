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
                """SELECT id,nombre,apellido,apodo,email,sexo,dob,peso,
                          device_id,demo_device,hr_rest,hr_max,hr_max_auto,is_sim
                   FROM users
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        with get_conn(self.db_path) as c:
            r = c.execute(
                """SELECT id,nombre,apellido,apodo,email,sexo,dob,peso,
                          device_id,demo_device,hr_rest,hr_max,hr_max_auto,is_sim
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
                   (nombre,apellido,apodo,email,sexo,dob,peso,
                    device_id,demo_device,hr_rest,hr_max,hr_max_auto,is_sim)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["nombre"],
                    data["apellido"],
                    data["apodo"],
                    (data.get("email") or None),                  # email opcional
                    str(data["sexo"]).upper(),
                    data["dob"],
                    float(data["peso"]),
                    data.get("device_id"),
                    data.get("demo_device"),
                    data.get("hr_rest"),
                    data.get("hr_max"),
                    int(data.get("hr_max_auto", 1)),
                    int(data.get("is_sim", 0)),
                ),
            )
            return int(cur.lastrowid)

    def update(self, user_id: int, **fields) -> Dict[str, Any]:
        allowed = {
            "nombre","apellido","apodo","email","sexo","dob","peso",
            "device_id","demo_device","hr_rest","hr_max","hr_max_auto","is_sim"
        }
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            # Normalizaciones ligeras
            if k == "sexo" and v is not None:
                v = str(v).upper()
            if k == "peso" and v is not None:
                v = float(v)
            if k in {"hr_rest","hr_max","hr_max_auto","is_sim","device_id","demo_device"} and v is not None:
                v = int(v)
            # email puede ser None → se guardará NULL
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

    # --- Asignación exclusiva del demo concreto (no toca otros demos) ---
    def assign_demo_device(self, user_id: int, demo_device: int | None) -> None:
        """Mover/poner un demo_device concreto a un usuario.
        - Si demo_device es None -> desasigna del usuario.
        - Si demo_device tiene valor -> lo quita de quien lo tenga y se lo pone a user_id.
        NO afecta a otros demo_device distintos."""
        with get_conn(self.db_path) as c:
            cur = c.cursor()
            cur.execute("BEGIN")
            try:
                if demo_device is None:
                    # quitar solo del usuario indicado
                    cur.execute("UPDATE users SET demo_device=NULL WHERE id=?", (int(user_id),))
                else:
                    # quitar el MISMO demo_device en otros usuarios (si lo tenían)
                    cur.execute("UPDATE users SET demo_device=NULL WHERE demo_device=?", (int(demo_device),))
                    # y asignarlo al usuario destino
                    cur.execute("UPDATE users SET demo_device=? WHERE id=?", (int(demo_device), int(user_id)))
                c.commit()
            except Exception:
                c.rollback()
                raise


# ==========================================================
#                 DEMO DEVICES REPO
# ==========================================================

class DemoDevicesRepo:
    """Repositorio para la tabla 'demo_devices' (id, device_id)."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or current_app.config["USERS_DB_PATH"]

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
        return dict(r)

    def list(self) -> List[Dict[str, Any]]:
        with get_conn(self.db_path) as c:
            rows = c.execute(
                "SELECT id, device_id FROM demo_devices ORDER BY device_id"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def create(self, device_id: int, id_: Optional[int] = None) -> int:
        """Crea un demo device. Si se pasa id_, se inserta con ese ID (tu front lo exige).
        Lanza sqlite3.IntegrityError si hay duplicados de device_id o id."""
        if device_id is None:
            raise ValueError("device_id requerido")
        with get_conn(self.db_path) as c:
            if id_ is None:
                cur = c.execute(
                    "INSERT INTO demo_devices(device_id) VALUES(?)",
                    (int(device_id),)
                )
                return int(cur.lastrowid)
            else:
                c.execute(
                    "INSERT INTO demo_devices(id, device_id) VALUES(?, ?)",
                    (int(id_), int(device_id))
                )
                return int(id_)

    def delete(self, row_id: int) -> None:
        with get_conn(self.db_path) as c:
            c.execute("DELETE FROM demo_devices WHERE id=?", (int(row_id),))


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
