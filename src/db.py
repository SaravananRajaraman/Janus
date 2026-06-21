"""SQLite action log.

Schema
------
action_log  — one row per file event (created by this module)
checkpoints — LangGraph SqliteSaver tables (created by langgraph itself)

Both live in the same .organiser/organiser.db file.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_db(db_path: str) -> sqlite3.Connection:
    """Open (and initialise if new) the organiser database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS action_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    thread_id     TEXT    NOT NULL,
    filename      TEXT    NOT NULL,
    path          TEXT    NOT NULL,
    file_hash     TEXT,
    status        TEXT    NOT NULL,
    proposal      TEXT,
    decision_note TEXT,
    moved_to      TEXT
);
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_log_status  ON action_log(status);
CREATE INDEX IF NOT EXISTS idx_log_thread  ON action_log(thread_id);
CREATE INDEX IF NOT EXISTS idx_log_hash    ON action_log(file_hash);
CREATE INDEX IF NOT EXISTS idx_log_created ON action_log(created_at);
"""


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_TABLE_DDL)
    # Migrate existing DBs that pre-date the file_hash column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(action_log)").fetchall()]
    if "file_hash" not in cols:
        conn.execute("ALTER TABLE action_log ADD COLUMN file_hash TEXT")
        conn.commit()
    conn.executescript(_INDEX_DDL)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def log_action(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    filename: str,
    path: str,
    status: str,
    file_hash: str | None = None,
    proposal: dict | None = None,
    decision_note: str | None = None,
    moved_to: str | None = None,
) -> int:
    """Insert a row and return its id."""
    row_id: int = conn.execute(
        """
        INSERT INTO action_log
               (thread_id, filename, path, file_hash, status, proposal, decision_note, moved_to)
        VALUES (?,          ?,        ?,    ?,         ?,      ?,        ?,             ?)
        """,
        (
            thread_id, filename, path, file_hash, status,
            json.dumps(proposal) if proposal else None,
            decision_note, moved_to,
        ),
    ).lastrowid
    conn.commit()
    return row_id


def update_action(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    status: str,
    decision_note: str | None = None,
    moved_to: str | None = None,
) -> None:
    """Update the most recent row for a thread_id."""
    conn.execute(
        """
        UPDATE action_log
        SET    status        = ?,
               decision_note = COALESCE(?, decision_note),
               moved_to      = COALESCE(?, moved_to)
        WHERE  thread_id = ?
          AND  id = (SELECT MAX(id) FROM action_log WHERE thread_id = ?)
        """,
        (status, decision_note, moved_to, thread_id, thread_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def hash_exists(conn: sqlite3.Connection, file_hash: str) -> bool:
    """Return True if this hash was already approved (i.e. the file was moved)."""
    if not file_hash:
        return False
    row = conn.execute(
        "SELECT id FROM action_log WHERE file_hash = ? AND status = 'approved' LIMIT 1",
        (file_hash,),
    ).fetchone()
    return row is not None


def get_pending_threads(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return rows whose graphs are paused at the approval interrupt."""
    return conn.execute(
        "SELECT * FROM action_log WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM action_log GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


def recent_actions(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
