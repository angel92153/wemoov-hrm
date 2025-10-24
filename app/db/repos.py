# app/db/repos.py
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional


class UsersRepo:
    """
    Repositorio simple para la tabla 'users' SIN columna 'edad'.
    Columnas esperadas (orden recomendado):
      id, nombre, apellido, apodo, sexo, dob, peso,
      device_id, hr_rest, hr_max, hr_max_auto, is_sim
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------
    # Helpers
    # ---------------------------
    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
        # Mapea por nombre de columna (más robusto que por índice)
        return {
            "id":          r["id"],
            "nombre":      r["nombre"],
            "apellido":    r["apellido"],
            "apodo":       r["apodo"],
            "sexo":        r["sexo"],
            "dob":         r["dob"],
            "peso":        r["peso"],
            "device_id":   r["device_id"],
            "hr_rest":     r["hr_rest"],
            "hr_max":      r["hr_max"],
            "hr_max_auto": r["hr_max_auto"],
            "is_sim":      r["is_sim"],
        }

    # ---------------------------
    # CRUD
    # ---------------------------
    def list(self, limit: int = 1000) -> List[Dict[str, Any]]:
        sql = """
            SELECT id, nombre, apellido, apodo, sexo, dob, peso,
                   device_id, hr_rest, hr_max, hr_max_auto, is_sim
            FROM users
            ORDER BY id DESC
            LIMIT ?
        """
        with self._conn() as c:
            rows = c.execute(sql, (int(limit),)).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        sql = """
            SELECT id, nombre, apellido, apodo, sexo, dob, peso,
                   device_id, hr_rest, hr_max, hr_max_auto, is_sim
            FROM users
            WHERE id = ?
        """
        with self._conn() as c:
            r = c.execute(sql, (int(user_id),)).fetchone()
            return self._row_to_dict(r) if r else None

    def create(
        self,
        *,
        nombre: str,
        apellido: str,
        apodo: str,
        sexo: str,
        dob: str,                 # 'YYYY-MM-DD'
        peso: float,
        device_id: Optional[int] = None,
        hr_rest: Optional[int] = None,
        hr_max: Optional[int] = None,
        hr_max_auto: int = 1,     # 1=auto, 0=manual
        is_sim: int = 0,
    ) -> int:
        # -------- Validaciones obligatorias --------
        nombre = (nombre or "").strip()
        apellido = (apellido or "").strip()
        apodo = (apodo or "").strip()
        sexo = (sexo or "").strip().upper()
        dob = (dob or "").strip()

        if not nombre:
            raise ValueError("El campo 'nombre' es obligatorio.")
        if not apellido:
            raise ValueError("El campo 'apellido' es obligatorio.")
        if not apodo:
            raise ValueError("El campo 'apodo' es obligatorio.")
        if sexo not in ("M", "F"):
            raise ValueError("El campo 'sexo' es obligatorio y debe ser 'M' o 'F'.")
        if not dob:
            raise ValueError("El campo 'dob' (fecha de nacimiento) es obligatorio (YYYY-MM-DD).")

        try:
            peso_f = float(peso)
            if peso_f <= 0:
                raise ValueError
        except Exception:
            raise ValueError("El campo 'peso' debe ser un número positivo.")

        # Normalizaciones
        device_i = int(device_id) if (device_id is not None and str(device_id).strip() != "") else None
        hr_rest_i = int(hr_rest) if (hr_rest is not None and str(hr_rest).strip() != "") else None
        hr_max_auto_i = 1 if int(hr_max_auto) != 0 else 0
        hr_max_i = None if hr_max_auto_i == 1 else (int(hr_max) if (hr_max is not None and str(hr_max).strip() != "") else None)

        sql = """
            INSERT INTO users
              (nombre, apellido, apodo, sexo, dob, peso,
               device_id, hr_rest, hr_max, hr_max_auto, is_sim)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._conn() as c:
            cur = c.execute(sql, (
                nombre, apellido, apodo, sexo, dob, peso_f,
                device_i, hr_rest_i, hr_max_i, hr_max_auto_i, int(is_sim) if is_sim is not None else 0
            ))
            return int(cur.lastrowid)

    def update(self, user_id: int, **fields) -> Dict[str, Any]:
        """
        Actualiza campos permitidos. Devuelve el usuario resultante.
        """
        allowed = {
            "nombre", "apellido", "apodo", "sexo", "dob", "peso",
            "device_id", "hr_rest", "hr_max", "hr_max_auto", "is_sim"
        }

        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("device_id", "hr_rest", "hr_max", "hr_max_auto", "is_sim"):
                v = (int(v) if v is not None and v != "" else None)
            elif k == "peso":
                v = (float(v) if v is not None and v != "" else None)
                if v is not None and v <= 0:
                    raise ValueError("El campo 'peso' debe ser un número positivo.")
            elif k == "sexo":
                v = (str(v).strip().upper() if v is not None and v != "" else None)
                if v is not None and v not in ("M", "F"):
                    raise ValueError("El campo 'sexo' debe ser 'M' o 'F'.")
            elif k in ("nombre", "apellido", "apodo", "dob"):
                v = (v.strip() if isinstance(v, str) else v)

            sets.append(f"{k}=?")
            vals.append(v)

        if sets:
            sql = f"UPDATE users SET {', '.join(sets)} WHERE id=?"
            with self._conn() as c:
                c.execute(sql, (*vals, int(user_id)))

        # Devuelve el usuario final
        got = self.get(user_id)
        if not got:
            raise ValueError("Usuario no encontrado tras actualizar.")
        return got

    def delete(self, user_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM users WHERE id=?", (int(user_id),))
