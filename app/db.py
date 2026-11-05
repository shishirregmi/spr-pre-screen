"""SQLite storage. One file, no server. Results are stored as JSON; review actions in their own tables."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    state TEXT NOT NULL,             -- UPLOADED, SCREENING, SCREENED, SCREEN_FAILED, IN_REVIEW, RELEASED, RETURNED
    result_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispositions (
    paper_id TEXT NOT NULL,
    flag_id TEXT NOT NULL,
    action TEXT NOT NULL,            -- accepted | overridden
    reason TEXT,
    reviewer TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (paper_id, flag_id)
);
CREATE TABLE IF NOT EXISTS manual_flags (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    paper_id TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT,
    rulebook_version TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_type TEXT NOT NULL,        -- human | system
    action TEXT NOT NULL,
    paper_id TEXT,
    payload TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            for ddl in ("ALTER TABLE papers ADD COLUMN source_format TEXT DEFAULT 'pdf'",
                        "ALTER TABLE papers ADD COLUMN original_path TEXT",
                        "ALTER TABLE papers ADD COLUMN batch_id TEXT",
                        "ALTER TABLE papers ADD COLUMN screened_at TEXT"):
                try:
                    self.conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass   # column already exists
            self.conn.execute(
                "UPDATE papers SET screened_at = (SELECT max(at) FROM audit WHERE audit.paper_id = papers.id "
                "AND audit.action IN ('screened', 'screen_failed')) WHERE screened_at IS NULL")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    # papers
    def paper(self, paper_id: str) -> dict | None:
        p = self.one("SELECT * FROM papers WHERE id=?", (paper_id,))
        if p and p.get("result_json"):
            p["result"] = json.loads(p.pop("result_json"))
        elif p:
            p.pop("result_json", None)
            p["result"] = None
        return p
