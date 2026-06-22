"""Dedupe node — SHA-256 content check.

Deterministic: no AI involved. If a file with the same content has
already been approved and moved, it is a duplicate and routes to .dupes/.
A file that was previously rejected is NOT considered a duplicate
(the user may want to re-evaluate it).
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from src.db import hash_exists, log_action
from src.state import FileState


def make_dedupe_node(conn: sqlite3.Connection):
    def node_dedupe(state: FileState) -> dict:
        path = Path(state["path"])

        # In scan mode (skip_content=True) we never read the file — classification
        # is by extension only, so content-based dedup is skipped too.
        # Path-based dedup is already handled in scanner.py before graph.invoke().
        if state.get("skip_content"):
            return {"file_hash": "", "is_duplicate": False}

        try:
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as exc:
            print(f"[dedupe] cannot read {path.name}: {type(exc).__name__}: {exc}")
            file_hash = ""

        is_duplicate = hash_exists(conn, file_hash)

        # Update the DB row with the hash so we can query it later
        if file_hash:
            conn.execute(
                "UPDATE action_log SET file_hash = ? WHERE thread_id = ? AND id = "
                "(SELECT MAX(id) FROM action_log WHERE thread_id = ?)",
                (file_hash, state["thread_id"], state["thread_id"]),
            )
            conn.commit()

        if is_duplicate:
            print(f"[dedupe] duplicate detected: {path.name} ({file_hash[:8]}…)")
        return {"file_hash": file_hash, "is_duplicate": is_duplicate}

    return node_dedupe
