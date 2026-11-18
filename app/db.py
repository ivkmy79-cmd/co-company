"""SQLite schema, connections and online backup."""
import sqlite3
from pathlib import Path

from . import config
from .weeks import now_kst

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    field TEXT,
    consultant TEXT,
    start_date TEXT,
    end_date TEXT,
    kpi_mode TEXT NOT NULL DEFAULT 'task' CHECK (kpi_mode IN ('task', 'category')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    no INTEGER NOT NULL,
    category TEXT,
    sub_category TEXT,
    task_type TEXT,
    title TEXT NOT NULL,
    owner TEXT,
    status TEXT NOT NULL DEFAULT '대기' CHECK (status IN ('대기', '진행중', '완료', '보류')),
    start_date TEXT,
    due_date TEXT,
    done_date TEXT,
    is_added INTEGER NOT NULL DEFAULT 0,
    improve_item TEXT,
    unit TEXT,
    target TEXT,
    before_val TEXT,
    after_val TEXT,
    effect TEXT,
    rate REAL,
    participants TEXT,
    to_be TEXT,
    saved_hours TEXT,
    saved_basis TEXT,
    money_effect TEXT,
    qual_effect TEXT,
    check_items TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tasks_company ON tasks(company_id);
CREATE TABLE IF NOT EXISTS progress_logs (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    progress INTEGER NOT NULL CHECK (progress BETWEEN 0 AND 100),
    content TEXT,
    issue TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, year, week)
);
CREATE TABLE IF NOT EXISTS weekly_notes (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    report_date TEXT,
    summary TEXT,
    common TEXT,
    detail TEXT,
    next_visit_date TEXT,
    next_visit_time TEXT,
    next_plan TEXT,
    UNIQUE (company_id, year, week)
);
"""

TABLES = ("companies", "tasks", "progress_logs", "weekly_notes")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def row_counts(conn: sqlite3.Connection) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def backup(conn: sqlite3.Connection, backup_dir: Path | None = None, keep: int | None = None) -> dict:
    """Online-backup the live DB, verify the copy, prune old copies.

    Returns {ok, path, integrity, counts, source_counts}."""
    backup_dir = Path(backup_dir or config.BACKUP_DIR)
    keep = config.BACKUP_KEEP if keep is None else keep
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_kst().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"app_{stamp}.db"
    n = 1
    while dest.exists():
        dest = backup_dir / f"app_{stamp}_{n}.db"
        n += 1

    source_counts = row_counts(conn)
    dst = sqlite3.connect(dest)
    try:
        conn.backup(dst)
    finally:
        dst.close()

    check = sqlite3.connect(dest)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {t: check.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        check.close()

    ok = integrity == "ok" and counts == source_counts
    copies = sorted(backup_dir.glob("app_*.db"))
    for old in copies[:-keep] if keep > 0 else []:
        old.unlink()
    return {"ok": ok, "path": str(dest), "integrity": integrity,
            "counts": counts, "source_counts": source_counts}
