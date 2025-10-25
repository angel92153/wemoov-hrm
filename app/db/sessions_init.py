# app/db/sessions_init.py
from __future__ import annotations
from .connection import get_conn

COLOR_GREEN  = "#16a34a"
COLOR_PURPLE = "#6b21a8"
COLOR_YELLOW = "#eab308"
COLOR_BLUE   = "#1d4ed8"

DDL_CREATE = [
    """
    CREATE TABLE IF NOT EXISTS classes (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL
    );
    """,
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
    """
    CREATE TABLE IF NOT EXISTS weekly_schedule (
        sched_id INTEGER PRIMARY KEY AUTOINCREMENT,
        dow INTEGER NOT NULL CHECK(dow BETWEEN 0 AND 6),
        time_str TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS one_off_schedule (
        ymd TEXT PRIMARY KEY,
        class_id TEXT NOT NULL,
        FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule_log (
        sched_id INTEGER NOT NULL,
        ymd TEXT NOT NULL,
        last_start_ts REAL NOT NULL,
        PRIMARY KEY (sched_id, ymd)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
]

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_class_phases_class ON class_phases(class_id, idx);",
    "CREATE INDEX IF NOT EXISTS idx_weekly_dow_time   ON weekly_schedule(dow, time_str);",
    "CREATE INDEX IF NOT EXISTS idx_schedule_log      ON schedule_log(sched_id, ymd);",
]

def init_sessions_db(db_path: str | None = None) -> None:
    with get_conn(db_path) as con:
        cur = con.cursor()
        for stmt in DDL_CREATE:
            cur.executescript(stmt)
        for stmt in DDL_INDEXES:
            cur.execute(stmt)

        # Seed por defecto (solo si no hay clases)
        cur.execute("SELECT COUNT(*) AS c FROM classes")
        if (cur.fetchone()["c"] or 0) == 0:
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
            # default settings
            cur.execute("""
                INSERT OR IGNORE INTO settings(key,value)
                VALUES('default_class_id','moov')
            """)
