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


def make_checkpointer(conn: sqlite3.Connection) -> SqliteSaver:
    """Return a SqliteSaver bound to the SAME connection as the action log.

    Sharing one connection means there is exactly ONE writer to the DB file, so
    LangGraph's checkpoint writes can never deadlock against our action-log writes
    on a *separate* connection (the source of the "database is locked" /
    "another row available" errors during a scan). SqliteSaver serialises its own
    access with an internal lock, and pysqlite serialises access from other
    threads (check_same_thread=False), so the shared connection stays safe.

    The connection is expected to already be configured (WAL, busy_timeout,
    autocommit) by ``get_db``.
    """
    return SqliteSaver(conn)
