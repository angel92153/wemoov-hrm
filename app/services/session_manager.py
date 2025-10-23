# ===================== session_manager.py =====================
"""
Gestor de sesiones/clases con:
- Catálogo editable (crear/editar/borrar clases con fases {key, dur_s, color}).
- Estado de sesión (activa, programada, pausada).
- Controles: start/stop/status/next/prev/toggle_pause/schedule/unschedule.
- Calendario semanal (L->D) con SOLO horarios; clase por defecto global.
- Programación puntual por día (sin hora).
- Persistencia en SQLite (sessions.db). Sin dependencias de Flask).
"""

import time
import sqlite3
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path

# ======================= COLORES =======================
COLOR_GREEN  = "#16a34a"  # warm up
COLOR_PURPLE = "#6b21a8"  # demo / transiciones
COLOR_YELLOW = "#eab308"  # bloques
COLOR_BLUE   = "#1d4ed8"  # cooldown

DB_PATH = Path("sessions.db")

# ======================= INIT DB =======================
DDL = [
    # clases
    """
    CREATE TABLE IF NOT EXISTS classes (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL
    );
    """,
    # fases de cada clase
    """
    CREATE TABLE IF NOT EXISTS class_phases (
        class_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        phase_key TEXT NOT NULL,
        dur_s INTEGER NOT NULL,
        color TEXT NOT NULL,
        PRIMARY KEY (class_id, idx),
        FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
    );
    """,
    # calendario semanal: 0=Lunes ... 6=Domingo (SOLO horarios)
    """
    CREATE TABLE IF NOT EXISTS weekly_schedule (
        sched_id INTEGER PRIMARY KEY AUTOINCREMENT,
        dow INTEGER NOT NULL CHECK(dow BETWEEN 0 AND 6),
        time_str TEXT NOT NULL -- 'HH:MM'
    );
    """,
    # programaciones puntuales por fecha concreta (SOLO día, sin hora)
    """
    CREATE TABLE IF NOT EXISTS one_off_schedule (
        ymd TEXT PRIMARY KEY,   -- YYYY-MM-DD
        class_id TEXT NOT NULL,
        FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
    );
    """,
    # registro de arranques/consumos de weekly para no duplicar en el mismo día
    """
    CREATE TABLE IF NOT EXISTS schedule_log (
        sched_id INTEGER NOT NULL,
        ymd TEXT NOT NULL, -- YYYY-MM-DD
        last_start_ts REAL NOT NULL,
        PRIMARY KEY (sched_id, ymd)
    );
    """,
    # settings simples clave-valor (default_class_id)
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
]


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # ⚙️ PRAGMAs de integridad + rendimiento
    cur.execute("PRAGMA foreign_keys=ON;")
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA temp_store=MEMORY;")
    cur.execute("PRAGMA cache_size=-20000;")  # ~20MB
    cur.close()
    return conn


def init_db_with_defaults():
    with _connect() as con:
        cur = con.cursor()

        # 1) TABLAS
        for stmt in DDL:
            cur.executescript(stmt)

        # 2) ÍNDICES
        cur.executescript("""
        CREATE INDEX IF NOT EXISTS idx_class_phases_class
            ON class_phases(class_id, idx);
        CREATE INDEX IF NOT EXISTS idx_weekly_dow_time
            ON weekly_schedule(dow, time_str);
        CREATE INDEX IF NOT EXISTS idx_schedule_log
            ON schedule_log(sched_id, ymd);
        """)

        # 3) DATOS POR DEFECTO
        cur.execute("SELECT COUNT(*) c FROM classes")
        if cur.fetchone()["c"] == 0:
            cur.execute("INSERT INTO classes(id,label) VALUES(?,?)", ("moov", "Moov Class"))
            phases = [
                ("moov", 0, "WARM UP",  3*60, COLOR_GREEN),
                ("moov", 1, "DEMO",     3*60, COLOR_PURPLE),
                ("moov", 2, "B1",       9*60, COLOR_YELLOW),
                ("moov", 3, "T1",       2*60, COLOR_PURPLE),
                ("moov", 4, "B2",       9*60, COLOR_YELLOW),
                ("moov", 5, "T2",       3*60, COLOR_PURPLE),
                ("moov", 6, "B3",       9*60, COLOR_YELLOW),
                ("moov", 7, "T3",       2*60, COLOR_PURPLE),
                ("moov", 8, "B4",       9*60, COLOR_YELLOW),
                ("moov", 9, "COOLDOWN", 3*60, COLOR_BLUE),
            ]
            cur.executemany(
                "INSERT INTO class_phases(class_id,idx,phase_key,dur_s,color) VALUES(?,?,?,?,?)",
                phases,
            )

        # 4) SETTINGS POR DEFECTO
        cur.execute("""
            INSERT OR IGNORE INTO settings(key,value)
            VALUES('default_class_id','moov')
        """)

        con.commit()


# ======================= CATALOGO (DB) =======================

def list_class_models() -> List[Dict[str, Any]]:
    """Devuelve lista: [{id, label, total_s, phases:[{key,dur_s,color,idx}]}]"""
    with _connect() as con:
        cur = con.cursor()
        cur.execute("SELECT id,label FROM classes ORDER BY LOWER(label)")
        rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            cid, label = r["id"], r["label"]
            cur.execute("SELECT idx,phase_key,dur_s,color FROM class_phases WHERE class_id=? ORDER BY idx", (cid,))
            ph_rows = cur.fetchall()
            phases = [
                {"idx": pr["idx"], "key": pr["phase_key"], "dur_s": pr["dur_s"], "color": pr["color"]}
                for pr in ph_rows
            ]
            total = sum(p["dur_s"] for p in phases)
            out.append({"id": cid, "label": label, "total_s": total, "phases": phases})
        return out


def upsert_class(class_id: str, label: str, phases: List[Dict[str, Any]]):
    """Crea o actualiza una clase completa (sobrescribe fases por índice)."""
    if class_id == "moov":
        raise ValueError("Moov Class no se puede editar.")
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO classes(id,label) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label",
            (class_id, label))
        cur.execute("DELETE FROM class_phases WHERE class_id=?", (class_id,))
        for i, ph in enumerate(phases):
            cur.execute(
                "INSERT INTO class_phases(class_id,idx,phase_key,dur_s,color) VALUES(?,?,?,?,?)",
                (class_id, i, str(ph["key"]), int(ph["dur_s"]), ph.get("color") or COLOR_YELLOW),
            )
        con.commit()
    # invalidar caché de fases en la instancia global (si existe)
    try:
        if "SESSION" in globals() and isinstance(SESSION, SessionManager):  # type: ignore  # noqa: F821
            SESSION._invalidate_phase_cache(class_id)  # type: ignore  # noqa: F821
    except Exception:
        pass


def delete_class(class_id: str):
    if class_id == "moov":
        raise ValueError("Moov Class no se puede eliminar.")
    with _connect() as con:
        con.execute("DELETE FROM classes WHERE id=?", (class_id,))
        con.commit()
    try:
        if "SESSION" in globals() and isinstance(SESSION, SessionManager):  # type: ignore  # noqa: F821
            SESSION._invalidate_phase_cache(class_id)  # type: ignore  # noqa: F821
    except Exception:
        pass


def get_phases(class_id: Optional[str]) -> List[Dict[str, Any]]:
    if not class_id:
        return []
    with _connect() as con:
        cur = con.cursor()
        cur.execute("SELECT idx,phase_key,dur_s,color FROM class_phases WHERE class_id=? ORDER BY idx", (class_id,))
        rows = cur.fetchall()
        return [
            {"idx": r["idx"], "key": r["phase_key"], "dur_s": r["dur_s"], "color": r["color"]}
            for r in rows
        ]

# ----- settings -----

def get_default_class_id() -> str:
    with _connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key='default_class_id'").fetchone()
        return (row["value"] if row else "moov")

def set_default_class_id(cid: str):
    with _connect() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES('default_class_id',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (cid,))
        con.commit()


# ======================= HELPERS COUNTDOWN SEMANAL =======================

WEEKLY_LEAD_S = 5 * 60  # 5 minutos

def _mk_epoch_for_local(hh: int, mm: int, days_ahead: int = 0) -> float:
    """Construye epoch local para hoy + days_ahead a HH:MM (segundos=0)."""
    import time as _t
    tm = _t.localtime()
    target = _t.struct_time((
        tm.tm_year, tm.tm_mon, tm.tm_mday + days_ahead,
        hh, mm, 0,  # H M S
        -1, -1, -1
    ))
    return _t.mktime(target)


# ======================= SESSION MANAGER =======================

class SessionManager:
    def __init__(self):
        init_db_with_defaults()
        self.reset()

    # -------- Estado ----------
    def reset(self):
        self.active: bool = False
        self.class_id: Optional[str] = None
        self.start_ts: Optional[float] = None
        self.phases: List[Dict[str, Any]] = []
        self.scheduled_ts: Optional[float] = None  # manual puntual
        self.lead_s: int = 0
        self.paused: bool = False
        self.pause_ts: Optional[float] = None
        self.pause_accum: int = 0

        # caches internas
        self._phase_cache: dict[str, List[Dict[str, Any]]] = {}
        self._weekly_cache = {"rows": None, "ts": 0}

    # -------- Helpers internos ----------
    def _now(self) -> float:
        return time.time()

    def _total_dur(self, phases: Optional[list]) -> int:
        return sum(p["dur_s"] for p in (phases or []))

    def _elapsed(self, now=None) -> int:
        if self.start_ts is None:
            return 0
        if now is None:
            now = self._now()
        base = int(now - self.start_ts)
        paused_extra = self.pause_accum
        if self.paused and self.pause_ts is not None:
            paused_extra += int(now - self.pause_ts)
        return max(0, base - paused_extra)

    def _progress(self, phases, start_ts, now=None) -> Tuple[int,int,Optional[int],int,int,Optional[dict]]:
        if not phases or start_ts is None:
            return (0, 0, None, 0, 0, None)
        if now is None:
            now = self._now()
        total = self._total_dur(phases)
        elapsed = self._elapsed(now)
        if elapsed >= total:
            return (total, total, None, 0, 0, None)
        acc = 0
        for i, ph in enumerate(phases):
            dur = ph["dur_s"]
            if elapsed < acc + dur:
                phase_elapsed = elapsed - acc
                phase_remaining = (acc + dur) - elapsed
                return (elapsed, total, i, phase_elapsed, phase_remaining, ph)
            acc += dur
        return (total, total, None, 0, 0, None)

    # ---------- Helpers de caché ----------
    def _get_phases_cached(self, class_id: Optional[str]) -> List[Dict[str, Any]]:
        if not class_id:
            return []
        if class_id not in self._phase_cache:
            self._phase_cache[class_id] = get_phases(class_id)
        return self._phase_cache[class_id]

    def _invalidate_phase_cache(self, class_id: Optional[str] = None):
        if class_id:
            self._phase_cache.pop(class_id, None)
        else:
            self._phase_cache.clear()

    def _weekly_rows(self):
        """Lee weekly_schedule con caché (TTL 60s)."""
        import time as _t
        now = _t.time()
        if (self._weekly_cache["rows"] is None) or (now - self._weekly_cache["ts"] > 60):
            with _connect() as con:
                self._weekly_cache["rows"] = con.execute(
                    "SELECT sched_id, dow, time_str FROM weekly_schedule"
                ).fetchall()
            self._weekly_cache["ts"] = now
        return self._weekly_cache["rows"]

    def _invalidate_weekly_cache(self):
        self._weekly_cache = {"rows": None, "ts": 0}

    def _next_weekly_occurrence(self, now: float) -> Optional[Tuple[int, float]]:
        """
        Devuelve (sched_id, epoch) del siguiente horario semanal >= now,
        buscando en los próximos 7 días.
        """
        import time as _t
        rows = self._weekly_rows()
        if not rows:
            return None

        tm_now = _t.localtime(now)
        now_dow = tm_now.tm_wday  # 0..6
        candidates: List[Tuple[int, float]] = []

        for r in rows:
            sched_id = int(r["sched_id"])
            dow = int(r["dow"])
            hh, mm = map(int, str(r["time_str"])[:5].split(":"))

            delta_days = (dow - now_dow) % 7
            t0 = _mk_epoch_for_local(hh, mm, days_ahead=delta_days)
            if t0 < now:
                t0 = _mk_epoch_for_local(hh, mm, days_ahead=(delta_days + 7))
            candidates.append((sched_id, t0))

        sched_id, epoch = min(candidates, key=lambda x: x[1])
        return (sched_id, epoch)

    # --- calendario semanal / one-off (día) ---
    def _oneoff_for_day(self, ymd: str) -> Optional[str]:
        with _connect() as con:
            row = con.execute("SELECT class_id FROM one_off_schedule WHERE ymd=?", (ymd,)).fetchone()
            return row["class_id"] if row else None

    def _maybe_autostart_by_calendar(self):
        """
        Disparo por calendario semanal (no preemptivo):
          - Si coincide HH:MM y no se ha consumido hoy:
              * si NO hay sesión activa ni hay 'scheduled_ts' manual => arrancar clase por calendario
              * si SÍ hay sesión activa => NO interrumpir, solo registrar consumo de hoy
        Nota: schedule_log garantiza 1 consumo por (sched_id, ymd).
        """
        now = self._now()
        now_tm = time.localtime(now)
        dow = now_tm.tm_wday  # 0 lunes .. 6 domingo
        ymd = f"{now_tm.tm_year:04d}-{now_tm.tm_mon:02d}-{now_tm.tm_mday:02d}"
        hhmm = f"{now_tm.tm_hour:02d}:{now_tm.tm_min:02d}"

        with _connect() as con:
            cur = con.cursor()
            cur.execute("SELECT sched_id FROM weekly_schedule WHERE dow=? AND time_str=?", (dow, hhmm))
            rows = cur.fetchall()
            if not rows:
                return

            for r in rows:
                sched_id = r["sched_id"]
                # ¿ya consumido hoy?
                cur.execute("SELECT 1 FROM schedule_log WHERE sched_id=? AND ymd=?", (sched_id, ymd))
                if cur.fetchone():
                    continue

                # Clase planificada para el día (one-off > default)
                cls = self._oneoff_for_day(ymd) or get_default_class_id()

                if (not self.active) and (self.scheduled_ts is None):
                    # libre: arrancar por calendario
                    self.start(cls)
                # Siempre registrar consumo del disparo semanal de hoy
                cur.execute(
                    "INSERT OR REPLACE INTO schedule_log(sched_id, ymd, last_start_ts) VALUES(?,?,?)",
                    (sched_id, ymd, now))
                con.commit()
            return

    def _maybe_autostart(self):
        # Programación manual puntual (si llega su hora)
        if self.scheduled_ts is not None and self._now() >= self.scheduled_ts:
            if not self.active:
                # arrancar manual
                self.active = True
                self.start_ts = self._now()
                self.phases = self._get_phases_cached(self.class_id or get_default_class_id())
                self.paused = False
                self.pause_ts = None
                self.pause_accum = 0
            return

        # Calendario semanal (no preemptivo; consume si coincide)
        self._maybe_autostart_by_calendar()

    # -------- API Pública ----------
    def start(self, class_id: str = "moov"):
        """Inicio inmediato (manual). Interrumpe cualquier countdown manual y reemplaza lo que hubiera."""
        self.active = True
        self.class_id = class_id
        self.start_ts = self._now()
        self.phases = self._get_phases_cached(class_id)
        self.scheduled_ts = None  # limpia programación puntual
        self.lead_s = 0
        self.paused = False
        self.pause_ts = None
        self.pause_accum = 0
        if not self.phases:
            raise ValueError(f"Clase desconocida o sin fases: {class_id}")

    def stop(self):
        self.reset()

    def schedule(self, class_id: str, start_epoch: float, lead_s: int = 0):
        """Programa manual puntual (no recurrente) con countdown configurable."""
        if start_epoch <= self._now():
            raise ValueError("La hora debe ser futura")
        self.class_id = class_id
        self.scheduled_ts = float(start_epoch)
        self.lead_s = max(0, int(lead_s))
        self.active = False
        self.start_ts = None
        self.phases = []
        self.paused = False
        self.pause_ts = None
        self.pause_accum = 0

    def unschedule(self):
        self.scheduled_ts = None
        self.lead_s = 0

    # ---- Controles de reproducción / navegación ----
    def toggle_pause(self):
        if not self.active:
            return
        if self.paused:
            if self.pause_ts is not None:
                self.pause_accum += int(self._now() - self.pause_ts)
            self.paused = False
            self.pause_ts = None
        else:
            self.paused = True
            self.pause_ts = self._now()

    def next_phase(self):
        if not self.active or not self.phases:
            return
        now = self._now()
        elapsed, total, idx, *_ = self._progress(self.phases, self.start_ts, now)
        if idx is None:
            return
        acc_before_next = sum(p["dur_s"] for p in self.phases[: idx + 1])
        self.start_ts = now - acc_before_next
        self.pause_accum = 0
        if self.paused:
            self.pause_ts = now

    def prev_phase(self):
        if not self.active or not self.phases:
            return
        now = self._now()
        elapsed, total, idx, phase_elapsed, *_ = self._progress(self.phases, self.start_ts, now)
        if idx is None:
            return
        acc_before_current = sum(p["dur_s"] for p in self.phases[: idx])
        if phase_elapsed < 2 and idx > 0:
            acc_before_current = sum(p["dur_s"] for p in self.phases[: idx - 1])
        self.start_ts = now - acc_before_current
        self.pause_accum = 0
        if self.paused:
            self.pause_ts = now

    def status(self) -> dict:
        """
        - Arranque automático si procede (manual puntual pasado / semanal a la hora exacta).
        - Cuenta atrás:
            * Manual puntual: según self.lead_s.
            * Semanal: 5 minutos antes del siguiente horario (WEEKLY_LEAD_S), solo si no hay otra pending manual.
        """
        self._maybe_autostart()
        now = self._now()
        resp = {
            "active": self.active,
            "class_id": self.class_id,
            "scheduled_ts": self.scheduled_ts,
            "lead_s": self.lead_s,
            "now": now,
            "paused": self.paused,
            "default_class_id": get_default_class_id(),
        }

        # ====== COUNTDOWN PARA PROGRAMACIÓN MANUAL ======
        if not self.active and self.scheduled_ts is not None:
            delta = int(self.scheduled_ts - now)
            resp["countdown_s"] = max(0, delta)
            resp["show_countdown"] = (delta <= self.lead_s)
            resp["total_dur_s"] = self._total_dur(self._get_phases_cached(self.class_id or get_default_class_id()))
            return resp

        # ====== COUNTDOWN PARA CALENDARIO SEMANAL ======
        if not self.active and self.scheduled_ts is None:
            nxt = self._next_weekly_occurrence(now)
            if nxt:
                sched_id, epoch = nxt
                delta = int(epoch - now)
                if 0 <= delta <= WEEKLY_LEAD_S:
                    import time as _t
                    ymd = f"{_t.localtime(epoch).tm_year:04d}-{_t.localtime(epoch).tm_mon:02d}-{_t.localtime(epoch).tm_mday:02d}"
                    planned_class = self._oneoff_for_day(ymd) or get_default_class_id()
                    resp.update({
                        "show_countdown": True,
                        "countdown_s": delta,
                        "next_class_id": planned_class,
                        "scheduled_ts": int(epoch),
                        "total_dur_s": self._total_dur(self._get_phases_cached(planned_class)),
                    })
                    return resp

        # ====== SESIÓN ACTIVA ======
        if self.active:
            phases = self.phases or self._get_phases_cached(self.class_id or get_default_class_id())
            elapsed, total, idx, phase_elapsed, phase_rem, phase = self._progress(phases, self.start_ts, now)
            if idx is None:
                self.stop()
                resp.update({"active": False, "finished": True})
            else:
                resp.update({
                    "elapsed_s": elapsed,
                    "total_s": total,
                    "phase_idx": idx,
                    "phase_key": phase["key"],
                    "phase_remaining_s": phase_rem,
                    "phase_elapsed_s": phase_elapsed,
                    "phase_color": phase["color"],
                })
            return resp

        # Estado inactivo sin countdown
        return resp

    # ------- Calendario semanal -------
    def list_schedule(self) -> List[Dict[str, Any]]:
        with _connect() as con:
            cur = con.cursor()
            cur.execute("SELECT sched_id,dow,time_str FROM weekly_schedule ORDER BY dow,time_str")
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def add_schedule(self, dow: int, time_str: str) -> int:
        with _connect() as con:
            cur = con.cursor()
            cur.execute("INSERT INTO weekly_schedule(dow,time_str) VALUES(?,?)", (dow, time_str))
            con.commit()
            self._invalidate_weekly_cache()
            return cur.lastrowid

    def update_schedule(self, sched_id: int, time_str: Optional[str] = None, dow: Optional[int] = None):
        if time_str is None and dow is None:
            return
        with _connect() as con:
            if time_str is not None and dow is not None:
                con.execute("UPDATE weekly_schedule SET time_str=?, dow=? WHERE sched_id=?", (time_str, dow, sched_id))
            elif time_str is not None:
                con.execute("UPDATE weekly_schedule SET time_str=? WHERE sched_id=?", (time_str, sched_id))
            else:
                con.execute("UPDATE weekly_schedule SET dow=? WHERE sched_id=?", (dow, sched_id))
            con.commit()
            self._invalidate_weekly_cache()

    def delete_schedule(self, sched_id: int):
        with _connect() as con:
            con.execute("DELETE FROM weekly_schedule WHERE sched_id=?", (sched_id,))
            con.commit()
            self._invalidate_weekly_cache()

    def replace_schedule(self, items: List[Dict[str, Any]]):
        norm = []
        seen = set()
        for it in items:
            dow = int(it["dow"])
            ts  = str(it["time_str"])[:5]
            key = (dow, ts)
            if 0 <= dow <= 6 and key not in seen:
                seen.add(key)
                norm.append({"dow": dow, "time_str": ts})
        with _connect() as con:
            cur = con.cursor()
            cur.execute("DELETE FROM weekly_schedule")
            cur.executemany("INSERT INTO weekly_schedule(dow,time_str) VALUES(?,?)",
                            [(x["dow"], x["time_str"]) for x in sorted(norm, key=lambda a:(a["dow"], a["time_str"]))])
            con.commit()
            self._invalidate_weekly_cache()

    # One-off puntual por **día**
    def list_oneoff(self) -> List[Dict[str, Any]]:
        with _connect() as con:
            cur = con.cursor()
            cur.execute("SELECT ymd,class_id FROM one_off_schedule ORDER BY ymd")
            return [dict(r) for r in cur.fetchall()]

    def add_oneoff(self, ymd: str, class_id: str):
        with _connect() as con:
            con.execute(
                "INSERT INTO one_off_schedule(ymd,class_id) VALUES(?,?) ON CONFLICT(ymd) DO UPDATE SET class_id=excluded.class_id",
                (ymd, class_id))
            con.commit()
            self._invalidate_weekly_cache()

    def delete_oneoff(self, ymd: str):
        with _connect() as con:
            con.execute("DELETE FROM one_off_schedule WHERE ymd=?", (ymd,))
            con.commit()
            self._invalidate_weekly_cache()


# Instancia global para usar desde el servidor
SESSION = SessionManager()
