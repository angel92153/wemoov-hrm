# app/db/repos.py
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional


class UsersRepo:
    """
    Repositorio simple para la tabla 'users'.
    - No aplica migraciones (eso lo hace app.db.users_init).
    - Valida campos obligatorios en create().
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------
    # Helpers
    # ---------------------------
    @staticmethod
    def _row_to_dict(r: sqlite3.Row | tuple) -> Dict[str, Any]:
        return {
            "id":          r[0],
            "nombre":      r[1],
            "apellido":    r[2],
            "apodo":       r[3],
            "edad":        r[4],
            "peso":        r[5],
            "device_id":   r[6],
            "sexo":        r[7],
            "hr_rest":     r[8],
            "hr_max":      r[9],
            "is_sim":      r[10],
            "dob":         r[11],
            "hr_max_auto": r[12],
        }

    # ---------------------------
    # CRUD
    # ---------------------------
    def list(self, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            cur = c.cursor()
            cur.execute(
                """
                SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo,
                       hr_rest, hr_max, is_sim, dob, hr_max_auto
                FROM users
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            cur = c.cursor()
            cur.execute(
                """
                SELECT id, nombre, apellido, apodo, edad, peso, device_id, sexo,
                       hr_rest, hr_max, is_sim, dob, hr_max_auto
                FROM users
                WHERE id=?
                """,
                (int(user_id),),
            )
            r = cur.fetchone
            r = cur.fetchone()
            return self._row_to_dict(r) if r else None

    def create(
        self,
        nombre: str,
        apellido: str,
        apodo: Optional[str] = None,
        sexo: Optional[str] = None,
        dob: Optional[str] = None,        # 'YYYY-MM-DD'
        peso: Optional[float] = None,
        device_id: Optional[int] = None,
        hr_rest: Optional[int] = None,
        hr_max: Optional[int] = None,
        hr_max_auto: int = 1,             # 1=auto, 0=manual
        is_sim: int = 0,
        edad: Optional[int] = None,       # legacy opcional
    ) -> int:
        # -------- Validaciones obligatorias --------
        nombre = (nombre or "").strip()
        apellido = (apellido or "").strip()
        apodo = (apodo or "").strip()
        sexo = (sexo or "").strip().upper() if sexo else ""
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
        if peso is None or str(peso).strip() == "":
            raise ValueError("El campo 'peso' es obligatorio.")
        try:
            peso_f = float(peso)
            if peso_f <= 0:
                raise ValueError
        except Exception:
            raise ValueError("El campo 'peso' debe ser un número positivo.")

        # Normalizaciones
        edad_i = int(edad) if (edad is not None and str(edad).strip() != "") else None
        device_i = int(device_id) if (device_id is not None and str(device_id).strip() != "") else None
        hr_rest_i = int(hr_rest) if (hr_rest is not None and str(hr_rest).strip() != "") else None
        hr_max_auto_i = 1 if int(hr_max_auto) != 0 else 0
        # Si hr_max_auto == 1, forzamos hr_max a NULL. Si == 0, intentamos guardar hr_max si viene.
        if hr_max_auto_i == 1:
            hr_max_i = None
        else:
            hr_max_i = int(hr_max) if (hr_max is not None and str(hr_max).strip() != "") else None

        with self._conn() as c:
            cur = c.cursor()
            cur.execute(
                """
                INSERT INTO users
                  (nombre, apellido, apodo, edad, peso, device_id, sexo,
                   hr_rest, hr_max, is_sim, dob, hr_max_auto)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nombre,
                    apellido,
                    apodo,
                    edad_i,
                    peso_f,
                    device_i,
                    sexo,
                    hr_rest_i,
                    hr_max_i,
                    int(is_sim) if is_sim is not None else 0,
                    dob,
                    hr_max_auto_i,
                ),
            )
            return cur.lastrowid

    def update(self, user_id: int, **fields) -> Dict[str, Any]:
        """
        Actualiza campos permitidos. Devuelve el usuario resultante.
        Nota: en update no exigimos obligatorios; solo normalizamos si llegan.
        """
        allowed = {
            "nombre", "apellido", "apodo", "edad", "peso", "device_id", "sexo",
            "hr_rest", "hr_max", "is_sim", "dob", "hr_max_auto"
        }
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("edad", "device_id", "hr_rest", "hr_max", "hr_max_auto", "is_sim"):
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
            with self._conn() as c:
                c.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE id=?",
                    (*vals, int(user_id)),
                )
        return self.get(user_id)  # type: ignore

    def delete(self, user_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM users WHERE id=?", (int(user_id),))
