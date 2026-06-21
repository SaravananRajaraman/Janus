"""SqliteSaver checkpointer factory.

Keeps the LangGraph checkpoint state in the same DB file as the action log
so there is only one file to back up / inspect.

Each file event gets a thread_id (uuid). When the graph pauses at the
human_approval interrupt, the full state is written to the checkpointer.
Resuming with Command(resume=decision) reads the state back and continues
the exact thread — even across process restarts.
"""
from __future__ import annotations

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver


def make_checkpointer(db_path: str) -> SqliteSaver:
    """Return a SqliteSaver bound to the given SQLite file.

    Uses a separate connection from the action-log connection so that
    LangGraph's own schema management doesn't interfere with ours.
    WAL mode is set so concurrent readers don't block the writer.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(conn)
