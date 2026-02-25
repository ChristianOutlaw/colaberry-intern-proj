"""
execution/db/sqlite.py

SQLite helper module for deterministic local persistence.
Provides only infrastructure: path resolution, connection setup, and schema initialization.
No business logic lives here.
"""

import os
import sqlite3
from pathlib import Path


def get_db_path() -> str:
    """Return the absolute path to the local SQLite database file.

    The file lives under the repo's /tmp folder (which is safe to delete
    and is never committed). Creates the directory if it does not exist.

    Returns:
        str: Absolute path to tmp/app.db relative to the repo root.
    """
    repo_root = Path(__file__).resolve().parents[2]  # execution/db/sqlite.py -> repo root
    tmp_dir = repo_root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return str(tmp_dir / "app.db")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open and return a sqlite3 connection with foreign key enforcement enabled.

    Args:
        db_path: Path to the SQLite file. Defaults to the result of get_db_path().

    Returns:
        sqlite3.Connection: An open connection with PRAGMA foreign_keys = ON.
    """
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row  # rows accessible by column name
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all application tables if they do not already exist.

    Safe to call multiple times (uses CREATE TABLE IF NOT EXISTS).
    Does not drop or migrate existing tables.

    Schema:
        leads               — core person record
        course_invites      — records that a "Free Intro to AI Class" invite was sent
        progress_events     — individual progress updates (phase/section level)
        course_state        — computed current position and completion for a lead
        hot_lead_signals    — derived readiness-for-booking indicator (rule-based)
        sync_records        — outbox audit log for GHL push attempts (future use)

    Args:
        conn: An open sqlite3.Connection (foreign keys should already be ON).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id          TEXT PRIMARY KEY,
            phone       TEXT,
            email       TEXT,
            name        TEXT,
            created_at  TEXT,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS course_invites (
            id            TEXT PRIMARY KEY,
            lead_id       TEXT NOT NULL,
            sent_at       TEXT,
            channel       TEXT,
            metadata_json TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads (id)
        );

        CREATE TABLE IF NOT EXISTS progress_events (
            id            TEXT PRIMARY KEY,
            lead_id       TEXT NOT NULL,
            section       TEXT,
            occurred_at   TEXT,
            metadata_json TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads (id)
        );

        CREATE TABLE IF NOT EXISTS course_state (
            lead_id          TEXT PRIMARY KEY,
            current_section  TEXT,
            completion_pct   REAL,
            last_activity_at TEXT,
            updated_at       TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads (id)
        );

        CREATE TABLE IF NOT EXISTS hot_lead_signals (
            lead_id    TEXT PRIMARY KEY,
            signal     TEXT,
            score      REAL,
            reason     TEXT,
            updated_at TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads (id)
        );

        CREATE TABLE IF NOT EXISTS sync_records (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id       TEXT NOT NULL,
            destination   TEXT NOT NULL,
            status        TEXT NOT NULL,
            reason        TEXT,
            payload_json  TEXT,
            response_json TEXT,
            error         TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            FOREIGN KEY (lead_id) REFERENCES leads (id) ON DELETE CASCADE,
            UNIQUE (lead_id, destination, status)
        );

        CREATE INDEX IF NOT EXISTS idx_sync_records_status
            ON sync_records (status);

        CREATE INDEX IF NOT EXISTS idx_sync_records_lead_id
            ON sync_records (lead_id);
    """)
    conn.commit()
